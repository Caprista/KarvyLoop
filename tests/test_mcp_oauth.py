"""test_mcp_oauth — MCP remote server 的 OAuth 2.1 客户端(docs/96 刀2)。

守三条安全边界(协议本身借 SDK,不测 SDK):
- 0600 独立文件存 token,**绝不进 config.yaml / log / repr**,export 排除。
- callback 忠实捕获 code+state 还给 SDK;授权被拒/超时 fail-loud。
- build_oauth_provider 造出的是 httpx.Auth,能塞进 remote 连接。

fixture key 一律带 FAKE-DO-NOT-LEAK(凭证纪律)。
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request
from pathlib import Path

import pytest

from karvyloop.mcp_oauth import (
    KarvyTokenStorage,
    OAuthCallbackError,
    _LocalCallbackReceiver,
    _safe_name,
    build_oauth_provider,
)

FAKE_ACCESS = "FAKE-DO-NOT-LEAK-access-tok-123"
FAKE_REFRESH = "FAKE-DO-NOT-LEAK-refresh-tok-456"


def _oauth_token(**kw):
    from mcp.shared.auth import OAuthToken
    d = dict(access_token=FAKE_ACCESS, token_type="Bearer",
             expires_in=3600, refresh_token=FAKE_REFRESH)
    d.update(kw)
    return OAuthToken(**d)


# ---- ① 存储:0600 独立文件,roundtrip,绝不进 config ----

@pytest.mark.asyncio
async def test_token_storage_roundtrip(tmp_path: Path):
    st = KarvyTokenStorage("gmail", base_dir=tmp_path)
    assert await st.get_tokens() is None            # 空库
    await st.set_tokens(_oauth_token())
    got = await st.get_tokens()
    assert got.access_token == FAKE_ACCESS and got.refresh_token == FAKE_REFRESH


@pytest.mark.asyncio
async def test_client_info_roundtrip(tmp_path: Path):
    from mcp.shared.auth import OAuthClientInformationFull
    st = KarvyTokenStorage("gmail", base_dir=tmp_path)
    assert await st.get_client_info() is None
    info = OAuthClientInformationFull(
        client_id="FAKE-client-id", redirect_uris=["http://127.0.0.1:9/callback"])
    await st.set_client_info(info)
    got = await st.get_client_info()
    assert got.client_id == "FAKE-client-id"


@pytest.mark.asyncio
async def test_token_and_client_info_share_one_file_no_clobber(tmp_path: Path):
    """token 和 client_info 同住一个 <server>.json,互不覆盖。"""
    from mcp.shared.auth import OAuthClientInformationFull
    st = KarvyTokenStorage("slack", base_dir=tmp_path)
    await st.set_tokens(_oauth_token())
    await st.set_client_info(OAuthClientInformationFull(
        client_id="FAKE-cid", redirect_uris=["http://127.0.0.1:9/callback"]))
    assert (await st.get_tokens()).access_token == FAKE_ACCESS   # 写 client_info 没冲掉 token
    assert (await st.get_client_info()).client_id == "FAKE-cid"


@pytest.mark.asyncio
async def test_per_server_isolation(tmp_path: Path):
    a = KarvyTokenStorage("gmail", base_dir=tmp_path)
    b = KarvyTokenStorage("slack", base_dir=tmp_path)
    await a.set_tokens(_oauth_token(access_token="FAKE-DO-NOT-LEAK-gmail"))
    await b.set_tokens(_oauth_token(access_token="FAKE-DO-NOT-LEAK-slack"))
    assert (await a.get_tokens()).access_token == "FAKE-DO-NOT-LEAK-gmail"
    assert (await b.get_tokens()).access_token == "FAKE-DO-NOT-LEAK-slack"
    # 两个独立文件
    files = sorted(p.name for p in (tmp_path / "mcp_oauth").glob("*.json"))
    assert files == ["gmail.json", "slack.json"]


@pytest.mark.asyncio
async def test_storage_lands_only_in_mcp_oauth_never_config(tmp_path: Path):
    """写 token **只**落 mcp_oauth/<server>.json;绝不碰 config.yaml(哪怕存在)。"""
    (tmp_path / "config.yaml").write_text("models: {}\n", encoding="utf-8")
    before = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    st = KarvyTokenStorage("gmail", base_dir=tmp_path)
    await st.set_tokens(_oauth_token())
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == before   # config 没被碰
    assert (tmp_path / "mcp_oauth" / "gmail.json").exists()
    # config.yaml 里绝无 token 影子
    assert FAKE_ACCESS not in before


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="0600 权限位只在 POSIX 有意义")
async def test_token_file_is_0600(tmp_path: Path):
    st = KarvyTokenStorage("gmail", base_dir=tmp_path)
    await st.set_tokens(_oauth_token())
    mode = (tmp_path / "mcp_oauth" / "gmail.json").stat().st_mode & 0o777
    assert mode == 0o600, f"token 文件必须 0600,实为 {oct(mode)}"


def test_storage_repr_never_leaks_token(tmp_path: Path):
    st = KarvyTokenStorage("gmail", base_dir=tmp_path)
    assert FAKE_ACCESS not in repr(st) and "access_token" not in repr(st)


@pytest.mark.asyncio
async def test_corrupt_file_treated_as_empty_not_crash(tmp_path: Path):
    st = KarvyTokenStorage("gmail", base_dir=tmp_path)
    (tmp_path / "mcp_oauth" / "gmail.json").write_text("}{ broken", encoding="utf-8")
    assert await st.get_tokens() is None      # 坏文件 → 当没有(宁重授权不喂坏 token)


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", ["null", "[1,2,3]", '"hi"', "42", "true"])
async def test_valid_json_but_not_object_treated_as_empty(tmp_path: Path, junk):
    """对抗验收 #8:有效 JSON 但不是对象(null/list/裸值)→ 当空,读写都不崩(能自愈重授权)。"""
    st = KarvyTokenStorage("gmail", base_dir=tmp_path)
    (tmp_path / "mcp_oauth" / "gmail.json").write_text(junk, encoding="utf-8")
    assert await st.get_tokens() is None            # 读不崩
    await st.set_tokens(_oauth_token())             # 写不崩(覆盖坏文件自愈)
    assert (await st.get_tokens()).access_token == FAKE_ACCESS


