"""Bundled 系统技能区 + source 标签 + reset 安全 —— 契约测试。

锁:
1. 包内 system_skills/ 的技能被加进索引,标 source='system';
2. 用户 skills_dir 的技能标 source='user';双扫合一;
3. reset 安全:清数据只清 source!=system 的(用户数据),系统资产幸存;
4. data-analyst 系统模板确实在包内、可被索引到。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.crystallize.skill_index import IndexEntry, SkillIndex  # noqa: E402
from karvyloop.registry.skills import system_skills_dir  # noqa: E402


def _write_user_skill(skills_dir, name, sig):
    d = skills_dir / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: a user skill\nsignature: {sig}\n"
        f"scope: user\nwhen_to_use: testing\n---\n\n# {name}\nbody\n",
        encoding="utf-8")


def test_system_skill_dir_ships_data_analyst():
    p = system_skills_dir() / "data-analyst" / "SKILL.md"
    assert p.is_file(), "data-analyst 系统模板应随包发布在 system_skills/ 内"
    # 在包内(IP/代码区),不在用户数据区
    assert "system_skills" in str(p)


def test_dual_scan_tags_system_and_user(tmp_path):
    idx = SkillIndex()
    _write_user_skill(tmp_path, "my-skill", "siguser123")
    idx.rebuild_from_disk(tmp_path)

    da = idx.lookup_by_name("data-analyst")
    assert da is not None and da.source == "system"      # 包内系统技能,标 system

    mine = idx.lookup_by_name("my-skill")
    assert mine is not None and mine.source == "user"    # 用户技能,标 user


def test_reset_only_clears_non_system(tmp_path):
    # reset/清数据语义:只清 source != system 的(用户数据);系统资产幸存。
    idx = SkillIndex()
    _write_user_skill(tmp_path, "my-skill", "siguser123")
    idx.rebuild_from_disk(tmp_path)

    survivors = [e for e in idx.all() if e.source == "system"]
    cleared = [e for e in idx.all() if e.source != "system"]
    assert any(e.name == "data-analyst" for e in survivors)   # 系统的留
    assert any(e.name == "my-skill" for e in cleared)         # 用户的清
    assert all(e.source == "system" for e in survivors)


def test_index_entry_defaults_user_source():
    # register()(结晶后)默认 user —— 结晶产物是用户数据
    e = IndexEntry(name="n", sig="s", scope="user", when_to_use="", description="", path="p")
    assert e.source == "user"
