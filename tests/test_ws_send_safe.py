"""_send_json_safe:drive 期间客户端断开 → send 不再炸 ASGI(2026-09 实拍 RuntimeError)。

场景:LLM drive 几十秒,用户中途关页/刷新 → close 已发;drive 完成后 send_json 抛
RuntimeError('Cannot call "send" once a close message has been sent.'),它不是
WebSocketDisconnect,穿透 ws_endpoint 的 except 直冒 "Exception in ASGI application"。
"""
from __future__ import annotations

import asyncio

from karvyloop.console.ws import _send_json_safe


class _FakeWebSocket:
    def __init__(self, exc: Exception | None) -> None:
        self.exc = exc
        self.sent: list = []

    async def send_json(self, data, mode: str = "text") -> None:
        if self.exc is not None:
            raise self.exc
        self.sent.append(data)


def test_send_json_safe_returns_false_when_closed():
    ws = _FakeWebSocket(RuntimeError('Cannot call "send" once a close message has been sent.'))
    ok = asyncio.run(_send_json_safe(ws, {"type": "drive_done"}))
    assert ok is False


def test_send_json_safe_returns_false_on_disconnect():
    from fastapi import WebSocketDisconnect
    ws = _FakeWebSocket(WebSocketDisconnect(code=1001))
    assert asyncio.run(_send_json_safe(ws, {"type": "drive_done"})) is False


def test_ws_endpoint_survives_application_state_disconnect():
    """回归(2026-11 线上实拍):连接被并发广播 send 失败标成 application_state=DISCONNECTED 后,
    下一轮 receive_text 的入口状态检查抛 WebSocketDisconnected(RuntimeError 子类,starlette<1 抛
    裸 RuntimeError)——它不是 WebSocketDisconnect,ws_endpoint 必须同样静默退出,不能冒 ASGI 500。"""
    from types import SimpleNamespace

    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.console import build_console_app
    from karvyloop.console.ws import WebSocketDisconnected, ws_endpoint

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)

    class _DisconnectedWS:
        def __init__(self) -> None:
            self.app = app
            self.headers: dict = {}          # 无 Origin → 同源门放行(非浏览器客户端)
            self.client = SimpleNamespace(host="127.0.0.1")
            self.cookies: dict = {}
            self.query_params: dict = {}
            self.accepted = False
            self.sent: list = []

        async def accept(self, *args, **kwargs) -> None:
            self.accepted = True

        async def send_json(self, data, mode: str = "text") -> None:
            self.sent.append(data)

        async def receive_text(self) -> str:
            raise WebSocketDisconnected('WebSocket is not connected. Need to call "accept" first.')

    ws = _DisconnectedWS()
    asyncio.run(ws_endpoint(ws))          # 不抛 = 通过(此前会 RuntimeError 冒到 uvicorn)
    assert ws.accepted is True
    assert ws not in app.state.ws_clients  # finally 仍把连接剔出去


def test_send_json_safe_delivers_when_alive():
    ws = _FakeWebSocket(None)
    payload = {"type": "drive_done", "payload": {"text": "hi"}}
    assert asyncio.run(_send_json_safe(ws, payload)) is True
    assert ws.sent == [payload]
