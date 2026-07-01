"""test_proposal_registry — PROPOSE 类型化 + 待决议表 + 兑现分派(修 D5,拍 9.4-B3a)。

设计:docs/30。AC 对应 PR-1..PR-4。

AC:
- AC1 (PR-1): Proposal 带 kind/payload/proposal_id;默认 kind=crystallize_skill(向后兼容)
- AC2 (PR-1): proposal_id 稳定派生(同 kind+habit+summary → 同 id;无随机)
- AC3 (PR-1): to_dict 含新字段
- AC4 (PR-2): registry register/get/remove/pending
- AC5 (PR-3): ACCEPT 按 kind dispatch 到对应 handler + 兑现后离开 registry
- AC6 (PR-3): REJECT 丢弃;DEFER 挂起(留 registry)
- AC7 (PR-3): 未知 proposal_id → None;缺 handler → ok=False 不抛不副作用
- AC8 (PR-4): handler 异常不外溢(ok=False)
"""
from __future__ import annotations

from karvyloop.karvy.atoms import Proposal
from karvyloop.karvy.proposal_registry import (
    KIND_CRYSTALLIZE_SKILL,
    KIND_RESOLVE_CONFLICT,
    KIND_ROUTE_TO_ROLE,
    DispatchResult,
    PendingProposalRegistry,
    dispatch_accept,
)


def _mk(summary="用户可能想打包", kind=KIND_CRYSTALLIZE_SKILL, habit_id=7, payload=None):
    return Proposal(
        summary=summary, options=("ACCEPT", "DEFER", "REJECT"), strength=0.8,
        evidence_refs=(1, 2), habit_id=habit_id, model_ref="m", ts=1.0,
        kind=kind, payload=payload or {},
    )


# ---- AC1/AC2: Proposal 类型化 + 稳定 id ----
def test_default_kind_backward_compat():
    p = Proposal(summary="s", options=(), strength=0.5, evidence_refs=(), habit_id=0,
                 model_ref="m", ts=1.0)
    assert p.kind == KIND_CRYSTALLIZE_SKILL
    assert p.payload == {}
    assert p.proposal_id  # 派生非空


def test_proposal_id_stable_derivation():
    a = _mk()
    b = _mk()  # 同 kind+habit+summary
    assert a.proposal_id == b.proposal_id  # 稳定(无随机)
    c = _mk(summary="另一件事")
    assert c.proposal_id != a.proposal_id
    d = _mk(kind=KIND_ROUTE_TO_ROLE)
    assert d.proposal_id != a.proposal_id  # kind 进 id
    assert d.proposal_id.startswith(KIND_ROUTE_TO_ROLE + "-")


def test_explicit_proposal_id_respected():
    p = Proposal(summary="s", options=(), strength=0.5, evidence_refs=(), habit_id=0,
                 model_ref="m", ts=1.0, proposal_id="fixed-123")
    assert p.proposal_id == "fixed-123"


# ---- AC3: to_dict ----
def test_to_dict_has_new_fields():
    d = _mk(payload={"sig": "abc"}).to_dict()
    assert d["kind"] == KIND_CRYSTALLIZE_SKILL
    assert d["payload"] == {"sig": "abc"}
    assert d["proposal_id"]
    # 老字段仍在(向后兼容)
    assert d["summary"] and "options" in d and "strength" in d


# ---- AC4: registry ----
def test_registry_register_get_remove():
    reg = PendingProposalRegistry()
    p = _mk()
    pid = reg.register(p)
    assert pid == p.proposal_id
    assert reg.get(pid) is p
    assert len(reg) == 1
    assert reg.remove(pid) is p
    assert reg.get(pid) is None
    assert len(reg) == 0


# ---- AC5: ACCEPT dispatch + 离开 registry ----
def test_accept_dispatches_by_kind_and_leaves_registry():
    reg = PendingProposalRegistry()
    seen = {}
    handlers = {
        KIND_CRYSTALLIZE_SKILL: lambda p: (True, f"crystallized {p.payload.get('sig')}"),
        KIND_ROUTE_TO_ROLE: lambda p: seen.setdefault("routed", True) or (True, "routed"),
    }
    p = _mk(payload={"sig": "xyz"})
    reg.register(p)
    res = reg.decide(p.proposal_id, "ACCEPT", handlers=handlers)
    assert isinstance(res, DispatchResult)
    assert res.kind == KIND_CRYSTALLIZE_SKILL
    assert res.ok and "xyz" in res.detail
    assert reg.get(p.proposal_id) is None  # 兑现后离开


