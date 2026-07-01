"""dev-report #8 — token_source 归属:锁住"记账只在 gateway 咽喉、按 contextvar source 归属"。

contextvar 默认 'unknown' + **不跨线程自动传播** → 忘记 `with token_source(...)` 就静默记到
'unknown'。本测试把不变量钉死:① 咽喉(gateway.complete)按当前 source 归属;② 没 set → 'unknown'
(可见,不静默丢);③ 跨线程要在线程内 set(否则丢成 'unknown')—— 防未来新调用点忘了 set。
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from karvyloop.gateway import (
    Done,
    GatewayClient,
    ModelRegistry,
    MockAdapter,
    TextDelta,
    Usage,
)
from karvyloop.llm.token_ledger import (
    TokenLedger,
    current_source,
    register_ledger,
    token_source,
)


def _gw():
    cfg = {
        "models": {"providers": {"p": {"base_url": "x", "models": [
            {"id": "p/a", "api": "anthropic-messages", "context_window": 1000, "max_tokens": 100},
        ]}}},
        "agents": {"defaults": {"model": "p/a"}},
        "embedding": {"model": "p/a"},
    }
    reg = ModelRegistry.from_config(cfg)
    adapters = {"anthropic-messages": MockAdapter(
        api="anthropic-messages",
        script=[TextDelta("hi"), Usage(input_tokens=10, output_tokens=5), Done("end_turn")])}
    return GatewayClient(reg, adapters=adapters)


async def _run_complete(g):
    async for _ in g.complete([{"role": "user", "content": "x"}], [], "p/a"):
        pass


@pytest.fixture
def ledger():
    led = TokenLedger()        # in-memory
    register_ledger(led)
    try:
        yield led
    finally:
        register_ledger(None)  # 别污染别的测试
        led.close()


def test_gateway_choke_point_attributes_to_active_source(ledger):
    """咽喉:gateway.complete 的 Usage 按**当前 token_source** 归属(token-recording-at-gateway)。"""
    with token_source("drive"):
        asyncio.run(_run_complete(_gw()))
    by = {r["source"]: r for r in ledger.by_source()}
    assert "drive" in by and by["drive"]["input"] == 10 and by["drive"]["output"] == 5
    assert "unknown" not in by


def test_forgotten_source_records_as_unknown_not_silent(ledger):
    """没 set token_source → 记到 'unknown'(默认值)。'unknown' 在 by_source 看板里可见 =
    忘记 set 的调用点暴露得出来,不是静默丢进随便哪个 source。"""
    asyncio.run(_run_complete(_gw()))           # 没包 token_source
    by = {r["source"]: r for r in ledger.by_source()}
    assert by.get("unknown", {}).get("total") == 15   # 老实记成 unknown(可被审计揪出)


def test_contextvar_does_not_leak_across_with_block(ledger):
    """token_source 是 with 作用域:出了块就 reset,不会把上一次的 source 漏给下一次调用。"""
    with token_source("forge"):
        asyncio.run(_run_complete(_gw()))
    assert current_source() == "unknown"        # 出块复位
    asyncio.run(_run_complete(_gw()))           # 这次没 set
    by = {r["source"]: r for r in ledger.by_source()}
    assert by["forge"]["total"] == 15 and by["unknown"]["total"] == 15   # 两次分别归属,不串味


def test_cross_thread_needs_set_inside_thread(ledger):
    """**跨线程坑**:contextvar 不自动传到新线程。外层 set、线程内调 LLM → 记成 'unknown';
    必须在线程内(贴着调用点)set 才对。这正是 quality/forge 等慢侧把 token_source 贴近调用点的原因。"""
    # (a) 外层 set,线程里 NOT set → 丢成 unknown(坑的演示)
    def _worker_no_set():
        asyncio.run(_run_complete(_gw()))
    with token_source("outer"):
        t = threading.Thread(target=_worker_no_set); t.start(); t.join()
    by = {r["source"]: r for r in ledger.by_source()}
    assert "outer" not in by and by["unknown"]["total"] == 15   # 外层 source 没跨进线程

    # (b) 线程内贴着调用点 set → 正确归属(正确写法)
    def _worker_set():
        with token_source("threaded_quality"):
            asyncio.run(_run_complete(_gw()))
    t2 = threading.Thread(target=_worker_set); t2.start(); t2.join()
    by2 = {r["source"]: r for r in ledger.by_source()}
    assert by2["threaded_quality"]["total"] == 15
