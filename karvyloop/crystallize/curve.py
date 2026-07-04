"""curve — 结晶裸分/成长曲线(Trace 回放推导,纯只读投影)。

docs/57 P1「护城河可感知」:把 promote_score / success_rate / 晋级进度做成**时间序列**,
让"越用越像你"变成用户看得见的增长曲线。

铁律(docs/40 §1):Trace 是所有评价的唯一数据源 —— 本模块**只读回放**已有记录做
day 粒度聚合,不改任何记账、不在执行热路径加计算(console 请求时才算)。

数据从 Trace 哪些记录推导(全部是既有事件,零新埋点):
  - ``eval_fact``(drive 每次跑完写;含召回重跑的 ``skill_rerun`` 标)→ 使用/成败流。
    带 ``checker_verdict`` 标的是独立验收回流,不是一次使用(生产 record_verdict 也
    不记 UsageStore)→ 不计入 usage。
  - ``crystallize``(晋级落盘时写)→ 晋级时刻(曲线上 crystallized 翻 true 的那天)。
  - ``skill_revision``(修订审计)→ 修订计数(全库成长曲线的 revisions)。

回放口径与生产记账对齐:
  - 60s 去抖同 observe()(窗口内重复只刷新 last_used,不重复计数);
  - 分数用 crystallize.usage_score **同一个函数**(7 天半衰期),不另造公式;
  - 每个日桶在「当日结束时刻(封顶 now)」评估 —— 长期不用,曲线诚实衰减(用进废退)。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from karvyloop.schemas import UsageStats

from .crystallize import MIN_SUCCESS_RATE, PROMOTE_SCORE
from .crystallize import success_rate as _success_rate
from .crystallize import usage_score as _usage_score
from .revision import REVISION_KIND
from .store import USAGE_DEBOUNCE_SEC
from .trace_eval import EVAL_FACT_KIND

# 每条曲线最多回给前端的点数(sparkline 用不到更久远的;留最新)
MAX_POINTS = 120


def _day_label(ts: float) -> str:
    """本地日历日标签(与 tokens 面板同纪律:day 桶按本地日,不是 UTC ts//86400)。"""
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _day_end(ts: float) -> float:
    """该时刻所属本地日历日的结束时刻(次日本地零点)。"""
    lt = time.localtime(ts)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return midnight + 86400.0


def _collect(trace) -> dict[str, dict[str, Any]]:
    """一遍扫 Trace,按 sig 收三类事件(只读;坏 payload 跳过不阻塞)。"""
    by_sig: dict[str, dict[str, Any]] = {}

    def _slot(sig: str) -> dict[str, Any]:
        return by_sig.setdefault(sig, {"name": "", "runs": [], "crystallized": [],
                                       "revisions": []})

    try:
        task_ids = trace.all_tasks()
    except Exception:
        return {}
    for tid in task_ids:
        try:
            entries = trace.query(tid)
        except Exception:
            continue
        for e in entries:
            p = e.payload or {}
            sig = str(p.get("sig", "") or "")
            if not sig:
                continue   # 归不了属,跳过(不猜)
            if e.kind == EVAL_FACT_KIND:
                if p.get("checker_verdict"):
                    continue   # 独立验收回流是"评",不是一次使用(与生产记账口径一致)
                _slot(sig)["runs"].append(
                    (float(e.ts), bool(p.get("success")), bool(p.get("skill_rerun"))))
            elif e.kind == "crystallize":
                s = _slot(sig)
                s["crystallized"].append(float(e.ts))
                if not s["name"]:
                    s["name"] = str(p.get("name", "") or "")
            elif e.kind == REVISION_KIND:
                _slot(sig)["revisions"].append(float(e.ts))
    return by_sig


def _replay_day_states(rec: dict[str, Any], *, now: float,
                       debounce_sec: float) -> list[tuple[str, float, dict[str, Any]]]:
    """按时间回放一个 sig 的事件 → 每个「有动静的本地日 + 今天」的日终累计状态。

    去抖口径同 observe():窗口内重复使用只刷新 last_used_at,不重复计数。
    返回 [(day_label, eval_at, state)],state = usage/success/reruns/last_used 累计值。
    """
    events: list[tuple[float, str, Any]] = []
    events += [(ts, "run", (ok, rerun)) for ts, ok, rerun in rec["runs"] if ts <= now]
    events += [(ts, "crystallize", None) for ts in rec["crystallized"] if ts <= now]
    events += [(ts, "revision", None) for ts in rec["revisions"] if ts <= now]
    events.sort(key=lambda t: t[0])
    usage = success = reruns = 0
    last_used = 0.0
    day_state: dict[str, dict[str, Any]] = {}
    day_first_ts: dict[str, float] = {}
    for ts, kind, extra in events:
        if kind == "run":
            ok, rerun = extra
            if usage > 0 and ts - last_used <= debounce_sec:
                last_used = ts   # 去抖:只刷新"最近想起"(同 observe)
            else:
                usage += 1
                success += 1 if ok else 0
                reruns += 1 if rerun else 0
                last_used = ts
        day = _day_label(ts)
        day_first_ts.setdefault(day, ts)
        day_state[day] = {"usage": usage, "success": success,
                          "reruns": reruns, "last_used": last_used}
    if events:
        today = _day_label(now)
        if today not in day_state:   # 今天没动静 → 补一个"现在"的点(诚实衰减可见)
            day_first_ts[today] = now
            day_state[today] = {"usage": usage, "success": success,
                                "reruns": reruns, "last_used": last_used}
    return [(day, min(_day_end(day_first_ts[day]), now), day_state[day])
            for day in sorted(day_state, key=lambda d: day_first_ts[d])]


def _skill_points(rec: dict[str, Any], *, now: float, promote_score: float,
                  debounce_sec: float, max_points: int) -> list[dict[str, Any]]:
    """一个 sig 的 day 粒度分数序列。每桶在 eval_at(当日结束,封顶 now)评估:
    usage_score 走 crystallize.usage_score 同一公式(7 天半衰期),不另算。"""
    first_cts = min(rec["crystallized"]) if rec["crystallized"] else None
    points: list[dict[str, Any]] = []
    for day, eval_at, st in _replay_day_states(rec, now=now, debounce_sec=debounce_sec):
        stats = UsageStats(usage_count=st["usage"], success_count=st["success"],
                           last_used_at=st["last_used"])
        score = _usage_score(stats, now=eval_at)
        points.append({
            "day": day,
            "ts": eval_at,
            "usage_count": st["usage"],
            "success_count": st["success"],
            "usage_score": round(score, 4),
            "success_rate": round(_success_rate(stats), 4),
            "promote_progress": round(min(1.0, score / promote_score), 4)
            if promote_score > 0 else 1.0,
            "reruns": st["reruns"],
            "crystallized": bool(first_cts is not None and first_cts <= eval_at),
        })
    return points[-max_points:]


def _growth_points(by_sig: dict[str, dict[str, Any]], *, now: float,
                   debounce_sec: float, max_points: int) -> list[dict[str, Any]]:
    """全库成长曲线:技能数(已晋级的不同 sig)/ 晋级数 / 修订数 / 累计 run /
    平均成功率(有使用的 sig 宏平均)/ 复用命中率(rerun 占比)随时间。"""
    per_sig = {sig: _replay_day_states(rec, now=now, debounce_sec=debounce_sec)
               for sig, rec in by_sig.items()}
    crystallize_ts = sorted(ts for rec in by_sig.values() for ts in rec["crystallized"]
                            if ts <= now)
    first_cts = {sig: min(rec["crystallized"]) for sig, rec in by_sig.items()
                 if rec["crystallized"]}
    revision_ts = sorted(ts for rec in by_sig.values() for ts in rec["revisions"]
                         if ts <= now)
    # 全库日集合 = 各 sig 有动静的日(含补的今天),按 eval_at 排
    day_eval: dict[str, float] = {}
    for states in per_sig.values():
        for day, eval_at, _st in states:
            day_eval[day] = max(day_eval.get(day, 0.0), eval_at)
    idx = {sig: 0 for sig in per_sig}
    cur: dict[str, dict[str, Any]] = {}
    points: list[dict[str, Any]] = []
    for day in sorted(day_eval, key=lambda d: day_eval[d]):
        eval_at = day_eval[day]
        for sig, states in per_sig.items():
            i = idx[sig]
            while i < len(states) and states[i][1] <= eval_at:
                cur[sig] = states[i][2]
                i += 1
            idx[sig] = i
        runs_total = sum(st["usage"] for st in cur.values())
        reruns_total = sum(st["reruns"] for st in cur.values())
        rates = [st["success"] / st["usage"] for st in cur.values() if st["usage"] > 0]
        points.append({
            "day": day,
            "ts": eval_at,
            "skills_total": sum(1 for cts in first_cts.values() if cts <= eval_at),
            "promotions": sum(1 for ts in crystallize_ts if ts <= eval_at),
            "revisions": sum(1 for ts in revision_ts if ts <= eval_at),
            "runs_total": runs_total,
            "avg_success_rate": round(sum(rates) / len(rates), 4) if rates else 0.0,
            "hit_rate": round(reruns_total / runs_total, 4) if runs_total else 0.0,
        })
    return points[-max_points:]


def build_curves(
    trace,
    *,
    now: Optional[float] = None,
    sig: str = "",
    promote_score: float = PROMOTE_SCORE,
    min_success_rate: float = MIN_SUCCESS_RATE,
    debounce_sec: float = USAGE_DEBOUNCE_SEC,
    max_points: int = MAX_POINTS,
    name_resolver: Optional[Callable[[str], str]] = None,
) -> dict[str, Any]:
    """曲线总装(GET /api/skills/curve 的数据面;契约形状别改):

      {"bucket": "day", "promote_score", "min_success_rate",
       "skills": [{"sig", "name", "crystallized_ts", "points": [...]}],
       "growth": {"points": [...]}}

    ``sig``:只要一个技能的曲线(growth 仍是全库 —— 顶部那条不随筛选变)。
    ``name_resolver``:sig→可读名兜底(SkillIndex.name_for_sig;Trace 里没名字的候选用)。
    trace=None / 空库 → 优雅空结构(不猜不编)。
    """
    now = now if now is not None else time.time()
    out: dict[str, Any] = {
        "bucket": "day",
        "promote_score": promote_score,
        "min_success_rate": min_success_rate,
        "skills": [],
        "growth": {"points": []},
    }
    if trace is None:
        return out
    by_sig = _collect(trace)
    if not by_sig:
        return out
    out["growth"]["points"] = _growth_points(by_sig, now=now,
                                             debounce_sec=debounce_sec,
                                             max_points=max_points)
    skills: list[dict[str, Any]] = []
    for s, rec in by_sig.items():
        if sig and s != sig:
            continue
        name = rec["name"]
        if not name and name_resolver is not None:
            try:
                name = str(name_resolver(s) or "")
            except Exception:
                name = ""
        skills.append({
            "sig": s,
            "name": name,
            "crystallized_ts": min(rec["crystallized"]) if rec["crystallized"] else None,
            "points": _skill_points(rec, now=now, promote_score=promote_score,
                                    debounce_sec=debounce_sec, max_points=max_points),
        })
    # 最近有动静的在前(与 skill_lifecycle 同序,面板默认关注活跃技能)
    skills.sort(key=lambda rec_: -(rec_["points"][-1]["ts"] if rec_["points"] else 0.0))
    out["skills"] = skills
    return out


__all__ = ["build_curves", "MAX_POINTS"]
