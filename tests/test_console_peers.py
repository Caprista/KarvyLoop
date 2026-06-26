"""test_console_peers — 业务域对话:场/角色 picker + 切场 + value_md 注入(M3+ 拍 9.2b)。

设计:docs/26 §C(CV-13/CV-14)。

AC 矩阵:
- AC1-AC2: /api/peers(无 registry 仅私聊 / 有 registry 列业务域角色,排除 user)
- AC3-AC4: /api/peer/switch(切私聊 / 切业务域,返该线历史轮)
- AC5: 切场隔离(私聊 ↔ 业务域 上下文不串)
- AC6-AC7: governance_text(私聊空 / 业务域返 value_md 框成系统指令)
- AC8: 业务域线 intent → governance 注入慢脑(drive_in_tui 收到 governance)
- AC9: 前端控件存在(peer-picker + switchPeer)
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.cognition.conversation import ConversationManager, ConversationStore, karvy_world_peer  # noqa: E402
from karvyloop.domain.registry import Address  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


# ---- fake registry / domain(duck-type)----


class _FakeValueMd:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeDomain:
    def __init__(self, id: str, name: str, value_text: str, members: list) -> None:
        self.id = id
        self.name = name
        self.value_md = _FakeValueMd(value_text)
        self._members = members  # list[Address]


class _FakeRegistry:
    def __init__(self, domains: list) -> None:
        self._domains = {d.id: d for d in domains}

    def list_active(self):
        return list(self._domains.values())

    def get(self, domain_id: str):
        return self._domains.get(domain_id)

    def resolve_members(self, domain_id: str):
        d = self._domains.get(domain_id)
        return tuple(d._members) if d else ()


VALUE_TEXT = "# 价值观\n\n- 诚实第一\n- 用户利益至上\n- 不夸大"


def _registry_with_one_domain():
    members = [
        Address(domain_id="dom-装修", role="user", agent_id="ch"),       # 用户(应被 /peers 排除)
        Address(domain_id="dom-装修", role="agent", agent_id="设计师"),
    ]
    return _FakeRegistry([_FakeDomain("dom-装修", "装修公司", VALUE_TEXT, members)])


@pytest.fixture
def mgr(tmp_path):
    return ConversationManager(
        ConversationStore(tmp_path / "conv"),
        domain_registry=_registry_with_one_domain(),
    )


@pytest.fixture
def app(mgr):
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mgr.start()  # 默认私聊小卡
    a.state.conversation_manager = mgr
    a.state.domain_registry = _registry_with_one_domain()
    return a


# ---- AC1-AC2: /api/peers ----


def test_peers_no_registry_only_private():
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    client = TestClient(a)
    r = client.get("/api/peers")
    assert r.status_code == 200
    peers = r.json()["peers"]
    # ch4:除私聊外还有 karvy world 大群(群场);非群场只有私聊小卡这一条
    non_group = [p for p in peers if not p.get("is_group")]
    assert len(non_group) == 1
    assert non_group[0]["is_private"] is True and non_group[0]["domain_id"] == "l0"
    assert any(p.get("is_group") and p["domain_id"] == "l0" for p in peers)  # 大群在


def test_peers_lists_business_domain_roles_excluding_user(app):
    client = TestClient(app)
    peers = client.get("/api/peers").json()["peers"]
    # 私聊小卡 + 业务域里的 agent(user 被排除)
    assert any(p["is_private"] for p in peers)
    biz = [p for p in peers if not p["is_private"] and not p.get("is_group")]
    assert len(biz) == 1
    assert biz[0]["domain_id"] == "dom-装修"
    assert biz[0]["role"] == "agent"
    assert biz[0]["agent_id"] == "设计师"
    # ch4:该域还有一条 域群(群场)
    assert any(p.get("is_group") and p["domain_id"] == "dom-装修" for p in peers)
    # user 不在 peers
    assert all(p["role"] != "user" for p in peers)


# ---- AC3-AC4: /api/peer/switch ----


def test_switch_to_business_domain(app, mgr):
    client = TestClient(app)
    r = client.post("/api/peer/switch", json={"domain_id": "dom-装修", "role": "agent", "agent_id": "设计师"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["domain_id"] == "dom-装修"
    assert mgr.current_peer().domain_id == "dom-装修"


def test_switch_back_to_private(app, mgr):
    client = TestClient(app)
    client.post("/api/peer/switch", json={"domain_id": "dom-装修", "role": "agent", "agent_id": "设计师"})
    r = client.post("/api/peer/switch", json={"domain_id": "l0", "role": "observer", "agent_id": "karvy"})
    assert r.json()["domain_id"] == "l0"
    assert mgr.current_peer().domain_id == "l0"


# ---- AC5: 切场隔离 ----


def test_switch_isolates_context(app, mgr):
    client = TestClient(app)
    # 私聊线记一轮
    mgr.record_turn("私聊的事", "私聊回答")
    # 切业务域 → 全新上下文,看不到私聊
    client.post("/api/peer/switch", json={"domain_id": "dom-装修", "role": "agent", "agent_id": "设计师"})
    assert mgr.context_view() == ()
    mgr.record_turn("装修的事", "装修回答")
    # 切回私聊 → 续上私聊线,看不到装修
    client.post("/api/peer/switch", json={"domain_id": "l0", "role": "observer", "agent_id": "karvy"})
    view = mgr.context_view()
    assert view[-1].user_intent == "私聊的事"
    assert all("装修" not in t.user_intent for t in view)


# ---- AC6-AC7: governance_text(CV-14)----


def test_governance_empty_for_private(mgr):
    mgr.set_peer(karvy_world_peer())
    assert mgr.governance_text() == ""


def test_governance_returns_value_md_for_business(mgr):
    mgr.set_peer(Address(domain_id="dom-装修", role="agent", agent_id="设计师"))
    gov = mgr.governance_text()
    assert "装修公司" in gov            # 框了域名
    assert "诚实第一" in gov            # 含 value.md 内容
    assert "value.md" in gov


def test_governance_empty_when_no_registry(tmp_path):
    m = ConversationManager(ConversationStore(tmp_path / "c"))  # 无 registry
    m.set_peer(Address(domain_id="dom-x", role="agent", agent_id="a"))
    assert m.governance_text() == ""


# ---- AC8: 业务域线 intent → governance 注入慢脑 ----


def test_business_intent_injects_governance(tmp_path, monkeypatch):
    from karvyloop.cli.main_loop import Brain
    import karvyloop.console.routes as routes_mod

    seen = {}

    async def fake_drive(intent, ml, *, ctx=None, governance="", **kw):
        from karvyloop.workbench.main_loop_bridge import DriveOutcome
        seen["governance"] = governance
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="ok",
                            skill_name="", fast_brain_hit=False, crystallized=False, task_id="t")

    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)

    mgr = ConversationManager(ConversationStore(tmp_path / "conv"),
                              domain_registry=_registry_with_one_domain())
    mgr.set_peer(Address(domain_id="dom-装修", role="agent", agent_id="设计师"))
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    a.state.conversation_manager = mgr
    client = TestClient(a)
    client.post("/api/intent", json={"intent": "帮我设计客厅"})
    # 业务域线 → governance 注入(含 value.md)
    assert "诚实第一" in seen["governance"]
    assert "装修公司" in seen["governance"]


def test_private_intent_no_governance(tmp_path, monkeypatch):
    from karvyloop.cli.main_loop import Brain
    import karvyloop.console.routes as routes_mod

    seen = {}

    async def fake_drive(intent, ml, *, ctx=None, governance="", **kw):
        from karvyloop.workbench.main_loop_bridge import DriveOutcome
        seen["governance"] = governance
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="ok",
                            skill_name="", fast_brain_hit=False, crystallized=False, task_id="t")

    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)

    mgr = ConversationManager(ConversationStore(tmp_path / "conv"),
                              domain_registry=_registry_with_one_domain())
    mgr.start()  # 私聊
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    a.state.conversation_manager = mgr
    client = TestClient(a)
    client.post("/api/intent", json={"intent": "随便聊聊"})
    assert seen["governance"] == ""  # 私聊无 governance


# ---- AC9: 前端控件 ----


def test_static_has_peer_picker():
    # 2026-06 重构:场切换从 <select id=peer-picker> 改为左栏可点的 #peer-list 列表(微信式)
    html = (ROOT / "karvyloop" / "console" / "static" / "index.html").read_text(encoding="utf-8")
    assert "peer-list" in html
    js = (ROOT / "karvyloop" / "console" / "static" / "app.js").read_text(encoding="utf-8")
    assert "switchPeer" in js
    assert "/api/peers" in js
    assert "/api/peer/switch" in js
