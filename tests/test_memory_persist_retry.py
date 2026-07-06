"""test_memory_persist_retry — P0②:持久化失败不静默 + 标脏重试自愈(断⑥补全)。

审计病灶:invalidate()/flush_usage() 依赖 _persist() 成功,失败时**内存态已改、盘上没改**,
且失败后若再无新写就永远没有重试 —— 重启后被推翻的旧知识"复活"(supersede 反复
失效/复活的幽灵循环),knowledge_tick 读脏 recall_count 误判"一年无用"。

修法(院规 fail-loud 不静默;内存态不回滚、标脏重试):
① _persist 失败 log.error 带上下文(op + 条数)——既有,锁住别退化;
② 有 trace 句柄时落一条 kind=belief_persist_failed(审计面,新增);
③ invalidate/write/archive 返回落盘结果(既有契约,锁住);
④ 失败置 _persist_dirty(不回滚内存)→ flush_usage(knowledge_tick 每 daily 调)兜底重试,
   盘一恢复即自愈,失效标记不再只活在内存。
"""
from __future__ import annotations

import logging
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.belief_store import BeliefStore  # noqa: E402
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402


def _belief(content: str) -> Belief:
    return Belief(content=content, provenance={"source": "test", "id": content, "ts": time.time()},
                  freshness_ts=time.time(), scope="personal")


class _GateStore(BeliefStore):
    """真 BeliefStore + 可开关的故障闸(fail=True 时 save_all 抛,如磁盘满)。"""

    def __init__(self, path) -> None:
        super().__init__(path)
        self.fail = False
        self.calls = 0

    def save_all(self, items) -> None:
        self.calls += 1
        if self.fail:
            raise OSError("disk full")
        super().save_all(items)


class _BoomStore:
    def load_all(self):
        return []

    def save_all(self, items):
        raise OSError("disk full")


class _TraceSink:
    def __init__(self):
        self.entries = []

    def append(self, entry):
        self.entries.append(entry)


class _BoomTrace:
    def append(self, entry):
        raise RuntimeError("trace down")


def _all_personal(mem: MemoryManager) -> list[Belief]:
    out, seen = [], set()
    for b in mem.index.all("personal"):
        if id(b) not in seen:
            seen.add(id(b))
            out.append(b)
    return out


# ---------- 病灶复现 + 自愈(修前红:flush_usage 假绿 noop,盘上失效标记永远丢) ----------

def test_failed_invalidate_is_loud_then_flush_retry_heals(tmp_path, caplog):
    path = tmp_path / "beliefs.json"
    gs = _GateStore(path)
    mem = MemoryManager(store=gs)
    b = _belief("旧结论:方案A更好")
    assert mem.write(b) is True                      # 基线:正常写盘

    gs.fail = True                                   # 盘坏了(磁盘满)
    with caplog.at_level(logging.ERROR, logger="karvyloop.cognition.memory"):
        ok = mem.invalidate(b, reason="superseded: 方案B 实测更好")
    assert ok is False                               # ③ 语义关键写:调用方能感知
    assert mem.persist_error                         # 状态可查(routes/doctor 上冒)
    assert any("Belief 落盘失败" in r.message for r in caplog.records)   # ① 响了
    assert b.invalid_at is not None                  # 内存态不回滚:进程内召回行为一致

    # 病灶实证:此刻"重启"(从盘重建)→ 失效标记丢了,被推翻的旧知识**复活**
    revived = _all_personal(MemoryManager(store=BeliefStore(path)))
    assert revived and revived[0].invalid_at is None   # ← 审计说的幽灵复活,病在

    # ④ 自愈:盘恢复后,daily 慢侧的 flush_usage(knowledge_tick 每天调)必须重试落盘。
    # 修前:无 usage 脏 → noop 返 True 但**没写盘**(假绿),失效标记永远只活在内存。
    gs.fail = False
    calls_before = gs.calls
    assert mem.flush_usage() is True
    assert gs.calls == calls_before + 1              # 真写了盘,不是 noop 假绿
    healed = _all_personal(MemoryManager(store=BeliefStore(path)))
    assert healed and healed[0].invalid_at is not None   # 失效标记落盘,重启不再复活
    assert mem.persist_error is None                 # 成功清 error


def test_failed_persist_marks_dirty_and_next_write_heals(tmp_path):
    """重试不只靠 flush_usage:失败后任意一次成功的写路径(全量快照)同样自愈。"""
    path = tmp_path / "beliefs.json"
    gs = _GateStore(path)
    mem = MemoryManager(store=gs)
    b = _belief("旧知识")
    assert mem.write(b) is True
    gs.fail = True
    assert mem.invalidate(b, reason="推翻") is False
    gs.fail = False
    assert mem.write(_belief("新知识")) is True       # 全量快照顺带把失效标记带上盘
    loaded = {x.content: x for x in _all_personal(MemoryManager(store=BeliefStore(path)))}
    assert loaded["旧知识"].invalid_at is not None


# ---------- ② Trace 审计面(修前红:构造不收 trace,失败无 Trace) ----------

def test_persist_failure_writes_trace_when_handle_present():
    sink = _TraceSink()
    mem = MemoryManager(store=_BoomStore(), trace=sink)
    assert mem.write(_belief("会丢的知识")) is False
    failed = [e for e in sink.entries if getattr(e, "kind", "") == "belief_persist_failed"]
    assert failed, "落盘失败必须落一条 Trace(有句柄时)"
    payload = failed[0].payload
    assert payload.get("op") == "write" and payload.get("error")


def test_trace_sink_failure_does_not_change_contract():
    """Trace 是审计不是命脉:trace.append 崩不改变返回契约、不抛出。"""
    mem = MemoryManager(store=_BoomStore(), trace=_BoomTrace())
    assert mem.write(_belief("x")) is False


# ---------- 成功路径 0 回归 ----------

def test_success_path_zero_regression_no_spurious_flush(tmp_path):
    gs = _GateStore(tmp_path / "beliefs.json")
    mem = MemoryManager(store=gs)
    assert mem.write(_belief("正常知识")) is True
    assert mem.persist_error is None
    n = gs.calls
    assert mem.flush_usage() is True
    assert gs.calls == n                             # 无脏 → noop,不多写一次盘


def test_invalidate_success_persists_and_returns_true(tmp_path):
    path = tmp_path / "beliefs.json"
    mem = MemoryManager(store=BeliefStore(path))
    b = _belief("要推翻的")
    assert mem.write(b) is True
    assert mem.invalidate(b, reason="过时") is True
    loaded = _all_personal(MemoryManager(store=BeliefStore(path)))
    assert loaded and loaded[0].invalid_at is not None
