"""test_minimax_oauth — MiniMax 设备码 OAuth 引擎(clean-room 自 openclaw 的真实流程)。

守:PKCE 发码 + CSRF state 核对 + 轮询 pending→success + 跨机(on_prompt 给网址+码,不开本机回调)。
fixture token 一律带 FAKE-DO-NOT-LEAK。
"""
from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest
import respx

from karvyloop.llm.minimax_oauth import (
    MiniMaxOAuthError,
    login_device_flow,
    request_device_code,
)

CN_OAUTH = "https://account.minimaxi.com"


def _echo_state_device(request):
    state = parse_qs(request.content.decode())["state"][0]
    return httpx.Response(200, json={
        "user_code": "WXYZ-1234", "verification_uri": "https://account.minimaxi.com/verify",
        "expired_in": 600, "interval": 2, "state": state})


@respx.mock
def test_device_code_pkce_and_state_ok():
    respx.post(CN_OAUTH + "/oauth2/device/code").mock(side_effect=_echo_state_device)
    dc = request_device_code(region="cn", client_id="test-cid", now=lambda: 1000.0)
    assert dc.user_code == "WXYZ-1234"
    assert dc.verification_uri.endswith("/verify")
    assert dc.verifier and dc.interval == 2.0
    assert dc.expires_at == 1600.0            # 相对秒 TTL:now(1000)+600


@respx.mock
def test_device_code_state_mismatch_is_csrf_error():
    respx.post(CN_OAUTH + "/oauth2/device/code").mock(
        return_value=httpx.Response(200, json={
            "user_code": "X", "verification_uri": "https://x/v", "expired_in": 600,
            "state": "WRONG-not-what-we-sent"}))
    with pytest.raises(MiniMaxOAuthError, match="state"):
        request_device_code(region="cn", client_id="test-cid")


def test_unknown_region():
    with pytest.raises(MiniMaxOAuthError, match="region"):
        request_device_code(region="mars", client_id="x")


@respx.mock
def test_login_device_flow_polls_pending_then_success():
    respx.post(CN_OAUTH + "/oauth2/device/code").mock(side_effect=_echo_state_device)
    # token 端点:头两次 pending,第三次 success
    calls = {"n": 0}

    def _token(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(200, json={"status": "pending"})
        return httpx.Response(200, json={
            "status": "success", "access_token": "FAKE-DO-NOT-LEAK-acc",
            "refresh_token": "FAKE-DO-NOT-LEAK-ref", "expired_in": 3600,
            "resource_url": "https://api.minimaxi.com"})
    respx.post(CN_OAUTH + "/oauth2/token").mock(side_effect=_token)

    prompted = {}
    tok = login_device_flow(
        region="cn", client_id="test-cid",
        on_prompt=lambda url, code: prompted.update(url=url, code=code),
        sleep=lambda s: None, now=lambda: 1000.0)
    assert prompted["url"].endswith("/verify") and prompted["code"] == "WXYZ-1234"  # 跨机:给网址+码
    assert tok.access == "FAKE-DO-NOT-LEAK-acc" and tok.refresh == "FAKE-DO-NOT-LEAK-ref"
    assert tok.resource_url == "https://api.minimaxi.com"
    assert calls["n"] == 3                    # 真轮询了(pending→pending→success)


@respx.mock
def test_login_device_flow_authorization_denied_errors():
    respx.post(CN_OAUTH + "/oauth2/device/code").mock(side_effect=_echo_state_device)
    respx.post(CN_OAUTH + "/oauth2/token").mock(
        return_value=httpx.Response(200, json={"status": "error"}))
    with pytest.raises(MiniMaxOAuthError):
        login_device_flow(region="cn", client_id="test-cid",
                          on_prompt=lambda u, c: None, sleep=lambda s: None, now=lambda: 1000.0)


@respx.mock
def test_login_device_flow_times_out():
    respx.post(CN_OAUTH + "/oauth2/device/code").mock(side_effect=_echo_state_device)
    respx.post(CN_OAUTH + "/oauth2/token").mock(
        return_value=httpx.Response(200, json={"status": "pending"}))
    t = {"v": 1000.0}

    def _now():
        t["v"] += 300.0     # 每次问时间就跳 300s → 很快越过 expires_at(1600)
        return t["v"]
    with pytest.raises(MiniMaxOAuthError, match="超时"):
        login_device_flow(region="cn", client_id="test-cid",
                          on_prompt=lambda u, c: None, sleep=lambda s: None, now=_now)


# ---- CLI 接线:写 provider 配置 + 无 client_id 守卫 ----

def test_write_minimax_provider_shape(tmp_path):
    import yaml

    from karvyloop.cli.minimax_login_cmd import _write_minimax_provider
    from karvyloop.llm.minimax_oauth import MiniMaxToken
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("models:\n  providers: {}\n", encoding="utf-8")
    tok = MiniMaxToken(access="FAKE-DO-NOT-LEAK-acc", refresh="FAKE-DO-NOT-LEAK-ref",
                       expires_at=9e12, resource_url="https://api.minimaxi.com")
    _write_minimax_provider(tok, region="cn", model="MiniMax-M3", config_path=cfg_path)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    mm = cfg["models"]["providers"]["minimax"]
    assert mm["auth_header"] == "Authorization"          # Bearer 系:网关据此发 Bearer
    assert mm["api_key"] == "FAKE-DO-NOT-LEAK-acc"        # OAuth access token 当 key
    assert mm["base_url"] == "https://api.minimaxi.com"
    assert mm["models"][0]["id"] == "minimax/MiniMax-M3"
    assert mm["models"][0]["api"] == "anthropic-messages"
    assert cfg["agents"]["defaults"]["model"] == "minimax/MiniMax-M3"   # 默认切到它


def test_minimax_login_needs_client_id():
    import io

    from karvyloop.cli.minimax_login_cmd import cmd_minimax_login
    out = io.StringIO()
    rc = cmd_minimax_login(client_id="", stdout=out)
    assert rc == 1 and "client-id" in out.getvalue()
