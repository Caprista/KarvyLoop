"""test_console_route_to_role — 执行-role 流(业务委派落地,拍 9.4-门2).

docs/29 KC-3/KC-5 + docs/30 route_to_role:小卡(l0,调度者)把业务活匹配 role → route_to_role
PROPOSE → 用户 ACCEPT → 该 role 在其域 value.md 治理下执行。

AC:
- AC1: proposal_for_route builder(kind=route_to_role + payload + summary)
- AC2: _governance_for 含 role 身份 + 域 value.md
- AC3: route_to_role handler 执行(用 role 治理串 drive,回执结果)
- AC4: handler 无 main_loop → 诚实失败回执(不崩)
- AC5: maybe_route_to_role 私聊小卡 + 委派意图 + 有匹配 role → 出 route_to_role PROPOSE
- AC6: 非委派意图(execute)→ None(小卡自己干,不路由)
- AC7: 业务 role peer(非 l0)→ None(该 role 自己执行)
- AC8: 匹配不到 role → None(退回小卡自己干,0 回归)
- AC9: K5 —— maybe_route 只建 PROPOSE 不执行(未 ACCEPT 不 drive)
"""
from __future__ import annotations

import asyncio
import types

from karvyloop.console.proposal_handlers import _governance_for, build_proposal_handlers
from karvyloop.console.routes import _match_role_for_intent, maybe_route_to_role
from karvyloop.domain import Address
from karvyloop.domain.deontic import Deontic
from karvyloop.domain.registry import BusinessDomainRegistry
from karvyloop.karvy.proposal_registry import (
    KIND_ROUTE_TO_ROLE,
    PendingProposalRegistry,
    proposal_for_route,
)


def _reg_with_designer() -> BusinessDomainRegistry:
    reg = BusinessDomainRegistry()
    reg.create(
        name="设计工作室", created_by="user:ch",
        value_md_raw="# 价值观\n- 诚实第一;用户利益至上",
        deontic=Deontic(), member_query="user:ch AND agent:设计师",
    )
    return reg


def _app(**state):
    return types.SimpleNamespace(state=types.SimpleNamespace(**state))


# ---- AC1 ----
def test_proposal_for_route_builder():
    p = proposal_for_route(domain_id="d1", role="设计师", agent_id="设计师",
                           domain_name="设计工作室", requirement="出一版海报", ts=1.0)
    assert p.kind == KIND_ROUTE_TO_ROLE
    assert p.payload["requirement"] == "出一版海报"
    assert p.payload["role"] == "设计师"
    assert "设计师" in p.summary and "海报" in p.summary


# ---- AC2 ----
def test_governance_for():
    reg = _reg_with_designer()
    d = list(reg.list_all())[0]
    gov = _governance_for(_app(domain_registry=reg),
                          {"domain_id": d.id, "role": "设计师", "domain_name": "设计工作室"})
    assert "设计师" in gov and "设计工作室" in gov
    assert "诚实第一" in gov  # value.md 注入


# ---- AC3 ----
def test_route_handler_executes(monkeypatch):
    reg = _reg_with_designer()
    d = list(reg.list_all())[0]
    captured = {}

    class _Result:
        text = "海报文案:夏日清凉一夏"
        error = ""

    class _ML:
        def drive(self, requirement, slow_brain=None):
            captured["req"] = requirement
            captured["gov"] = slow_brain[1]  # 桩 forge 返回 ("sb", governance)
            return _Result()

    import karvyloop.runtime.main_loop as ml_mod
    monkeypatch.setattr(ml_mod, "forge_slow_brain_factory",
                        lambda **kw: ("sb", kw.get("governance", "")))
    app = _app(main_loop=_ML(),
               runtime_kwargs={"token": 1, "sandbox": 2, "gateway": 3, "workspace_root": "/tmp"},
               domain_registry=reg)
    handlers = build_proposal_handlers(app)
    assert KIND_ROUTE_TO_ROLE in handlers
    p = proposal_for_route(domain_id=d.id, role="设计师", agent_id="设计师",
                           domain_name="设计工作室", requirement="出一版海报", ts=1.0)
    ok, detail = handlers[KIND_ROUTE_TO_ROLE](p)
    assert ok and "设计师" in detail and "海报" in detail
    assert captured["req"] == "出一版海报"
    # 执行带 role 身份 + value.md 治理
    assert "设计师" in captured["gov"] and "诚实第一" in captured["gov"]


