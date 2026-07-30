"""test_oauth_broker — 跨机 OAuth 回调中枢(docs/43 远程 + docs/96 刀2)。

守:回调按 state 关联"挂授权URL"与"等 code";命中 deliver→唤醒;超时/被拒 fail-loud;
callback 路由把 ?code=&state= 送进 broker;build_oauth_provider 跨机分支用 console 路由做
redirect_uri(非 localhost)、同机分支退回 localhost。这些是"任何机器都能 OAuth"的地基。
真·跨机 E2E(云主机/远程 + 真厂商 + 真授权)是门到门验收(需 Hardy),不在单测内。
"""
from __future__ import annotations

import asyncio

import pytest

from karvyloop.console.oauth_broker import (
    CALLBACK_PATH,
    OAuthBroker,
    OAuthCallbackError,
)

AUTH_URL = "https://auth.example.com/authorize?response_type=code&state=ST8TE-xyz&client_id=c1"


# ---- ① broker 按 state 关联:redirect 登记 → deliver 唤醒 → callback 拿到 code+state ----

@pytest.mark.asyncio
async def test_flow_redirect_then_deliver_resolves_callback():
    broker = OAuthBroker()
    flow = broker.new_flow("linear")
    await flow.redirect_handler(AUTH_URL)                 # SDK 先调:登记 state + 挂 URL
    assert broker.pending_auth_url("linear") == AUTH_URL  # 前端能取到授权 URL 去导航浏览器
    # 模拟浏览器授权后回调打进来
    async def _deliver():
        await asyncio.sleep(0.01)
        assert broker.deliver("ST8TE-xyz", "THE_CODE") is True
    asyncio.ensure_future(_deliver())
    code, state = await flow.callback_handler()
    assert code == "THE_CODE" and state == "ST8TE-xyz"
    assert broker.pending_auth_url("linear") is None      # 完成后清理


@pytest.mark.asyncio
async def test_deliver_unknown_state_returns_false():
    broker = OAuthBroker()
    assert broker.deliver("no-such-state", "x") is False


@pytest.mark.asyncio
async def test_deliver_error_makes_callback_fail_loud():
    broker = OAuthBroker()
    flow = broker.new_flow("linear")
    await flow.redirect_handler(AUTH_URL)
    async def _deny():
        await asyncio.sleep(0.01)
        assert broker.deliver_error("ST8TE-xyz", "access_denied") is True
    asyncio.ensure_future(_deny())
    with pytest.raises(OAuthCallbackError):
        await flow.callback_handler()


@pytest.mark.asyncio
async def test_callback_times_out_fail_loud(monkeypatch):
    import karvyloop.console.oauth_broker as ob
    monkeypatch.setattr(ob, "_FLOW_TIMEOUT_S", 0.2)
    broker = OAuthBroker()
    flow = broker.new_flow("linear")
    await flow.redirect_handler(AUTH_URL)
    with pytest.raises(OAuthCallbackError):
        await flow.callback_handler()      # 没人 deliver → 0.2s 超时抛错


# ---- ② callback 路由把 ?code=&state= 送进 broker(浏览器落点)----

def test_callback_route_delivers_to_broker():
    from fastapi.testclient import TestClient

    from karvyloop.console import build_console_app
    from karvyloop.console.oauth_broker import get_broker
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    broker = get_broker(app)

    async def _setup_flow():
        flow = broker.new_flow("linear")
        await flow.redirect_handler(AUTH_URL)     # 登记 state=ST8TE-xyz(在 broker._by_state)
        return flow
    asyncio.run(_setup_flow())

    client = TestClient(app)
    r = client.get(CALLBACK_PATH, params={"code": "ROUTE_CODE", "state": "ST8TE-xyz"})
    # 200 + "授权完成" 只在 broker.deliver(state,code) 命中并送达时才出 → 即证路由把 code 送进了 broker
    assert r.status_code == 200 and "授权完成" in r.text


def test_callback_route_no_matching_flow_is_honest_409():
    from fastapi.testclient import TestClient

    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    r = TestClient(app).get(CALLBACK_PATH, params={"code": "x", "state": "ghost"})
    assert r.status_code == 409 and "没有匹配" in r.text


def test_callback_route_error_param_shows_denied():
    from fastapi.testclient import TestClient

    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    r = TestClient(app).get(CALLBACK_PATH, params={"error": "access_denied"})
    assert "授权未完成" in r.text


# ---- ③ build_oauth_provider 跨机 vs 同机:redirect_uri 打到 console 路由 or localhost ----

def test_provider_crossmachine_uses_console_route_redirect(monkeypatch, tmp_path):
    captured = {}

    class _SpyProvider:
        def __init__(self, *, server_url, client_metadata, storage, redirect_handler,
                     callback_handler, **kw):
            captured["redirect_uris"] = [str(u) for u in client_metadata.redirect_uris]
            captured["redirect_handler"] = redirect_handler
    monkeypatch.setattr("mcp.client.auth.OAuthClientProvider", _SpyProvider)

    from karvyloop.mcp_oauth import build_oauth_provider
    broker = OAuthBroker()
    flow = broker.new_flow("linear")
    build_oauth_provider("https://mcp.linear.app/mcp", "linear", base_dir=tmp_path,
                         callback_base_url="https://mybox.example.com", flow=flow)
    assert captured["redirect_uris"] == ["https://mybox.example.com" + CALLBACK_PATH]
    # 绑定方法每次取都是新对象,比 __self__:redirect_handler 确实来自这个 broker flow(非 localhost)
    assert captured["redirect_handler"].__self__ is flow


def test_provider_samemachine_falls_back_to_localhost(monkeypatch, tmp_path):
    captured = {}

    class _SpyProvider:
        def __init__(self, *, server_url, client_metadata, storage, redirect_handler,
                     callback_handler, **kw):
            captured["redirect_uris"] = [str(u) for u in client_metadata.redirect_uris]
    monkeypatch.setattr("mcp.client.auth.OAuthClientProvider", _SpyProvider)

    from karvyloop.mcp_oauth import build_oauth_provider
    build_oauth_provider("https://mcp.linear.app/mcp", "linear", base_dir=tmp_path)  # 无 base_url/flow
    assert captured["redirect_uris"][0].startswith("http://127.0.0.1:")             # 退回 localhost
