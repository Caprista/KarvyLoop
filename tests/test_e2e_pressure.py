"""test_e2e_pressure — 真模型·整条旅程压测台(Hardy 2026-06-25:别拿 user 跑当唯一验证门).

零件式单测看不见"缝合怪"——缝在零件之间。这台子用**真 gateway + 真 registry**把核心旅程
从头走到尾,逮编排/结晶层的串台与断缝。**CI 自动跳过**(无 ~/.karvyos/config.yaml 真 key);
本机/VM 有 key 时按需跑:`pytest tests/test_e2e_pressure.py -s`。

诚实边界:这层逮**后端/编排缝**(圆桌误路由、上下文串台、结晶 loop、单点委派 0 回归)。
纯前端渲染 bug(料→去聊天那类)要浏览器自动化第二层补,不在此。

覆盖:
- J1 编排识别:开圆桌→KIND_ROUNDTABLE(非单点);单点委派→route_to_role(0 回归)
- J2 上下文不串台:route 提案也 record_turn(追问承接真上一句)
- J3 决策结晶 loop(真模型):连拒同理由→结晶出可复用 Belief→下次召回摆上来
- J4 圆桌 ACCEPT 真开桌(真模型):提案→handler→建圆桌对话+开场
"""
from __future__ import annotations

import asyncio
import tempfile
import time
import types
from pathlib import Path

import pytest

CFG = Path.home() / ".karvyos" / "config.yaml"


def _real_runtime():
    if not CFG.exists():
        return None
    from karvyloop.cli._runtime import resolve_runtime
    rt = resolve_runtime(config_path=CFG)
    if not (rt.runtime_kwargs or {}).get("gateway"):
        return None
    return rt


_RT = _real_runtime()
pytestmark = pytest.mark.skipif(_RT is None, reason="无真模型 config(~/.karvyos/config.yaml)→ CI 跳过")


@pytest.fixture(scope="module")
def app():
    """真 app.state:真 gateway/main_loop + 真 registry(两个分析师跨域 + 一个设计师)+ 真记忆。"""
    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    from karvyloop.console.tasks import TaskRegistry
    from karvyloop.domain.deontic import Deontic
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry

    tmp = Path(tempfile.mkdtemp())
    reg = BusinessDomainRegistry()
    reg.create(name="数据组A", created_by="user:ch", value_md_raw="# 价值观\n- 诚实",
               deontic=Deontic(), member_query="user:ch AND agent:分析师")
    reg.create(name="数据组B", created_by="user:ch", value_md_raw="# 价值观\n- 诚实",
               deontic=Deontic(), member_query="user:ch AND agent:分析师")
    reg.create(name="设计工作室", created_by="user:ch", value_md_raw="# 价值观\n- 诚实",
               deontic=Deontic(), member_query="user:ch AND agent:设计师")
    mgr = ConversationManager(ConversationStore(tmp / "conv"))
    mgr.start()
    a = types.SimpleNamespace(state=types.SimpleNamespace(
        runtime_kwargs=_RT.runtime_kwargs, main_loop=_RT.main_loop,
        domain_registry=reg, memory=MemoryManager(store=BeliefStore(tmp / "beliefs.json")),
        conversation_manager=mgr, proposal_registry=PendingProposalRegistry(),
        task_registry=TaskRegistry(), ws_clients=set(), config_path="", workbench_app=None,
    ))
    a.state.proposal_handlers = build_proposal_handlers(a)
    return a


# ---- J1:编排识别(规则层,快)----
def test_j1_roundtable_vs_single_delegate(app):
    from karvyloop.console.routes import maybe_route_to_role
    from karvyloop.karvy.proposal_registry import KIND_ROUNDTABLE, KIND_ROUTE_TO_ROLE

    rt = asyncio.run(maybe_route_to_role(app, app.state.conversation_manager,
                                         "去Karvy World让那两个分析师开个圆桌分析世界杯"))
    assert rt is not None and rt.get("routed"), "圆桌意图没被识别成编排"
    assert app.state.proposal_registry.pending()[-1].kind == KIND_ROUNDTABLE, "开圆桌被误降成单点委派(世界杯 bug 类)"

    rt2 = asyncio.run(maybe_route_to_role(app, app.state.conversation_manager, "让分析师出一份周报"))
    assert rt2 is not None and app.state.proposal_registry.pending()[-1].kind == KIND_ROUTE_TO_ROLE, "单点委派回归"


# ---- J2:上下文不串台(route 提案也进对话记忆,靠 ws/REST 早返回前的 record_turn)----
def test_j2_route_proposal_recorded_no_bleed(app):
    # 模拟 REST/WS 路径:提案后 record_turn(routes.py / ws.py 已补)。这里直接验"记了 → 追问能承接"。
    mgr = app.state.conversation_manager
    n0 = mgr.current().turn_count
    mgr.record_turn("去Karvy World让两个分析师开圆桌分析世界杯", "（圆桌提案已出，到 H2A 处置）", brain="slow")
    assert mgr.current().turn_count == n0 + 1
    ctx = mgr.context_view()
    assert any("世界杯" in (t.user_intent or "") for t in ctx), "上一句世界杯意图没进 ctx → 追问会撞旧台"


