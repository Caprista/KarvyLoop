"""test_console_ws — WebSocket /ws 端点(M3+ 批 8.5-C-backend)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-C。

AC 列表:
- AC6: WS connect → server push 首次 snapshot
- AC7: client 发 intent → server push drive_done
- AC8: K5 audit: h2a_decision ACCEPT → server push h2a_envelope with by=[]
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


@pytest.fixture
def app():
    workbench = WorkbenchObserver()
    return build_console_app(workbench=workbench, main_loop=None)


@pytest.fixture
def client(app):
    return TestClient(app)


# ---------- AC6: WS connect → 收到首次 snapshot ----------

class TestAC6WSInitialSnapshot:
    def test_ws_connect_sends_initial_snapshot(self, client):
        """AC6: WS connect → server 推 1 条 snapshot。"""
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "snapshot"
            assert "payload" in msg
            # payload 是 widget_snapshot dict
            assert "domains" in msg["payload"]


# ---------- AC7: WS intent 收发 ----------

class TestAC7WSIntentRoundtrip:
    def test_ws_intent_returns_drive_done(self, client):
        """AC7: client 发 intent → server push drive_done(无 main_loop → error)。"""
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # 首次 snapshot
            ws.send_json({"type": "intent", "payload": {"intent": "hello"}})
            msg = ws.receive_json()
            assert msg["type"] == "drive_done"
            assert "error" in msg["payload"]
            assert "MainLoop" in msg["payload"]["error"]

    def test_ws_empty_intent_returns_error(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "intent", "payload": {"intent": ""}})
            msg = ws.receive_json()
            assert msg["type"] == "error"


# ---------- AC8: K5 audit — h2a_decision ACCEPT → envelope, by=[] ----------

class TestAC8WSH2AK5Audit:
    def test_ws_h2a_accept_returns_envelope_with_empty_by(self, client):
        """AC8: WS h2a_decision ACCEPT → server push h2a_envelope,envelope.by=[]。"""
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "h2a_decision", "payload": {
                "proposal_id": "p1", "decision": "ACCEPT",
            }})
            msg = ws.receive_json()
            assert msg["type"] == "h2a_envelope"
            env = msg["payload"]["envelope"]
            assert env is not None
            assert env["type"] == "accept"  # EnvelopeType.ACCEPT = "accept"
            assert env["by"] == []  # K5 不变量

    def test_ws_h2a_reject_without_reason_is_allowed(self, client):
        """WS REJECT 无 reason → 不报错:补占位 reason,返 by=[] 的 reject envelope。

        Hardy:不强制用户填理由;协议 A8(REJECT 必带非空 reason)由占位守住,用户不被挡。
        """
        from karvyloop.console.routes import DEFAULT_REJECT_REASON
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "h2a_decision", "payload": {
                "proposal_id": "p1", "decision": "REJECT", "reason": "",
            }})
            msg = ws.receive_json()
            assert msg["type"] == "h2a_envelope"
            env = msg["payload"]["envelope"]
            assert env is not None and not msg["payload"].get("error")
            assert env["type"] == "reject"
            assert env["by"] == []                              # K5 不变量仍锁
            assert env["payload"]["reason"] == DEFAULT_REJECT_REASON  # A8:非空占位

    def test_ws_h2a_defer_returns_null_envelope(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "h2a_decision", "payload": {
                "proposal_id": "p1", "decision": "DEFER",
            }})
            msg = ws.receive_json()
            assert msg["type"] == "h2a_envelope"
            assert msg["payload"]["envelope"] is None


# ---------- ping/pong ----------

class TestWSPingPong:
    def test_ws_ping_returns_pong(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "ping"})
            msg = ws.receive_json()
            assert msg["type"] == "pong"
