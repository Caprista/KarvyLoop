"""test_console_roundtable_orchestration — 全局小卡识别"开圆桌"编排意图(Hardy 2026-06-25 bug).

病根:私聊小卡说"去Karvy World让两个分析师开圆桌分析世界杯"时,小卡只会单点委派
(route_to_role,一个角色),不认"多人圆桌";且 route 早返回不 record_turn → 紧接的追问
撞上旧的无关 ctx,答非所问。

修:① 识别圆桌/多人协作意图 → 出 roundtable PROPOSE(几个人坐一起,非单点委派);
   ② ACCEPT → 真在群里开圆桌(切群 + 拉成员 + 目标对齐开场);
   ③ 委派/圆桌 PROPOSE 也 record_turn(追问承接真正的上一句,不串旧台)。

AC:
- RT1: proposal_for_roundtable builder(kind/payload/summary)
- RT2: _resolve_roundtable_from_intent —— ≥2 角色 → 圆桌(跨域 → l0 大群)
- RT3: 圆桌关键词 + 1 角色 + 显式 Karvy World → 圆桌(l0 大群)
- RT4: 单角色无圆桌信号 → None(退回单点委派,不误升圆桌)
- RT5: maybe_route_to_role 圆桌意图 → 出 KIND_ROUNDTABLE(不是 route_to_role)
- RT6: maybe_route_to_role 单点委派意图 → 仍 route_to_role(0 回归)
- RT7: roundtable handler ACCEPT → 真建圆桌对话 + 落 roundtable_state
"""
from __future__ import annotations

import asyncio
import types

from karvyloop.console.routes import _resolve_roundtable_from_intent, maybe_route_to_role
from karvyloop.domain import Address
from karvyloop.domain.deontic import Deontic
from karvyloop.domain.registry import BusinessDomainRegistry
from karvyloop.karvy.proposal_registry import (
    KIND_ROUNDTABLE,
    KIND_ROUTE_TO_ROLE,
    PendingProposalRegistry,
    proposal_for_roundtable,
)


def _reg_two_analysts() -> BusinessDomainRegistry:
    """两个域各一个分析师 → 角色名"分析师"跨 2 个域(开圆桌该落到 l0 大群)。"""
    reg = BusinessDomainRegistry()
    reg.create(name="数据组A", created_by="user:ch", value_md_raw="# 价值观\n- 诚实",
               deontic=Deontic(), member_query="user:ch AND agent:分析师")
    reg.create(name="数据组B", created_by="user:ch", value_md_raw="# 价值观\n- 诚实",
               deontic=Deontic(), member_query="user:ch AND agent:分析师")
    return reg


def _reg_one_analyst() -> BusinessDomainRegistry:
    reg = BusinessDomainRegistry()
    reg.create(name="数据组", created_by="user:ch", value_md_raw="# 价值观\n- 诚实",
               deontic=Deontic(), member_query="user:ch AND agent:分析师")
    return reg


def _app(**state):
    return types.SimpleNamespace(state=types.SimpleNamespace(**state))


# ---- RT1 ----
def test_proposal_for_roundtable_builder():
    p = proposal_for_roundtable(
        group_domain_id="l0", group_name="Karvy World",
        participants=["分析师", "分析师"], participant_names=["分析师(数据组A)", "分析师(数据组B)"],
        topic="分析本届世界杯筹办", ts=1.0)
    assert p.kind == KIND_ROUNDTABLE
    assert p.payload["group_domain_id"] == "l0"
    assert p.payload["topic"] == "分析本届世界杯筹办"
    # 卡文案走 i18n(按当前 locale 定稿):模板取表断言(locale 无关),主题数据原样在
    from karvyloop import i18n
    assert p.summary == i18n.t("proposal.roundtable.summary", group="Karvy World",
                               who="分析师(数据组A)、分析师(数据组B)", topic="分析本届世界杯筹办")
    assert "世界杯" in p.summary


# ---- RT2: ≥2 角色 → 圆桌(跨域 → l0) ----
def test_resolve_two_analysts_is_roundtable():
    reg = _reg_two_analysts()
    rt = _resolve_roundtable_from_intent(_app(domain_registry=reg),
                                         "让那两个分析师开个圆桌分析世界杯")
    assert rt is not None
    assert rt["group_domain_id"] == "l0"          # 跨 2 域 → 大群
    assert len(rt["participants"]) == 2
    assert rt["topic"] == "让那两个分析师开个圆桌分析世界杯"


