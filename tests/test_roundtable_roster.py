"""test_roundtable_roster — ch4 #3:圆桌名册(谁能上桌)+ 任意群场(大群/域群)。

Hardy 定调:大群、业务域都能起圆桌;谁参与由你勾。本测锁名册端点:
- AC1: 域群 → 名册 = 本域 agent(排除 user)
- AC2: karvy world 大群(l0)→ 名册 = 跨所有活跃域的 agent(去重)
- AC3: 私聊(非群场)→ ok:False(圆桌在群里开)
- AC4: _roundtable_roster 纯函数:大群跨域聚合
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.cognition.conversation import (  # noqa: E402
    ConversationManager, ConversationStore, karvy_world_peer,
)
from karvyloop.domain.registry import Address, BusinessDomainRegistry  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


@pytest.fixture
def setup(tmp_path):
    reg = BusinessDomainRegistry()
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"), domain_registry=reg)
    mgr.start()
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.conversation_manager = mgr
    app.state.domain_registry = reg
    # 建两个域,各入职一个 agent
    d1 = reg.create(name="装修", created_by="user:ch", value_md_raw="",
                    member_query="user:ch AND agent:设计师")
    d2 = reg.create(name="财务", created_by="user:ch", value_md_raw="",
                    member_query="user:ch AND agent:会计")
    return app, mgr, reg, d1, d2


def _group_peer(domain_id: str) -> Address:
    # 群场身份 = role="group" + agent_id=""(与 /api/peers、peer/switch 生产一致)
    return Address(domain_id=domain_id, role="group", agent_id="")


# ---- AC1: 域群名册 = 本域 agent ----
def test_roster_in_domain_group(setup):
    app, mgr, reg, d1, d2 = setup
    mgr.set_peer(_group_peer(d1.id))
    r = TestClient(app).get("/api/roundtable/roster")
    body = r.json()
    assert body["ok"] is True
    ids = [m["agent_id"] for m in body["members"]]
    assert "设计师" in ids and "会计" not in ids          # 只本域
    assert all(m["role"] != "user" for m in body["members"])


# ---- AC2: 大群名册 = 跨域全员 ----
def test_roster_in_karvy_world_group(setup):
    app, mgr, reg, d1, d2 = setup
    # 大群 = l0 群场
    world = karvy_world_peer()
    mgr.set_peer(Address(domain_id=world.domain_id, role="group", agent_id="karvy"))
    r = TestClient(app).get("/api/roundtable/roster")
    body = r.json()
    assert body["ok"] is True
    ids = sorted(m["agent_id"] for m in body["members"])
    assert "设计师" in ids and "会计" in ids               # 跨域聚合(Hardy:大群也能起圆桌)


# ---- AC3: 私聊非群场 → ok:False ----
def test_roster_rejected_in_private(setup):
    app, mgr, reg, d1, d2 = setup
    mgr.set_peer(karvy_world_peer())   # 私聊小卡(observer,非 group)
    body = TestClient(app).get("/api/roundtable/roster").json()
    assert body["ok"] is False and body["members"] == []


# ---- AC4: _roundtable_roster 纯函数跨域聚合 ----
def test_roster_helper_dedups_across_domains(setup):
    app, mgr, reg, d1, d2 = setup
    from karvyloop.console.routes import _roundtable_roster
    world_peer = Address(domain_id="l0", role="group", agent_id="karvy")
    roster = _roundtable_roster(app, world_peer)
    ids = sorted(a.agent_id for a in roster)
    assert ids == ["会计", "设计师"]


# ---- ch4:圆桌结果回流工作台首页(注册成 task → 流进来的料卡 → 点开聊天追问)----
def test_roundtable_result_doc_builder():
    from karvyloop.console.routes import _roundtable_result_doc
    doc = _roundtable_result_doc({
        "conclusion": "结论:选低估值+高股息", "rounds": 2,
        "transcript": [{"round": 1, "speaker": "分析师", "text": "看多"},
                       {"round": 2, "speaker": "风控", "text": "注意回撤"}],
    })
    assert doc.startswith("结论:选低估值+高股息")   # 结论为主
    assert "内部讨论" in doc and "分析师" in doc and "风控" in doc  # 讨论附后(查看用)
    # 空结论 → 诚实占位,不崩
    assert "未给出结论" in _roundtable_result_doc({"conclusion": "", "transcript": []})


def test_roundtable_syncs_to_task_board(setup, monkeypatch):
    app, mgr, reg, d1, d2 = setup
    mgr.set_peer(_group_peer(d1.id))                       # 切到装修域群
    from karvyloop.console.tasks import TaskRegistry
    app.state.task_registry = TaskRegistry()
    app.state.main_loop = object()                         # 非 None 才走
    app.state.runtime_kwargs = {"gateway": object(), "model_ref": "x", "workspace_root": "/"}
    # 不打真 LLM:整段 session 用假的(member_reply/host_moderate 不会被调到)
    import karvyloop.karvy.roundtable as rt_mod

    async def fake_session(topic, members, **kw):
        return {"topic": topic, "rounds": 1, "converged": True,
                "conclusion": "结论:就按 A 方案",
                "transcript": [{"round": 1, "speaker": "设计师", "text": "我选 A"}]}
    monkeypatch.setattr(rt_mod, "run_roundtable_session", fake_session)

    client = TestClient(app)
    # 阶段0:对齐目标(建圆桌对话 + 小卡开场;假 gw → 走 fallback 开场)
    start = client.post("/api/roundtable/start",
                        json={"intent": "客厅怎么改", "participants": ["设计师"]}).json()
    assert start["ok"] is True and start["conversation_id"] and start["opening"]
    cid0 = start["conversation_id"]
    # 阶段1:你点开始讨论 → 跑(假 session)
    body = client.post("/api/roundtable/discuss", json={"conversation_id": cid0}).json()
    assert body["ok"] is True
    # 圆桌登记成 task → 同步首页【流进来的料】,done + 结论在 result,domain/role 指回群场可追问
    rt_tasks = [tk for tk in app.state.task_registry.list() if "圆桌" in (tk.get("who") or "")]
    assert len(rt_tasks) == 1
    tk = rt_tasks[0]
    assert tk["status"] == "done"
    assert "结论:就按 A 方案" in (tk.get("result") or "")
    assert tk["domain_id"] == d1.id and tk["role"] == "group"   # 点"打开聊天"跳回这个群场
    # Hardy 修:圆桌存成独立对话记录 + task 挂 conversation_id → 点卡精准跳这条聊天追问
    cid = tk.get("conversation_id")
    assert cid and body.get("conversation_id") == cid
    # 这条对话在群场 history 里、可 resume(精准跳转/当独立话题追问)
    group_peer = Address(domain_id=d1.id, role="group", agent_id="")
    metas = mgr.list_conversations(group_peer)
    assert any(m.id == cid for m in metas)
    resumed = mgr.resume(group_peer, cid)
    assert resumed is not None
    assert any("就按 A 方案" in (tn.agent_response or "") for tn in resumed.turns)  # 圆桌产出在记录里
    # 结构化负载落盘+重读保真 → 重开时前端渲成群聊串(而非一坨 markdown)
    rt_turn = next(tn for tn in resumed.turns if tn.data and tn.data.get("roundtable"))
    rtd = rt_turn.data["roundtable"]
    assert rtd["conclusion"] == "结论:就按 A 方案"
    assert rtd["transcript"][0]["speaker"] == "设计师"


def test_create_record_independent_conversation(setup):
    """ConversationManager.create_record:建独立对话+写一轮,不切走当前(给圆桌做 history 记录)。"""
    app, mgr, reg, d1, d2 = setup
    group_peer = Address(domain_id=d1.id, role="group", agent_id="")
    mgr.set_peer(group_peer)
    before = mgr.current()
    conv = mgr.create_record(group_peer, title="🎡 选股", user_intent="圆桌:如何选股",
                             agent_response="结论:低估值")
    assert mgr.current() is before                       # 没切走当前对话
    assert conv.id
    assert mgr.resume(group_peer, conv.id) is not None    # 在 history 里、可重开


# ---- 圆桌待办态持久化(配 config_path → 重启续"开始讨论")----
def test_roundtable_state_persists(setup, tmp_path):
    app, mgr, reg, d1, d2 = setup
    from karvyloop.console.routes import _roundtable_state, _persist_roundtable_state
    app.state.config_path = str(tmp_path / "config.yaml")   # base=tmp_path
    st = _roundtable_state(app)
    st["c1"] = {"topic": "选股", "participants": ["分析师"], "phase": "aligning"}
    _persist_roundtable_state(app)
    assert (tmp_path / "roundtables.json").exists()
    # 模拟重启:清缓存 → 从盘重读
    del app.state.roundtables
    app.state._roundtables_path = None
    st2 = _roundtable_state(app)
    assert st2.get("c1", {}).get("phase") == "aligning"      # 待办还在,可继续"开始讨论"


def test_roundtable_state_in_memory_without_config(setup):
    # 无 config_path(测试默认)→ 纯内存,不碰真实 home
    app = setup[0]
    from karvyloop.console.routes import _roundtable_state, _persist_roundtable_state
    _roundtable_state(app)["x"] = {"phase": "aligning"}
    _persist_roundtable_state(app)   # 无路径 → no-op,不抛
    assert getattr(app.state, "_roundtables_path", None) is None


# ---- 圆桌对话式自动开始(Hardy:少按钮)—— 小卡判 READY → 自己跑讨论 ----
class _AlignGW:
    def __init__(self, text): self._t = text
    def resolve_model(self, scope): return "m"
    async def complete(self, messages, tools, ref, *, system=None):
        from karvyloop.gateway.events import TextDelta
        yield TextDelta(text=self._t)


def test_roundtable_align_autostarts_on_ready(setup, monkeypatch):
    app, mgr, reg, d1, d2 = setup
    mgr.set_peer(_group_peer(d1.id))
    from karvyloop.console.tasks import TaskRegistry
    app.state.task_registry = TaskRegistry()
    app.state.main_loop = object()
    # gw 让 clarify_turn 判 READY(末行 READY)→ 自动开始
    app.state.runtime_kwargs = {"gateway": _AlignGW("好,我这就组织大家讨论\nREADY"),
                                "model_ref": "m", "workspace_root": "/"}
    import karvyloop.karvy.roundtable as rt_mod

    async def fake_session(goal, members, **kw):
        return {"topic": goal, "rounds": 1, "converged": True, "conclusion": "结论:就这么定",
                "transcript": [{"round": 1, "speaker": "设计师", "text": "ok"}]}
    monkeypatch.setattr(rt_mod, "run_roundtable_session", fake_session)
    # 也让 goal_summary 不炸(用同一个 gw)
    c = TestClient(app)
    start = c.post("/api/roundtable/start", json={"intent": "改客厅", "participants": ["设计师"]}).json()
    cid = start["conversation_id"]
    res = c.post("/api/roundtable/align", json={"conversation_id": cid, "message": "就按现代简约风"}).json()
    assert res["ok"] is True and res["started"] is True            # 小卡自己开始了(没按钮)
    assert res["result"]["ok"] is True and "就这么定" in res["result"]["conclusion"]


def test_roundtable_align_keeps_clarifying(setup):
    app, mgr, reg, d1, d2 = setup
    mgr.set_peer(_group_peer(d1.id))
    app.state.main_loop = object()
    app.state.runtime_kwargs = {"gateway": _AlignGW("再确认一下:你想要什么风格?\nASK"),
                                "model_ref": "m", "workspace_root": "/"}
    c = TestClient(app)
    cid = c.post("/api/roundtable/start", json={"intent": "改客厅", "participants": ["设计师"]}).json()["conversation_id"]
    res = c.post("/api/roundtable/align", json={"conversation_id": cid, "message": "随便"}).json()
    assert res["ok"] is True and res["started"] is False           # 还在对齐,没开始
    assert "ASK" not in res["reply"] and "风格" in res["reply"]    # 标记被剥掉