def test_safe_name_sanitizes():
    assert _safe_name("Gmail/../../etc") == "Gmail_.._.._etc"
    assert _safe_name("") == "server"
    assert _safe_name("a b:c") == "a_b_c"


# ---- ② 回调 receiver:忠实捕获 code+state,fail-loud ----

@pytest.mark.asyncio
async def test_callback_captures_code_and_state(tmp_path: Path):
    r = _LocalCallbackReceiver()
    r._start()

    def _hit():
        urllib.request.urlopen(
            r.redirect_uri + "?code=THE_CODE&state=THE_STATE", timeout=5).read()
    threading.Thread(target=_hit, daemon=True).start()
    code, state = await r.callback_handler()
    assert code == "THE_CODE" and state == "THE_STATE"


def test_shutdown_on_unstarted_receiver_does_not_hang(tmp_path: Path):
    """对抗验收 note#2:没起服务线程就 _shutdown() 不能永久阻塞(HTTPServer.shutdown 的坑)。"""
    r = _LocalCallbackReceiver()
    done = threading.Event()
    threading.Thread(target=lambda: (r._shutdown(), done.set()), daemon=True).start()
    assert done.wait(timeout=3), "未起线程的 _shutdown 挂死了(应只 server_close 不 shutdown)"


@pytest.mark.asyncio
async def test_stray_request_without_code_does_not_consume_callback(tmp_path: Path):
    """对抗验收 note#3:杂请求(无 code 无 error,如 favicon)不消费一次性回调 → 真 code 仍收得到。"""
    r = _LocalCallbackReceiver()
    r._start()
    # 先来一发杂请求(favicon 式,无 code)—— 不该把 event 触发掉
    urllib.request.urlopen(r.redirect_uri.replace("/callback", "/favicon.ico"), timeout=5).read()
    assert not r._event.is_set(), "杂请求不该消费一次性回调"
    # 再来真回调 → 仍能拿到 code
    def _hit():
        urllib.request.urlopen(r.redirect_uri + "?code=REAL&state=S", timeout=5).read()
    threading.Thread(target=_hit, daemon=True).start()
    code, state = await r.callback_handler()
    assert code == "REAL" and state == "S"


@pytest.mark.asyncio
async def test_callback_rejects_when_authorization_denied(tmp_path: Path):
    r = _LocalCallbackReceiver()
    r._start()

    def _hit():
        urllib.request.urlopen(r.redirect_uri + "?error=access_denied", timeout=5).read()
    threading.Thread(target=_hit, daemon=True).start()
    with pytest.raises(OAuthCallbackError):
        await r.callback_handler()


@pytest.mark.asyncio
async def test_redirect_handler_opens_browser_or_prints(monkeypatch, capsys):
    r = _LocalCallbackReceiver()
    calls = {}
    monkeypatch.setattr("webbrowser.open", lambda u: calls.setdefault("url", u) or True)
    await r.redirect_handler("https://auth.example.com/authorize?x=1")
    assert calls["url"] == "https://auth.example.com/authorize?x=1"
    r._shutdown()


@pytest.mark.asyncio
async def test_redirect_handler_prints_url_when_browser_fails(monkeypatch, capsys):
    r = _LocalCallbackReceiver()
    monkeypatch.setattr("webbrowser.open", lambda u: (_ for _ in ()).throw(RuntimeError("no display")))
    await r.redirect_handler("https://auth.example.com/authorize?x=1")
    out = capsys.readouterr().out
    assert "https://auth.example.com/authorize?x=1" in out   # fail-loud 指路
    r._shutdown()


# ---- ③ provider:造出 httpx.Auth,redirect_uri 与回调端口一致 ----

def test_build_provider_is_httpx_auth(tmp_path: Path):
    import httpx
    p = build_oauth_provider("https://mcp.example.com/mcp", "gmail",
                             scopes=["gmail.readonly"], base_dir=tmp_path)
    assert isinstance(p, httpx.Auth)


