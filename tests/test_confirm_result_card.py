"""docs/02 §15.5 尾巴②:结果确认卡 —— 人 accept role 结果(依据)→ ACCEPT 时 role 综合裁自造 atom。

不变量:① 人 accept 的是结果、不直接碰 atom ② ACCEPT 才触发 role 综合裁(judge+sediment),
不替人自动留 ③ 留则入 role composition、撤则删 ④ 无 gateway 保守不留。
"""

from __future__ import annotations

from pathlib import Path

from karvyloop.karvy.proposal_registry import (
    ALL_KINDS,
    KIND_CONFIRM_RESULT,
    proposal_for_confirm_result,
)
from karvyloop.console.proposal_handlers import build_proposal_handlers
from karvyloop.atoms.registry import AtomRegistry
from karvyloop.roles.registry import RoleRegistry


class TextDelta:
    def __init__(self, t):
        self.text = t


class FakeGateway:
    def __init__(self, out):
        self.out = out

    def resolve_model(self, scope):
        return "m"

    async def complete(self, msgs, tools, model_ref, *, system=None):
        yield TextDelta(self.out)


class _App:
    class _S:
        pass

    def __init__(self, areg, rreg, gw):
        self.state = _App._S()
        self.state.atom_registry = areg
        self.state.role_registry = rreg
        self.state.runtime_kwargs = {"gateway": gw, "model_ref": "m"} if gw else {}


_MINTED = [{"id": "zh_en", "purpose": "把中文翻成英文"}]


def test_kind_registered():
    assert KIND_CONFIRM_RESULT in ALL_KINDS


def test_card_basis_is_about_result_not_atom():
    # 卡文案走 i18n(按当前 locale 定稿)→ 锁 zh 断言中文原文;atom/role 名是数据 locale 无关
    from karvyloop import i18n
    try:
        i18n.set_locale("zh")
        p = proposal_for_confirm_result(role="译者", requirement="翻译这篇文档", minted=_MINTED, ts=1.0)
    finally:
        i18n.set_locale(None)
    assert p.kind == KIND_CONFIRM_RESULT
    assert "认可这次结果" in p.basis and "zh_en" in p.basis
    assert "综合裁" in p.basis or "综合判断" in p.basis or "由 译者" in p.basis
    assert p.payload["minted"] == _MINTED
    # 幂等
    q = proposal_for_confirm_result(role="译者", requirement="翻译这篇文档", minted=_MINTED, ts=99.0)
    assert p.proposal_id == q.proposal_id and p.proposal_id.startswith("confirm_result-")


def test_accept_keeps_via_role_judgment(tmp_path: Path):
    """ACCEPT → role 综合裁 keep=True → 留 + 入 role composition(人认可结果才发生,不是自动)。"""
    areg = AtomRegistry()
    areg.create("zh_en", "task", "把中文翻成英文", provisional=True, origin="self_created")
    rreg = RoleRegistry(tmp_path / "roles")
    rreg.create("译者", identity="中英翻译")
    app = _App(areg, rreg, FakeGateway('{"keep": true, "reason": "通用且过验"}'))
    handlers = build_proposal_handlers(app)
    card = proposal_for_confirm_result(role="译者", requirement="翻译", minted=_MINTED, ts=1.0)
    ok, detail = handlers[KIND_CONFIRM_RESULT](card)
    assert ok is True and "留下 1" in detail
    assert areg.get("zh_en") is not None and "zh_en" in rreg.get("译者").atom_ids


def test_accept_drops_when_role_judges_no(tmp_path: Path):
    """ACCEPT 但 role 综合裁 keep=False(太窄)→ 撤,不留。"""
    areg = AtomRegistry()
    areg.create("zh_en", "task", "把中文翻成英文", provisional=True, origin="self_created")
    rreg = RoleRegistry(tmp_path / "roles")
    rreg.create("译者", identity="中英翻译")
    app = _App(areg, rreg, FakeGateway('{"keep": false, "reason": "只为这一次,太窄"}'))
    handlers = build_proposal_handlers(app)
    card = proposal_for_confirm_result(role="译者", requirement="翻译", minted=_MINTED, ts=1.0)
    ok, _ = handlers[KIND_CONFIRM_RESULT](card)
    assert ok is True and areg.get("zh_en") is None  # 撤了


def test_accept_no_gateway_conservative_drop(tmp_path: Path):
    """无 gateway 无法综合判断 → 保守不留(宁缺毋污染),atom 留 provisional 待 ④ 巡检。"""
    areg = AtomRegistry()
    areg.create("zh_en", "task", "x", provisional=True, origin="self_created")
    rreg = RoleRegistry(tmp_path / "roles")
    rreg.create("译者", identity="r")
    app = _App(areg, rreg, None)
    handlers = build_proposal_handlers(app)
    card = proposal_for_confirm_result(role="译者", requirement="翻译", minted=_MINTED, ts=1.0)
    ok, _ = handlers[KIND_CONFIRM_RESULT](card)
    assert ok is True
    assert areg.get("zh_en") is None  # 保守不留 → sediment(approved=False) 撤孤儿
