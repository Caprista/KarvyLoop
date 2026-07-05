"""routes_demo — /api/demo/*:随包演示实例的**只读**浏览(「👀 看一个用了一周的实例」)。

给新用户看"满级号":一个虚构人物「小林/Lin」用了 7 个(虚拟)日历日之后,实例长成什么样。
诚实红线(与随包 manifest 的 disclosure 一致):
- 人物虚构、时间戳是演示用虚拟日历日;**全部机制产物(记忆/技能/曲线/决策偏好)是
  KarvyLoop 真实机制 + 真模型跑出来的**,构建 harness 见 manifest.builder。
- **纯只读**:本模块只有 GET 端点;sqlite 一律 `mode=ro&immutable=1` 打开(不产生
  -wal/-shm,包内文件零改动);json 只读不写。没有任何写路径 → "退出即弃"天然成立,
  与用户实例零污染(不碰 app.state.memory / main_loop / 任何用户存储)。
- 曲线**现算不预烤**:/api/skills/curve 同一个 build_curves,从随包 trace.sqlite 只读
  回放推导 —— 数字可被任何人对着包内数据复核。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_ALLOWED = ("lin-zh", "lin-en")   # 白名单:路径参数绝不拼给文件系统之外的目录


def demo_instances_root() -> Path:
    return Path(__file__).resolve().parent.parent / "demo_instances"


def _instance_dir(iid: str) -> Optional[Path]:
    if iid not in _ALLOWED:
        return None
    d = demo_instances_root() / iid
    return d if (d / "instance.json").exists() else None


def _manifest(d: Path) -> dict:
    try:
        return json.loads((d / "instance.json").read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[demo] manifest 读取失败({d.name}): {e}")
        return {}


def _dir_size(d: Path) -> int:
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file())


# ---- 只读 Trace 读取器(duck-type 兼容 build_curves 只用到的 all_tasks/query)----

class _ReadOnlyTrace:
    """以 sqlite `mode=ro&immutable=1` 打开随包 trace.sqlite:零写入、零 -wal/-shm。"""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True,
            check_same_thread=False)

    def all_tasks(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT task_id FROM trace_entries ORDER BY task_id").fetchall()
        return [r[0] for r in rows]

    def query(self, task_id: str, *, kind: Optional[str] = None) -> list:
        from karvyloop.cognition.trace import TraceEntry
        sql = ("SELECT task_id, seq, kind, payload_json, ts, agent, source "
               "FROM trace_entries WHERE task_id = ?")
        params: list = [task_id]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        rows = self._conn.execute(sql + " ORDER BY seq ASC", tuple(params)).fetchall()
        out = []
        for task, seq, k, payload_json, ts, agent, source in rows:
            out.append(TraceEntry(
                task_id=task, seq=int(seq), kind=k,
                payload=json.loads(payload_json) if payload_json else {},
                ts=float(ts), agent=agent or "", source=source or ""))
        return out

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


def _beliefs(d: Path) -> list:
    """随包 beliefs.json → Belief 对象(纯内存构造,不经 BeliefStore,零写风险)。"""
    from karvyloop.schemas.cognition import Belief
    p = d / "beliefs.json"
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    records = raw if isinstance(raw, list) else raw.get("beliefs", []) if isinstance(raw, dict) else []
    out = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        try:
            out.append(Belief(**{k: v for k, v in rec.items() if k in Belief.model_fields}))
        except Exception:
            continue
    return out


def _skills(d: Path) -> list[dict]:
    """随包 skills/ 的 SKILL.md 元数据(name/description/tags/verified/sig)。"""
    from karvyloop.registry.skills import parse_frontmatter
    root = d / "skills"
    if not root.is_dir():
        return []
    out = []
    for smd in sorted(root.glob("*/SKILL.md")):
        try:
            fm, _body = parse_frontmatter(smd)
        except Exception:
            continue
        raw = fm.raw or {}          # parse_frontmatter 返 SkillFrontmatter dataclass,不是 dict
        out.append({
            "name": str(fm.name or smd.parent.name),
            "sig": str(fm.signature or ""),
            "description": str(fm.description or "")[:200],
            "when_to_use": str(fm.when_to_use or "")[:200],
            "tags": list(fm.tags or []),
            "verified": str(raw.get("verified", "")).strip().lower() == "true",
            "source": str(raw.get("source", "") or "user"),
        })
    return out


def _tokens_by_day(d: Path) -> list[dict]:
    p = d / "tokens.db"
    if not p.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro&immutable=1", uri=True)
        rows = conn.execute(
            "SELECT day, SUM(input), SUM(output), COUNT(*) FROM token_usage "
            "GROUP BY day ORDER BY day").fetchall()
        conn.close()
        return [{"day": r[0], "input": int(r[1] or 0), "output": int(r[2] or 0),
                 "calls": int(r[3] or 0)} for r in rows]
    except Exception as e:
        logger.debug(f"[demo] tokens.db 读取失败: {e}")
        return []


def _taste_progress(d: Path) -> dict:
    """口味押注命中率 + 静音门进度(真实门槛常量;7 天到不了 → 如实展示"在爬")。"""
    from karvyloop.karvy.silence import (
        SILENCE_MIN_N, SILENCE_MIN_WILSON_LB, wilson_lower_bound)
    p = d / "taste_predictions.json"
    outcomes: list[dict] = []
    if p.exists():
        try:
            outcomes = list(json.loads(p.read_text(encoding="utf-8")).get("outcomes") or [])
        except Exception:
            outcomes = []
    n = len(outcomes)
    hits = sum(1 for o in outcomes if o.get("hit"))
    return {
        "n": n, "hits": hits,
        "hit_rate": (hits / n) if n else None,
        "wilson_lb": wilson_lower_bound(hits, n) if n else 0.0,
        "gate_min_n": SILENCE_MIN_N,
        "gate_min_wilson_lb": SILENCE_MIN_WILSON_LB,
        "need_more": max(0, SILENCE_MIN_N - n),   # 离静音门还差几个样本(诚实:26)
        "earned": False,   # 诚实:7 天样本远不够 n≥35,静音没挣到(见 manifest.honest_notes)
    }


def _conversations_meta(d: Path) -> dict:
    root = d / "conversations"
    if not root.is_dir():
        return {"count": 0, "turns": 0}
    count = 0
    turns = 0
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        try:
            if f.suffix == ".jsonl":
                count += 1
                turns += sum(1 for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip())
            elif f.suffix == ".json":
                rec = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(rec, dict) and rec.get("turns") is not None:
                    count += 1
                    turns += len(rec.get("turns") or [])
        except Exception:
            continue
    return {"count": count, "turns": turns}


def _workspace_files(d: Path) -> dict[str, dict]:
    """随包 workspace/ 里小林产出的稿件片段(只读、截断)——每日卡『产出』可点开看一眼。

    key = 文件名(task_ledger 里的 intent 靠标题模糊匹配到它);value = {name, snippet, bytes}。
    """
    root = d / "workspace"
    if not root.is_dir():
        return {}
    out: dict[str, dict] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        out[p.name] = {
            "name": p.name,
            "snippet": raw[:600],
            "bytes": p.stat().st_size,
        }
    return out


def _daily_timeline(man: dict) -> list[dict]:
    """把 manifest.task_ledger 按天分组 → 7 张每日卡的数据(做了什么/聊了什么/沉淀什么)。

    纯读 manifest(builder 写的地面真相),零推导、零 sqlite —— 与 effort_curve 同源对齐。
    每天:entries(intent/vtime/channel/写入几条/是否纠正/H2A 决策) + effort(亲手轮数/纠正数)。
    """
    ledger = man.get("task_ledger") or []
    curve = {int(e.get("day") or 0): e for e in (man.get("stats", {}).get("effort_curve") or [])}
    days: dict[int, dict] = {}
    for row in ledger:
        if not isinstance(row, dict):
            continue
        day = int(row.get("day") or 0)
        if day <= 0:
            continue
        bucket = days.setdefault(day, {"day": day, "day_label": "", "entries": []})
        eff = curve.get(day) or {}
        bucket["day_label"] = eff.get("day_label", "") or bucket["day_label"]
        entry = {
            "vtime": row.get("vtime", ""),
            "channel": row.get("channel", ""),
            "intent": row.get("intent", ""),
            "written": row.get("written"),           # 晨读喂料:沉淀几条知识
            "correction": bool(row.get("correction")),
            "routed": bool(row.get("routed")),
            "skill": row.get("skill", ""),
            "decision": row.get("decision", ""),      # H2A 卡:ACCEPT / REJECT
            "reason": row.get("reason", ""),
            "decision_mode": row.get("decision_mode", ""),
        }
        bucket["entries"].append(entry)
    out = []
    for day in sorted(days):
        eff = curve.get(day) or {}
        b = days[day]
        b["hands_on_turns"] = eff.get("hands_on_turns")
        b["corrections"] = eff.get("corrections")
        b["decision_modes"] = list(eff.get("decision_modes") or [])
        b["new_skills"] = list(eff.get("new_skills") or [])
        b["silence_progress"] = eff.get("silence_progress", "")
        out.append(b)
    return out


@router.get("/demo/instances")
def api_demo_instances() -> dict[str, Any]:
    """随包演示实例清单(没打包 → 诚实空表,前端隐藏入口)。"""
    out = []
    for iid in _ALLOWED:
        d = _instance_dir(iid)
        if d is None:
            continue
        man = _manifest(d)
        out.append({
            "id": iid,
            "lang": man.get("lang", ""),
            "persona": man.get("persona", {}),
            "virtual_days": man.get("virtual_days", []),
            "model": man.get("model", ""),
            "built_at": man.get("built_at", ""),
            "size_bytes": _dir_size(d),
        })
    return {"instances": out}


@router.get("/demo/instance/{iid}")
def api_demo_instance(iid: str) -> dict[str, Any]:
    """一个演示实例的只读总览(banner/成长曲线/技能/偏好/知识/命中率,全部现算)。"""
    d = _instance_dir(iid)
    if d is None:
        return {"ok": False, "reason": "unknown demo instance"}
    man = _manifest(d)
    from karvyloop.crystallize.curve import build_curves
    from karvyloop.crystallize.decision_pref import is_decision_pref
    from karvyloop.roles.experience import is_role_experience

    skills = _skills(d)
    name_by_sig = {s["sig"]: s["name"] for s in skills if s["sig"]}

    curves: dict = {"skills": [], "growth": {"points": []}}
    tp = d / "trace.sqlite"
    if tp.exists():
        ro = _ReadOnlyTrace(tp)
        try:
            curves = build_curves(ro, now=float(man.get("end_ts") or 0) or None,
                                  name_resolver=lambda s: name_by_sig.get(s, ""))
        finally:
            ro.close()
    # 展示层本地化:曲线里的技能名以**本实例包内** SKILL.md 的名字为准(en 实例已译)
    for s in curves.get("skills", []):
        if s.get("sig") in name_by_sig:
            s["name"] = name_by_sig[s["sig"]]

    beliefs = _beliefs(d)
    prefs = [b for b in beliefs if is_decision_pref(b)]
    exps = [b for b in beliefs if is_role_experience(b)]
    knowledge = [b for b in beliefs if not is_decision_pref(b) and not is_role_experience(b)]
    knowledge.sort(key=lambda b: float(getattr(b, "freshness_ts", 0) or 0), reverse=True)

    growth = curves.get("growth", {}).get("points", [])

    def _ts_of(b) -> float:
        try:
            return float((b.provenance or {}).get("ts") or b.freshness_ts or 0)
        except Exception:
            return 0.0

    day1_end = float(man.get("day0_ts") or 0) + 86400.0
    day1_extra = {"knowledge": sum(1 for b in knowledge if _ts_of(b) <= day1_end),
                  "prefs": sum(1 for b in prefs if _ts_of(b) <= day1_end)}
    day7_extra = {"knowledge": len(knowledge), "prefs": len(prefs)}

    # 每日时间线(做了什么/聊了什么/产出/沉淀) + 参与递减曲线 —— 都是 manifest 地面真相,只读透出
    stats = man.get("stats", {}) if isinstance(man.get("stats"), dict) else {}
    effort_curve = list(stats.get("effort_curve") or [])
    timeline = _daily_timeline(man)
    workspace = _workspace_files(d)
    return {
        "ok": True,
        "id": iid,
        "manifest": {
            "persona": man.get("persona", {}),
            "disclosure": man.get("disclosure", {}),
            "honest_notes": man.get("honest_notes", {}),
            "virtual_days": man.get("virtual_days", []),
            "model": man.get("model", ""),
            "built_at": man.get("built_at", ""),
            "builder": man.get("builder", ""),
            "localization_note": man.get("localization_note", ""),
        },
        # —— 高潮:参与递减曲线(D1 亲手5/纠正2 → D7 亲手2/纠正0 + 决策模式 冷→预对齐)
        "effort_curve": effort_curve,
        # —— 主体:7 张每日卡(每天做了什么/聊了什么/产出/沉淀)
        "timeline": timeline,
        # —— 产出:workspace 稿件片段(每日卡『产出』可点开看一眼),按文件名索引
        "workspace": workspace,
        "growth": growth,
        "day1": growth[0] if growth else {},
        "day7": growth[-1] if growth else {},
        "day1_extra": day1_extra,
        "day7_extra": day7_extra,
        "skills": skills,
        "skills_curve": curves.get("skills", []),
        "decision_prefs": [{
            "content": b.content,
            "kind": b.provenance.get("kind", ""),
            "strength": b.provenance.get("strength"),
            "status": b.provenance.get("status", ""),
            "explicit": bool(b.provenance.get("explicit")),
        } for b in prefs],
        "role_experiences": [{
            "content": b.content,
            "role": (b.provenance.get("applies") or {}).get("role", ""),
            "kind": b.provenance.get("kind", ""),
        } for b in exps],
        "knowledge_total": len(knowledge),
        "knowledge_recent": [{
            "content": (b.content or "")[:160],
            "source": b.provenance.get("source", ""),
        } for b in knowledge[:8]],
        "taste": _taste_progress(d),
        "tokens_by_day": _tokens_by_day(d),
        "conversations": _conversations_meta(d),
    }


__all__ = ["router", "demo_instances_root"]
