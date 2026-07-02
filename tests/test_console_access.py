"""test_console_access — 访问令牌门(跨设备访问 console 的鉴权)。

不变量:① 本机 loopback 判定对 ② 未设 token → 不启用(编程式/测试照旧)③ 设了 token 后**非本机**请求
必须带 token(?token= / cookie / header),否则 401 ④ 带对的 token → 放行 + 落 cookie ⑤ runtime 文件
往返 + `karvyloop url` 读得到 ⑥ 绑 0.0.0.0 才给跨设备带 token 链接,只绑本机不给。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.console import access as acc  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def test_is_loopback():
    for h in ("127.0.0.1", "::1", "localhost", "127.0.0.5", "::ffff:127.0.0.1"):
        assert acc.is_loopback(h)
    for h in ("192.168.1.10", "10.0.0.2", "testclient", "0.0.0.0", ""):
        assert not acc.is_loopback(h)


def test_access_urls(monkeypatch):
    monkeypatch.setattr(acc, "_lan_ip", lambda: "192.168.1.5")
    # 绑 0.0.0.0 → 给带 token 的 LAN 链接
    u = acc.access_urls("0.0.0.0", 8766, "TK")
    assert u["remote"] == "http://192.168.1.5:8766/?token=TK" and u["local"] == "http://localhost:8766/"
    # 显式 LAN host → 用该 host
    assert acc.access_urls("192.168.1.9", 8766, "TK")["remote"] == "http://192.168.1.9:8766/?token=TK"
    # 只绑本机 → 无跨设备链接
    assert acc.access_urls("127.0.0.1", 8766, "TK")["remote"] == ""


def test_runtime_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "runtime_path", lambda: tmp_path / "console.runtime.json")
    acc.write_runtime("SECRET-TOKEN", "0.0.0.0", 8766)
    rt = acc.read_runtime()
    assert rt and rt["token"] == "SECRET-TOKEN" and rt["port"] == 8766
    # 坏文件 → None(不炸)
    (tmp_path / "console.runtime.json").write_text("{ bad", encoding="utf-8")
    assert acc.read_runtime() is None


@pytest.fixture
def client():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    return TestClient(app)


def test_gate_open_without_token(client):
    """没设 access_token(编程式/测试)→ 门不启用,请求照旧过。"""
    assert client.get("/api/snapshot").status_code == 200


def test_gate_requires_token_for_non_loopback(client):
    """设了 token 后,非本机(TestClient host=testclient)请求必须带对的 token。"""
    client.app.state.access_token = "SECRET"
    # 无 token → 401
    r = client.get("/api/snapshot")
    assert r.status_code == 401 and r.json()["ok"] is False
    # 错 token → 401
    assert client.get("/api/snapshot", headers={"x-karvy-token": "WRONG"}).status_code == 401
    # header 带对的 → 200
    assert client.get("/api/snapshot", headers={"x-karvy-token": "SECRET"}).status_code == 200
    # ?token= 带对的 → 200 且落 cookie(之后免带)
    r2 = client.get("/api/snapshot?token=SECRET")
    assert r2.status_code == 200
    assert "karvy_token" in r2.cookies or any("karvy_token" in v for v in r2.headers.get_list("set-cookie"))


def test_url_command_reads_runtime(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(acc, "runtime_path", lambda: tmp_path / "console.runtime.json")
    from karvyloop.cli.main import _cmd_url
    assert _cmd_url() == 1   # 没 runtime → 1
    acc.write_runtime("TK123", "0.0.0.0", 8766)
    monkeypatch.setattr(acc, "_lan_ip", lambda: "192.168.1.5")
    assert _cmd_url() == 0
    out = capsys.readouterr().out
    assert "http://192.168.1.5:8766/?token=TK123" in out   # 打印带 token 的跨设备链接


# ---- 同源门(P0-C1/H2:堵 CSRF + 跨站 WebSocket 劫持)----
def test_origin_ok_unit():
    from karvyloop.console.access import origin_ok
    # 同源:Origin==Host → 放行
    assert origin_ok("http://localhost:8766", "same-origin", "localhost:8766")
    assert origin_ok("http://192.168.1.5:8766", "", "192.168.1.5:8766")
    # 跨源:Origin≠Host → 拒(含 localhost 不同端口的本地恶意页)
    assert not origin_ok("https://evil.com", "", "localhost:8766")
    assert not origin_ok("http://localhost:9999", "", "localhost:8766")
    # Sec-Fetch-Site: cross-site → 拒(即便没 Origin,如跨站 <script> GET)
    assert not origin_ok("", "cross-site", "localhost:8766")
    # 无 Origin(curl/CLI/顶层导航)→ 放行
    assert origin_ok("", "", "localhost:8766")
    assert origin_ok("", "none", "localhost:8766")


def test_gate_rejects_cross_origin(client):
    """带 evil.com Origin 的请求(=恶意网页 CSRF)→ 403,即便同机 loopback。"""
    r = client.get("/api/snapshot", headers={"origin": "https://evil.com", "host": "localhost:8766"})
    assert r.status_code == 403 and "跨源" in r.json()["reason"]
    # sec-fetch-site: cross-site 同样拒
    assert client.get("/api/snapshot", headers={"sec-fetch-site": "cross-site"}).status_code == 403


def test_gate_allows_same_origin_and_nonbrowser(client):
    """同源(Origin==Host)+ 非浏览器(无 Origin,如 TestClient/curl)→ 放行(不破坏正常前端和工具)。"""
    # TestClient 默认不带 Origin → 放行(现有 200 端点不受影响)
    assert client.get("/api/snapshot").status_code == 200
    # 显式同源 → 放行
    r = client.get("/api/snapshot", headers={"origin": "http://testserver", "host": "testserver"})
    assert r.status_code == 200
