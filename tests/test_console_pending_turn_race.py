"""test_console_pending_turn_race — 执行中对话归属竞态:REST + WS 两个 drive 入口的接线验收。

bug(执行中对话归属竞态):此前 record_turn / set_conversation 只在 drive **结束**才做 →
drive 执行中那段窗口刷新时,消息读不到、正在跑的任务飘到小卡。修法:begin_turn 落挂起轮 +
立即 set_conversation(都在 drive **之前**),drive 结束 finalize 就地回填。

这里 stub drive_in_tui,在"drive 执行中"那一刻从盘另读一遍(模拟刷新)——断言:
- 挂起轮已落盘、user 消息在、pending=True
- 任务已绑到**捕获到的**那条对话(conversation_id 非空、running)
- drive 结束后就地回填,不叠第二条
覆盖清单 ②(drive 中途)+ REST/WS 两路 + 早返回零变(⑤)。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.console.tasks import TaskRegistry  # noqa: E402
from karvyloop.cognition.conversation import (  # noqa: E402
    ConversationManager, ConversationStore, karvy_world_peer)
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def _outcome(intent, text="结果", task_id="traceX", error=""):
    from karvyloop.runtime.main_loop import Brain
    from karvyloop.workbench.main_loop_bridge import DriveOutcome
    return DriveOutcome(
        intent=intent, brain=Brain.SLOW, text=text, skill_name="",
        fast_brain_hit=False, crystallized=False, task_id=task_id, error=error)


# ---- ② REST /api/intent:drive 执行中挂起轮已落盘 + 任务已绑对话 ----

def test_rest_pending_and_task_bound_during_drive(tmp_path, monkeypatch):
    import karvyloop.console.routes as routes_mod
    conv_dir = tmp_path / "conv"
    mid = {}

    async def fake_drive(intent, ml, *, ctx=None, **kw):
        # 模拟 drive 执行中:另开一个 store 从盘读(= 刷新时读到的东西)
        probe = ConversationStore(conv_dir).load(karvy_world_peer(), mgr.current().id)
        mid["turns"] = list(probe.turns) if probe else []
        mid["tasks"] = app.state.task_registry.list()
        return _outcome(intent)

    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)
    mgr = ConversationManager(ConversationStore(conv_dir))
    mgr.start()
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    app.state.conversation_manager = mgr
    app.state.task_registry = TaskRegistry()
    client = TestClient(app)

    r = client.post("/api/intent", json={"intent": "跑个活"})
    assert r.status_code == 200

    # drive 执行中(mid 快照):挂起轮已落盘 + user 消息在
    assert len(mid["turns"]) == 1, "drive 执行中挂起轮必须已在盘上(刷新读得到)"
    assert mid["turns"][0].pending is True
    assert mid["turns"][0].user_intent == "跑个活"
    assert mid["turns"][0].agent_response == ""
    # drive 执行中:任务已绑到这条对话(不飘小卡默认场),状态 running
    assert len(mid["tasks"]) == 1
    assert mid["tasks"][0]["conversation_id"] == mgr.current().id
    assert mid["tasks"][0]["status"] == "running"

    # drive 结束:就地回填,不叠第二条
    final = ConversationStore(conv_dir).load(karvy_world_peer(), mgr.current().id)
    assert len(final.turns) == 1 and final.turns[0].pending is False
    assert final.turns[0].agent_response == "结果" and final.turns[0].task_id == "traceX"


# ---- ③ REST:drive 抛异常 → 挂起轮 finalize 成失败,不留僵尸 ----

def test_rest_drive_exception_finalizes_pending_as_error(tmp_path, monkeypatch):
    import karvyloop.console.routes as routes_mod
    conv_dir = tmp_path / "conv"

    async def boom_drive(intent, ml, *, ctx=None, **kw):
        raise RuntimeError("模型炸了")

    monkeypatch.setattr(routes_mod, "drive_in_tui", boom_drive)
    mgr = ConversationManager(ConversationStore(conv_dir))
    mgr.start()
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    app.state.conversation_manager = mgr
    app.state.task_registry = TaskRegistry()
    client = TestClient(app)

    r = client.post("/api/intent", json={"intent": "会崩的活"})
    assert r.status_code == 200
    assert r.json().get("error")
    # 挂起轮已就地标失败(不是永远 pending 僵尸)
    final = ConversationStore(conv_dir).load(karvy_world_peer(), mgr.current().id)
    assert len(final.turns) == 1
    assert final.turns[0].pending is False and final.turns[0].status == "error"


# ---- ② WS /ws intent:同样在 drive 执行中挂起轮已落盘 + 任务已绑 ----

def test_ws_pending_and_task_bound_during_drive(tmp_path, monkeypatch):
    import karvyloop.console.ws as ws_mod
    conv_dir = tmp_path / "conv"
    mid = {}

    async def fake_drive(intent, ml, *, ctx=None, **kw):
        probe = ConversationStore(conv_dir).load(karvy_world_peer(), mgr.current().id)
        mid["turns"] = list(probe.turns) if probe else []
        mid["tasks"] = app.state.task_registry.list()
        return _outcome(intent)

    monkeypatch.setattr(ws_mod, "drive_in_tui", fake_drive)
    mgr = ConversationManager(ConversationStore(conv_dir))
    mgr.start()
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    app.state.conversation_manager = mgr
    app.state.task_registry = TaskRegistry()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # 首次 snapshot
        ws.send_json({"type": "intent", "payload": {"intent": "WS 跑活"}})
        # 收到 drive_done(可能先夹带 task_status/ambient 等推送 → 循环到 drive_done)
        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "drive_done":
                break
        assert msg["type"] == "drive_done"

    assert len(mid["turns"]) == 1 and mid["turns"][0].pending is True
    assert mid["turns"][0].user_intent == "WS 跑活"
    assert len(mid["tasks"]) == 1
    assert mid["tasks"][0]["conversation_id"] == mgr.current().id
    assert mid["tasks"][0]["status"] == "running"

    final = ConversationStore(conv_dir).load(karvy_world_peer(), mgr.current().id)
    assert len(final.turns) == 1 and final.turns[0].pending is False
    assert final.turns[0].agent_response == "结果"


# ---- ② serializer:peer/switch 返回的 turns 带 pending/status(前端据此渲占位)----

def test_peer_switch_turns_carry_pending_flags(tmp_path):
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    conv = mgr.start()
    mgr.begin_turn("挂起中的活", conv=conv)   # 只 begin 不 finalize → 挂起态
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.conversation_manager = mgr
    client = TestClient(app)

    r = client.post("/api/peer/switch",
                    json={"domain_id": "l0", "role": "observer", "agent_id": "karvy"})
    assert r.status_code == 200
    turns = r.json()["turns"]
    assert len(turns) == 1
    assert turns[0]["pending"] is True and turns[0]["status"] == "running"
    assert "agent_response" in turns[0]   # 契约字段齐


# ---- ⑤ 早返回零变:群里不 @ 任何人(nudge)不建挂起轮 ----

def test_group_no_mention_nudge_does_not_begin_pending(tmp_path, monkeypatch):
    """早返回路径(nudge)在 begin_turn **之前**返回 → 不落挂起轮(行为零变)。"""
    import karvyloop.console.routes as routes_mod
    from karvyloop.domain.registry import Address
    conv_dir = tmp_path / "conv"

    # nudge 触发:群场 + 不 @ 人。用 group peer;group_no_mention_nudge 命中即早返回。
    mgr = ConversationManager(ConversationStore(conv_dir))
    grp = Address(domain_id="l0", role="group", agent_id="")
    mgr.set_peer(grp)
    conv = mgr.current()

    called = {"drive": False}

    async def fake_drive(intent, ml, *, ctx=None, **kw):
        called["drive"] = True
        return _outcome(intent)

    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    app.state.conversation_manager = mgr
    app.state.task_registry = TaskRegistry()
    client = TestClient(app)

    r = client.post("/api/intent", json={"intent": "大家好啊"})   # 群里不 @ 任何人
    assert r.status_code == 200
    body = r.json()
    if body.get("no_mention_nudge"):
        # 命中 nudge 早返回:没跑 drive、没落挂起轮
        assert called["drive"] is False
        reloaded = ConversationStore(conv_dir).load(grp, conv.id)
        assert reloaded is None or all(not t.pending for t in reloaded.turns)


# ---- ⑦ 前端静态:挂起轮 → "执行中…" 占位渲染存在(不空白)----

_STATIC = ROOT / "karvyloop" / "console" / "static"


def test_frontend_renders_pending_placeholder():
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    # 回复侧分支:挂起 → 占位;已中断/失败 → 提示
    assert "_renderTurnReply" in app_js
    assert "_pushExecutingPlaceholder" in app_js
    assert "pending-turn" in app_js            # 占位行 class(带呼吸点骨架)
    assert "tn.pending" in app_js              # 读挂起态位
    assert 'tn.status === "interrupted"' in app_js
    assert "_clearPendingPlaceholders" in app_js   # drive_done 到达清占位,不叠双


def test_frontend_restores_active_peer_on_refresh():
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    # 刷新恢复到非默认场(否则回落小卡):存/取 localStorage active peer
    assert "_restoreActivePeerOrHistory" in app_js
    assert "_saveActivePeer" in app_js
    assert "karvyloop_active_peer" in app_js


def test_frontend_i18n_keys_present():
    i18n_js = (_STATIC / "i18n.js").read_text(encoding="utf-8")
    assert i18n_js.count('"chat.turn_interrupted"') == 2   # en + zh 两表都在
    assert '"chat.executing"' in i18n_js                   # 占位复用(en+zh 已有)