# ---- AC4 ----
def test_route_handler_no_mainloop():
    handlers = build_proposal_handlers(_app(main_loop=None))
    p = proposal_for_route(domain_id="d", role="r", agent_id="r",
                           domain_name="n", requirement="x", ts=1.0)
    ok, detail = handlers[KIND_ROUTE_TO_ROLE](p)
    assert not ok


# ---- AC5: 委派 → PROPOSE ----
def test_route_split_emits_proposal():
    reg = _reg_with_designer()
    pr = PendingProposalRegistry()
    app = _app(domain_registry=reg, proposal_registry=pr, ws_clients=set())
    routed = asyncio.run(maybe_route_to_role(app, None, "让设计师出一版海报文案"))
    assert routed is not None and routed.get("routed")
    assert len(pr) == 1
    p = pr.pending()[0]
    assert p.kind == KIND_ROUTE_TO_ROLE and p.payload["role"] == "设计师"
    assert p.payload["requirement"] == "让设计师出一版海报文案"


# ---- AC6: execute 意图不路由 ----
def test_route_split_execute_no_route():
    reg = _reg_with_designer()
    pr = PendingProposalRegistry()
    app = _app(domain_registry=reg, proposal_registry=pr, ws_clients=set())
    assert asyncio.run(maybe_route_to_role(app, None, "搜一下今天天气")) is None
    assert len(pr) == 0


# ---- AC7: 业务 peer 不路由 ----
def test_route_split_business_peer_no_route():
    reg = _reg_with_designer()
    pr = PendingProposalRegistry()
    app = _app(domain_registry=reg, proposal_registry=pr, ws_clients=set())

    class _Mgr:
        def current_peer(self):
            return Address(domain_id="dom-1", role="agent", agent_id="设计师")

    assert asyncio.run(maybe_route_to_role(app, _Mgr(), "让设计师出海报")) is None


# ---- AC8: 匹配不到 role ----
def test_route_split_no_match():
    reg = _reg_with_designer()
    pr = PendingProposalRegistry()
    app = _app(domain_registry=reg, proposal_registry=pr, ws_clients=set())
    assert asyncio.run(maybe_route_to_role(app, None, "让会计做个月度账")) is None
    assert len(pr) == 0


# ---- AC9: K5 —— PROPOSE 不执行 ----
def test_route_split_does_not_execute():
    reg = _reg_with_designer()
    pr = PendingProposalRegistry()
    # main_loop 缺席:若 maybe_route 误执行会炸;它只该建 PROPOSE
    app = _app(domain_registry=reg, proposal_registry=pr, ws_clients=set())
    routed = asyncio.run(maybe_route_to_role(app, None, "让设计师出海报"))
    assert routed is not None and routed.get("routed")
    # 没有任何 drive 发生(只 PROPOSE);ACCEPT 才执行(handler 路径,AC3 覆盖)


# ---- Step 0(a):你的决策标准在委派执行时也注入 governance(不只 l0 聊天)----
def test_route_handler_injects_your_standards(monkeypatch):
    import pathlib
    import tempfile
    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.crystallize.decision_pref import make_decision_pref_belief

    reg = _reg_with_designer()
    d = list(reg.list_all())[0]
    mem = MemoryManager(store=BeliefStore(pathlib.Path(tempfile.mkdtemp()) / "b.json"))
    mem.write(make_decision_pref_belief("海报配色必须先过无障碍对比度", "constraint",
                                        strength=0.8, status="confirmed", explicit=True))
    captured = {}

    class _Result:
        text = "ok"
        error = ""

    class _ML:
        def drive(self, requirement, slow_brain=None):
            captured["gov"] = slow_brain[1]
            return _Result()

    import karvyloop.runtime.main_loop as ml_mod
    monkeypatch.setattr(ml_mod, "forge_slow_brain_factory",
                        lambda **kw: ("sb", kw.get("governance", "")))
    app = _app(main_loop=_ML(),
               runtime_kwargs={"token": 1, "sandbox": 2, "gateway": 3, "workspace_root": "/tmp"},
               domain_registry=reg, memory=mem)
    handlers = build_proposal_handlers(app)
    p = proposal_for_route(domain_id=d.id, role="设计师", agent_id="设计师",
                           domain_name="设计工作室", requirement="出一版海报", ts=1.0)
    ok, _ = handlers[KIND_ROUTE_TO_ROLE](p)
    assert ok
    # 你的标准 + 回执 进了委派执行的 governance(委派也认你怎么拍板)
    assert "海报配色必须先过无障碍对比度" in captured["gov"]
    assert "诚实第一" in captured["gov"]   # 域 value.md 仍在(base 没丢)
