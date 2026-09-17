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


def test_send_json_safe_delivers_when_alive():
    ws = _FakeWebSocket(None)
    payload = {"type": "drive_done", "payload": {"text": "hi"}}
    assert asyncio.run(_send_json_safe(ws, payload)) is True
    assert ws.sent == [payload]
