"""docs/14 §11.2 / docs/02 §15.5 — 原子语义合并接提案流:建议成卡,ACCEPT 才真合并。

不变量:① 合并**不静默**(suggest=卡,apply=人拍 ACCEPT 触发)② ACCEPT 走 rewire-before-delete
(先改角色引用再删,无悬空)③ 幂等(同一簇收敛一张卡)④ 成员被先前合并吃掉(<2 真实存在)→ 不动、如实回执。
"""

from __future__ import annotations

from karvyloop.karvy.proposal_registry import (
    ALL_KINDS,
    KIND_MERGE_ATOMS,
    proposal_for_merge_atoms,
)
from karvyloop.console.proposal_handlers import build_proposal_handlers


def test_kind_registered():
    assert KIND_MERGE_ATOMS in ALL_KINDS


def test_card_basis_is_honest_about_rewire_before_delete():
    # 卡文案走 i18n(按当前 locale 定稿)→ 锁 zh 断言中文原文;reason 是数据 locale 无关
    from karvyloop import i18n
    try:
        i18n.set_locale("zh")
        p = proposal_for_merge_atoms(
            canonical_id="web_search", member_ids=["web_search", "search_web", "do_web_search"],
            reason="三者都是网页检索", ts=1.0)
    finally:
        i18n.set_locale(None)
    assert p.kind == KIND_MERGE_ATOMS
    assert "rewire-before-delete" in p.basis
    assert "不留悬空引用" in p.basis
    assert "三者都是网页检索" in p.basis           # reason 进 basis
    assert p.payload["canonical_id"] == "web_search"
    assert p.payload["member_ids"] == ["web_search", "search_web", "do_web_search"]


def test_card_id_idempotent_same_cluster():
    a = proposal_for_merge_atoms(canonical_id="c", member_ids=["a", "b"], ts=1.0)
    b = proposal_for_merge_atoms(canonical_id="c", member_ids=["b", "a"], ts=99.0)  # 顺序无关
    assert a.proposal_id == b.proposal_id
    assert a.proposal_id.startswith("merge_atoms-")
    c = proposal_for_merge_atoms(canonical_id="c", member_ids=["a", "x"], ts=1.0)
    assert c.proposal_id != a.proposal_id


# ---- 假 registry:验 ACCEPT 真走 rewire-before-delete ----

class _FakeAtom:
    def __init__(self, aid):
        self.id = aid


class _FakeAtomReg:
    def __init__(self, ids):
        self._atoms = {i: _FakeAtom(i) for i in ids}
        self.created = []

    def get(self, aid):
        return self._atoms.get(aid)

    def create(self, aid, kind, purpose, tools=None):
        self._atoms[aid] = _FakeAtom(aid)
        self.created.append(aid)

    def remove(self, aid):
        return self._atoms.pop(aid, None) is not None


class _FakeRole:
    def __init__(self, rid, atom_ids):
        self.id = rid
        self.atom_ids = list(atom_ids)


class _FakeRoleReg:
    def __init__(self, roles):
        self._roles = {r.id: r for r in roles}
        self.rewire_order = []

    def list_all(self):
        return list(self._roles.values())

    def rewrite_atom_refs(self, role_id, mapping):
        r = self._roles[role_id]
        new = [mapping.get(a, a) for a in r.atom_ids]
        new = list(dict.fromkeys(new))
        changed = new != r.atom_ids
        r.atom_ids = new
        if changed:
            self.rewire_order.append(role_id)
        return changed


class _AppState:
    pass


class _App:
    def __init__(self, areg, rreg):
        self.state = _AppState()
        self.state.atom_registry = areg
        self.state.role_registry = rreg


def test_accept_merges_via_rewire_before_delete():
    """ACCEPT → apply_merge:角色引用先改写到 canonical,冗余原子才删,无悬空。"""
    areg = _FakeAtomReg(["web_search", "search_web", "do_web_search"])
    rA = _FakeRole("analyst", ["search_web", "summarize"])
    rB = _FakeRole("writer", ["do_web_search"])
    rreg = _FakeRoleReg([rA, rB])
    handlers = build_proposal_handlers(_App(areg, rreg))
    assert KIND_MERGE_ATOMS in handlers

    card = proposal_for_merge_atoms(
        canonical_id="web_search",
        member_ids=["web_search", "search_web", "do_web_search"], ts=1.0)
    ok, detail = handlers[KIND_MERGE_ATOMS](card)

    assert ok is True
    # 角色引用已改写到 canonical
    assert "web_search" in rA.atom_ids and "search_web" not in rA.atom_ids
    assert rB.atom_ids == ["web_search"]
    # 冗余原子已删,canonical 还在(无悬空)
    assert areg.get("search_web") is None and areg.get("do_web_search") is None
    assert areg.get("web_search") is not None
    assert "改写 2 个角色引用" in detail and "删 2 个冗余原子" in detail


def test_accept_no_op_when_members_already_gone():
    """成员被先前合并吃掉(真实存在 < 2)→ apply_merge ok=False,如实回执,不假装。"""
    areg = _FakeAtomReg(["web_search"])  # 另外两个已不在
    rreg = _FakeRoleReg([])
    handlers = build_proposal_handlers(_App(areg, rreg))
    card = proposal_for_merge_atoms(
        canonical_id="web_search", member_ids=["web_search", "gone1", "gone2"], ts=1.0)
    ok, detail = handlers[KIND_MERGE_ATOMS](card)
    assert ok is False
    assert "未合并" in detail


def test_accept_guards_incomplete_plan():
    areg = _FakeAtomReg(["a"])
    handlers = build_proposal_handlers(_App(areg, _FakeRoleReg([])))
    card = proposal_for_merge_atoms(canonical_id="a", member_ids=["a"], ts=1.0)  # 成员<2
    ok, detail = handlers[KIND_MERGE_ATOMS](card)
    assert ok is False
