"""test_report_card — 执行后回报卡:✓ 只来自真验收(非 inconclusive),绝不伪 ✓。"""
from __future__ import annotations

import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.decision_card import build_report_card  # noqa: E402


# ---- builder 诚实性 ----
def test_passed_rigorous_is_grounded_solved():
    c = build_report_card(problem="做X", approach="由分析师执行",
                          passed=True, inconclusive=False)
    assert c["resolvable"] == "solved" and c["grounded"] is True
    assert c["narrated_warning"] is False
    assert c["criteria"][0]["source"] == "verify_gate" and c["criteria"][0]["status"] == "passed"
    assert c["kind"] == "report" and c["surface_full"] is False   # 纯通过 → 可压一行


def test_failed_rigorous_is_grounded_failed_with_feedback():
    c = build_report_card(problem="做X", approach="重跑",
                          passed=False, inconclusive=False, feedback="缺少边界处理")
    assert c["resolvable"] == "failed" and c["grounded"] is True
    assert c["criteria"][0]["status"] == "failed"
    assert c["surface_full"] is True and c["feedback"] == "缺少边界处理"


def test_inconclusive_is_unverifiable_never_fake_check():
    # 验收无能力/未给明确判定 → 老实 unverifiable,绝不伪 ✓
    c = build_report_card(problem="做X", approach="执行",
                          passed=True, inconclusive=True, feedback="(未接验收能力)")
    assert c["resolvable"] == "unverifiable" and c["grounded"] is False
    assert c["narrated_warning"] is True
    assert c["criteria"] == []                       # 没有接地依据
    assert c["surface_full"] is True


# ---- handler stash + pop ----
def test_stash_and_pop_report_card():
    from karvyloop.console.proposal_handlers import _stash_report_card, pop_report_card
    app = types.SimpleNamespace(state=types.SimpleNamespace())
    proposal = types.SimpleNamespace(proposal_id="route_to_role-1-abc")
    checked = types.SimpleNamespace(verdict=types.SimpleNamespace(passed=True, inconclusive=False, feedback=""))
    _stash_report_card(app, proposal, checked, problem="做报表", approach="由分析师执行")
    card = pop_report_card(app, "route_to_role-1-abc")
    assert card is not None and card["resolvable"] == "solved" and card["proposal_id"] == "route_to_role-1-abc"
    assert pop_report_card(app, "route_to_role-1-abc") is None   # 取一次即清


def test_stash_no_verdict_is_unverifiable_not_fake():
    from karvyloop.console.proposal_handlers import _stash_report_card, pop_report_card
    app = types.SimpleNamespace(state=types.SimpleNamespace())
    proposal = types.SimpleNamespace(proposal_id="p1")
    checked = types.SimpleNamespace(verdict=None)     # 没 verdict → 当未决,不伪 ✓
    _stash_report_card(app, proposal, checked, problem="x", approach="y")
    card = pop_report_card(app, "p1")
    assert card["resolvable"] == "unverifiable" and card["grounded"] is False


def test_pop_no_store_returns_none():
    from karvyloop.console.proposal_handlers import pop_report_card
    app = types.SimpleNamespace(state=types.SimpleNamespace())
    assert pop_report_card(app, "whatever") is None


# ---- 端到端(真 /api/h2a_decide,快 stub handler 不打 LLM):ACCEPT 回包带 report_card ----
def test_h2a_decide_response_carries_report_card():
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.console.proposal_handlers import _stash_report_card
    from karvyloop.karvy.atoms import Proposal
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    reg = PendingProposalRegistry()
    p = Proposal(summary="把报表交给分析师", options=("ACCEPT", "DEFER", "REJECT"), strength=0.9,
                 evidence_refs=(), habit_id=0, model_ref="x", ts=0.0, kind="run_task",
                 payload={}, basis="b")
    reg.register(p)
    app.state.proposal_registry = reg

    def _fast_handler(proposal):   # 不打 LLM:就地 stash 一张"通过验收"的回报卡
        _stash_report_card(app, proposal,
                           types.SimpleNamespace(verdict=types.SimpleNamespace(
                               passed=True, inconclusive=False, feedback="")),
                           problem="把报表交给分析师", approach="由分析师执行")
        return True, "done"

    app.state.proposal_handlers = {"run_task": _fast_handler}
    r = TestClient(app).post("/api/h2a_decide", json={
        "proposal_id": p.proposal_id, "decision": "ACCEPT", "reason": ""}).json()
    assert r["report_card"] is not None
    assert r["report_card"]["resolvable"] == "solved" and r["report_card"]["grounded"] is True
    assert r["report_card"]["proposal_id"] == p.proposal_id
