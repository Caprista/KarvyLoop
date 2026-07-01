"""test_decision_card_wire — 决策卡接进活的 H2A 提案层(console 侧)的接线测试。

锁住四件事:
1. 提案有 sig 且 verify store 有通过证明 → 卡接地(grounded=True, criteria 有 verify_gate 源)。
2. 提案无 sig / 无证明 → 老实 unverifiable(grounded=False, narrated_warning=True),绝不伪 solved。
3. judge engaged(改/删依据)→ 回喂 observe_decision(EDIT 样本)。
4. 反投降:连续零修改 Accept 达阈值 → needs_recheck=True;一旦 engaged 立即重置。
"""
from __future__ import annotations

import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console.decision_card_wire import build_card_for_proposal, judge_card  # noqa: E402
from karvyloop.karvy.atoms import Proposal  # noqa: E402
from karvyloop.karvy.proposal_registry import PendingProposalRegistry  # noqa: E402


def _proposal(payload=None, summary="部署到预发环境", basis="改动只动了文案,风险低"):
    return Proposal(summary=summary, options=("ACCEPT", "DEFER", "REJECT"), strength=0.9,
                    evidence_refs=(), habit_id=0, model_ref="x/y", ts=0.0,
                    kind="run_task", payload=payload or {}, basis=basis)


def _mem_with_prefs(beliefs):
    """最小 memory 桩:.index.all(scope) 返回该 scope 的 beliefs。"""
    by_scope: dict = {"personal": [], "domain": []}
    for b in beliefs:
        by_scope.setdefault(b.scope, []).append(b)
    idx = types.SimpleNamespace(all=lambda scope: by_scope.get(scope, []))
    return types.SimpleNamespace(index=idx)


def _app(*, proposal, verify_proof=None, memory=None):
    """最小假 app:proposal_registry + (可选) main_loop.verify + (可选) memory。"""
    reg = PendingProposalRegistry()
    reg.register(proposal)
    ml = None
    if verify_proof is not None:
        sig, proof = verify_proof
        vs = types.SimpleNamespace(
            has_gate=lambda s, _sig=sig: s == _sig,
            latest_proof=lambda s, _sig=sig, _p=proof: _p if s == _sig else None,
        )
        ml = types.SimpleNamespace(verify=vs)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        proposal_registry=reg, main_loop=ml, memory=memory))
    return app, proposal.proposal_id


# ---- 1. 接地:有 sig + 通过证明 ----
def test_grounded_when_sig_has_passing_proof():
    sig = "sig-abc"
    proof = types.SimpleNamespace(note="测试套件全绿", trace_ref="trace://t1", passed=True)
    p = _proposal(payload={"sig": sig})
    app, pid = _app(proposal=p, verify_proof=(sig, proof))
    card = build_card_for_proposal(app, pid)
    assert card is not None
    assert card["grounded"] is True
    assert card["narrated_warning"] is False
    assert card["resolvable"] == "solved"
    assert card["criteria"][0]["source"] == "verify_gate"
    assert card["criteria"][0]["grounded"] is True
    assert card["provenance"] == ["trace://t1"]
    assert card["proposal_id"] == pid
    assert card["problem"] == "部署到预发环境"


# ---- 2. 老实:无 sig → unverifiable,绝不伪 solved ----
def test_unverifiable_when_no_sig():
    p = _proposal(payload={})
    app, pid = _app(proposal=p)
    card = build_card_for_proposal(app, pid)
    assert card["grounded"] is False
    assert card["narrated_warning"] is True
    assert card["resolvable"] == "unverifiable"
    assert card["criteria"] == []


def test_unverifiable_when_sig_but_no_proof():
    """提案声称有 sig,但 verify store 根本没这道门 → 仍 unverifiable(不能凭声称接地)。"""
    p = _proposal(payload={"sig": "sig-nonexistent"})
    other = types.SimpleNamespace(note="x", trace_ref="t", passed=True)
    app, pid = _app(proposal=p, verify_proof=("sig-DIFFERENT", other))
    card = build_card_for_proposal(app, pid)
    assert card["grounded"] is False
    assert card["resolvable"] == "unverifiable"


