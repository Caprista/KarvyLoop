"""cognition/weekly_digest.py — 周报卡:每周一张"这周你的团队干了什么"决策卡。

**为什么**:决策 loop 的人不该主动问"怎么样了"(反模式)——系统每周把事实 push 回决策舱:
跑了哪些任务(成/败)、烧了多少 token(谁烧的)、结晶/修订了什么技能、你拍了哪些板、
口味命中率、还挂着什么。

**数据源纪律**(Trace 是所有评价的唯一数据源,别在别处另算):
- 任务/技能事件:全部从 Trace 读(atom_run / eval_fact / fast_brain_hit / crystallize /
  skill_revision),数字全部可回链(带 trace_ref = "task_id:seq" + 事件里的原 trace_ref)。
- token:TokenLedger(tokens.db)只读窗口查询(window_totals / window_by_source)。
- 拍板:DecisionLog(既有审计流水,query 自带时间窗)。
- 口味命中率:TastePredictionStore.stats()(自带 n≥10 样本门;注意它是**滚动窗**不是本周切片,
  digest 里如实标注)。
- 挂着的:PendingProposalRegistry.pending()(Proposal.ts 算挂龄)。

**零 LLM**:build_weekly_digest 纯确定性汇总。可选的"一段人话总结"走 summarize_fn 注入,
主线接 LLM 时必须自己打 token_source 标(tick 里已兜底裹 token_source("weekly_digest"))。

水位:weekly_digest_tick 落 `~/.karvyloop/weekly_digest_tick.json`,7 天一发,幂等防重
(先 register 成功再推水位;坏文件当空,fail-safe)。**未接 app.py**,接线见模块尾注。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# kind 常量放本模块(⑤a 在改 proposal_registry.py,不去那儿凑热闹;revision.py 同款先例)
KIND_WEEKLY_DIGEST = "weekly_digest"

WINDOW_DAYS = 7                 # 周报窗口(天)
FAILURE_LIST_CAP = 20           # 失败清单上限(卡别无限长;总数照实报)
PENDING_LIST_CAP = 10           # 挂着的清单上限
TASTE_MIN_N = 10                # 口味命中率样本门(与 crystallize.taste_eval.MIN_N 同值;不够如实写)


# ---------------------------------------------------------------- 纯确定性汇总

def build_weekly_digest(trace: Any, token_ledger: Any, taste_store: Any,
                        registry: Any, now: float, *,
                        window_days: int = WINDOW_DAYS,
                        decision_log: Any = None,
                        summarize_fn: Optional[Callable[[dict], str]] = None) -> dict:
    """纯确定性周报汇总(零 LLM)。任何一路数据源缺席(None)都如实标 available=False,不崩不编。

    Args:
        trace: TraceStore/SqliteTraceStore(query 带 start_ts/end_ts 时间窗)。
        token_ledger: TokenLedger 或 None。
        taste_store: TastePredictionStore 或 None。
        registry: PendingProposalRegistry 或 None(挂着的卡)。
        now: 窗口右端(Unix ts);窗口 = [now - window_days*86400, now] 闭区间。
        decision_log: DecisionLog 或 None(ACCEPT/REJECT/DEFER 流水;既有账本)。
        summarize_fn: 可选"一段人话总结"接口(digest dict → str)。**本函数不调 LLM**;
            主线注入 LLM 版时打 token_source 标。失败/返空 → summary=None(宁空勿毒)。
    """
    start_ts = float(now) - int(window_days) * 86400.0
    end_ts = float(now)

    by_kind: dict[str, list] = {}
    if trace is not None:
        for tid in trace.all_tasks():
            for e in trace.query(tid, start_ts=start_ts, end_ts=end_ts):
                by_kind.setdefault(e.kind, []).append(e)

    def _ref(e: Any) -> str:
        return f"{e.task_id}:{e.seq}"

    # ---- 任务:atom_run / eval_fact 计数、成功率、失败清单(带 trace_ref)----
    runs = by_kind.get("atom_run", [])
    succeeded = [e for e in runs if (e.payload or {}).get("success")]
    failed = [e for e in runs if not (e.payload or {}).get("success")]
    failures = [{
        "task_id": e.task_id,
        "trace_ref": (e.payload or {}).get("trace_ref") or _ref(e),
        "entry_ref": _ref(e),
        "atom_id": (e.payload or {}).get("atom_id", ""),
        "terminal": (e.payload or {}).get("terminal", ""),
        "ts": e.ts,
    } for e in sorted(failed, key=lambda x: x.ts, reverse=True)[:FAILURE_LIST_CAP]]

    fast_hits = by_kind.get("fast_brain_hit", [])
    eval_facts = by_kind.get("eval_fact", [])
    skill_reruns = [e for e in eval_facts if (e.payload or {}).get("skill_rerun")]
    # 快脑/召回命中率:命中 = stable 回放(fast_brain_hit)+ dynamic 制导重跑(eval_fact.skill_rerun);
    # 分母 = 有 Trace 痕的 drive 总数(fast_brain_hit + atom_run;dynamic 重跑也写 atom_run,不双计)。
    denom = len(fast_hits) + len(runs)
    recall_hit_rate = ((len(fast_hits) + len(skill_reruns)) / denom) if denom else None

    tasks = {
        "atom_runs": len(runs),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "success_rate": (len(succeeded) / len(runs)) if runs else None,
        "eval_facts": len(eval_facts),
        "fast_brain_hits": len(fast_hits),
        "skill_reruns": len(skill_reruns),
        "recall_hit_rate": recall_hit_rate,
        "failures": failures,
        "failures_truncated": max(0, len(failed) - len(failures)),
    }

    # ---- token:tokens.db 只读窗口聚合(谁烧的)----
    if token_ledger is not None:
        tokens = {
            "available": True,
            **token_ledger.window_totals(start_ts=start_ts, end_ts=end_ts),
            "by_source": token_ledger.window_by_source(start_ts=start_ts, end_ts=end_ts),
        }
    else:
        tokens = {"available": False, "total": 0, "calls": 0, "by_source": []}

    # ---- 技能:新结晶 / 修订(29112e9 的 skill_revision 事件)----
    crystallized = [{
        "name": (e.payload or {}).get("name", ""),
        "sig": (e.payload or {}).get("sig", ""),
        "when_to_use": (e.payload or {}).get("when_to_use", ""),
        "trace_ref": (e.payload or {}).get("trace_ref") or _ref(e),
        "entry_ref": _ref(e),
        "ts": e.ts,
    } for e in by_kind.get("crystallize", [])]
    # mode 语义(crystallize/revision.py):auto=小改已落 / h2a_applied=你 ACCEPT 后落地 /
    # proposed=大改出了卡 / noop=触发了但没修动。落地的和只是尝试的分开报,不吹。
    revisions = [{
        "skill_name": (e.payload or {}).get("skill_name", ""),
        "sig": (e.payload or {}).get("sig", ""),
        "mode": (e.payload or {}).get("mode", ""),
        "trigger": (e.payload or {}).get("trigger", ""),
        "entry_ref": _ref(e),
        "ts": e.ts,
    } for e in by_kind.get("skill_revision", [])]
    landed_modes = ("auto", "h2a_applied")
    skills = {
        "crystallized": crystallized,
        "crystallized_count": len(crystallized),
        "revisions": revisions,
        "revisions_landed": sum(1 for r in revisions if r["mode"] in landed_modes),
        "revisions_proposed": sum(1 for r in revisions if r["mode"] == "proposed"),
    }

    # ---- 决策:ACCEPT/REJECT/DEFER 计数(既有 DecisionLog 账本)+ 口味命中率 ----
    if decision_log is not None:
        rows = decision_log.query(since=start_ts, until=end_ts, limit=100000)
        counts = {"ACCEPT": 0, "REJECT": 0, "DEFER": 0, "REVOKE": 0}
        for r in rows:
            d = r.get("decision", "")
            if d in counts:
                counts[d] += 1
        decisions: dict = {"available": True, "total": len(rows), **{k.lower(): v for k, v in counts.items()}}
    else:
        decisions = {"available": False, "total": 0,
                     "accept": 0, "reject": 0, "defer": 0, "revoke": 0}
    # 口味命中率:store 自带 n≥MIN_N 样本门;它是**滚动窗**(近 20 次拍板)不是本周切片,如实标注
    if taste_store is not None:
        ts_stats = taste_store.stats()
        n = int(ts_stats.get("taste_n", 0) or 0)
        decisions["taste"] = {
            "available": True,
            "scope": "rolling",   # 诚实:滚动窗口,非本周切片
            "n": n,
            "hit_rate": ts_stats.get("taste_hit_rate"),
            "prev_rate": ts_stats.get("taste_prev_rate"),
            "trend": ts_stats.get("taste_trend"),
            "enough": bool(ts_stats.get("taste_enough")),
            "need_more": int(ts_stats.get("taste_need_more", 0) or 0),
            "note": "" if ts_stats.get("taste_enough") else f"样本不足(n={n} < {TASTE_MIN_N}),不报百分比",
        }
    else:
        decisions["taste"] = {"available": False, "n": 0, "hit_rate": None,
                              "enough": False, "note": "口味押注未接线"}

    # ---- 挂着的:pending proposals 数 + 最老挂龄 ----
    pending_list = list(registry.pending()) if registry is not None else []
    items = sorted(({
        "proposal_id": getattr(p, "proposal_id", ""),
        "kind": getattr(p, "kind", ""),
        "summary": (getattr(p, "summary", "") or "")[:120],
        "ts": float(getattr(p, "ts", 0.0) or 0.0),
    } for p in pending_list), key=lambda d: d["ts"])
    oldest = items[0] if items else None
    pending = {
        "available": registry is not None,
        "count": len(items),
        "oldest_age_days": (max(0.0, (end_ts - oldest["ts"]) / 86400.0)
                            if oldest and oldest["ts"] > 0 else None),
        "oldest": oldest,
        "items": items[:PENDING_LIST_CAP],
    }

    quiet = (not runs and not fast_hits and not crystallized and not revisions
             and decisions["total"] == 0 and int(tokens.get("calls", 0) or 0) == 0)

    digest = {
        "kind": KIND_WEEKLY_DIGEST,
        "window": {
            "start_ts": start_ts, "end_ts": end_ts, "days": int(window_days),
            "start_label": _label(start_ts), "end_label": _label(end_ts),
        },
        "tasks": tasks,
        "tokens": tokens,
        "skills": skills,
        "decisions": decisions,
        "pending": pending,
        "quiet": quiet,
        "summary": None,   # 人话总结留接口:summarize_fn 注入(主线接 LLM 时打 token_source 标)
    }
    if summarize_fn is not None:
        try:
            s = summarize_fn(digest)
            digest["summary"] = s.strip() if isinstance(s, str) and s.strip() else None
        except Exception:
            logger.warning("[weekly_digest] summarize_fn 失败(总结留空,数字照发)", exc_info=True)
    return digest


def _label(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


# ---------------------------------------------------------------- markdown 渲染

def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def render_digest_markdown(d: dict) -> str:
    """digest dict → 渲染友好的 markdown(卡片正文)。空周诚实:「这周很安静」。"""
    w = d.get("window", {})
    lines = [f"## 周报 · {w.get('start_label', '')} → {w.get('end_label', '')}", ""]
    if d.get("quiet"):
        lines.append("这周很安静:没有任务运行、没烧 token、没有结晶/修订,也没有要你拍的板。")
    t = d.get("tasks", {})
    lines += [
        "### 任务",
        f"- 跑了 **{t.get('atom_runs', 0)}** 次:成 {t.get('succeeded', 0)} / 败 {t.get('failed', 0)}"
        f"(成功率 {_pct(t.get('success_rate'))})",
        f"- 快脑/召回命中率 {_pct(t.get('recall_hit_rate'))}"
        f"(stable 回放 {t.get('fast_brain_hits', 0)} + 技能制导重跑 {t.get('skill_reruns', 0)})",
    ]
    fails = t.get("failures") or []
    if fails:
        lines.append(f"- 失败清单(最近 {len(fails)} 条"
                     + (f",另有 {t.get('failures_truncated')} 条未列" if t.get("failures_truncated") else "")
                     + "):")
        for f in fails:
            term = f" [{f['terminal']}]" if f.get("terminal") else ""
            lines.append(f"  - `{f.get('atom_id') or f.get('task_id')}`{term} → {f.get('trace_ref')}")
    tok = d.get("tokens", {})
    lines += ["", "### Token"]
    if not tok.get("available"):
        lines.append("- 账本未接线(无数据,不猜)")
    else:
        lines.append(f"- 共 **{tok.get('total', 0):,}** tokens / {tok.get('calls', 0)} 次调用"
                     f"(in {tok.get('input', 0):,} / out {tok.get('output', 0):,})")
        for s in (tok.get("by_source") or [])[:8]:
            lines.append(f"  - {s.get('source') or 'unknown'}: {s.get('total', 0):,}({s.get('calls', 0)} 次)")
    sk = d.get("skills", {})
    lines += ["", "### 技能"]
    lines.append(f"- 新结晶 **{sk.get('crystallized_count', 0)}** 个"
                 + ("" if not sk.get("crystallized") else ":"))
    for c in sk.get("crystallized") or []:
        lines.append(f"  - {c.get('name')}(sig {str(c.get('sig'))[:8]},回链 {c.get('trace_ref')})")
    lines.append(f"- 修订:落地 {sk.get('revisions_landed', 0)} / 出卡待你拍 {sk.get('revisions_proposed', 0)}")
    for r in sk.get("revisions") or []:
        lines.append(f"  - {r.get('skill_name')}({r.get('mode')},回链 {r.get('entry_ref')})")
    dec = d.get("decisions", {})
    lines += ["", "### 你拍的板"]
    if not dec.get("available"):
        lines.append("- 决策流水未接线(无数据,不猜)")
    else:
        lines.append(f"- ACCEPT {dec.get('accept', 0)} / REJECT {dec.get('reject', 0)}"
                     f" / DEFER {dec.get('defer', 0)}"
                     + (f" / REVOKE {dec.get('revoke', 0)}" if dec.get("revoke") else ""))
    taste = dec.get("taste") or {}
    if taste.get("enough"):
        trend = taste.get("trend")
        arrow = "" if trend is None else(" ↑" if trend > 0 else (" ↓" if trend < 0 else " →"))
        lines.append(f"- 口味命中率(滚动窗):**{_pct(taste.get('hit_rate'))}**{arrow}(n={taste.get('n', 0)})")
    else:
        lines.append(f"- 口味命中率:{taste.get('note') or '样本不足'}")
    p = d.get("pending", {})
    lines += ["", "### 还挂着的"]
    if p.get("count"):
        age = p.get("oldest_age_days")
        lines.append(f"- **{p.get('count')}** 张卡等你拍"
                     + (f",最老挂了 {age:.1f} 天" if isinstance(age, (int, float)) else ""))
        for it in p.get("items") or []:
            lines.append(f"  - [{it.get('kind')}] {it.get('summary')}")
    else:
        lines.append("- 没有挂着的卡")
    if d.get("summary"):
        lines += ["", "### 一句话", d["summary"]]
    return "\n".join(lines)


# ---------------------------------------------------------------- 出卡 + 周 tick

def build_weekly_digest_proposal(digest: dict, *, now: float):
    """digest → KIND_WEEKLY_DIGEST Proposal(payload = 结构化 digest + markdown)。

    proposal_id 由 (kind, habit_id, summary) 稳定派生,summary 含窗口日期 →
    同一周重建同 id(registry.register 幂等覆盖),不同周不同 id。
    纯信息卡:ACCEPT=已读(无 handler 时 dispatch 只记录,不兑现副作用)。
    """
    from karvyloop.karvy.atoms import Proposal
    w = digest.get("window", {})
    t = digest.get("tasks", {})
    tok = digest.get("tokens", {})
    if digest.get("quiet"):
        gist = "这周很安静(无任务/无消耗)"
    else:
        gist = (f"跑了 {t.get('atom_runs', 0)} 次任务(成 {t.get('succeeded', 0)}/败 {t.get('failed', 0)}),"
                f"烧了 {tok.get('total', 0):,} tokens")
    summary = f"周报 {w.get('start_label', '')}→{w.get('end_label', '')}:{gist}"
    return Proposal(
        summary=summary,
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=1.0,                      # 确定性汇总,非猜测
        evidence_refs=(),
        habit_id=0,
        model_ref="",                      # 零 LLM
        ts=now,
        kind=KIND_WEEKLY_DIGEST,
        payload={"digest": digest, "markdown": render_digest_markdown(digest)},
        basis=("数字全部从 Trace / tokens.db / 决策流水确定性汇总,零 LLM、可回链"
               "(每条带 trace_ref/id)。ACCEPT 仅表示已读,不触发任何执行。"),
    )


def _default_state_path() -> Path:
    return Path.home() / ".karvyloop" / "weekly_digest_tick.json"


def _load_state(path: Optional[Path]) -> dict:
    p = path or _default_state_path()
    if not p.exists():
        return {"last_sent_ts": 0.0, "last_proposal_id": ""}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError("not a dict")
        return {"last_sent_ts": float(d.get("last_sent_ts", 0.0) or 0.0),
                "last_proposal_id": str(d.get("last_proposal_id", "") or "")}
    except Exception:
        return {"last_sent_ts": 0.0, "last_proposal_id": ""}   # 坏文件当空(fail-safe)


def _save_state(state: dict, path: Optional[Path]) -> None:
    p = path or _default_state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[weekly_digest] 水位落盘失败(下轮可能重发一次,不致命): %s", e)


async def weekly_digest_tick(*, trace: Any, token_ledger: Any = None,
                             taste_store: Any = None, registry: Any = None,
                             decision_log: Any = None,
                             summarize_fn: Optional[Callable[[dict], str]] = None,
                             state_path: Optional[Path] = None,
                             now: Optional[float] = None,
                             window_days: int = WINDOW_DAYS) -> dict:
    """周报 tick:距上次发卡 ≥ window_days 天才发,否则幂等跳过(同周重调不重发)。

    水位语义:`state.last_sent_ts` = 上次成功 register 的时刻;**register 成功后才推水位**
    (register 抛出 → 水位不动,下轮重试)。窗口 = [now - window_days 天, now] 闭区间。
    返回 {ran, proposal_id, quiet, reason}。
    """
    if now is None:
        now = time.time()
    state = _load_state(state_path)
    elapsed = now - float(state.get("last_sent_ts", 0.0) or 0.0)
    if elapsed < window_days * 86400.0:
        return {"ran": False, "proposal_id": state.get("last_proposal_id", ""),
                "quiet": False,
                "reason": f"距上次发卡 {elapsed / 86400.0:.1f} 天 < {window_days} 天(幂等跳过)"}
    if summarize_fn is not None:
        # 兜底打 token_source 标(注入的 summarize_fn 若真调 LLM,账记到 weekly_digest 名下)
        from karvyloop.llm.token_ledger import token_source
        with token_source("weekly_digest"):
            digest = build_weekly_digest(trace, token_ledger, taste_store, registry, now,
                                         window_days=window_days, decision_log=decision_log,
                                         summarize_fn=summarize_fn)
    else:
        digest = build_weekly_digest(trace, token_ledger, taste_store, registry, now,
                                     window_days=window_days, decision_log=decision_log)
    card = build_weekly_digest_proposal(digest, now=now)
    if registry is not None:
        registry.register(card)            # 抛出则不推水位(下轮重试)
    state = {"last_sent_ts": now, "last_proposal_id": card.proposal_id}
    _save_state(state, state_path)
    return {"ran": True, "proposal_id": card.proposal_id,
            "quiet": bool(digest.get("quiet")), "reason": ""}


# ---------------------------------------------------------------- 桌面纪念物(P1.5 灵魂缺口③,轻读口)

def memento_from_digest(digest: dict) -> dict:
    """digest → 桌面纪念物(GET /api/desk/memento 的契约形状,冻结):
    {"week_label","tasks_done","skills_new","decisions","tokens_total"}。纯投影,零计算零 LLM。"""
    w = digest.get("window") or {}
    t = digest.get("tasks") or {}
    sk = digest.get("skills") or {}
    dec = digest.get("decisions") or {}
    tok = digest.get("tokens") or {}

    def _i(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "week_label": f"{w.get('start_label', '')} → {w.get('end_label', '')}",
        "tasks_done": _i(t.get("succeeded")),
        "skills_new": _i(sk.get("crystallized_count")),
        "decisions": _i(dec.get("total")),
        "tokens_total": _i(tok.get("total")),
    }


def load_memento(*, registry: Any = None, state_path: Optional[Path] = None) -> Optional[dict]:
    """有 digest 水位就读现成的(别重算重的):水位文件记着上次发的周报卡 id,卡还挂在
    registry 里 → 直接从卡 payload 的结构化 digest 投影纪念物。

    读不到(没发过卡 / 卡已被拍掉 / registry 未接)→ None,调用方自行决定要不要
    确定性重建一份(build_weekly_digest 零 LLM)。纯只读,不动水位不动卡。"""
    if registry is None:
        return None
    state = _load_state(state_path)
    pid = (state.get("last_proposal_id") or "").strip()
    if not pid:
        return None
    try:
        card = registry.get(pid)
    except Exception:
        return None
    payload = getattr(card, "payload", None) or {}
    digest = payload.get("digest") if isinstance(payload, dict) else None
    if not isinstance(digest, dict):
        return None
    return memento_from_digest(digest)


# ---------------------------------------------------------------- 接线(主线做,本模块不碰 app.py)
# console/entry.py 挂周期任务处(daily_poll 同侧)加:
#   from karvyloop.cognition.weekly_digest import weekly_digest_tick
#   await weekly_digest_tick(
#       trace=app.state.trace, token_ledger=get_ledger(),
#       taste_store=getattr(app.state, "taste_predictions", None),
#       registry=app.state.proposal_registry,
#       decision_log=getattr(app.state, "decision_log", None))
# (可选)发卡后走 console.proposals.broadcast_proposal 推前端;不推也会随 pending 快照带出。


__all__ = [
    "KIND_WEEKLY_DIGEST", "WINDOW_DAYS",
    "build_weekly_digest", "render_digest_markdown",
    "build_weekly_digest_proposal", "weekly_digest_tick",
    "memento_from_digest", "load_memento",
]
