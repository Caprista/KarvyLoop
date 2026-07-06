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
    assert r.json() == {"conversations": [], "current_id": None, "unsettled": 0}


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
    """roundtable 提案必须进【拍板】H2A 卡,不是【你可能想做】预判卡。
    P1-b 后:分流改成"预判白名单 + 默认进决策列",roundtable 不在预判白名单 → 走决策列。"""
    app_js = (ROOT / "karvyloop" / "console" / "static" / "app.js").read_text(encoding="utf-8")
    import re
    m = re.search(r"_PREDICT_KINDS\s*=\s*\[([^\]]*)\]", app_js)
    assert m, "_PREDICT_KINDS 数组没找到"
    assert '"roundtable"' not in m.group(1), "roundtable 落进预判白名单 → 会被降级成软预判卡"


# ---- AC4-AC5: intent 走对话(record_turn + 喂 ctx)----


def test_intent_records_turn_and_feeds_ctx(tmp_path, monkeypatch):
    """intent 经 console → drive 收到 ctx + 这一轮 record 进当前对话。"""
    from karvyloop.runtime.main_loop import Brain
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
    from karvyloop.runtime.main_loop import Brain
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


# ---- docs/66 §E/§F:知识模块(/api/knowledge/*)—— 会话=存放区,按 id 操作,主聊天零耦合 ----

import json as _json  # noqa: E402

from karvyloop.cognition.conversation import Turn, karvy_world_peer  # noqa: E402
from karvyloop.cognition.knowledge_chat import knowledge_peer  # noqa: E402


class TextDelta:  # 名字必须叫 TextDelta(代码按 type().__name__ 收)
    def __init__(self, text: str) -> None:
        self.text = text


class _ConvergeFakeGateway:
    """收敛端点用:吐一张两层候选(经历+涌现)的严格 JSON。"""
    def __init__(self) -> None:
        self.calls = 0

    def resolve_model(self, scope):  # noqa: ANN001
        return "fake"

    async def complete(self, messages, tools, ref, system=None):  # noqa: ANN001
        self.calls += 1
        out = _json.dumps([
            {"content": "从 React 换到了 Vue", "layer": "experience", "why": "", "when": None},
            {"content": "每个决策都藏着隐含假设", "layer": "emergent", "why": "聊才涌现", "when": None},
        ], ensure_ascii=False)
        yield TextDelta(out)


class _ChatFakeGateway:
    """馆员聊天端点用:回一句固定话;记录收到的 system(验人设注入)。"""
    def __init__(self) -> None:
        self.systems = []

    def resolve_model(self, scope):  # noqa: ANN001
        return "fake"

    async def complete(self, messages, tools, ref, system=None):  # noqa: ANN001
        self.systems.append(system)
        yield TextDelta("馆员消化好了:这说法的隐含假设是 X。")


class _FakeMem:
    def __init__(self) -> None:
        self.written = []
        self.concept_cache = None

    def write(self, b, *, pinned: bool = False) -> bool:  # noqa: ANN001
        self.written.append(b)
        return True

    def recall_block(self, q, **kw):  # noqa: ANN001
        return ""


def _kconv(mgr, *turns):
    """直接在知识线建一段会话(不动 mgr 当前态,和端点同一路径)。"""
    store = mgr._store
    conv = store.new(knowledge_peer())
    for u, a in turns:
        store.append_turn(conv, Turn(user_intent=u, agent_response=a, brain="slow"))
    return conv


def test_store_close_tombstone_idempotent_and_meta(tmp_path):
    store = ConversationStore(tmp_path / "conv")
    peer = karvy_world_peer()
    conv = store.new(peer, "t")
    ts1 = store.close(conv)
    ts2 = store.close(conv)                     # 幂等:第二次不再追加
    assert ts1 == ts2 and conv.closed_at == ts1
    loaded = store.load(peer, conv.id)
    assert loaded.closed_at == ts1 and loaded.turn_count == 0
    meta = [m for m in store.list_conversations(peer) if m.id == conv.id][0]
    assert meta.closed_at == ts1 and meta.turn_count == 0


def test_manager_close_current_opens_new_and_counts(mgr):
    mgr.start()
    first = mgr.current()
    mgr.record_turn("聊了一句", "回了一句")
    assert mgr.open_count() >= 1
    ts = mgr.close_conversation(first.id)
    assert ts is not None
    cur = mgr.current()
    assert cur is not None and cur.id != first.id        # 关当前 → 顺势开新的
    metas = {m.id: m for m in mgr.list_conversations()}
    assert metas[first.id].closed_at == ts
    assert metas[cur.id].closed_at is None


