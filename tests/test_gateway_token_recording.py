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
