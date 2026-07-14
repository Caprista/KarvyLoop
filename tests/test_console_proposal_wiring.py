"""test_console_proposal_wiring — 门2:D5 handler + D4 域冲突触发 live 接线(拍 9.4).

D5:console 注册 crystallize_skill 采纳 handler → ACCEPT 真回执(非"no handler")。
D4:建业务域 = 触发② → 检全局技能 × 新域治理冲突 → 注册 resolve_conflict PROPOSE。

AC:
- AC1 (D5): build_proposal_handlers 含 crystallize_skill;registry.decide(ACCEPT)→ ok + 含 summary
- AC2 (D5): 未注册 kind 仍走 registry 默认诚实回执(no handler),不假装
- AC3 (D4): _detect_domain_skill_conflicts 命中(技能 when_to_use 沾域 value 关键词)→ 注册 resolve_conflict + 回执
- AC4 (D4): 无 main_loop / 无技能 → 返空,不报错
- AC5 (D4): 检出的 Proposal 是 resolve_conflict kind + 进 registry(可被 ACCEPT)
"""
from __future__ import annotations

import types

from karvyloop.console.proposal_handlers import build_proposal_handlers
from karvyloop.console.routes import _detect_domain_skill_conflicts
from karvyloop.karvy.atoms import Proposal
from karvyloop.karvy.proposal_registry import (
    KIND_CRYSTALLIZE_SKILL,
    KIND_RESOLVE_CONFLICT,
    PendingProposalRegistry,
)


# ---- D5 ----
def test_crystallize_handler_acks():
    handlers = build_proposal_handlers(app=None)
    assert KIND_CRYSTALLIZE_SKILL in handlers
    reg = PendingProposalRegistry()
    p = Proposal(summary="每天导出报表", options=("ACCEPT",), strength=0.9,
                 evidence_refs=(), habit_id=3, model_ref="m", ts=1.0)
    reg.register(p)
    res = reg.decide(p.proposal_id, "ACCEPT", handlers=handlers)
    assert res.ok and res.kind == KIND_CRYSTALLIZE_SKILL
    assert "每天导出报表" in res.detail


def test_unregistered_kind_honest_no_handler():
    handlers = build_proposal_handlers(app=None)
    reg = PendingProposalRegistry()
    # spend_budget_alert = "有意不注册 handler"的 kind(纯提醒卡,无副作用兑现;
    # 原标本 set_preference 已落葬 docs/79——被 §11 决策偏好取代)
    p = Proposal(summary="x", options=(), strength=0.5, evidence_refs=(), habit_id=0,
                 model_ref="m", ts=1.0, kind="spend_budget_alert")  # 未注册
    reg.register(p)
    res = reg.decide(p.proposal_id, "ACCEPT", handlers=handlers)
    assert not res.ok and "no handler" in res.detail  # 诚实,不假装


# ---- D4 ----

class _Idx:
    """假 skill_index:all() 返回带 name/sig/when_to_use 的条目。"""
    def __init__(self, entries):
        self._e = entries
    def all(self):
        return self._e


def _entry(name, sig, wtu):
    return types.SimpleNamespace(name=name, sig=sig, when_to_use=wtu)


def _domain(domain_id, value_text, forbid=(), oblige=()):
    vm = types.SimpleNamespace(text=value_text, principles=tuple(
        ln.strip("# -") for ln in value_text.splitlines() if ln.strip().startswith(("-", "#"))
    ))
    deon = types.SimpleNamespace(forbid=tuple(forbid), oblige=tuple(oblige))
    return types.SimpleNamespace(id=domain_id, value_md=vm, deontic=deon)


def _app(main_loop=None, registry=None):
    st = types.SimpleNamespace(main_loop=main_loop, proposal_registry=registry)
    return types.SimpleNamespace(state=st)


def test_detect_registers_resolve_conflict():
    reg = PendingProposalRegistry()
    idx = _Idx([_entry("批量删库", "sig-del", "when_to_use: 删除数据库 drop table 清空")])
    ml = types.SimpleNamespace(skill_index=idx)
    dom = _domain("dom-x", "# 价值观\n- 禁止删除生产数据库", forbid=("删除生产数据库",))
    app = _app(main_loop=ml, registry=reg)

    out = _detect_domain_skill_conflicts(app, dom, agent="DBA")
    assert len(out) >= 1
    pid = out[0]["proposal_id"]
    # 注册进待决议表,kind=resolve_conflict
    p = reg.get(pid)
    assert p is not None and p.kind == KIND_RESOLVE_CONFLICT
    assert "批量删库" in out[0]["summary"]


def test_detect_no_mainloop_returns_empty():
    out = _detect_domain_skill_conflicts(_app(main_loop=None), _domain("d", "# 价值观\n- x"), agent="r")
    assert out == []


def test_detect_no_skills_returns_empty():
    ml = types.SimpleNamespace(skill_index=_Idx([]))
    out = _detect_domain_skill_conflicts(_app(main_loop=ml), _domain("d", "# 价值观\n- x"), agent="r")
    assert out == []