# ---- ④ export 排除:mcp_oauth/ 整个目录不入包 ----

def test_export_excludes_mcp_oauth_dir():
    from pathlib import PurePosixPath
    from karvyloop.cli.export_cmd import _is_excluded
    assert _is_excluded(PurePosixPath("mcp_oauth/gmail.json")) is True
    assert _is_excluded(PurePosixPath("mcp_oauth/slack.json")) is True
    # 别误伤:名字里带 mcp_oauth 但不在该目录下的正常数据照常入包
    assert _is_excluded(PurePosixPath("skills/mcp_oauth_helper/SKILL.md")) is False


# ---- ⑤ 接线:McpServerConfig 带 oauth 字段 → _open_session 走 OAuth 分支 ----

@pytest.mark.asyncio
async def test_open_session_builds_oauth_provider_for_oauth_server(monkeypatch, tmp_path):
    """auth_kind=='oauth' 的 remote server → _open_session 用 build_oauth_provider 造 auth
    塞进 httpx client(不真连;拦截 build_oauth_provider + streamable_http_client 验接线)。"""
    import contextlib

    from karvyloop import mcp_client
    from karvyloop.mcp_client import McpServerConfig

    seen = {}

    def _fake_build(url, name, *, scopes=None):
        seen["url"] = url
        seen["name"] = name
        seen["scopes"] = scopes
        import httpx
        return httpx.BasicAuth("x", "y")   # 任意 httpx.Auth 占位

    monkeypatch.setattr("karvyloop.mcp_oauth.build_oauth_provider", _fake_build)

    # 拦 streamable_http_client:别真发网络,拿到 http_client 看 auth 有没有挂上
    captured = {}

    @contextlib.asynccontextmanager
    async def _fake_shc(url, http_client=None, terminate_on_close=True):
        captured["auth"] = getattr(http_client, "auth", None)
        raise mcp_client.McpConnectError("stop-here")   # 挂上就够了,不继续
        yield  # pragma: no cover

    monkeypatch.setattr("mcp.client.streamable_http.streamable_http_client", _fake_shc)

    cfg = McpServerConfig(name="gmail", url="https://mcp.example.com/mcp",
                          transport="http", auth_kind="oauth",
                          oauth_scopes=["gmail.readonly"])
    stack = contextlib.AsyncExitStack()
    with pytest.raises(mcp_client.McpConnectError):
        await mcp_client._open_session(stack, cfg)
    await stack.aclose()

    assert seen == {"url": "https://mcp.example.com/mcp", "name": "gmail",
                    "scopes": ["gmail.readonly"]}
    assert captured["auth"] is not None      # OAuth provider 真挂到了 httpx client 上


# ---- ⑥ config.yaml 路径:`auth: oauth` + scopes 读得回来,不落 token ----

def test_config_reads_auth_oauth_and_scopes(tmp_path: Path):
    from karvyloop.coding.tools.mcp_tool import read_mcp_server_configs
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcp:\n"
        "  servers:\n"
        "    - name: linear\n"
        "      url: https://mcp.linear.app/mcp\n"
        "      auth: oauth\n"
        "      scopes: [read, write]\n",
        encoding="utf-8")
    out = read_mcp_server_configs(str(cfg))
    assert len(out) == 1
    c = out[0]
    assert c.name == "linear" and c.transport_kind == "http"
    assert c.auth_kind == "oauth" and c.oauth_scopes == ["read", "write"]
    assert not c.headers      # OAuth 不落静态 Authorization header


def test_config_static_token_stays_non_oauth(tmp_path: Path):
    """老的 token 形状不受影响:auth_kind 空,token → Authorization header。"""
    from karvyloop.coding.tools.mcp_tool import read_mcp_server_configs
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcp:\n"
        "  servers:\n"
        "    - name: notion\n"
        "      url: https://mcp.notion.com/mcp\n"
        "      token: FAKE-DO-NOT-LEAK-ntn\n",
        encoding="utf-8")
    c = read_mcp_server_configs(str(cfg))[0]
    assert c.auth_kind == "" and c.oauth_scopes == []
    assert c.headers.get("Authorization") == "Bearer FAKE-DO-NOT-LEAK-ntn"


# ---- ⑦ 预设机制:build_remote_server_config 支持 auth=oauth(不落 token,OAuth 必 https)----

def test_build_remote_config_oauth_shape():
    from karvyloop.console.mcp_presets import build_remote_server_config
    entry = build_remote_server_config("https://mcp.linear.app/mcp", name="linear",
                                       auth="oauth", scopes=["read", "write"])
    assert entry == {"name": "linear", "url": "https://mcp.linear.app/mcp",
                     "transport": "http", "auth": "oauth", "scopes": ["read", "write"]}
    assert "token" not in entry


def test_build_remote_config_oauth_refuses_plain_http():
    from karvyloop.console.mcp_presets import build_remote_server_config
    with pytest.raises(ValueError):
        build_remote_server_config("http://mcp.example.com/mcp", name="x", auth="oauth")