def test_missing_proposal_returns_none():
    app, _pid = _app(proposal=_proposal())
    assert build_card_for_proposal(app, "no-such-id") is None


# ---- 预对齐:把适用的决策偏好摆到卡上 + 高价值标记 ----
def test_aligned_prefs_surfaced_on_card():
    from karvyloop.crystallize.decision_pref import make_decision_pref_belief
    # 一条全局高强度约束(高价值)+ 一条限定别的域(不该命中)
    hit = make_decision_pref_belief("碰生产前必须先过测试", "constraint",
                                    strength=0.9, status="confirmed")
    miss = make_decision_pref_belief("设计先看移动端", "standing",
                                     scope="domain", domain="design-dom", strength=0.8)
    mem = _mem_with_prefs([hit, miss])
    p = _proposal(payload={"domain_id": "data-dom", "role": "分析师"})
    app, pid = _app(proposal=p, memory=mem)
    card = build_card_for_proposal(app, pid)
    contents = [x["content"] for x in card["aligned_prefs"]]
    assert "碰生产前必须先过测试" in contents      # 全局 → 命中
    assert "设计先看移动端" not in contents         # 限定别的域 → 不命中
    assert card["high_value"] is True              # 命中高强度约束
    one = card["aligned_prefs"][0]
    assert one["kind"] == "constraint" and one["kind_label"] == "约束"


def test_no_memory_no_aligned_prefs():
    p = _proposal()
    app, pid = _app(proposal=p)                     # memory=None
    card = build_card_for_proposal(app, pid)
    assert card["aligned_prefs"] == []
    assert card["high_value"] is False


def test_high_value_standard_text_on_card():
    from karvyloop.crystallize.decision_pref import make_decision_pref_belief
    hit = make_decision_pref_belief("碰生产前必须先过测试", "constraint",
                                    strength=0.9, status="confirmed")
    p = _proposal(payload={})
    app, pid = _app(proposal=p, memory=_mem_with_prefs([hit]))
    card = build_card_for_proposal(app, pid)
    assert card["high_value"] is True
    assert card["high_value_standard"] == "碰生产前必须先过测试"


def test_card_carries_needs_recheck_from_tracker():
    from karvyloop.cognition.decision_card import SurfaceTracker
    p = _proposal()
    app, pid = _app(proposal=p)
    # 没 tracker → False
    assert build_card_for_proposal(app, pid)["needs_recheck"] is False
    # tracker 已达阈值(连着无脑拍)→ 卡带 needs_recheck=True(拍之前就能拦)
    tr = SurfaceTracker(threshold=2)
    tr.record(accepted=True, engaged=False)
    tr.record(accepted=True, engaged=False)
    app.state.decision_card_tracker = tr
    assert build_card_for_proposal(app, pid)["needs_recheck"] is True


# ---- 3. 回喂:engaged 改/删依据 → observe_decision EDIT ----
def test_judge_engaged_feeds_observe_decision():
    captured = {}
    p = _proposal()
    app, pid = _app(proposal=p)
    # 桩掉 observe_decision(import 路径在 judge_card 内部)
    import karvyloop.console.decision_wire as dw
    orig = dw.observe_decision
    dw.observe_decision = lambda app, sample: captured.update(
        decision=sample.decision, context=sample.context)
    try:
        out = judge_card(app, proposal_id=pid, decision="ACCEPT", engaged=True,
                         edited_criteria=[{"text": "不应动生产库"}])
    finally:
        dw.observe_decision = orig
    assert out["ok"] is True
    assert captured["decision"] == "EDIT"
    assert "不应动生产库" in captured["context"]


def test_judge_basis_feeds_explicit_state_signal():
    # unverifiable 卡:用户陈述判断依据(无 criteria 可改)→ STATE 显式信号喂楔子 + 算 engaged
    captured = []
    p = _proposal()
    app, pid = _app(proposal=p)
    import karvyloop.console.decision_wire as dw
    orig = dw.observe_decision
    dw.observe_decision = lambda app, sample: captured.append((sample.decision, sample.context))
    try:
        out = judge_card(app, proposal_id=pid, decision="ACCEPT", engaged=False,
                         basis="委派前必须确认对方这周有空")
    finally:
        dw.observe_decision = orig
    assert out["ok"] is True
    assert ("STATE", "委派前必须确认对方这周有空") in captured   # 显式信号


