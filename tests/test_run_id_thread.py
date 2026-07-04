"""可观测性收敛① + ③:run_id 长在现有 Trace 上(串联,不另起账本)+ replay --run 过滤。

铁律锚:Trace = 运行记录 + 所有评价的唯一数据源 —— run_id 只是 TraceEntry/token 账本的
一个**可选字段**(contextvar 从 drive 入口透传,写入咽喉盖戳),不是第二套事件流。

覆盖:
- run_scope contextvar:进/出 scope、显式 id、退出复位
- drive 全链透传:drive → slow_brain(内 asyncio.run 走 gateway.complete)→ Trace + token 账本
  三处拿到**同一个** run_id(跨 asyncio.run 的 contextvar 复制是真实缝,必须真走)
- Trace 老格式兼容:老 sqlite 库(无 run_id 列)自动迁移,老行 run_id='' 照常读
- token 账本:record 带 run_id 打标;老 tokens.db 迁移;run_totals 只读汇总
- replay --run:只输出该 run 的条目 + stderr 摘要行;rc 语义(1=没条目,3=俩参数都没给)
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

import pytest

from karvyloop.cognition.sqlite_trace import SqliteTraceStore
from karvyloop.cognition.trace import TraceEntry, TraceStore, current_run_id, new_run_id, run_scope
from karvyloop.llm.token_ledger import TokenLedger, register_ledger
from karvyloop.schemas import AtomRun


# ---- contextvar 语义 ----

def test_run_scope_sets_and_resets():
    assert current_run_id() == ""
    with run_scope() as rid:
        assert rid and current_run_id() == rid
        assert len(rid) == 12  # uuid4().hex[:12]
    assert current_run_id() == ""


def test_run_scope_explicit_id_and_nesting():
    with run_scope("outer-run-01") as rid:
        assert rid == "outer-run-01"
        with run_scope("inner-run-02"):
            assert current_run_id() == "inner-run-02"
        assert current_run_id() == "outer-run-01"


def test_new_run_id_short_hex():
    rid = new_run_id()
    assert len(rid) == 12
    int(rid, 16)  # 是 hex


# ---- Trace 写入咽喉盖戳(内存版)----

def test_inmemory_append_stamps_run_id_and_query_run():
    store = TraceStore(clock=lambda: 100.0)
    store.append(TraceEntry(task_id="T0", kind="x", payload={}))  # scope 外 → 缺省 ''
    with run_scope("runa00000001"):
        store.append(TraceEntry(task_id="T1", kind="x", payload={}))
        store.append(TraceEntry(task_id="T2", kind="y", payload={}))
    assert store.query("T0")[0].run_id == ""
    assert [e.task_id for e in store.query_run("runa00000001")] == ["T1", "T2"]
    assert store.query_run("") == []  # 空 run_id 不把无戳老记录当一个 run


# ---- Trace 老格式兼容(sqlite 版迁移)----

def test_old_sqlite_trace_without_run_id_column_migrates(tmp_path: Path):
    p = tmp_path / "trace.sqlite"
    conn = sqlite3.connect(str(p))
    conn.executescript(
        "CREATE TABLE trace_entries (task_id TEXT NOT NULL, seq INTEGER NOT NULL, "
        "kind TEXT NOT NULL, payload_json TEXT NOT NULL, ts REAL NOT NULL, "
        "agent TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', "
        "trace_ref TEXT NOT NULL DEFAULT '', PRIMARY KEY (task_id, seq));")
    conn.execute("INSERT INTO trace_entries (task_id, seq, kind, payload_json, ts) "
                 "VALUES ('T1', 0, 'atom_run', '{\"a\":1}', 1.0)")
    conn.commit()
    conn.close()

    store = SqliteTraceStore(p)
    old = store.query("T1")
    assert old and old[0].run_id == "" and old[0].payload == {"a": 1}  # 老记录照常读
    with run_scope("runb00000002"):
        store.append(TraceEntry(task_id="T1", kind="x", payload={}))
    assert store.query("T1")[-1].run_id == "runb00000002"
    assert [e.seq for e in store.query_run("runb00000002")] == [1]
    store.close()


def test_sqlite_trace_fresh_db_roundtrips_run_id(tmp_path: Path):
    store = SqliteTraceStore(tmp_path / "trace.sqlite")
    e = TraceEntry(task_id="T1", kind="x", payload={}, run_id="explicit12345")
    store.append(e)  # 显式 run_id 优先于 contextvar
    got = store.query("T1")[0]
    assert got.run_id == "explicit12345"
    store.close()


# ---- token 账本打标(记账逻辑不变,只多一列)----

def test_ledger_record_run_id_and_run_totals():
    led = TokenLedger(path=None)
    led.record(source="s", model="m", input=10, output=20, run_id="runc00000003")
    led.record(source="s", model="m", input=1, output=2)  # 无 run → 不进 run 聚合
    tot = led.run_totals("runc00000003")
    assert tot == {"input": 10, "output": 20, "total": 30, "calls": 1}
    assert led.run_totals("")["calls"] == 0
    assert led.totals()["calls"] == 2  # 总账不变:记账逻辑本身没动
    led.close()


def test_old_tokens_db_gains_run_id_column(tmp_path: Path):
    p = tmp_path / "tokens.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(
        "CREATE TABLE token_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
        "day TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'unknown', model TEXT NOT NULL DEFAULT '', "
        "input INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0, "
        "cache_read INTEGER NOT NULL DEFAULT 0, cache_write INTEGER NOT NULL DEFAULT 0);")
    conn.execute("INSERT INTO token_usage (ts, day, source, model, input, output) "
                 "VALUES (1.0, '2026-07-01', 'forge', 'm', 50, 50)")
    conn.commit()
    conn.close()
    led = TokenLedger(p)
    assert led.totals()["total"] == 100                       # 老数据在
    led.record(source="s", model="m", input=7, output=3, run_id="rund00000004")
    assert led.run_totals("rund00000004")["total"] == 10      # 新列可用
    led.close()


# ---- 全链透传:drive → gateway.complete → Trace + 账本 同一个 run_id ----

class _M:
    id = "test-model"
    api = "fake"
    cost: dict = {}
    role = "chat"


class _Reg:
    def get(self, ref):
        return _M()

    def provider_of(self, ref):
        return None


class _UsageAdapter:
    async def complete(self, messages, tools, m, prov, system=None, **kw):
        from karvyloop.gateway.events import Done, TextDelta, Usage
        yield TextDelta(text="hi")
        yield Usage(input_tokens=100, output_tokens=20)
        yield Done(stop_reason="end_turn")


def test_run_id_threads_drive_gateway_trace_and_ledger(tmp_path: Path):
    """对抗点:contextvar 必须熬过 drive(同步)→ slow_brain 内 asyncio.run(新 loop)→
    gateway.complete(async gen)→ token_ledger.record 这条真实链;三处 run_id 一致。"""
    from karvyloop.gateway.client import GatewayClient
    from karvyloop.runtime.main_loop import MainLoop

    led = TokenLedger(path=None)
    register_ledger(led)
    try:
        gw = GatewayClient(_Reg(), adapters={"fake": _UsageAdapter()})
        seen: dict = {}

        def slow_brain(intent: str):
            async def go():
                async for _ in gw.complete([{"role": "user", "content": "x"}], [], "test-model"):
                    pass
            asyncio.run(go())
            seen["rid"] = current_run_id()
            return "ok", AtomRun(
                atom_id="a1", input={"intent": intent}, output={"text": "ok"}, success=True,
                tool_calls=[{"name": "t", "input": {}}], trace_ref="trace://a1/1",
                ts=time.time(), terminal="completed")

        ml = MainLoop(skills_dir=tmp_path / "skills")
        ml.bootstrap()
        r = ml.drive("thread the run id", slow_brain=slow_brain)

        rid = seen.get("rid", "")
        assert rid and len(rid) == 12, f"drive 没在链上生成 run_id: {seen}"
        entries = ml.trace.query(r.task_id)
        assert entries, "drive 应写 Trace"
        assert all(e.run_id == rid for e in entries), \
            f"Trace 条目 run_id 不一致: {[(e.kind, e.run_id) for e in entries]}"
        assert led.run_totals(rid)["calls"] == 1, "gateway 咽喉记账应带同一 run_id"
        assert current_run_id() == ""  # drive 退出后复位,不跨 run 泄漏
    finally:
        register_ledger(None)


def test_two_drives_get_distinct_run_ids(tmp_path: Path):
    from karvyloop.runtime.main_loop import MainLoop

    rids: list = []

    def slow_brain(intent: str):
        rids.append(current_run_id())
        return "ok", AtomRun(
            atom_id="a1", input={"intent": intent}, output={"text": "ok"}, success=True,
            tool_calls=[], trace_ref=f"trace://a1/{len(rids)}", ts=time.time(),
            terminal="completed")

    ml = MainLoop(skills_dir=tmp_path / "skills")
    ml.bootstrap()
    ml.drive("first completely unrelated intent alpha", slow_brain=slow_brain)
    ml.drive("second thoroughly different intent beta", slow_brain=slow_brain)
    assert len(rids) == 2 and rids[0] and rids[1] and rids[0] != rids[1]


# ---- replay --run 过滤(③)----

def _seed_trace(tmp_path: Path) -> Path:
    p = tmp_path / "trace.sqlite"
    store = SqliteTraceStore(p)
    with run_scope("runx00000001"):
        store.append(TraceEntry(task_id="task-a", kind="atom_run",
                                payload={"atom_id": "a"}, ts=1000.0, source="t"))
        store.append(TraceEntry(task_id="task-a", kind="eval_fact",
                                payload={"sig": "s"}, ts=1010.0, source="t"))
    with run_scope("runy00000002"):
        store.append(TraceEntry(task_id="task-b", kind="atom_run",
                                payload={"atom_id": "b"}, ts=2000.0, source="t"))
    store.close()
    return p


def test_replay_run_filters_by_run_id(tmp_path: Path, capsys):
    from karvyloop.cli.replay import cmd_replay

    p = _seed_trace(tmp_path)
    rc = cmd_replay(run_id="runx00000001", trace_path=p, tokens_path=tmp_path / "no-tokens.db")
    assert rc == 0
    cap = capsys.readouterr()
    lines = [json.loads(l) for l in cap.out.splitlines() if l.strip()]
    assert len(lines) == 2
    assert all(l["run_id"] == "runx00000001" for l in lines)
    assert {l["kind"] for l in lines} == {"atom_run", "eval_fact"}
    # 摘要行在 stderr(stdout 保持纯 NDJSON 可管道)
    assert "run=runx00000001" in cap.err and "entries=2" in cap.err and "duration=" in cap.err


def test_replay_run_summary_includes_tokens(tmp_path: Path, capsys):
    from karvyloop.cli.replay import cmd_replay

    p = _seed_trace(tmp_path)
    tok = tmp_path / "tokens.db"
    led = TokenLedger(tok)
    led.record(source="forge", model="m", input=100, output=20, run_id="runx00000001")
    led.close()
    rc = cmd_replay(run_id="runx00000001", trace_path=p, tokens_path=tok)
    assert rc == 0
    err = capsys.readouterr().err
    assert "tokens=120" in err and "calls=1" in err


def test_replay_run_not_found_and_missing_args(tmp_path: Path, capsys):
    from karvyloop.cli.replay import cmd_replay

    p = _seed_trace(tmp_path)
    assert cmd_replay(run_id="nope00000000", trace_path=p) == 1
    assert "nope00000000" in capsys.readouterr().err
    assert cmd_replay(trace_path=p) == 3  # task_id 和 --run 都没给


def test_replay_task_id_path_unchanged_and_carries_run_id(tmp_path: Path, capsys):
    """老用法 replay <task_id> 行为不变;NDJSON 行**多**一个 run_id 字段(加法,不破老消费者)。"""
    from karvyloop.cli.replay import cmd_replay

    p = _seed_trace(tmp_path)
    rc = cmd_replay("task-a", trace_path=p)
    assert rc == 0
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) == 2 and all(l["task_id"] == "task-a" for l in lines)
    assert all(l["run_id"] == "runx00000001" for l in lines)


def test_replay_run_and_task_id_intersect(tmp_path: Path, capsys):
    from karvyloop.cli.replay import cmd_replay

    p = _seed_trace(tmp_path)
    # run x 里没有 task-b 的条目 → 交集为空 → 1
    assert cmd_replay("task-b", run_id="runx00000001", trace_path=p) == 1
    capsys.readouterr()
    assert cmd_replay("task-b", run_id="runy00000002", trace_path=p) == 0
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) == 1 and lines[0]["task_id"] == "task-b"
