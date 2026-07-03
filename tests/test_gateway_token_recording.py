"""test_gateway_token_recording — 直连 gateway.complete 的 LLM 调用也要进 token 账本.

病根(round2 压测逮到):`record()` 只在 forge/executor 路径调,**所有走 gateway.complete 的直连调用**
(导入拆解/模糊调度/ops/圆桌goal)全漏记 —— 实测导入 68 次真拆解账本记 0、by_source 单一 forge。
修:在 GatewayClient.complete 的 Usage 事件上按 contextvar source 记一次。
"""
from __future__ import annotations

import asyncio

import pytest

from karvyloop.gateway.client import GatewayClient
from karvyloop.gateway.events import Done, TextDelta, Usage
from karvyloop.llm.token_ledger import (
    TokenLedger, register_ledger, token_source)


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


class _Adapter:
    def __init__(self, inp=100, out=20):
        self.inp, self.out = inp, out

    async def complete(self, messages, tools, m, prov, system=None):
        yield TextDelta(text="hi")
        yield Usage(input_tokens=self.inp, output_tokens=self.out, cache_read=5, cache_write=0)
        yield Done(stop_reason="end_turn")


@pytest.fixture
def ledger():
    led = TokenLedger(path=None)
    register_ledger(led)
    try:
        yield led
    finally:
        register_ledger(None)


def _run(gw, src):
    async def go():
        with token_source(src):
            async for _ in gw.complete([{"role": "user", "content": "x"}], [], "test-model"):
                pass
    asyncio.run(go())


def test_gateway_complete_records_with_source(ledger):
    gw = GatewayClient(_Reg(), adapters={"fake": _Adapter(100, 20)})
    _run(gw, "agent_import")
    t = ledger.totals()
    assert t["calls"] == 1 and t["input"] == 100 and t["output"] == 20 and t["total"] == 120, t
    by_src = {s["source"]: s for s in ledger.by_source()}
    assert "agent_import" in by_src, f"source 没归到 agent_import: {list(by_src)}"
    assert by_src["agent_import"]["total"] == 120


def test_distinct_sources_separated(ledger):
    gw = GatewayClient(_Reg(), adapters={"fake": _Adapter(10, 2)})
    _run(gw, "agent_import")
    _run(gw, "fuzzy_dispatch")
    _run(gw, "fuzzy_dispatch")
    by_src = {s["source"]: s["calls"] for s in ledger.by_source()}
    # by_source 真按功能分(不再单一 forge)
    assert by_src.get("agent_import") == 1 and by_src.get("fuzzy_dispatch") == 2, by_src


def test_no_usage_event_no_record(ledger):
    """没有 Usage 事件(纯文本流)→ 不记(不凭空造数)。"""
    class _NoUsage:
        async def complete(self, messages, tools, m, prov, system=None):
            yield TextDelta(text="hi")
            yield Done(stop_reason="end_turn")
    gw = GatewayClient(_Reg(), adapters={"fake": _NoUsage()})
    _run(gw, "x")
    assert ledger.totals()["calls"] == 0


# ---- per-task 归因 + 成本预估(#42:打计费黑箱,"花钱之前告诉你"的地基)----

def test_task_attribution_and_estimate(ledger):
    from karvyloop.llm.token_ledger import token_task
    gw = GatewayClient(_Reg(), adapters={"fake": _Adapter(100, 20)})

    async def go(task):
        with token_source("route_to_role"), token_task(task):
            async for _ in gw.complete([{"role": "user", "content": "x"}], [], "test-model"):
                pass
    asyncio.run(go("prop-1"))
    asyncio.run(go("prop-1"))   # 同任务两次调用 → 聚到一起
    asyncio.run(go("prop-2"))
    assert ledger.task_total("prop-1") == 240 and ledger.task_total("prop-2") == 120
    est = ledger.estimate_task_cost(n=10)
    assert est["n"] == 2 and est["mean"] == 180 and est["min"] == 120 and est["max"] == 240
    # 无归因的调用(老路径)不进任务级聚合(诚实:不猜)
    asyncio.run(go(""))
    assert ledger.estimate_task_cost(n=10)["n"] == 2


def test_migration_old_db_gains_task_column(tmp_path):
    """老 tokens.db(无 task_id 列)→ 新版打开自动迁移,老数据保留、老行不进任务聚合。"""
    import sqlite3
    p = tmp_path / "tokens.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(
        "CREATE TABLE token_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
        "day TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'unknown', model TEXT NOT NULL DEFAULT '', "
        "input INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0, "
        "cache_read INTEGER NOT NULL DEFAULT 0, cache_write INTEGER NOT NULL DEFAULT 0);")
    conn.execute("INSERT INTO token_usage (ts, day, source, model, input, output) "
                 "VALUES (1.0, '2026-07-01', 'forge', 'm', 50, 50)")
    conn.commit(); conn.close()
    led = TokenLedger(p)
    assert led.totals()["total"] == 100                      # 老数据在
    assert led.estimate_task_cost()["n"] == 0                # 老行无归因,不猜
    led.record(source="s", model="m", input=10, output=0, task_id="t")
    assert led.task_total("t") == 10                         # 新列可用
    led.close()