# ---- RT3: 圆桌词 + 1 角色 + 显式 Karvy World ----
def test_resolve_keyword_plus_world():
    reg = _reg_one_analyst()
    rt = _resolve_roundtable_from_intent(_app(domain_registry=reg),
                                         "去Karvy World让分析师开个圆桌聊聊")
    assert rt is not None
    assert rt["group_domain_id"] == "l0" and rt["group_name"] == "Karvy World"
    assert rt["participant_names"] == ["分析师"]


# ---- RT4: 单角色无圆桌信号 → 不升圆桌 ----
def test_resolve_single_role_no_signal_is_none():
    reg = _reg_one_analyst()
    assert _resolve_roundtable_from_intent(_app(domain_registry=reg),
                                           "让分析师出一份周报") is None


# ---- RT4b: 同名角色跨多域 + 单点意图(无圆桌词)→ 仍不升圆桌(压测台逮到的 bug)----
def test_resolve_same_name_across_domains_single_intent_is_none():
    reg = _reg_two_analysts()  # "分析师" 在两个域都有
    # "出周报" 是单产出意图、无圆桌词 → 命中两域的"分析师"也只算单点委派,不该升圆桌
    assert _resolve_roundtable_from_intent(_app(domain_registry=reg),
                                           "让分析师出一份周报") is None


# ---- RT5: 圆桌意图 → KIND_ROUNDTABLE ----
def test_maybe_route_emits_roundtable():
    reg = _reg_two_analysts()
    pr = PendingProposalRegistry()
    app = _app(domain_registry=reg, proposal_registry=pr, ws_clients=set())
    routed = asyncio.run(maybe_route_to_role(app, None, "让那两个分析师开个圆桌分析世界杯"))
    assert routed is not None and routed.get("routed")
    assert "圆桌" in routed["text"]
    assert len(pr) == 1
    assert pr.pending()[0].kind == KIND_ROUNDTABLE


# ---- RT6: 单点委派仍 route_to_role(0 回归) ----
def test_maybe_route_single_still_route_to_role():
    reg = _reg_one_analyst()
    pr = PendingProposalRegistry()
    app = _app(domain_registry=reg, proposal_registry=pr, ws_clients=set())
    routed = asyncio.run(maybe_route_to_role(app, None, "让分析师出一份周报"))
    assert routed is not None and routed.get("routed")
    assert len(pr) == 1
    assert pr.pending()[0].kind == KIND_ROUTE_TO_ROLE


# ---- RT7: ACCEPT → 真开圆桌 ----
def test_roundtable_handler_opens_table(monkeypatch):
    from karvyloop.console.proposal_handlers import build_proposal_handlers

    async def _fake_title(gw, model_ref, text, **kw):
        return "世界杯筹办"

    async def _fake_opening(gw, model_ref, topic, names):
        return f"好,先对齐目标:关于「{topic}」想聚焦哪几点?"

    import karvyloop.console.routes as routes_mod
    monkeypatch.setattr(routes_mod, "_refine_run_title", _fake_title)
    monkeypatch.setattr(routes_mod, "_roundtable_clarify_opening", _fake_opening)

    reg = _reg_two_analysts()

    # 轻量 fake mgr:够 handler 用(set_peer / new_conversation / record_turn)。
    class _Conv:
        id = "conv-rt-1"

    class _Mgr:
        def __init__(self):
            self.peer = None
            self.turns = []

        def set_peer(self, p):
            self.peer = p

        def new_conversation(self, title=""):
            self.title = title
            return _Conv()

        def record_turn(self, u, a, brain="slow"):
            self.turns.append((u, a))

    mgr = _Mgr()
    app = _app(domain_registry=reg, conversation_manager=mgr,
               runtime_kwargs={"gateway": object(), "model_ref": ""})
    handlers = build_proposal_handlers(app)
    assert KIND_ROUNDTABLE in handlers
    p = proposal_for_roundtable(
        group_domain_id="l0", group_name="Karvy World",
        participants=["分析师", "分析师"], participant_names=["分析师", "分析师"],
        topic="分析世界杯筹办", ts=1.0)
    ok, detail = handlers[KIND_ROUNDTABLE](p)
    assert ok, detail
    assert "圆桌" in detail
    assert mgr.peer is not None and mgr.peer.role == "group" and mgr.peer.domain_id == "l0"
    assert app.state.roundtables.get("conv-rt-1", {}).get("topic") == "分析世界杯筹办"
    assert mgr.turns and "圆桌" in mgr.turns[0][0]