def test_knowledge_chat_creates_session_and_injects_persona(app_with_mgr, mgr):
    gw = _ChatFakeGateway()
    app_with_mgr.state.runtime_kwargs = {"gateway": gw, "model_ref": ""}
    app_with_mgr.state.memory = _FakeMem()
    client = TestClient(app_with_mgr)
    r = client.post("/api/knowledge/chat", json={"session_id": "", "message": "帮我消化一个说法"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    sid = body["session_id"]
    assert sid and body["turn_count"] == 1 and "馆员消化好了" in body["reply"]
    # 人设真注入(馆员不是裸模型)
    sys_text = str(getattr(gw.systems[0], "static", gw.systems[0]))
    assert "知识馆员" in sys_text
    # 同一 session 续聊:轮数累计
    r2 = client.post("/api/knowledge/chat", json={"session_id": sid, "message": "再展开说说"})
    assert r2.json()["session_id"] == sid and r2.json()["turn_count"] == 2
    # 主聊天当前会话没被碰(零耦合)
    assert mgr.current().turn_count == 0


def test_knowledge_converge_by_id_returns_card(app_with_mgr, mgr):
    gw = _ConvergeFakeGateway()
    app_with_mgr.state.runtime_kwargs = {"gateway": gw, "model_ref": ""}
    conv = _kconv(mgr, ("我从 React 换到了 Vue", "为什么切换?"))
    client = TestClient(app_with_mgr)
    r = client.post("/api/knowledge/converge", json={"session_id": conv.id})
    body = r.json()
    assert body["ok"] is True and gw.calls == 1
    card = body["card"]
    assert card["kind"] == "sediment" and card["n"] == 2 and card["conversation_ref"] == conv.id
    assert card["items"][-1]["layer"] == "emergent" and card["items"][-1]["needs_attention"] is True


def test_knowledge_converge_empty_or_missing_refuses(app_with_mgr, mgr):
    app_with_mgr.state.runtime_kwargs = {"gateway": _ConvergeFakeGateway(), "model_ref": ""}
    client = TestClient(app_with_mgr)
    conv = _kconv(mgr)   # 没聊过
    assert client.post("/api/knowledge/converge", json={"session_id": conv.id}).json()["ok"] is False
    assert client.post("/api/knowledge/converge", json={"session_id": "no-such"}).json()["ok"] is False


def test_knowledge_sediment_by_id_closes_only_that_session(app_with_mgr, mgr):
    """核心结构性保证(Hardy 三次收敛):沉淀只关那段知识会话,主聊天当前会话动都不动。"""
    mem = _FakeMem()
    app_with_mgr.state.memory = mem
    app_with_mgr.state.runtime_kwargs = {"gateway": None, "model_ref": ""}
    mgr.record_turn("正常聊工作", "好的")            # 主聊天先有一段工作会话
    work = mgr.current()
    conv = _kconv(mgr, ("我从 React 换到了 Vue", "为什么切换?"))
    client = TestClient(app_with_mgr)
    from karvyloop.cognition.converge import CognitionCandidate
    items = [
        CognitionCandidate(content="从 React 换到了 Vue", layer="experience").to_dict(),
        CognitionCandidate(content="每个决策都藏着隐含假设", layer="emergent").to_dict(),
    ]
    decisions = {items[0]["id"]: {"action": "accept"},
                 items[1]["id"]: {"action": "edit", "content": "跨域套用认知前先刨隐含假设"}}
    r = client.post("/api/knowledge/sediment", json={
        "conversation_id": conv.id, "items": items, "decisions": decisions})
    body = r.json()
    assert body["ok"] is True and body["written"] == 2 and body["closed_at"] is not None
    assert body["unsettled"] == 0
    # 只沉确认的、user_explicit、edit 沉改后的话
    assert all(b.provenance["source"] == "user_explicit" for b in mem.written)
    assert {b.content for b in mem.written} == {"从 React 换到了 Vue", "跨域套用认知前先刨隐含假设"}
    # 主聊天当前会话:还是那段工作会话、没被关、轮数没变(结构性零耦合)
    assert mgr.current().id == work.id
    assert mgr.current().closed_at is None and mgr.current().turn_count == 1
    # 那段知识会话真关了
    assert mgr._store.load(knowledge_peer(), conv.id).closed_at is not None


def test_knowledge_sediment_missing_session_404(app_with_mgr):
    app_with_mgr.state.memory = _FakeMem()
    client = TestClient(app_with_mgr)
    r = client.post("/api/knowledge/sediment", json={
        "conversation_id": "no-such", "items": [], "decisions": {}})
    assert r.status_code == 404


def test_knowledge_debt_and_session_endpoints(app_with_mgr, mgr):
    client = TestClient(app_with_mgr)
    assert client.get("/api/knowledge/debt").json() == {"unsettled": 0, "sessions": []}
    conv = _kconv(mgr, ("丢一份资料进来先存着", "读好了,随时聊"))
    debt = client.get("/api/knowledge/debt").json()
    assert debt["unsettled"] == 1
    assert debt["sessions"][0]["id"] == conv.id
    assert debt["sessions"][0]["snippet"].startswith("丢一份资料")   # 首句当脸
    sess = client.get("/api/knowledge/session", params={"id": conv.id}).json()
    assert sess["id"] == conv.id and len(sess["turns"]) == 1
    assert client.get("/api/knowledge/session", params={"id": "no-such"}).status_code == 404
    mgr._store.close(mgr._store.load(knowledge_peer(), conv.id))
    assert client.get("/api/knowledge/debt").json()["unsettled"] == 0


def test_knowledge_line_never_routes(app_with_mgr, mgr):
    """知识线豁免路由层(docs/66 §F,防御性保留):馆员自己接,最强触发词也不路由。"""
    import asyncio as _a
    from karvyloop.console.routes import maybe_route_to_role
    mgr.set_peer(knowledge_peer())
    out = _a.run(maybe_route_to_role(app_with_mgr, mgr, "让几个角色开圆桌讨论这个说法"))
    assert out is None