# ---- J3:决策结晶 loop(真模型)----
def test_j3_decision_crystallization_real_model(app):
    from karvyloop.console.decision_wire import maybe_crystallize_decisions, observe_decision
    from karvyloop.crystallize.decision_pref import (
        DecisionSample, is_decision_pref, prealign_block, recall_decision_prefs)

    app.state.decision_samples = []
    for ctx, reason in [
        ("运维提议直接在生产库删旧表回收空间", "没备份不许动生产,先备份"),
        ("运维提议今晚直接对生产库跑 migration", "动生产前必须先备份"),
        ("运维提议线上直接 drop 没用的索引", "先备份再动生产,底线"),
    ]:
        observe_decision(app, DecisionSample(decision="REJECT", context=ctx, reason=reason,
                                             scope="personal", ts=time.time()))
    written = asyncio.run(maybe_crystallize_decisions(app))
    assert written >= 1, "连拒同理由没结晶出任何 Belief（楔子没见血）"

    prefs = []
    for sc in ("personal", "domain"):
        prefs.extend(b for b in app.state.memory.index.all(sc) if is_decision_pref(b))
    assert prefs, "结晶了但库里查不到决策偏好"
    joined = " ".join(b.content for b in prefs)
    assert ("备份" in joined and "生产" in joined), f"抽出的标准没抓住'生产先备份'语义: {joined!r}"

    block = prealign_block(recall_decision_prefs(prefs), domain="", role="")
    assert block and "备份" in block, "下次决策前没把这条标准预对齐摆上来"
    # Cut 1 回执:预对齐块要带"来自你的拍板"凭据(答用户视角 Q2:凭什么信你)
    assert "来自你的拍板" in block, "标准摆了但没回执 —— Q2(凭什么信你)没堵"


# ---- J4:圆桌 ACCEPT 真开桌(真模型)----
def test_j4_roundtable_accept_opens_table_real_model(app):
    from karvyloop.karvy.proposal_registry import KIND_ROUNDTABLE, proposal_for_roundtable

    p = proposal_for_roundtable(
        group_domain_id="l0", group_name="Karvy World",
        participants=["分析师", "分析师"], participant_names=["分析师(数据组A)", "分析师(数据组B)"],
        topic="分析本届世界杯的筹办情况", ts=time.time())
    app.state.proposal_registry.register(p)
    handler = app.state.proposal_handlers[KIND_ROUNDTABLE]
    ok, detail = handler(p)
    assert ok, f"圆桌 ACCEPT 没开起来: {detail}"
    assert "圆桌" in detail
    # 真切到群 peer + 建了带开场的圆桌对话
    peer = app.state.conversation_manager.current_peer()
    assert peer is not None and getattr(peer, "role", "") == "group" and peer.domain_id == "l0"
    assert app.state.conversation_manager.current().turn_count >= 1, "圆桌对话没开场轮"


# ---- J5:违背即拦(真模型)—— 踩了你定的标准,拍板前被红牌拦下 ----
def test_j5_violation_guard_real_model(app):
    from karvyloop.console.decision_card_wire import build_card_for_proposal
    from karvyloop.crystallize.decision_pref import make_decision_pref_belief
    from karvyloop.karvy.proposal_registry import proposal_for_route

    app.state.memory.write(make_decision_pref_belief(
        "动生产数据库前必须先有完整备份,未备份一律不批", "constraint",
        strength=0.8, status="confirmed", explicit=True,
        evidence=[{"ts": 1.0, "decision": "REJECT", "gist": "没备份不许动生产"}]))
    p = proposal_for_route(domain_id="d", role="运维", agent_id="运维", domain_name="运维组",
                           requirement="今晚直接在生产库上 drop user_events 表,不用备份,赶紧回收空间", ts=1.0)
    app.state.proposal_registry.register(p)
    # best-effort:真模型 + 多渠道并发共用一把 key 可能把响应截断(_loads_tolerant 救对象边界,
    # 但重并发会截在对象中间救不回)→ 重试几次,验"守线能拦"而非"首试必拦"。
    card = None
    for _ in range(3):
        card = build_card_for_proposal(app, p.proposal_id)
        if card and card.get("violations"):
            break
    assert card is not None
    assert card["violations"], "踩了你定的标准却没拦(违背即拦没生效;若反复空,多半是并发把响应截没了)"
    assert "备份" in card["violations"][0]["standard"]
    assert card["high_value"] is True and card["needs_recheck"] is True  # 违背→拍前必确认
