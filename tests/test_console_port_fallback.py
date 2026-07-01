"""test_console_port_fallback — 撞端口的处理:外部进程占→挪端口;KarvyLoop 自己占→别静默挪(升级安全)。

Hardy 2026-06-27 抓到的坑:升级时旧版没退还占着 8766,新版一起来若**静默挪到 8767**,用户开 8766
看到的还是旧版、新版偷偷在别处 → 完全不知道。修法:探测占用者是不是 KarvyLoop(GET /api/update_status
有 'current' 即是),是 → 如实告知+退出,不是 → 才安全挪端口。
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from karvyloop.console.entry import (
    _next_free_port, _port_free, _probe_karvyloop_version)


def _a_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---- _port_free / _next_free_port ----
def test_port_free_true_when_free():
    assert _port_free("127.0.0.1", _a_free_port()) is True


def test_port_free_false_when_occupied():
    p = _a_free_port()
    occ = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occ.bind(("127.0.0.1", p)); occ.listen()
    try:
        assert _port_free("127.0.0.1", p) is False
        assert _next_free_port("127.0.0.1", p) > p          # 挪到更高的空闲端口
    finally:
        occ.close()


# ---- _probe_karvyloop_version:区分 KarvyLoop vs 外部 ----
def _serve(handler_body: dict | None, status: int = 200):
    """起一个一次性 http server,/api/update_status 返回给定 JSON(None=非 JSON)。返回 (port, stop)。"""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"not json" if handler_body is None else json.dumps(handler_body).encode())
        def log_message(self, *a):  # 静音
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return port, srv.shutdown


def test_probe_recognizes_karvyloop():
    port, stop = _serve({"current": "2026.6.27", "latest": None, "newer": False})
    try:
        assert _probe_karvyloop_version("127.0.0.1", port) == "2026.6.27"
    finally:
        stop()


def test_probe_foreign_server_returns_none():
    # 有 HTTP 服务但不是 KarvyLoop(没有 'current')→ None → 可安全挪端口
    port, stop = _serve({"some": "other-app"})
    try:
        assert _probe_karvyloop_version("127.0.0.1", port) is None
    finally:
        stop()
    # 返回非 JSON 也 → None
    port2, stop2 = _serve(None)
    try:
        assert _probe_karvyloop_version("127.0.0.1", port2) is None
    finally:
        stop2()


def test_probe_dead_port_returns_none():
    assert _probe_karvyloop_version("127.0.0.1", _a_free_port()) is None  # 没人监听


# ---- i18n 三条新文案两语言齐全 ----
def test_i18n_port_messages_both_langs():
    from karvyloop import i18n
    for lang in ("en", "zh"):
        i18n.set_locale(lang)
        assert "8766" in i18n.t("console.port_fallback", orig=8766, port=8767)
        assert "8767" in i18n.t("console.port_fallback", orig=8766, port=8767)
        assert i18n.t("console.already_running", url="http://x", ver="2026.6.27")
        assert i18n.t("console.old_running", url="http://x", old="2026.6.26", new="2026.6.27")
