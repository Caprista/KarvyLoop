"""docs/02 §15.5 — 临时原子生命周期:被复用的转正,孤儿撤回(护城河自清洁)。

不变量:① 只动 provisional 原子,正式原子永不碰 ② 被 ≥1 角色引用 → confirm(转正)
③ 孤儿(0 引用)→ revert(删,0 引用故无悬空,安全)④ 合并新生的规范原子 born provisional。
"""

from __future__ import annotations

from pathlib import Path

from karvyloop.atoms.registry import AtomRegistry, AtomStore
from karvyloop.atoms.provisional import review_provisional


class _FakeRole:
    def __init__(self, rid, atom_ids):
        self.id = rid
        self.atom_ids = list(atom_ids)


class _FakeRoleReg:
    def __init__(self, roles):
        self._roles = list(roles)

    def list_all(self):
        return list(self._roles)


def _areg(tmp_path: Path) -> AtomRegistry:
    return AtomRegistry(store=AtomStore(tmp_path / "atoms.json"))


def test_create_provisional_and_origin(tmp_path):
    areg = _areg(tmp_path)
    a = areg.create("p1", "task", "x", provisional=True, origin="merge")
    assert a.provisional is True and a.origin == "merge"
    b = areg.create("normal", "task", "y")  # 默认正式
    assert b.provisional is False and b.origin == ""


def test_provisional_referenced_gets_confirmed(tmp_path):
    areg = _areg(tmp_path)
    areg.create("used", "task", "x", provisional=True, origin="merge")
    rreg = _FakeRoleReg([_FakeRole("analyst", ["used", "other"])])
    res = review_provisional(areg, rreg)
    assert res["confirmed"] == ["used"] and res["reverted"] == []
    assert areg.get("used").provisional is False        # 转正,留库


def test_provisional_orphan_gets_reverted(tmp_path):
    areg = _areg(tmp_path)
    areg.create("orphan", "task", "x", provisional=True, origin="merge")
    rreg = _FakeRoleReg([_FakeRole("analyst", ["something_else"])])  # 没人引 orphan
    res = review_provisional(areg, rreg)
    assert res["reverted"] == ["orphan"] and res["confirmed"] == []
    assert areg.get("orphan") is None                   # 孤儿被删


def test_formal_atoms_never_touched(tmp_path):
    """正式原子(provisional=False)即使没人引用也**绝不**被巡检删 —— 只清临时原子。"""
    areg = _areg(tmp_path)
    areg.create("formal_unused", "task", "x")  # 正式、0 引用
    rreg = _FakeRoleReg([])
    res = review_provisional(areg, rreg)
    assert res == {"confirmed": [], "reverted": []}
    assert areg.get("formal_unused") is not None        # 正式原子安然无恙


def test_confirm_is_idempotent_and_only_provisional(tmp_path):
    areg = _areg(tmp_path)
    areg.create("a", "task", "x", provisional=True, origin="inward")
    assert areg.confirm("a") is True
    assert areg.confirm("a") is False                   # 已转正,再调无改动
    assert areg.confirm("missing") is False


def test_merge_canonical_born_provisional(tmp_path):
    """③ apply_merge 新建的规范原子 born provisional(靠后续复用挣身份)。"""
    from karvyloop.atoms.consolidate import apply_merge

    areg = _areg(tmp_path)
    areg.create("search_web", "task", "web search")
    areg.create("do_web_search", "task", "web search too")

    class _RoleReg:
        def __init__(self, roles):
            self._roles = {r.id: r for r in roles}

        def list_all(self):
            return list(self._roles.values())

        def rewrite_atom_refs(self, rid, mapping):
            r = self._roles[rid]
            new = list(dict.fromkeys(mapping.get(a, a) for a in r.atom_ids))
            changed = new != r.atom_ids
            r.atom_ids = new
            return changed

    rreg = _RoleReg([_FakeRole("analyst", ["search_web"]), _FakeRole("writer", ["do_web_search"])])
    res = apply_merge("web_search", ["search_web", "do_web_search"], merged_purpose="网页检索",
                      atom_registry=areg, role_registry=rreg)
    assert res["ok"] is True
    canon = areg.get("web_search")
    assert canon is not None and canon.provisional is True and canon.origin == "merge"
    # 它被 2 个角色引用(rewire 后)→ 下次巡检会转正
    review = review_provisional(areg, rreg)
    assert "web_search" in review["confirmed"]
    assert areg.get("web_search").provisional is False


def test_round_trip_persists_provisional(tmp_path):
    """provisional/origin 要熬过 atoms.json 持久化往返(不然重启就丢)。"""
    p = tmp_path / "atoms.json"
    a1 = AtomRegistry(store=AtomStore(p))
    a1.create("p", "task", "x", provisional=True, origin="merge")
    a2 = AtomRegistry(store=AtomStore(p))  # 重新从盘加载
    got = a2.get("p")
    assert got is not None and got.provisional is True and got.origin == "merge"
