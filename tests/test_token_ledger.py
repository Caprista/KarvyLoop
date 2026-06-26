"""test_token_ledger — Token 账本(测量层,M3+ 拍 9.3a / docs/28 TK-1)。

AC:
- AC1-AC3: record + totals/by_source/by_model 聚合(cache 读写分开)
- AC4: token_source contextvar 归属
- AC5: 模块级 record() 走全局 ledger;无 ledger no-op 不崩
- AC6: 跨进程(sqlite 持久)
- AC7: provider.chat 经 token_source 记进账本(集成,用 MockProvider/真 transport stub)
- AC8: /api/tokens 看板端点
- AC9: 按天聚合
"""
from __future__ import annotations

import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.llm.token_ledger import (  # noqa: E402
    TokenLedger, token_source, current_source, register_ledger, get_ledger, record,
)


@pytest.fixture(autouse=True)
def _reset_global():
    yield
    register_ledger(None)  # 每个测试后清全局,防串


# ---- AC1-AC3: record + 聚合 ----


def test_record_and_totals():
    led = TokenLedger()
    led.record(source="forge", model="m1", input=100, output=50, cache_read=10, cache_write=5)
    led.record(source="凝习惯", model="m2", input=200, output=80)
    t = led.totals()
    assert t["input"] == 300 and t["output"] == 130
    assert t["cache_read"] == 10 and t["cache_write"] == 5
    assert t["total"] == 430 and t["calls"] == 2


def test_by_source_and_model():
    led = TokenLedger()
    led.record(source="forge", model="m1", input=100, output=50)
    led.record(source="forge", model="m1", input=100, output=50)
    led.record(source="凝习惯", model="m2", input=10, output=5)
    bs = {r["source"]: r for r in led.by_source()}
    assert bs["forge"]["total"] == 300 and bs["forge"]["calls"] == 2
    assert bs["凝习惯"]["total"] == 15
    bm = {r["model"]: r for r in led.by_model()}
    assert bm["m1"]["calls"] == 2


def test_cache_separated():
    """cache 读/写分开统计(便宜 10×,必须分开看真实成本)。"""
    led = TokenLedger()
    led.record(source="x", model="m", input=1000, output=0, cache_read=900, cache_write=100)
    t = led.totals()
    assert t["cache_read"] == 900 and t["cache_write"] == 100


# ---- AC4: token_source contextvar ----


def test_token_source_contextvar():
    assert current_source() == "unknown"
    with token_source("drive"):
        assert current_source() == "drive"
        with token_source("forge"):
            assert current_source() == "forge"
        assert current_source() == "drive"
    assert current_source() == "unknown"


# ---- AC5: 模块级 record 走全局 ledger;无 ledger no-op ----


def test_module_record_uses_global_and_source():
    led = TokenLedger()
    register_ledger(led)
    with token_source("意图"):
        record(model="m", input=42, output=8)
    rows = led.by_source()
    assert rows[0]["source"] == "意图" and rows[0]["input"] == 42


def test_record_no_ledger_noop():
    register_ledger(None)
    # 不崩
    record(model="m", input=1, output=1)
    assert get_ledger() is None


# ---- AC6: 跨进程持久 ----


def test_persistence(tmp_path):
    p = tmp_path / "tokens.db"
    l1 = TokenLedger(p)
    l1.record(source="forge", model="m", input=100, output=50)
    l1.close()
    l2 = TokenLedger(p)
    assert l2.totals()["total"] == 150


# ---- AC9: 按天聚合 ----


def test_by_day(tmp_path):
    base = [1_700_000_000.0]
    led = TokenLedger(tmp_path / "t.db", clock=lambda: base[0])
    led.record(source="a", model="m", input=10, output=0)
    base[0] += 86400 * 2  # +2 天
    led.record(source="a", model="m", input=20, output=0)
    days = led.by_day()
    assert len(days) == 2


# ---- AC7: provider.chat 经 token_source 记账(集成)----


def test_provider_chat_records_with_source(monkeypatch):
    """真 AnthropicProvider._achat 路径:stub transport 返 usage → record 进账本带 source。"""
    from karvyloop.llm.provider import AnthropicProvider, ChatRequest, Message
    from karvyloop.llm.config import ProviderConfig
    import karvyloop.llm.provider as prov_mod

    led = TokenLedger()
    register_ledger(led)

    # stub transport.achat 返带 usage 的 response
    class _Resp:
        content = "ok"; model = "anthropic/claude-sonnet-4-6"
        usage = {"prompt_tokens": 123, "completion_tokens": 45,
                 "cache_read_input_tokens": 60, "cache_creation_input_tokens": 7}

    class _Transport:
        async def achat(self, request, profile, api_key=""):
            return _Resp()

    monkeypatch.setattr("karvyloop.llm.transports.require_transport", lambda mode: _Transport())

    cfg = ProviderConfig(type="anthropic", api_key="", base_url="", default_model="x")
    p = AnthropicProvider(cfg)
    with token_source("forge"):
        resp = p.chat(ChatRequest(model="anthropic/claude-sonnet-4-6", messages=[Message(role="user", content="hi")]))
    assert resp.input_tokens == 123 and resp.output_tokens == 45
    row = led.by_source()[0]
    assert row["source"] == "forge"
    assert row["input"] == 123 and row["output"] == 45
    assert row["cache_read"] == 60 and row["cache_write"] == 7


# ---- AC8: /api/tokens 看板 ----


def test_api_tokens_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    led = TokenLedger(tmp_path / "t.db")
    led.record(source="forge", model="m1", input=100, output=50)
    led.record(source="凝习惯", model="m2", input=20, output=5)
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.token_ledger = led
    client = TestClient(app)
    r = client.get("/api/tokens")
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["total"] == 175
    sources = {x["source"] for x in body["by_source"]}
    assert sources == {"forge", "凝习惯"}


def test_api_tokens_no_ledger():
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    client = TestClient(app)
    r = client.get("/api/tokens")
    assert r.status_code == 200
    assert r.json()["totals"] == {}