def test_basis_counts_as_engaged_resets_surrender(_app_factory=None):
    # basis 算真判断 → 反投降重置(就算 engaged 标记是 False)
    from karvyloop.cognition.decision_card import SurfaceTracker
    p = _proposal()
    app, pid = _app(proposal=p)
    tr = SurfaceTracker(threshold=2)
    tr.record(accepted=True, engaged=False)
    tr.record(accepted=True, engaged=False)
    app.state.decision_card_tracker = tr
    assert tr.needs_recheck() is True
    import karvyloop.console.decision_wire as dw
    orig = dw.observe_decision
    dw.observe_decision = lambda app, sample: None
    try:
        out = judge_card(app, proposal_id=pid, decision="ACCEPT", engaged=False,
                         basis="我看过上下文,可以放")
    finally:
        dw.observe_decision = orig
    assert out["needs_recheck"] is False   # basis=真判断 → streak 重置


def test_judge_not_engaged_does_not_feed():
    captured = {"n": 0}
    p = _proposal()
    app, pid = _app(proposal=p)
    import karvyloop.console.decision_wire as dw
    orig = dw.observe_decision
    dw.observe_decision = lambda app, sample: captured.update(n=captured["n"] + 1)
    try:
        judge_card(app, proposal_id=pid, decision="ACCEPT", engaged=False)
    finally:
        dw.observe_decision = orig
    assert captured["n"] == 0


# ---- 4. 反投降:连续零修改 Accept 达阈值 → recheck;engaged 重置 ----
def test_blind_accepts_trigger_recheck_then_reset():
    p = _proposal()
    app, pid = _app(proposal=p)
    out = None
    for _ in range(5):                              # 默认阈值 5
        out = judge_card(app, proposal_id=pid, decision="ACCEPT", engaged=False)
    assert out["needs_recheck"] is True
    # 一旦真判断(engaged)→ 立即重置
    out = judge_card(app, proposal_id=pid, decision="ACCEPT", engaged=True)
    assert out["needs_recheck"] is False


# ---- 5. 端到端:两个 HTTP 端点(经真 console app)----
def _client_with_proposal(proposal):
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    reg = PendingProposalRegistry()
    reg.register(proposal)
    app.state.proposal_registry = reg
    return TestClient(app), proposal.proposal_id


def test_endpoint_decision_card_unverifiable():
    c, pid = _client_with_proposal(_proposal(payload={}))
    r = c.get("/api/decision_card", params={"proposal_id": pid}).json()
    assert r["ok"] is True
    assert r["card"]["resolvable"] == "unverifiable"
    assert r["card"]["narrated_warning"] is True


def test_endpoint_decision_card_missing_id():
    c, _pid = _client_with_proposal(_proposal())
    r = c.get("/api/decision_card").json()
    assert r["ok"] is False


def test_endpoint_judge_engaged_returns_recheck_flag():
    c, pid = _client_with_proposal(_proposal())
    r = c.post("/api/decision_card/judge", json={
        "proposal_id": pid, "decision": "ACCEPT", "engaged": True,
        "edited_criteria": [{"text": "别动生产数据"}]}).json()
    assert r["ok"] is True
    assert r["needs_recheck"] is False


# ---- 6. 开机拉取待决提案(跨刷新存活)----
def test_endpoint_pending_proposals_lists_registered():
    p = _proposal()
    c, pid = _client_with_proposal(p)
    r = c.get("/api/proposals/pending").json()
    ids = [x["proposal_id"] for x in r["proposals"]]
    assert pid in ids
    # 形态足够前端 _routeProposal 用
    one = next(x for x in r["proposals"] if x["proposal_id"] == pid)
    assert one["kind"] == "run_task" and "summary" in one and "basis" in one


def test_endpoint_pending_empty_when_no_registry():
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    r = TestClient(app).get("/api/proposals/pending").json()
    assert r["proposals"] == []
