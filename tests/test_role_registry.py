"""角色库验收(P1,拍 9.5 #3)。

角色镜像 = agent 目录(7 文件 + COMPOSITION.yaml),沿用 adapter/validator 格式(不另起炉灶)。
关键:物化出来的目录必须**过现有 validator + auditor**(证明是真镜像,不是平行格式)。
甲:COMPOSITION 引的原子必须在公共原子库。
"""
from __future__ import annotations

import pytest

from karvyloop.atoms.registry import AtomRegistry
from karvyloop.roles.registry import (
    SLOT_NAMES,
    DuplicateRoleError,
    RoleRegistry,
    UnknownAtomError,
)


def _atoms_with(*ids):
    reg = AtomRegistry()
    for i in ids:
        reg.create(i, "task", f"{i} 干活")
    return reg


def test_create_materializes_7_files(tmp_path):
    reg = RoleRegistry(tmp_path / "roles")
    v = reg.create("pm", identity="我是产品经理", atom_ids=[])
    d = v.path
    for slot in SLOT_NAMES:
        fname = "COMPOSITION.yaml" if slot == "COMPOSITION" else f"{slot}.md"
        assert (d / fname).exists(), f"缺 {fname}"
    assert "我是产品经理" in (d / "IDENTITY.md").read_text(encoding="utf-8")


def test_create_writes_identity_and_soul(tmp_path):
    """9.5:IDENTITY + SOUL 由用户填,物化进对应文件;其余仍 stub。"""
    reg = RoleRegistry(tmp_path / "roles")
    v = reg.create("pm", identity="我是产品经理", soul="佛系、用户至上")
    assert "我是产品经理" in (v.path / "IDENTITY.md").read_text(encoding="utf-8")
    assert "佛系、用户至上" in (v.path / "SOUL.md").read_text(encoding="utf-8")
    assert "待充实" in (v.path / "USER.md").read_text(encoding="utf-8")  # 其余仍 stub


def test_composition_has_step_id_and_atom_refs(tmp_path):
    atoms = _atoms_with("web_search", "prd_writer")
    reg = RoleRegistry(tmp_path / "roles", atom_registry=atoms)
    v = reg.create("pm", identity="PM", atom_ids=["web_search", "prd_writer"])
    comp = (v.path / "COMPOSITION.yaml").read_text(encoding="utf-8")
    assert "step_id: COMPOSITION" in comp
    assert "atom: web_search" in comp and "atom: prd_writer" in comp


def test_materialized_role_passes_existing_validator(tmp_path):
    """物化的目录必须过 adapter 的 Paradigm Loader 烟测(7 文件齐 + COMPOSITION 头)。"""
    from karvyloop.adapter.validator import _default_loader
    reg = RoleRegistry(tmp_path / "roles")
    v = reg.create("pm", identity="PM", atom_ids=[])
    _, errs = _default_loader(str(v.path))
    assert errs == (), f"validator 报错: {errs}"


def test_unknown_atom_rejected(tmp_path):
    """甲:挑的原子不在公共库 → 拦(先买糖)。"""
    atoms = _atoms_with("web_search")
    reg = RoleRegistry(tmp_path / "roles", atom_registry=atoms)
    with pytest.raises(UnknownAtomError):
        reg.create("pm", atom_ids=["nonexistent_atom"])


def test_get_reads_back_atoms_and_identity(tmp_path):
    atoms = _atoms_with("a", "b")
    reg = RoleRegistry(tmp_path / "roles", atom_registry=atoms)
    reg.create("eng", identity="我是工程师", atom_ids=["a", "b"])
    v = reg.get("eng")
    assert v is not None
    assert v.identity == "我是工程师"
    assert set(v.atom_ids) == {"a", "b"}


def test_duplicate_role_rejected(tmp_path):
    reg = RoleRegistry(tmp_path / "roles")
    reg.create("pm")
    with pytest.raises(DuplicateRoleError):
        reg.create("pm")


def test_list_and_remove(tmp_path):
    reg = RoleRegistry(tmp_path / "roles")
    reg.create("pm")
    reg.create("eng")
    assert len(reg) == 2
    assert {v.id for v in reg.list_all()} == {"pm", "eng"}
    assert reg.remove("pm") is True
    assert len(reg) == 1
    assert reg.remove("pm") is False


def test_persisted_across_registry_instances(tmp_path):
    """角色目录本身是持久态 → 新 registry 实例读得到(§2.1)。"""
    root = tmp_path / "roles"
    RoleRegistry(root).create("pm", identity="PM")
    reg2 = RoleRegistry(root)
    assert reg2.get("pm") is not None


def test_bad_role_id_rejected(tmp_path):
    reg = RoleRegistry(tmp_path / "roles")
    with pytest.raises(ValueError):
        reg.create("")
    with pytest.raises(ValueError):
        reg.create("bad name with spaces")


# ============ brick4:花名/职务(身份模型)============
def test_role_nickname_title_roundtrip_and_display(tmp_path):
    from karvyloop.roles.registry import RoleRegistry
    reg = RoleRegistry(tmp_path / "roles")
    reg.create("designer", identity="设计师", nickname="张三", title="产品经理")
    # 重新读(走 profile.json)
    rv = reg.get("designer")
    assert rv is not None
    assert rv.nickname == "张三" and rv.title == "产品经理"
    assert rv.display_name() == "张三(产品经理)"          # 哟吼/张三(产品经理) 那种
    assert rv.to_dict()["nickname"] == "张三"


def test_role_display_name_falls_back_to_id(tmp_path):
    from karvyloop.roles.registry import RoleRegistry
    reg = RoleRegistry(tmp_path / "roles")
    reg.create("dba")                                     # 没花名/职务
    rv = reg.get("dba")
    assert rv.display_name() == "dba"                     # 回退 role_id
    assert rv.nickname == "" and rv.title == ""


# ---- 角色级模型(role 是 agent 特例,可单独配;空=默认)----
def test_role_model_persists(tmp_path):
    from karvyloop.roles.registry import RoleRegistry
    reg = RoleRegistry(tmp_path / "roles")
    v = reg.create("设计师", identity="设计", model="minimax/MiniMax-M3")
    assert v.model == "minimax/MiniMax-M3"
    # 重读保真
    again = reg.get("设计师")
    assert again.model == "minimax/MiniMax-M3"
    # 不配 → 空(层叠到默认)
    v2 = reg.create("产品", identity="产品")
    assert v2.model == "" and reg.get("产品").model == ""
