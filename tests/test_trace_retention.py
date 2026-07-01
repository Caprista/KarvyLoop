"""Trace 原文层容量环(docs/27)+ funnel 事件截断——契约测试。

锁:prune_raw 保提炼物(satisfaction/quality/lesson)、原文超额丢最旧;sqlite 同款;
funnel 大字段写前截断(防大输出把 10MB 环冲爆)。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.sqlite_trace import SqliteTraceStore  # noqa: E402
from karvyloop.cognition.trace import TraceEntry, TraceStore  # noqa: E402


def _seed(trace, n_raw=20):
    for i in range(n_raw):
        trace.append(TraceEntry(task_id=f"t{i}", kind="atom_run",
                                payload={"i": i}, ts=float(i)))
    trace.append(TraceEntry(task_id="lesson:s", kind="lesson",
                            payload={"sig": "s", "lesson": "L"}, ts=100.0))
    trace.append(TraceEntry(task_id="t0", kind="satisfaction",
                            payload={"sig": "s", "trace_ref": "r"}, ts=101.0))


def test_prune_raw_keeps_distillates_drops_old_raw():
    trace = TraceStore()
    _seed(trace, 20)
    dropped = trace.prune_raw(5)                  # 保 5 条最新原文 + 全部提炼物
    assert dropped == 15
    assert len(trace.query("lesson:s", kind="lesson")) == 1       # 提炼物留着
    assert len(trace.query("t0", kind="satisfaction")) == 1
    raw_left = sum(len(trace.query(f"t{i}", kind="atom_run")) for i in range(20))
    assert raw_left == 5                          # 只剩 5 条最新原文


def test_prune_raw_noop_under_cap():
    trace = TraceStore()
    _seed(trace, 3)
    assert trace.prune_raw(100) == 0              # 没超 → 不动


def test_sqlite_prune_raw_parity(tmp_path):
    trace = SqliteTraceStore(None)                # 内存 sqlite
    _seed(trace, 20)
    assert trace.prune_raw(5) == 15
    assert len(trace.query("lesson:s", kind="lesson")) == 1
    raw_left = sum(len(trace.query(f"t{i}", kind="atom_run")) for i in range(20))
    assert raw_left == 5
    trace.close()


def test_prune_keeps_unjudged_eval_facts():
    # 对抗验收 C-1:还没评的 eval_fact **绝不丢**(否则它的满意度永久丢)
    trace = TraceStore()
    for i in range(20):
        trace.append(TraceEntry(task_id=f"t{i}", kind="atom_run", payload={"i": i}, ts=float(i)))
    for i in range(5):
        trace.append(TraceEntry(task_id=f"t{i}", kind="eval_fact",
                                payload={"sig": "s", "trace_ref": f"r{i}"}, ts=float(100 + i)))
    trace.prune_raw(2)                                   # 狠剪
    evf = sum(len(trace.query(f"t{i}", kind="eval_fact")) for i in range(5))
    assert evf == 5                                      # eval_fact 全留
    raw = sum(len(trace.query(f"t{i}", kind="atom_run")) for i in range(20))
    assert raw == 2                                      # 只剪 atom_run


def test_prune_ts_tie_keeps_newest_both_backends(tmp_path):
    # 对抗验收 C-2:ts 全打平时,两后端都留**最新**(不是最旧)
    for make in (lambda: TraceStore(), lambda: SqliteTraceStore(None)):
        trace = make()
        for i in range(6):
            trace.append(TraceEntry(task_id="t", kind="atom_run", payload={"i": i}, ts=777.0))
        trace.prune_raw(2)
        kept = sorted(e.payload["i"] for e in trace.query("t", kind="atom_run"))
        assert kept == [4, 5]                            # 留最新两条
        close = getattr(trace, "close", None)
        if callable(close):
            close()


def test_funnel_per_scope_eviction_protects_quiet_role(tmp_path):
    # Hardy:固定全局 10MB 不随 role 涨 → 忙 role 覆掉安静 role 的未消费上下文。改 per-scope 后:
    # 忙 scope 只覆自己的最旧,安静 scope 的事件**幸存**;总量随 scope 数涨。
    from karvyloop.karvy.fastbrain.trace_index import TraceIndex
    idx = TraceIndex(tmp_path / "f.sqlite", raw_capacity=3000)   # 每 scope 3KB 预算
    idx.append_raw({"q": "quiet-role-event"}, scope="role_quiet")
    for i in range(60):                                          # 忙 role:狂写 → 覆自己最旧
        idx.append_raw({"x": "y" * 200, "i": i}, scope="role_busy")
    quiet = idx.list_raw(limit=100, scope="role_quiet")
    assert any("quiet-role-event" in str(r.payload) for r in quiet)   # 安静 role 的没被覆
    busy = idx.list_raw(limit=100, scope="role_busy")
    assert busy and all(r.scope == "role_busy" for r in busy)         # 忙 role 自己滚动
    idx.close()


def test_funnel_per_scope_chain_survives_eviction(tmp_path):
    # per-scope 淘汰会在全局链中间留洞 → 必须 hash-chain 也按 scope 串,否则 verify_chain 误报篡改
    from karvyloop.karvy.fastbrain.trace_index import TraceIndex
    idx = TraceIndex(tmp_path / "f2.sqlite", raw_capacity=3000)
    for i in range(60):                                         # 两 scope 交错,忙的触发自己淘汰
        idx.append_raw({"x": "y" * 200, "i": i}, scope="role_busy")
        if i % 10 == 0:
            idx.append_raw({"q": i}, scope="role_quiet")
    ok, broken = idx.verify_chain("raw")
    assert ok, f"per-scope 淘汰后链被误判断在 seq={broken}"      # 链按 scope 串 → 不误报
    idx.close()


def test_funnel_event_large_field_truncated(tmp_path):
    from karvyloop.cli.main_loop import MainLoop

    captured = {}

    class _Funnel:
        def append_raw(self, payload, scope="global"):
            captured.update(payload)

    ml = MainLoop(skills_dir=tmp_path / "s")
    ml.set_trace_funnel(_Funnel())
    ml._emit_funnel_event({"intent": "x", "output": "字" * 5000})
    assert "字" * 5000 != captured["output"]               # 真被截了
    assert len(captured["output"].encode("utf-8")) <= 2000 + 4
