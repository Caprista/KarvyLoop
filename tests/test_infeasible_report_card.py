"""docs/02 §15.3 — 不可行报告决策卡:role 自助重规划耗尽 → 带证据回头,不甩裸问题。

不变量(§15.7):① basis 必须由真实尝试轨迹拼(无轨迹=假报告)② 天然 unverifiable(无 sig)
③ 幂等(同一卡住目标收敛成一张卡)④ ACCEPT 不执行任何东西(报告非动作)。
"""

from __future__ import annotations

from karvyloop.karvy.proposal_registry import (
    ALL_KINDS,
    KIND_INFEASIBLE_REPORT,
    proposal_for_infeasible_report,
)
from karvyloop.console.proposal_handlers import build_proposal_handlers


_TRAIL = [
    {"attempt": 1, "terminal": "max_turns", "note": "写到 3/9 文件就到步数上限"},
    {"attempt": 2, "terminal": "circuit_open", "note": "连续工具失败触发断路"},
]


def test_kind_registered():
    assert KIND_INFEASIBLE_REPORT in ALL_KINDS


def test_card_basis_is_built_from_real_trail():
    """§15.7:回头必带尝试轨迹 —— basis 里要见到真实 attempt 轨迹,不是空话。

    卡文案走 i18n(按当前 locale 定稿)→ 锁 zh 断言中文原文;轨迹数据 locale 无关。"""
    from karvyloop import i18n
    try:
        i18n.set_locale("zh")
        p = proposal_for_infeasible_report(goal="生成季度报表", role="分析师", attempts=_TRAIL, ts=1.0)
        # 轨迹进 basis(带证据)
        assert "第 1 次" in p.basis and "max_turns" in p.basis
        assert "第 2 次" in p.basis and "circuit_open" in p.basis
        # 不是裸问题:明说"带证据的结论,不是问你怎么办"
        assert "不是问你" in p.basis or "带证据" in p.basis
    finally:
        i18n.set_locale(None)
    assert p.kind == KIND_INFEASIBLE_REPORT
    # payload 留全轨迹给溯源
    assert p.payload["attempts"] == _TRAIL
    assert p.payload["terminal_reasons"] == ["circuit_open", "max_turns"]


def test_card_is_unverifiable_no_sig():
    """报告卡无 sig / evidence_refs —— 决策卡 build 时自然落"未核验"区,不伪 grounded。"""
    p = proposal_for_infeasible_report(goal="x", role="r", attempts=_TRAIL, ts=1.0)
    assert p.evidence_refs == ()
    assert getattr(p, "model_ref", "") == ""


def test_card_id_idempotent_same_stuck_goal():
    """同一 role+目标+终止原因集合 → 同一 proposal_id(不刷屏)。"""
    a = proposal_for_infeasible_report(goal="g", role="r", attempts=_TRAIL, ts=1.0)
    b = proposal_for_infeasible_report(goal="g", role="r", attempts=_TRAIL, ts=999.0)
    assert a.proposal_id == b.proposal_id
    assert a.proposal_id.startswith("infeasible-")
    # 不同目标 → 不同卡
    c = proposal_for_infeasible_report(goal="other", role="r", attempts=_TRAIL, ts=1.0)
    assert c.proposal_id != a.proposal_id


def test_options_force_a_decision():
    p = proposal_for_infeasible_report(goal="g", role="r", attempts=_TRAIL, ts=1.0)
    assert p.options == ("ACCEPT", "DEFER", "REJECT")


def test_handler_records_but_executes_nothing():
    """ACCEPT 只记录知悉,绝不跑执行(报告非动作;系统不替你重试)。"""
    handlers = build_proposal_handlers(app=None)  # 该 handler 不依赖 app
    assert KIND_INFEASIBLE_REPORT in handlers
    p = proposal_for_infeasible_report(goal="生成报表", role="分析师", attempts=_TRAIL, ts=1.0)
    ok, detail = handlers[KIND_INFEASIBLE_REPORT](p)
    assert ok is True
    assert "不会自动重试" in detail
    assert "分析师" in detail and "生成报表" in detail


def test_empty_trail_still_safe():
    """边角:空轨迹不崩(虽然调用方应保证非空)—— basis 退化但不假装有证据。"""
    from karvyloop import i18n
    p = proposal_for_infeasible_report(goal="g", role="r", attempts=[], ts=1.0)
    assert p.kind == KIND_INFEASIBLE_REPORT
    assert i18n.t("proposal.infeasible.no_trail") in p.basis   # 按当前 locale 取表(locale 无关)
    assert p.payload["attempts"] == []
