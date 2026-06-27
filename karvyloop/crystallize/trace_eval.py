"""trace_eval — Trace-派生的异步评价器（crystallize/trace_eval.py）。

docs/40 §3 快慢分离 + §1 Trace 唯一数据源:

  **快**:`drive()` 只管跑 —— 把"评价事实"(eval_fact:sig/success/verified/steps/trace_ref)
         写进 Trace,**不在热路径算任何满意度**。
  **慢**:本模块读 Trace 里的 eval_fact,算确定性满意度(达成 + 效率),写 SatisfactionStore,
         **离执行热路径**(经 background_review 维护 / daily_poll)。

设计:① 评价**只从 Trace 派生**(不旁路);② 按 run `trace_ref` 去重(水位),后台可反复跑;
③ 信用按 sig 隔离(record_facts 只吃本条事实)。LLM 质量维(做好·质量)是更慢的一档,
走 daily_poll(下一切片),同样读 Trace,在此预留 kind。
"""

from __future__ import annotations

import time as _time
from typing import Iterable, Optional

from .atom_critic import SatisfactionStore, record_facts


# drive 写进 Trace 的"评价事实"事件类型(评价器据此读)
EVAL_FACT_KIND = "eval_fact"
# 评价**结果**回写 Trace 的事件类型(docs/40 §1 "学"自反闭环 + 重启水位/样本重建)
SATISFACTION_KIND = "satisfaction"


def rehydrate(trace, satisfaction: SatisfactionStore) -> int:
    """重启后从 Trace 的 satisfaction 结果**重建** SatisfactionStore(samples + 去重水位)。

    修对抗验收 CRITICAL #1:水位是内存态、Trace 是持久态 → 重启后历史 run 会被**重复评**(N 重计)。
    在 MainLoop 构造时调一次:把已评结果灌回内存态 store,评价器据水位跳过 → 不重复评。返回重建条数。
    """
    if trace is None or satisfaction is None:
        return 0
    from .atom_critic import AtomSatisfaction
    try:
        task_ids = list(trace.all_tasks())
    except Exception:
        return 0
    n = 0
    for tid in task_ids:
        try:
            entries = trace.query(tid, kind=SATISFACTION_KIND)
        except Exception:
            continue
        for e in entries:
            p = getattr(e, "payload", None) or {}
            sig = p.get("sig", "")
            ref = p.get("trace_ref", "")
            if not sig or not ref or satisfaction.judged(ref):
                continue
            q = p.get("quality", None)
            satisfaction.record(sig, AtomSatisfaction(
                sig=sig,
                achievement=float(p.get("achievement", 0.0) or 0.0),
                efficiency=float(p.get("efficiency", 0.0) or 0.0),
                quality=(float(q) if isinstance(q, (int, float)) else None),
                critique=str(p.get("critique", "") or ""),
                trace_ref=ref,
                at=float(p.get("ts", 0.0) or 0.0),
            ), int(p.get("steps", 0) or 0))
            n += 1
    return n


def _writeback(trace, tid: str, sat, steps: int, clk) -> None:
    """把评价结果回写 Trace(自反 + 持久水位源)。失败不拖垮评价。"""
    try:
        from karvyloop.cognition.trace import TraceEntry
        trace.append(TraceEntry(
            task_id=tid, kind=SATISFACTION_KIND,
            payload={
                "sig": sat.sig, "trace_ref": sat.trace_ref,
                "achievement": sat.achievement, "efficiency": sat.efficiency,
                "quality": sat.quality, "critique": sat.critique,
                "overall": sat.overall, "steps": int(steps),
            },
            ts=clk(), source="trace_eval",
        ))
    except Exception:
        pass


def evaluate_pending(trace, satisfaction: SatisfactionStore, *,
                     tasks: Optional[Iterable[str]] = None, clock=None) -> int:
    """读 Trace 里**未评**的 eval_fact → 记确定性满意度。返回本轮新评的条数。

    - `trace`:任何有 `all_tasks()` + `query(task_id, kind=)` 的 TraceStore(duck-type)。
    - `tasks`:限定 task_id(None=全部;调用方可只给最近的 task 以控开销)。
    - 去重:`trace_ref` 已评过(satisfaction.judged)→ 跳过;无 sig/无 trace_ref → 跳过
      (无 trace_ref 无法做水位,宁可不评也不重复污染)。
    """
    if trace is None or satisfaction is None:
        return 0
    clk = clock or _time.time
    try:
        task_ids = list(tasks) if tasks is not None else list(trace.all_tasks())
    except Exception:
        return 0
    n = 0
    for tid in task_ids:
        if not tid:
            continue
        try:
            entries = trace.query(tid, kind=EVAL_FACT_KIND)
        except Exception:
            continue
        for e in entries:
            p = getattr(e, "payload", None) or {}
            sig = p.get("sig", "")
            ref = p.get("trace_ref", "")
            if not sig or not ref or satisfaction.judged(ref):
                continue
            try:
                steps = int(p.get("steps", 0) or 0)
                sat = record_facts(
                    satisfaction, sig,
                    success=bool(p.get("success", False)),
                    verified=bool(p.get("verified", False)),
                    steps=steps, trace_ref=ref, clock=clk,
                )
                _writeback(trace, tid, sat, steps, clk)  # 回写 Trace(自反 + 重启水位源)
                n += 1
            except Exception:
                continue  # 单条坏事实不拖垮整轮评价(append-only,坏数据不阻塞)
    return n


__all__ = ["EVAL_FACT_KIND", "SATISFACTION_KIND", "evaluate_pending", "rehydrate"]
