"""test_skill_sources — 可配置检索源(btw-2):增删改+开关,≥1开才存。"""
from __future__ import annotations
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from karvyloop.registry.skill_sources import SkillSources  # noqa: E402
from karvyloop.registry import skill_catalog as sc  # noqa: E402


def test_seeds_defaults_when_empty(tmp_path):
    s = SkillSources(tmp_path / "skill_sources.json")
    ids = {x["id"] for x in s.list()}
    assert "official" in ids and "skillsmp" in ids


def test_save_requires_at_least_one_enabled(tmp_path):
    s = SkillSources(tmp_path / "skill_sources.json")
    ok, reason = s.save([
        {"id": "official", "type": "github", "repo": "anthropics/skills", "enabled": False},
        {"id": "skillsmp", "type": "skillsmp", "enabled": False}])
    assert not ok and "至少" in reason


def test_save_rejects_bad_github_repo(tmp_path):
    s = SkillSources(tmp_path / "skill_sources.json")
    ok, _ = s.save([{"id": "x", "type": "github", "repo": "not a repo", "enabled": True}])
    assert not ok


def test_save_roundtrip_and_toggle(tmp_path):
    p = tmp_path / "skill_sources.json"
    s = SkillSources(p)
    ok, _ = s.save([
        {"id": "official", "type": "github", "repo": "anthropics/skills", "root": "skills", "enabled": True},
        {"id": "skillsmp", "type": "skillsmp", "enabled": False}])
    assert ok
    s2 = SkillSources(p)
    en = {x["id"] for x in s2.enabled()}
    assert en == {"official"}   # 只 official 开


def test_dup_id_rejected(tmp_path):
    s = SkillSources(tmp_path / "skill_sources.json")
    ok, reason = s.save([
        {"id": "a", "type": "skillsmp", "enabled": True},
        {"id": "a", "type": "skillsmp", "enabled": True}])
    assert not ok and "重复" in reason


def test_catalog_honors_configured_sources():
    # 只配一个自定义 github 源 → 只走它
    import json
    LIST = json.dumps([{"name": "foo", "type": "dir", "path": "skills/foo"}]).encode()
    MD = b"---\nname: foo\ndescription: a foo skill\n---\n# foo\n"
    def fake(url):
        if "api.github.com/repos/me/myskills" in url: return LIST
        if url.endswith("SKILL.md"): return MD
        raise AssertionError(url)
    srcs = [{"id": "mine", "type": "github", "repo": "me/myskills", "root": "skills", "ref": "main", "enabled": True}]
    entries = sc.search_catalog("foo", sources=srcs, fetch=fake)
    assert len(entries) == 1 and entries[0].name == "foo" and entries[0].source == "me/myskills/skills/foo"
