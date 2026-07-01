"""test_role_skill_binding — 角色↔技能绑定(Hardy:agent/role 编写时要能直接引用 skill)。"""
from __future__ import annotations
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from karvyloop.roles.registry import RoleRegistry, UnknownSkillError  # noqa: E402
from karvyloop.crystallize.recall import recall, load_bound_skills  # noqa: E402


def _mk_skill(skills_dir: pathlib.Path, name: str, when: str = "", desc: str = "x"):
    d = skills_dir / name; d.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: {desc}\nwhen_to_use: {when}\nsignature: sig-{name}\n---\n# {name}\nbody-{name}\n"
    (d / "SKILL.md").write_text(fm, encoding="utf-8")


def test_role_declares_and_persists_skills(tmp_path):
    sk = tmp_path / "skills"; _mk_skill(sk, "brand-guide")
    reg = RoleRegistry(tmp_path / "roles", skills_dir=sk)
    v = reg.create("designer", identity="设计师", skill_ids=["brand-guide"])
    assert v.skill_ids == ["brand-guide"]
    # 重新读盘:绑定从 COMPOSITION.yaml 解析回来
    v2 = reg.get("designer")
    assert v2.skill_ids == ["brand-guide"]
    comp = (tmp_path / "roles" / "designer" / "COMPOSITION.yaml").read_text(encoding="utf-8")
    assert "skill: brand-guide" in comp


def test_bind_unknown_skill_rejected(tmp_path):
    sk = tmp_path / "skills"; _mk_skill(sk, "exists")
    reg = RoleRegistry(tmp_path / "roles", skills_dir=sk)
    try:
        reg.create("r", skill_ids=["ghost"])
        assert False, "应拒绝引用不存在的技能"
    except UnknownSkillError:
        pass


def test_update_skills_keeps_atoms(tmp_path):
    sk = tmp_path / "skills"; _mk_skill(sk, "a"); _mk_skill(sk, "b")
    reg = RoleRegistry(tmp_path / "roles", skills_dir=sk)
    reg.create("r", skill_ids=["a"])
    reg.update("r", skill_ids=["a", "b"])
    assert sorted(reg.get("r").skill_ids) == ["a", "b"]


def test_load_bound_skills_always_loads(tmp_path):
    sk = tmp_path / "skills"; _mk_skill(sk, "brand-guide")
    hits = load_bound_skills(["brand-guide", "missing"], skills_dir=sk)
    assert len(hits) == 1 and hits[0].name == "brand-guide" and hits[0].score == 1.0
    assert "body-brand-guide" in hits[0].body


def test_recall_prefer_flips_winner(tmp_path):
    # weak 命中 2 token(0.67),bound 命中 1 token(0.33)。无 prefer→weak 胜;
    # prefer=bound → bound +0.5=0.83 反超。证明绑定优先于碰运气(但仍走 scope/token)。
    sk = tmp_path / "skills"
    _mk_skill(sk, "weak", when="alpha beta", desc="zz")
    _mk_skill(sk, "bound", when="alpha", desc="zz")
    plain = recall("alpha beta gamma", skills_dir=sk)
    assert plain is not None and plain.name == "weak"
    boosted = recall("alpha beta gamma", skills_dir=sk, prefer=["bound"])
    assert boosted is not None and boosted.name == "bound"
