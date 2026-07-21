"""test_private_mention_fastlane — U-03(Hardy 拍板):私聊小卡里 @角色 = 快通道委派卡一键批。

内测反馈:私聊里 @某角色 完全没反应(旧 _resolve_mention 仅群场生效),用户当它坏了。
拍板:@ 心智统一 —— 私聊 @某角色 → **已填好的 route_to_role 委派卡**(保留 H2A 拍板点),
不让角色直接答(「私聊=小卡的场」+ K1 问责链不破),也不烧 LLM(用户已点名,跳过意图识别)。

AC:
- AC1: 私聊 @存在角色 → route_to_role 卡(kind/role/requirement/domain 对)+ 轻回执
       + record_turn 被调 + **没走 LLM drive**(drive 桩若被调即炸)
- AC2: @不存在的名字 / @邮箱 → 当普通消息走原 drive(精确命中才触发,不误吞)
- AC3: 私聊 @多人 → 引导句(不出卡、不 drive)
- AC4: 群场语义不动(群 peer 不走快通道;既有群 @ 测试另有 test_mention_routing 守)
- AC5: 解析细节 —— 同源名册精确命中;前缀重叠取最长;光 @ 没正文不出空卡
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop import i18n  # noqa: E402
from karvyloop.cognition.conversation import (  # noqa: E402
    ConversationManager,
    ConversationStore,
    karvy_world_peer,
)
from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.domain.registry import Address, BusinessDomainRegistry  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.karvy.proposal_registry import (  # noqa: E402
    KIND_ROUTE_TO_ROLE,
    PendingProposalRegistry,
)


@pytest.fixture
def setup(tmp_path):
    reg = BusinessDomainRegistry()
    d = reg.create(name="设计工作室", created_by="user:ch", value_md_raw="",
                   member_query="user:ch AND agent:设计师")
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"), domain_registry=reg)
    mgr.start()
    mgr.set_peer(karvy_world_peer())   # 私聊小卡(l0, observer, karvy)
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.conversation_manager = mgr
    app.state.domain_registry = reg
    app.state.proposal_registry = PendingProposalRegistry()
    app.state.main_loop = object()   # 过 main_loop 门(快通道在 drive 之前返回)
    return app, mgr, reg, d


def _boom_drive(routes_mod, monkeypatch):
    """drive_in_tui 桩:被调即炸 —— 证明快通道**没走 LLM drive**。"""
    async def boom(*a, **k):
        raise AssertionError("私聊 @角色 快通道不该走 LLM drive")
    monkeypatch.setattr(routes_mod, "drive_in_tui", boom)


def _fake_drive(routes_mod, monkeypatch, seen):
    from karvyloop.runtime.main_loop import Brain
    from karvyloop.workbench.main_loop_bridge import DriveOutcome

    async def fake(intent, ml, **kw):
        seen["drive_intent"] = intent
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="小卡答",
                            skill_name="", fast_brain_hit=False, crystallized=False,
                            task_id="t")
    monkeypatch.setattr(routes_mod, "drive_in_tui", fake)


# ---- AC1: 私聊 @存在角色 → 已填好的委派卡 + 轻回执 + record_turn + 零 LLM ----
def test_private_at_role_emits_prefilled_card(setup, monkeypatch):
    app, mgr, reg, d = setup
    import karvyloop.console.routes as routes_mod
    _boom_drive(routes_mod, monkeypatch)

    recorded = []
    _orig_record = mgr.record_turn

    def spy_record(user_intent, agent_response, **kw):
        recorded.append((user_intent, agent_response))
        return _orig_record(user_intent, agent_response, **kw)
    monkeypatch.setattr(mgr, "record_turn", spy_record)

    r = TestClient(app).post("/api/intent", json={"intent": "@设计师 出一版海报"})
    body = r.json()

    # 卡:kind/role/requirement/domain 全对(已填好,ACCEPT 即走既有 route_to_role handler)
    pr = app.state.proposal_registry
    assert len(pr) == 1
    p = pr.pending()[0]
    assert p.kind == KIND_ROUTE_TO_ROLE
    assert p.payload["role"] == "设计师" and p.payload["agent_id"] == "设计师"
    assert p.payload["requirement"] == "出一版海报"     # 去掉 @ 后的正文
    assert p.payload["domain_id"] == d.id               # 域 = 该角色所属域
    assert p.payload["domain_name"] == "设计工作室"

    # 轻回执(i18n)+ routed 标
    assert body["text"] == i18n.t("route.mention_fastlane_hint", role="设计师")
    assert body.get("routed") is True

    # record_turn 被调(早返回不记 = ctx 串台的血教训)
    assert recorded and recorded[-1] == ("@设计师 出一版海报", body["text"])


# ---- AC2a: @不存在的名字 → 走原 drive(不误吞) ----
def test_private_at_unknown_name_falls_to_drive(setup, monkeypatch):
    app, mgr, reg, d = setup
    import karvyloop.console.routes as routes_mod
    seen = {}
    _fake_drive(routes_mod, monkeypatch, seen)
    TestClient(app).post("/api/intent", json={"intent": "@查无此人 帮我看看"})
    assert seen.get("drive_intent") == "@查无此人 帮我看看"   # 原样交 drive
    assert len(app.state.proposal_registry) == 0


# ---- AC2b: @邮箱/句子里的 @ → 不误吞 ----
def test_private_at_email_not_swallowed(setup, monkeypatch):
    app, mgr, reg, d = setup
    import karvyloop.console.routes as routes_mod
    seen = {}
    _fake_drive(routes_mod, monkeypatch, seen)
    TestClient(app).post("/api/intent", json={"intent": "把报告发给 hardy@example.com"})
    assert seen.get("drive_intent") == "把报告发给 hardy@example.com"
    assert len(app.state.proposal_registry) == 0


# ---- AC3: 私聊 @多人 → 引导句(不出卡、不 drive) ----
def test_private_at_multiple_roles_guidance(setup, monkeypatch):
    app, mgr, reg, d = setup
    reg.create(name="财务", created_by="user:ch", value_md_raw="",
               member_query="user:ch AND agent:会计")
    import karvyloop.console.routes as routes_mod
    _boom_drive(routes_mod, monkeypatch)
    r = TestClient(app).post("/api/intent", json={"intent": "@设计师 @会计 一起对下预算"})
    body = r.json()
    assert body["text"] == i18n.t("route.mention_multi_hint")
    assert len(app.state.proposal_registry) == 0   # 不出卡(去群里 @ / 开圆桌)


# ---- AC4: 群场不走快通道(群 @ 语义不动) ----
def test_group_peer_skips_fastlane(setup):
    app, mgr, reg, d = setup
    from karvyloop.console.routes import maybe_route_to_role
    mgr.set_peer(Address(domain_id="l0", role="group", agent_id=""))   # karvy world 大群
    out = asyncio.run(maybe_route_to_role(app, mgr, "@设计师 出一版海报"))
    # 群 peer 漏进 maybe_route 时,快通道不接手(命中也不出快通道回执/卡)
    if out is not None:
        assert out["text"] != i18n.t("route.mention_fastlane_hint", role="设计师")
    fast_cards = [p for p in app.state.proposal_registry.pending()
                  if p.kind == KIND_ROUTE_TO_ROLE and p.payload.get("requirement") == "出一版海报"]
    assert not fast_cards


# ---- AC5a: 光 @ 没正文 → 不出空卡(空 requirement 的卡 ACCEPT 必败),照常 drive ----
def test_bare_at_role_falls_to_drive(setup, monkeypatch):
    app, mgr, reg, d = setup
    import karvyloop.console.routes as routes_mod
    seen = {}
    _fake_drive(routes_mod, monkeypatch, seen)
    TestClient(app).post("/api/intent", json={"intent": "@设计师"})
    assert seen.get("drive_intent") == "@设计师"
    assert len(app.state.proposal_registry) == 0


# ---- AC5b: 解析器 —— 同源名册精确命中 + 前缀重叠取最长 ----
def test_resolver_longest_match_wins(setup):
    app, mgr, reg, d = setup
    reg.create(name="平面组", created_by="user:ch", value_md_raw="",
               member_query="user:ch AND agent:设计")   # 「设计」是「设计师」的前缀
    from karvyloop.console.routes import _resolve_private_mentions
    hits = _resolve_private_mentions(app, mgr.current_peer(), "@设计师 出一版海报")
    assert [a.agent_id for a in hits] == ["设计师"]     # 取最长,不重复记「设计」
    hits2 = _resolve_private_mentions(app, mgr.current_peer(), "@设计 出个 logo")
    assert [a.agent_id for a in hits2] == ["设计"]


# ---- AC5c: WS 与 REST 共用同一挂点(maybe_route_to_role)—— 直接调共用点验证 ----
def test_fastlane_via_shared_choke_point(setup):
    app, mgr, reg, d = setup
    from karvyloop.console.routes import maybe_route_to_role
    out = asyncio.run(maybe_route_to_role(app, mgr, "@设计师 出一版海报"))
    assert out is not None and out.get("routed") is True
    assert out["text"] == i18n.t("route.mention_fastlane_hint", role="设计师")
    assert len(app.state.proposal_registry) == 1
