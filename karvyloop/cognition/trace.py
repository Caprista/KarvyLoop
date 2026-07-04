"""cognition.trace — Trace 情景日志（cognition/trace.py）。

规格：docs/modules/cognition-memory.md §3 trace.py
- append-only:append() 只增不改
- 事件底座:HR-7 provenance 来源
- 供 crystallize.observe 读 AtomRun(同一底座)
- M1 v1:纯内存(后续可接 sqlite / jsonl 文件;接口稳定)
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional

from karvyloop.schemas import AtomRun


# ---- run_id 串联(可观测性收敛①):contextvar per-run scope ----
# 一次 drive(或 daily/后台各自入口)生成一个短 run_id,顺 contextvar 全链透传
# (drive → forge/coding → gateway.complete → 工具执行;asyncio 复制上下文,入口裹一次即可)。
# **长在现有 Trace 上**:它只是 TraceEntry/token 账本的一个可选字段,不是第二套事件流。
# 模式与 capability/deontic_gate 的 per-run contextvar 一致(set → 干活 → finally reset)。
# 无 scope 时字段缺省 = ""(零行为变化;老记录/非入口路径照旧)。

_RUN_ID: contextvars.ContextVar[str] = contextvars.ContextVar("trace_run_id", default="")


def new_run_id() -> str:
    """短 run_id(uuid4 hex[:12]),replay --run / 账本串联用。"""
    return uuid.uuid4().hex[:12]


def current_run_id() -> str:
    """当前 run scope 的 run_id;不在任何 scope 内 = ""(诚实缺省)。"""
    return _RUN_ID.get()


@contextlib.contextmanager
def run_scope(run_id: str = ""):
    """在 with 块内标记一次 run(空参 = 自动生成)。随块退出复位,绝不跨 run 泄漏。

    yield 生成的 run_id,入口方(drive/daily)可回传给调用者做串联。
    """
    rid = run_id or new_run_id()
    token = _RUN_ID.set(rid)
    try:
        yield rid
    finally:
        with contextlib.suppress(Exception):   # 跨 context 复位失败不冒泡(同 deontic gate)
            _RUN_ID.reset(token)


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
    run_id: str = ""  # 可观测性①:本条属于哪次 run(入口 run_scope 生成;缺省 "" = 无 scope,照旧)


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
            if not entry.run_id:
                # 可观测性①:在唯一写入咽喉盖 run_id 戳 —— 调用点零改动,contextvar 读一次,无 I/O。
                entry.run_id = _RUN_ID.get()
            self._seq[entry.task_id] = seq + 1
            self._by_task.setdefault(entry.task_id, []).append(entry)
            return f"{entry.task_id}:{seq}"

    def query(self, task_id: str, *, kind: Optional[str] = None,
              start_ts: Optional[float] = None,
              end_ts: Optional[float] = None) -> list[TraceEntry]:
        """按 task_id(+可选 kind +可选时间窗)查。

        时间窗(周报卡等时段汇总用):`start_ts <= e.ts <= end_ts`(闭区间,
        与 DecisionLog.query 同口径);None = 不限(默认,向后兼容 —— 不带窗的
        旧调用行为一字不变)。keyword-only,不影响既有 `query(tid, kind=...)` 调用。
        """
        with self._lock:
            entries = list(self._by_task.get(task_id, []))
        if kind is not None:
            entries = [e for e in entries if e.kind == kind]
        if start_ts is not None:
            entries = [e for e in entries if e.ts >= start_ts]
        if end_ts is not None:
            entries = [e for e in entries if e.ts <= end_ts]
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
                    terminal=p.get("terminal") or None,  # §15:终止语义随重建保留(否则结晶侧读回是 None)
                ))
            except Exception:
                # 防御:payload 不合法 → 跳过(append-only 不该被坏数据阻塞)
                continue
        return out

    def query_run(self, run_id: str) -> list[TraceEntry]:
        """按 run_id 跨 task 查(replay --run 用)。空 run_id → [](不把无戳老记录当一个 run)。"""
        if not run_id:
            return []
        with self._lock:
            out = [e for ents in self._by_task.values() for e in ents if e.run_id == run_id]
        out.sort(key=lambda e: (e.ts, e.seq))
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


__all__ = ["TraceEntry", "TraceStore", "DROPPABLE_KINDS",
           "run_scope", "new_run_id", "current_run_id"]
