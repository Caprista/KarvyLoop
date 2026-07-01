"""test_skill_catalog — 技能目录浏览(P1-b):官方 GitHub + 市场 SkillsMP,注入 fetch 不触网。"""
from __future__ import annotations
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from karvyloop.registry import skill_catalog as sc  # noqa: E402

OFFICIAL_LIST = json.dumps([
    {"name": "pdf", "type": "dir", "path": "skills/pdf"},
    {"name": "xlsx", "type": "dir", "path": "skills/xlsx"},
    {"name": "README.md", "type": "file", "path": "skills/README.md"},
]).encode()
SKILL_MD = b"---\nname: pdf\ndescription: Fill and read PDF forms\n---\n# pdf\n"

MARKET = json.dumps({"success": True, "data": {"skills": [
    {"id": "x", "name": "seo-audit", "author": "alice",
     "description": "audit a site for SEO",
     "githubUrl": "https://github.com/alice/seo/tree/main/skills/seo-audit", "stars": 42},
    {"id": "y", "name": "no-source", "description": "skip me"},  # 无 githubUrl → 跳过
]}, "meta": {}}).encode()


def test_browse_official_lists_skill_dirs(monkeypatch):
    def fake(url):
        if "contents/skills" in url: return OFFICIAL_LIST
        if url.endswith("SKILL.md"): return SKILL_MD
        raise AssertionError(url)
    entries = sc.browse_official(fetch=fake)
    names = {e.name: e for e in entries}
    assert "pdf" in names and "xlsx" in names and "README.md" not in names  # 文件不算技能
    assert names["pdf"].origin == "official"
    assert names["pdf"].source == "anthropics/skills/skills/pdf"
    assert "PDF" in names["pdf"].description


def test_search_marketplace_maps_githuburl_as_source():
    entries = sc.search_marketplace("seo", fetch=lambda u: MARKET)
    assert len(entries) == 1  # 无 githubUrl 那条被跳
    e = entries[0]
    assert e.name == "seo-audit" and e.origin == "market" and e.stars == 42
    assert e.source == "https://github.com/alice/seo/tree/main/skills/seo-audit"  # 直接可喂 importer


def test_search_marketplace_empty_query_returns_nothing():
    assert sc.search_marketplace("", fetch=lambda u: MARKET) == []


def test_search_catalog_merges_both():
    def fake(url):
        if "api.github.com" in url: return OFFICIAL_LIST
        if url.endswith("SKILL.md"): return SKILL_MD
        if "skillsmp" in url: return MARKET
        raise AssertionError(url)
    # query "pdf" 命中官方 pdf;市场 mock 不依赖 query → 两源都出
    entries = sc.search_catalog("pdf", source="all", fetch=fake)
    origins = {e.origin for e in entries}
    assert "official" in origins and "market" in origins


def test_network_failure_returns_empty_not_crash():
    def boom(url): raise OSError("offline")
    assert sc.browse_official(fetch=boom) == []
    assert sc.search_marketplace("x", fetch=boom) == []
