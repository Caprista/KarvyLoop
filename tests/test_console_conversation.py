"""test_console_conversation — console 接对话编排(M3+ 拍 9.1d)。

设计:docs/26 §B。

AC 矩阵:
- AC1-AC3: /api/conversations(空/有 + current_id)+ /api/conversation/new + resume(找到/404)
- AC4: intent 经 console → record_turn 进当前对话(带 brain)
- AC5: intent 喂 ctx 给 drive(drive_in_tui 收到 ctx)
- AC6: 无 manager 时端点优雅退化
- AC7: 前端静态资源含对话控件(grep)
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.cognition.conversation import ConversationManager, ConversationStore  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


@pytest.fixture
def mgr(tmp_path):
    return ConversationManager(ConversationStore(tmp_path / "conv"))


@pytest.fixture
def app_with_mgr(mgr):
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mgr.start()
    app.state.conversation_manager = mgr
    return app


# ---- AC1: /api/conversations ----


def test_conversations_empty(app_with_mgr):
    client = TestClient(app_with_mgr)
    r = client.get("/api/conversations")
    assert r.status_code == 200
    body = r.json()
    # start() 建了一段空的当前对话
    assert body["current_id"] is not None
    assert isinstance(body["conversations"], list)


def test_conversations_no_manager_graceful():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    client = TestClient(app)
    r = client.get("/api/conversations")
    assert r.status_code == 200
    assert r.json() == {"conversations": [], "current_id": None}


# ---- AC2: new ----


def test_conversation_new(app_with_mgr, mgr):
    # 先记一轮,再开新对话
    mgr.record_turn("旧句", "旧应")
    client = TestClient(app_with_mgr)
    r = client.post("/api/conversation/new")
    assert r.status_code == 200
    new_id = r.json()["id"]
    assert new_id is not None
    assert new_id == mgr.current().id
    assert mgr.current().turn_count == 0  # 新对话空


def test_conversation_new_no_manager():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    client = TestClient(app)
    r = client.post("/api/conversation/new")
    assert r.status_code == 200
    assert r.json()["id"] is None


# ---- AC3: resume ----


def test_conversation_resume_found(app_with_mgr, mgr):
    mgr.record_turn("记我", "记住了")
    target = mgr.current().id
    mgr.new_conversation()  # 切走
    client = TestClient(app_with_mgr)
    r = client.post("/api/conversation/resume", json={"conversation_id": target})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == target
    assert body["turns"][0]["user_intent"] == "记我"
    assert mgr.current().id == target


def test_conversation_resume_404(app_with_mgr):
    client = TestClient(app_with_mgr)
    r = client.post("/api/conversation/resume", json={"conversation_id": "nope"})
    assert r.status_code == 404


# ---- 料→去聊天定位:turns payload 必须带 task_id(否则前端找不到那一轮)----


def test_line_open_by_conv_turns_carry_task_id(app_with_mgr, mgr):
    """料→去聊天靠 turn.task_id 锚定:open_by_conv 返回的每个 turn 必须带 task_id。"""
    mgr.record_turn("分析世界杯", "分析结果…", brain="slow", task_id="task-abc")
    conv_id = mgr.current().id
    client = TestClient(app_with_mgr)
    r = client.post("/api/line/open_by_conv", json={"conversation_id": conv_id})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["turns"][0]["task_id"] == "task-abc"


def test_static_has_turn_locate():
    """前端有"滚到并高亮对应那一轮"的机制(打 data-task-id + 定位函数 + 高亮类)。"""
    root = pathlib.Path(__file__).resolve().parents[1] / "karvyloop" / "console" / "static"
    app_js = (root / "app.js").read_text(encoding="utf-8")
    assert "_locateTurnByTask" in app_js
    assert "dataset.taskId" in app_js
    assert "turn-locate-flash" in app_js
    # openConvById 把定位键透传:l0=trace_id,工作流/圆桌=id(两个 id 空间,先 trace 再回退)
    assert "openConvById(tk.conversation_id, tk.trace_id || tk.id)" in app_js
    css = (root / "styles.css").read_text(encoding="utf-8")
    assert ".turn-locate-flash" in css


def test_static_roundtable_is_decision_kind():
    """roundtable 提案必须进【拍板】H2A 卡(_DECISION_KINDS),不是【你可能想做】预判卡。"""
    app_js = (ROOT / "karvyloop" / "console" / "static" / "app.js").read_text(encoding="utf-8")
    import re
    m = re.search(r"_DECISION_KINDS\s*=\s*\[([^\]]*)\]", app_js)
    assert m, "_DECISION_KINDS 数组没找到"
    assert '"roundtable"' in m.group(1), "roundtable 不在拍板 kind 白名单 → 降级成软预判卡"


# ---- AC4-AC5: intent 走对话(record_turn + 喂 ctx)----


def test_intent_records_turn_and_feeds_ctx(tmp_path, monkeypatch):
    """intent 经 console → drive 收到 ctx + 这一轮 record 进当前对话。"""
    from karvyloop.cli.main_loop import Brain
    import karvyloop.console.routes as routes_mod

    # 假 drive_in_tui:记录收到的 ctx,返成功 outcome
    seen = {}

    async def fake_drive(intent, ml, *, ctx=None, **kw):
        from karvyloop.workbench.main_loop_bridge import DriveOutcome
        seen["ctx"] = ctx
        seen["intent"] = intent
        return DriveOutcome(
            intent=intent, brain=Brain.SLOW, text="回应:" + intent,
            skill_name="", fast_brain_hit=False, crystallized=False,
            task_id="tid-1",
        )

    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)

    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()
    mgr.record_turn("第一句", "第一应")  # 预置一轮,使 ctx 非空

    # main_loop 非 None(用占位对象,drive_in_tui 已被 patch 不真跑)
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    app.state.conversation_manager = mgr
    client = TestClient(app)

    r = client.post("/api/intent", json={"intent": "第二句"})
    assert r.status_code == 200
    # drive 收到了 ctx(含第一轮)
    assert seen["ctx"] is not None
    assert len(seen["ctx"]) >= 1
    assert seen["ctx"][0].user_intent == "第一句"
    # 第二轮 record 进对话
    assert mgr.current().turn_count == 2
    assert mgr.current().turns[1].user_intent == "第二句"
    assert mgr.current().turns[1].brain == "slow"


# ---- 料→去聊天:真路径(非手注 id)—— l0 任务必须挂 conversation_id + trace_id==turn.task_id ----


def test_intent_task_carries_conversation_and_trace_for_locate(tmp_path, monkeypatch):
    """走真 /api/intent:l0 任务必须挂上 conversation_id,且 trace_id == 该轮 turn.task_id。

    这是定位的命门:feed 卡的 tk.id 是 registry id(12-hex),而 l0 轮的 turn.task_id 是 drive
    trace id(16-hex)——两个 id 空间。若任务不回填 trace_id,前端按 tk.id querySelector 永远落空,
    定位静默失效。本测试 drive 真路径(只 stub drive_in_tui 返回一个已知 trace task_id),断言闭环。
    """
    from karvyloop.cli.main_loop import Brain
    from karvyloop.console.tasks import TaskRegistry
    import karvyloop.console.routes as routes_mod

    TRACE = "trace16hexabc12345"  # 模拟 drive 的 trace task_id(≠ registry id)

    async def fake_drive(intent, ml, *, ctx=None, **kw):
        from karvyloop.workbench.main_loop_bridge import DriveOutcome
        return DriveOutcome(
            intent=intent, brain=Brain.SLOW, text="回应:" + intent,
            skill_name="", fast_brain_hit=False, crystallized=False, task_id=TRACE,
        )

    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)

    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    app.state.conversation_manager = mgr
    app.state.task_registry = TaskRegistry()
    client = TestClient(app)

    r = client.post("/api/intent", json={"intent": "分析世界杯"})
    assert r.status_code == 200
    conv_id = mgr.current().id

    # 1) 任务挂上了这条对话 + 回填了 trace_id
    tasks = client.get("/api/tasks").json()["tasks"]
    assert len(tasks) == 1
    tk = tasks[0]
    assert tk["conversation_id"] == conv_id, "l0 任务没挂 conversation_id → 去聊天连对话都开不了"
    assert tk["trace_id"] == TRACE, "trace_id 没回填 → 定位键对不上"

    # 2) 该轮 turn.task_id 确实 == 任务回填的 trace_id(前端 querySelector 据此命中)
    opened = client.post("/api/line/open_by_conv", json={"conversation_id": conv_id}).json()
    assert opened["ok"] is True
    turn = opened["turns"][-1]
    assert turn["task_id"] == TRACE
    assert turn["task_id"] == tk["trace_id"]   # 闭环:定位键 == 那一轮的锚


# ---- AC7: 前端控件 ----


def test_static_has_conversation_controls():
    html = (ROOT / "karvyloop" / "console" / "static" / "index.html").read_text(encoding="utf-8")
    assert "conv-new-btn" in html
    assert "conv-history" in html
    js = (ROOT / "karvyloop" / "console" / "static" / "app.js").read_text(encoding="utf-8")
    assert "newConversation" in js
    assert "resumeConversation" in js
    assert "/api/conversation/new" in js
    assert "/api/conversations" in js
