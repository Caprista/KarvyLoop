"""test_atom_consolidate — 原子语义合并(docs/14 §11.2,护城河).

验:① parse 宁空勿毒(编造成员/不足2个/坏JSON 丢)② apply rewire-before-delete(先改角色引用再删冗余,
绝不留悬空引用)③ suggest 假 gateway 走通。
"""
from __future__ import annotations

import asyncio

from karvyloop.atoms.consolidate import apply_merge, parse_clusters, suggest_consolidation
from karvyloop.atoms.registry import AtomRegistry, AtomStore
from karvyloop.roles.registry import RoleRegistry


class TextDelta:
    def __init__(self, text): self.text = text


class FakeGateway:
    def __init__(self, payload): self._p = payload
    def resolve_model(self, scope): return "fake"  # noqa: ANN001
    async def complete(self, messages, tools, ref, system=None):  # noqa: ANN001
        yield TextDelta(self._p)


# ---- parse 宁空勿毒 ----
def test_parse_keeps_real_clusters_drops_fabricated():
    valid = {"market_research", "market_survey", "pricing"}
    txt = ('{"clusters":[{"canonical_id":"market_research","member_ids":["market_research","market_survey"],'
           '"merged_purpose":"做市场调研","merged_tools":["web_search"],"reason":"同一件事"},'
           '{"canonical_id":"ghost","member_ids":["ghost_a","ghost_b"],"merged_purpose":"编的"}]}')
    cs = parse_clusters(txt, valid)
    assert len(cs) == 1, "编造成员的簇没被丢"
    assert cs[0]["canonical_id"] == "market_research"
    assert set(cs[0]["member_ids"]) == {"market_research", "market_survey"}


def test_parse_drops_single_member_and_garbage():
    valid = {"a", "b"}
    assert parse_clusters('{"clusters":[{"canonical_id":"a","member_ids":["a"]}]}', valid) == []  # <2
    assert parse_clusters("就这俩差不多", valid) == []                                            # prose
    assert parse_clusters('{"clusters":[]}', valid) == []
    # 成员含假的 → 只留真的,真的<2 → 丢
    assert parse_clusters('{"clusters":[{"canonical_id":"a","member_ids":["a","fake"]}]}', valid) == []


# ---- suggest(假 gateway)----
def test_suggest_with_fake_gateway():
    atoms = [type("A", (), {"id": "market_research", "prompt": "市场调研"})(),
             type("A", (), {"id": "market_survey", "prompt": "做市场调查"})()]
    gw = FakeGateway('{"clusters":[{"canonical_id":"market_research",'
                     '"member_ids":["market_research","market_survey"],"merged_purpose":"市场调研"}]}')
    cs = asyncio.run(suggest_consolidation(atoms, gateway=gw))
    assert len(cs) == 1 and cs[0]["canonical_id"] == "market_research"


# ---- apply:rewire-before-delete,无悬空引用 ----
def _setup(tmp):
    areg = AtomRegistry(store=AtomStore(tmp / "atoms.json"))
    rreg = RoleRegistry(tmp / "roles", atom_registry=areg)
    for aid in ("market_research", "market_survey", "pricing"):
        areg.create(aid, "task", aid)
    rreg.create("pm", identity="产品经理", atom_ids=["market_research", "pricing"])
    rreg.create("cmo", identity="市场总监", atom_ids=["market_survey", "pricing"])
    return areg, rreg


def test_apply_merge_rewires_then_deletes_no_dangling(tmp_path):
    areg, rreg = _setup(tmp_path)
    r = apply_merge("market_research", ["market_research", "market_survey"],
                    merged_purpose="市场调研", atom_registry=areg, role_registry=rreg)
    assert r["ok"] and "cmo" in r["rewired_roles"] and r["removed_atoms"] == ["market_survey"]
    # 冗余原子真删了
    assert areg.get("market_survey") is None and areg.get("market_research") is not None
    # **没有任何角色还引着被删的 market_survey**(无悬空引用)
    for role in rreg.list_all():
        assert "market_survey" not in role.atom_ids, f"{role.id} 还悬空引着 market_survey"
    # cmo 现在引的是规范原子 + pricing 不动
    cmo = rreg.get("cmo")
    assert "market_research" in cmo.atom_ids and "pricing" in cmo.atom_ids and "market_survey" not in cmo.atom_ids
    # pm 不变(本就有 market_research,去重无新增)
    assert sorted(rreg.get("pm").atom_ids) == ["market_research", "pricing"]


def test_apply_merge_creates_new_canonical_if_absent(tmp_path):
    areg, rreg = _setup(tmp_path)
    r = apply_merge("market_intel", ["market_research", "market_survey"],
                    merged_purpose="市场情报", atom_registry=areg, role_registry=rreg)
    assert r["ok"] and areg.get("market_intel") is not None
    # 两个旧的都删了、都被改写到新规范
    assert areg.get("market_research") is None and areg.get("market_survey") is None
    for role in rreg.list_all():
        assert all(a not in role.atom_ids for a in ("market_research", "market_survey"))


def test_apply_merge_refuses_under_two_real(tmp_path):
    areg, rreg = _setup(tmp_path)
    r = apply_merge("x", ["market_research", "ghost"], atom_registry=areg, role_registry=rreg)
    assert r["ok"] is False and areg.get("market_research") is not None  # 没动
