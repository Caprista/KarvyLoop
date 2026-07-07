"""test_recall_as_of_drive — docs/69 Q4 收尾:drive 侧过去认知问句 → recall_block(as_of=T)。

复现先行:此前"你当时/上个月怎么理解的"仍走当下召回(recall_block 的 as_of 永远 None,
drive_done 也没有 recall_as_of 标)。本文件钉死:
- 过去认知问句(命中 + 可解析时刻)→ recall_block 收到 as_of(非 None) + payload 带 recall_as_of;
- 普通问句 / 只有"当时"没日期 → recall_block 的 as_of 仍 None + payload 无 recall_as_of(零回归);
- REST /api/intent 与 WS intent 双路径不漂移。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402

_NOW = 1_700_000_000.0


def _belief(content: str) -> Belief:
    return Belief(content=content, provenance={"source": "distill", "ts": _NOW},
                  freshness_ts=_NOW, scope="personal")


class _SpyMemory(MemoryManager):
    """记住每次 recall_block 收到的 as_of(验 drive 侧到底传没传时点)。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.as_of_calls: list = []

    def recall_block(self, query, *, as_of=None, **kw):
        self.as_of_calls.append(as_of)
        return super().recall_block(query, as_of=as_of, **kw)


def _stub_drive(monkeypatch):
    import karvyloop.console.routes as routes_mod
    import karvyloop.console.ws as ws_mod
    from karvyloop.runtime.main_loop import Brain

    async def fake_drive(intent, ml, *, ctx=None, **kw):
        from karvyloop.workbench.main_loop_bridge import DriveOutcome
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="回应:" + intent,
                            skill_name="", fast_brain_hit=False, crystallized=False,
                            task_id="t1")

    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)
    monkeypatch.setattr(ws_mod, "drive_in_tui", fake_drive)


def _console_app(tmp_path, mem):
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    app.state.conversation_manager = mgr
    app.state.memory = mem
    return app


# ---- REST /api/intent ----

def test_rest_past_cognition_query_passes_as_of(tmp_path, monkeypatch):
    """【复现靶】'上个月你以为我在哪家公司' → recall_block 收到 as_of(非 None)+ payload 带 recall_as_of。"""
    _stub_drive(monkeypatch)
    mem = _SpyMemory()
    mem.write(_belief("用户在 A 公司上班"))
    client = TestClient(_console_app(tmp_path, mem))

    r = client.post("/api/intent", json={"intent": "上个月你以为我在哪家公司?"})
    assert r.status_code == 200
    assert mem.as_of_calls and mem.as_of_calls[0] is not None, "过去认知问句却没按时点召回(as_of=None)"
    body = r.json()
    assert "recall_as_of" in body, "payload 未标 recall_as_of(chip 无从显示'按 X 时点的记忆')"
    assert isinstance(body["recall_as_of"], (int, float))


def test_rest_ordinary_query_no_as_of(tmp_path, monkeypatch):
    """普通问句 → as_of 仍 None + payload 无 recall_as_of(零回归)。"""
    _stub_drive(monkeypatch)
    mem = _SpyMemory()
    mem.write(_belief("用户在 A 公司上班"))
    client = TestClient(_console_app(tmp_path, mem))

    r = client.post("/api/intent", json={"intent": "帮我查下公司地址"})
    assert r.status_code == 200
    assert mem.as_of_calls and mem.as_of_calls[0] is None
    assert "recall_as_of" not in r.json()


def test_rest_report_task_with_time_word_no_as_of(tmp_path, monkeypatch):
    """取舍例:'上个月的报表做了吗'带时间词但不是问过去认知 → 绝不触发 as_of。"""
    _stub_drive(monkeypatch)
    mem = _SpyMemory()
    mem.write(_belief("用户在 A 公司上班"))
    client = TestClient(_console_app(tmp_path, mem))

    r = client.post("/api/intent", json={"intent": "上个月的报表做了吗?"})
    assert r.status_code == 200
    assert mem.as_of_calls and mem.as_of_calls[0] is None
    assert "recall_as_of" not in r.json()


# ---- WS intent(与 REST 不漂移)----

def _ws_drive_done(client, intent):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()   # 首次 snapshot
        ws.send_json({"type": "intent", "payload": {"intent": intent}})
        for _ in range(20):   # 中间可能插 ambient_recall/task_status 广播
            msg = ws.receive_json()
            if msg["type"] == "drive_done":
                return msg["payload"]
    raise AssertionError("没等到 drive_done")


def test_ws_past_cognition_query_passes_as_of(tmp_path, monkeypatch):
    _stub_drive(monkeypatch)
    mem = _SpyMemory()
    mem.write(_belief("用户在 A 公司上班"))
    client = TestClient(_console_app(tmp_path, mem))

    payload = _ws_drive_done(client, "上个月你以为我在哪家公司?")
    assert mem.as_of_calls and mem.as_of_calls[0] is not None
    assert "recall_as_of" in payload and isinstance(payload["recall_as_of"], (int, float))


def test_ws_ordinary_query_no_as_of(tmp_path, monkeypatch):
    _stub_drive(monkeypatch)
    mem = _SpyMemory()
    mem.write(_belief("用户在 A 公司上班"))
    client = TestClient(_console_app(tmp_path, mem))

    payload = _ws_drive_done(client, "帮我查下公司地址")
    assert mem.as_of_calls and mem.as_of_calls[0] is None
    assert "recall_as_of" not in payload