# ---- AC6: REJECT/DEFER ----
def test_reject_discards():
    reg = PendingProposalRegistry()
    p = _mk()
    reg.register(p)
    res = reg.decide(p.proposal_id, "REJECT")
    assert res.ok and res.detail == "rejected"
    assert reg.get(p.proposal_id) is None


def test_defer_keeps():
    reg = PendingProposalRegistry()
    p = _mk()
    reg.register(p)
    res = reg.decide(p.proposal_id, "DEFER")
    assert res.ok and res.detail == "deferred"
    assert reg.get(p.proposal_id) is p  # 仍在


# ---- AC7: 未知 id / 缺 handler ----
def test_unknown_proposal_id_returns_none():
    reg = PendingProposalRegistry()
    assert reg.decide("nope", "ACCEPT") is None


def test_missing_handler_no_sideeffect():
    reg = PendingProposalRegistry()
    p = _mk(kind=KIND_RESOLVE_CONFLICT)
    reg.register(p)
    res = reg.decide(p.proposal_id, "ACCEPT", handlers={})  # 无 handler
    assert not res.ok and "no handler" in res.detail
    assert reg.get(p.proposal_id) is None  # 仍离开(已处置)


# ---- AC8: handler 异常不外溢 ----
def test_handler_exception_contained():
    def boom(p):
        raise RuntimeError("kaboom")
    res = dispatch_accept(_mk(), {KIND_CRYSTALLIZE_SKILL: boom})
    assert not res.ok and "handler error" in res.detail


# ---- AC9-AC11: console 活接线(register on broadcast + dispatch on /api/h2a_decide)----

import asyncio

from fastapi.testclient import TestClient

from karvyloop.console import broadcast_proposal, build_console_app
from karvyloop.karvy.observer import WorkbenchObserver


class _State:
    pass


class _FakeApp:
    def __init__(self):
        self.state = _State()
        self.state.ws_clients = set()


def test_broadcast_registers_proposal():
    """AC9(PR-2):broadcast_proposal 把 Proposal 登记进 app.state.proposal_registry。"""
    app = _FakeApp()
    app.state.proposal_registry = PendingProposalRegistry()
    p = _mk()
    asyncio.run(broadcast_proposal(app, p))
    assert app.state.proposal_registry.get(p.proposal_id) is p


def test_h2a_decide_accept_dispatches_via_route():
    """AC10(PR-3 活路径):/api/h2a_decide ACCEPT 凭 proposal_id 兑现,response 带 dispatch。"""
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.proposal_registry = PendingProposalRegistry()
    fulfilled = {}
    app.state.proposal_handlers = {
        KIND_CRYSTALLIZE_SKILL: lambda pr: (fulfilled.setdefault("hit", pr.proposal_id), (True, "done"))[1],
    }
    p = _mk()
    app.state.proposal_registry.register(p)
    client = TestClient(app)
    r = client.post("/api/h2a_decide", json={
        "proposal_id": p.proposal_id, "decision": "ACCEPT", "reason": "ok",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["ok"] and body["dispatch"]["kind"] == KIND_CRYSTALLIZE_SKILL
    assert fulfilled.get("hit") == p.proposal_id
    # 兑现后离开 registry(PR-3)
    assert app.state.proposal_registry.get(p.proposal_id) is None
    # K5 仍在:envelope 照常产出
    assert body["envelope"] is not None


def test_h2a_decide_no_registry_backward_compat():
    """AC11:未接 registry → dispatch=None,K5 envelope 照常(0 回归)。"""
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    client = TestClient(app)
    r = client.post("/api/h2a_decide", json={
        "proposal_id": "p-x", "decision": "ACCEPT", "reason": "ok",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["dispatch"] is None
    assert body["envelope"] is not None
