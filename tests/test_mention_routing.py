"""test_mention_routing — ch4 #1:群里 @ 角色 → 定向给它(它照自己人格/域回话)。

Hardy:大群里我能 @ 不同角色协作。本测锁后端路由:
- AC1: _resolve_mention 在群场命中 roster agent → (persona非空, speaker, "domain")
- AC2: 非群场 / 未知 @ → (None, "", None)
- AC3: POST /api/intent 带 mention(群场)→ drive 走 domain scope + 回复署名=被 @ 角色
- AC4: @ 命中 → 跳过 route_to_role(你已点名,直接定向)
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
from karvyloop.domain.registry import Address, BusinessDomainRegistry  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


@pytest.fixture
def setup(tmp_path):
    reg = BusinessDomainRegistry()
    d = reg.create(name="装修", created_by="user:ch", value_md_raw="",
                   member_query="user:ch AND agent:设计师")
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"), domain_registry=reg)
    mgr.start()
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.conversation_manager = mgr
    app.state.domain_registry = reg
    return app, mgr, reg, d


def _group_peer(domain_id: str) -> Address:
    return Address(domain_id=domain_id, role="group", agent_id="karvy")


# ---- 大群里同名(两个设计师)→ 带 domain 精准消歧 + 署名挂域名 ----
def test_resolve_mention_disambiguates_by_domain(tmp_path):
    from karvyloop.console import build_console_app
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.console.routes import _resolve_mention
    reg = BusinessDomainRegistry()
    d1 = reg.create(name="哟吼", created_by="user:ch", value_md_raw="", member_query="user:ch AND agent:设计师")
    d2 = reg.create(name="装修", created_by="user:ch", value_md_raw="", member_query="user:ch AND agent:设计师")
    mgr = ConversationManager(ConversationStore(tmp_path / "c"), domain_registry=reg)
    mgr.start()
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.conversation_manager = mgr
    app.state.domain_registry = reg
    # 进 karvy world 大群(l0 群场,跨域聚合 → 两个"设计师")
    mgr.set_peer(Address(domain_id="l0", role="group", agent_id=""))
    # 带 d1 → 署名挂"哟吼";带 d2 → 挂"装修"
    _, sp1, _ = _resolve_mention(app, mgr, "设计师", "/", domain=d1.id)
    _, sp2, _ = _resolve_mention(app, mgr, "设计师", "/", domain=d2.id)
    assert "哟吼" in sp1 and "装修" not in sp1
    assert "装修" in sp2 and "哟吼" not in sp2


# ---- AC1/AC2: _resolve_mention ----
def test_resolve_mention_in_group(setup):
    app, mgr, reg, d = setup
    from karvyloop.console.routes import _resolve_mention
    mgr.set_peer(_group_peer(d.id))
    persona, speaker, scope = _resolve_mention(app, mgr, "设计师", "/")
    assert persona is not None          # 被 @ 角色有人格
    assert "设计师" in (speaker or "")    # 署名是它
    assert scope == "domain"            # 域 scope


def test_resolve_mention_misses(setup):
    app, mgr, reg, d = setup
    from karvyloop.console.routes import _resolve_mention
    mgr.set_peer(_group_peer(d.id))
    assert _resolve_mention(app, mgr, "查无此人", "/") == (None, "", None)   # 未知 @
    # 非群场(私聊)→ 不生效
    from karvyloop.cognition.conversation import karvy_world_peer
    mgr.set_peer(karvy_world_peer())
    assert _resolve_mention(app, mgr, "设计师", "/") == (None, "", None)


# ---- AC3/AC4: POST /api/intent 带 mention 定向 ----
def test_intent_with_mention_routes_to_role(setup, monkeypatch):
    app, mgr, reg, d = setup
    import karvyloop.console.routes as routes_mod
    from karvyloop.cli.main_loop import Brain
    from karvyloop.workbench.main_loop_bridge import DriveOutcome

    seen = {}

    async def fake_drive(intent, ml, *, ctx=None, governance="", persona=None, scope=None, **kw):
        seen["scope"] = scope
        seen["persona_is_set"] = persona is not None
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="我来设计",
                            skill_name="", fast_brain_hit=False, crystallized=False, task_id="t")

    # 命中即跳过 route_to_role:把它打成"若被调用就炸"以证明没走
    async def boom_route(*a, **k):
        raise AssertionError("@ 命中时不该走 route_to_role")

    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)
    monkeypatch.setattr(routes_mod, "maybe_route_to_role", boom_route)
    app.state.main_loop = object()
    mgr.set_peer(_group_peer(d.id))

    r = TestClient(app).post("/api/intent", json={"intent": "客厅怎么改", "mention": "设计师"})
    body = r.json()
    assert seen["scope"] == "domain"            # 定向 → domain scope
    assert seen["persona_is_set"] is True       # 用了被 @ 角色人格
    assert "设计师" in (body.get("speaker") or "")   # 回复署名 = 被 @ 角色


# ---- 角色级模型接通:@ 的角色配了模型 → drive 用它(空=默认)----
def test_mention_uses_role_model(setup, monkeypatch, tmp_path):
    app, mgr, reg, d = setup
    from karvyloop.roles.registry import RoleRegistry
    rr = RoleRegistry(tmp_path / "roles")
    rr.create("设计师", identity="设计", model="minimax/MiniMax-M3")   # 这个角色配了模型
    app.state.role_registry = rr
    import karvyloop.console.routes as routes_mod
    from karvyloop.cli.main_loop import Brain
    from karvyloop.workbench.main_loop_bridge import DriveOutcome
    seen = {}

    async def fake_drive(intent, ml, *, persona=None, scope=None, **kw):
        seen["model_ref"] = kw.get("model_ref")
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="ok",
                            skill_name="", fast_brain_hit=False, crystallized=False, task_id="t")
    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)
    app.state.main_loop = object()
    app.state.runtime_kwargs = {"gateway": object(), "model_ref": "default/x", "workspace_root": "/"}
    mgr.set_peer(_group_peer(d.id))
    TestClient(app).post("/api/intent", json={"intent": "改客厅", "mention": "设计师"})
    assert seen["model_ref"] == "minimax/MiniMax-M3"   # @ 角色配的模型生效(覆盖全局 default)
