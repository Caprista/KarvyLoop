"""cognition.trace — Trace 情景日志（cognition/trace.py）。

规格：docs/modules/cognition-memory.md §3 trace.py
- append-only:append() 只增不改
- 事件底座:HR-7 provenance 来源
- 供 crystallize.observe 读 AtomRun(同一底座)
- M1 v1:纯内存(后续可接 sqlite / jsonl 文件;接口稳定)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional

from karvyloop.schemas import AtomRun


@dataclass
class TraceEntry:
    """Trace 一条记录。HR-7:每条都带 source/task_id/agent/ts/trace_ref。"""
    task_id: str
    kind: str  # "atom_run" | "user_turn" | "assistant_turn" | "tool_call" | ...
    payload: dict
    ts: float = 0.0
    agent: str = ""
    source: str = ""
    seq: int = 0  # 由 TraceStore 分配


class TraceStore:
    """append-only 事件底座。供 crystallize.observe 派生 UsageStats。

    - append(entry) → trace_ref ("<task_id>:<seq>")
    - query(task_id, kind=None) → entries
    - query_atom_runs(task_id) → list[AtomRun]  ← 给 crystallize.observe 用
    - query_beliefs(task_id) → list[Belief]      ← 给 distill 用
    """

    def __init__(self, *, clock=time.time) -> None:
        self._by_task: dict[str, list[TraceEntry]] = {}
        self._seq: dict[str, int] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def append(self, entry: TraceEntry) -> str:
        with self._lock:
            seq = self._seq.get(entry.task_id, 0)
            entry.seq = seq
            if not entry.ts:
                entry.ts = self._clock()
            self._seq[entry.task_id] = seq + 1
            self._by_task.setdefault(entry.task_id, []).append(entry)
            return f"{entry.task_id}:{seq}"

    def query(self, task_id: str, *, kind: Optional[str] = None) -> list[TraceEntry]:
        with self._lock:
            entries = list(self._by_task.get(task_id, []))
            if kind is not None:
                entries = [e for e in entries if e.kind == kind]
            return entries

    def query_atom_runs(self, task_id: str) -> list[AtomRun]:
        """给 crystallize.observe 用的投影:取所有 atom_run 事件 → AtomRun。"""
        out: list[AtomRun] = []
        for e in self.query(task_id, kind="atom_run"):
            p = e.payload
            try:
                out.append(AtomRun(
                    atom_id=p.get("atom_id", ""),
                    input=p.get("input", {}),
                    output=p.get("output"),
                    success=p.get("success", False),
                    tool_calls=p.get("tool_calls", []),
                    trace_ref=p.get("trace_ref", ""),
                    ts=p.get("ts", e.ts),
                ))
            except Exception:
                # 防御:payload 不合法 → 跳过(append-only 不该被坏数据阻塞)
                continue
        return out

    def all_tasks(self) -> list[str]:
        with self._lock:
            return list(self._by_task.keys())

    def prune_raw(self, max_raw: int) -> int:
        """docs/27 原文层"容量环":只丢**大块原文事件**(atom_run / turns / tool_call,见 DROPPABLE_KINDS)
        超 `max_raw` 的最旧那些;**eval_fact(几百字节,且可能还没评)+ 全部提炼物一律保留**。返回丢弃条数。

        为什么只丢这几类:① 真涨的是 atom_run 的大输出(几十KB),eval_fact 微小;② **绝不丢没评的
        eval_fact**(否则它的满意度永久丢 —— 对抗验收 C-1)。append-only = 不改事件,不是永不滚动。
        留最新:按 (ts, seq) 排,丢最旧(与 sqlite 版 ORDER BY ts DESC, rowid DESC 对齐 —— 对抗验收 C-2)。
        """
        with self._lock:
            drop_able = [(e.ts, e.seq, tid) for tid, ents in self._by_task.items()
                         for e in ents if e.kind in DROPPABLE_KINDS]
            if len(drop_able) <= max_raw:
                return 0
            drop_able.sort(key=lambda r: (r[0], r[1]))   # (ts, seq) 升序 → 最旧在前
            drop = {(tid, seq) for _, seq, tid in drop_able[:len(drop_able) - max_raw]}
            n = 0
            for tid in list(self._by_task.keys()):
                ents = self._by_task[tid]
                kept = [e for e in ents if (tid, e.seq) not in drop]
                n += len(ents) - len(kept)
                if kept:
                    self._by_task[tid] = kept
                else:
                    del self._by_task[tid]               # 原文全滚走的空 task 清掉(不重置 _seq)
            return n


# 容量环里**可丢的大块原文**(其余一律保留:eval_fact 微小且可能未评、satisfaction/quality/lesson 是提炼物)。
DROPPABLE_KINDS = frozenset({"atom_run", "user_turn", "assistant_turn", "tool_call"})


__all__ = ["TraceEntry", "TraceStore", "DROPPABLE_KINDS"]
