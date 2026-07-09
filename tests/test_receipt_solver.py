"""test_receipt_solver — 确定性算术求解/纠错(杠杆②;不靠模型)。

锁的是 Hardy 要的那个"防降级"性质:**用票据自身冗余把 OCR 读错/漏读的数反解回来**,
唯一确定才纠、欠定/冲突只 flag、绝不猜。全程纯算术,与模型强弱无关。
"""
from __future__ import annotations

from karvyloop.receipt_solver import arithmetic_reconcile


def test_reverse_solve_single_missing_amount():
    """低把握价被 LLM 留 null(①→②配合)→ 小计把它唯一钉死 → 反解回来。
    星巴克:拿铁 null、美式 30、合计 96 → 拿铁必是 66。"""
    data = {
        "line_items": [
            {"name": "拿铁", "qty": 2, "unit_price": None, "amount": None},
            {"name": "美式", "qty": 1, "unit_price": 30.0, "amount": 30.0},
        ],
        "subtotal": 96.0, "tax": None, "total": 96.0,
    }
    out = arithmetic_reconcile(data)
    latte = out["line_items"][0]
    assert latte["amount"] == 66.0, f"应由小计反解出 66: {latte}"
    assert out["balanced"] is True
    assert any(r["field"].endswith("amount") and r["to"] == 66.0 for r in out["reconciled"])


def test_line_level_solve_amount_from_qty_unit():
    """行内单价×数量反解金额(舜山:养生海参盅 58×6=348)。"""
    out = arithmetic_reconcile({"line_items": [
        {"name": "养生海参盅", "qty": 6, "unit_price": 58.0, "amount": None}]})
    assert out["line_items"][0]["amount"] == 348.0


def test_solve_tax_and_total_chain():
    """小计+税=总额:缺税 → 反解(12.65 − 11.74 = 0.91)。"""
    out = arithmetic_reconcile({"line_items": [], "subtotal": 11.74, "tax": None, "total": 12.65})
    assert out["tax"] == 0.91 and out["balanced"] is True


def test_underdetermined_two_missing_flags_not_guess():
    """两个缺失金额=欠定 → **不猜**,只 flag(奥格登那种:多项无价)。"""
    data = {"line_items": [
        {"name": "A", "qty": 1, "unit_price": None, "amount": 10.0},
        {"name": "B", "qty": 1, "unit_price": None, "amount": None},
        {"name": "C", "qty": 1, "unit_price": None, "amount": None}],
        "subtotal": 30.0, "tax": None, "total": None}
    out = arithmetic_reconcile(data)
    assert out["line_items"][1]["amount"] is None and out["line_items"][2]["amount"] is None, "欠定不许猜"
    # 诚实:仍有明细金额定不下来 → 必须 flag,别让"账平"盖住"明细不全"
    assert any("明细项金额未能确定" in f for f in out["flags"]), "未确定的明细项要点出来"


def test_inconsistent_all_present_flags_not_overwrite():
    """三值都在但违约束(单价×数量≠金额)→ flag,**不盲改**(不知哪个错)。"""
    out = arithmetic_reconcile({"line_items": [
        {"name": "X", "qty": 2, "unit_price": 5.0, "amount": 15.0}]})  # 2×5=10 ≠ 15
    assert out["line_items"][0]["amount"] == 15.0, "不盲改原值"
    assert any("≠" in f for f in out["flags"])


def test_subtotal_items_mismatch_flagged():
    """Σ明细 ≠ 小计 → flag 并报差额(不强行凑平)。"""
    out = arithmetic_reconcile({"line_items": [
        {"name": "A", "qty": 1, "unit_price": 10.0, "amount": 10.0},
        {"name": "B", "qty": 1, "unit_price": 20.0, "amount": 20.0}],
        "subtotal": 49.48, "tax": 4.08, "total": 53.56})
    assert any("≠ 小计" in f for f in out["flags"])
    assert out["balanced"] is True, "小计+税=总额 仍自洽(mismatch 只在明细侧)"


def test_never_crashes_on_garbage():
    """脏输入(None/字符串/缺键)→ 不崩,原样返回 + 空 reconciled。"""
    for bad in ({}, {"line_items": None}, {"line_items": [{"amount": "abc"}], "total": "x"}):
        out = arithmetic_reconcile(bad)
        assert "reconciled" in out and "flags" in out and "balanced" in out


def test_free_items_zero_amount_safe():
    """0 价免费项(Fire Sauce)不触发除零/误 flag。"""
    out = arithmetic_reconcile({"line_items": [
        {"name": "Fire Sauce", "qty": 12, "unit_price": 0.0, "amount": 0.0},
        {"name": "Meal", "qty": 1, "unit_price": 10.49, "amount": 10.49}],
        "subtotal": 10.49, "tax": 0.0, "total": 10.49})
    assert out["balanced"] is True and not out["flags"]
