"""test_skill_disable — 技能「停用」开关(借业界 harness 的 disabled 语义)。

语义:用户主动停用 = 在册但不被召回(recall)/不随角色绑定加载(load_bound_skills);
与归档(evict)不同:归档命中会自动复活,停用不许越过。写 SKILL.md frontmatter
`disabled: true`(范式可见可编,手改文件也生效)。

AC:
- AC1: set_skill_disabled 写入/移除 frontmatter 行,其余字段不动;skill_is_disabled 读取
- AC2: recall 跳过 disabled(索引路径 + 扫盘兜底路径)
- AC3: load_bound_skills 跳过 disabled
- AC4: /api/skill/disable 端点翻转 + /api/skills 列表带 disabled 标
- AC5: 无 frontmatter 的坏卡拒写(fail-closed)
"""
from __future__ import annotations

import time
from pathlib import Path

from karvyloop.registry.skills import skill_is_disabled, set_skill_disabled

_SKILL = """---
name: demo-skill
description: 演示技能
when_to_use: 做演示的时候
signature: sig-demo-1
---
# 方法
1. 先这样
"""


def _write_skill(root: Path, name: str, text: str = _SKILL) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(text, encoding="utf-8")
    return p


# ---- AC1: 读写 ----
def test_set_and_read_disabled(tmp_path):
    p = _write_skill(tmp_path, "demo-skill")
    assert skill_is_disabled({}) is False
    assert set_skill_disabled(p, True)
    text = p.read_text(encoding="utf-8")
    assert "disabled: true" in text
    assert "name: demo-skill" in text   # 其余字段不动
    assert "# 方法" in text             # body 不动
    from karvyloop.registry.skills import parse_frontmatter
    fm, _ = parse_frontmatter(p)
    assert skill_is_disabled(fm.raw) is True
    # 恢复 = 移除该行
    assert set_skill_disabled(p, False)
    fm2, _ = parse_frontmatter(p)
    assert skill_is_disabled(fm2.raw) is False


def test_set_disabled_refuses_no_frontmatter(tmp_path):
    p = _write_skill(tmp_path, "bare", "# 无 frontmatter 的坏卡\n")
    assert set_skill_disabled(p, True) is False


# ---- AC2: recall 跳过 ----
def test_recall_skips_disabled(tmp_path):
    from karvyloop.crystallize.recall import recall
    p = _write_skill(tmp_path, "demo-skill")
    # 意图与 when_to_use("做演示的时候")共享 ≥2 个 CJK bigram,过召回的 CJK 门
    q = "做演示的时候到了"
    # 未停用 → 能召回
    hit = recall(q, skills_dir=tmp_path)
    assert hit is not None and hit.name == "demo-skill"
    # 停用 → 召回为空(不复活)
    set_skill_disabled(p, True)
    assert recall(q, skills_dir=tmp_path) is None
    # 恢复 → 又能召回
    set_skill_disabled(p, False)
    assert recall(q, skills_dir=tmp_path) is not None


# ---- AC3: 绑定加载跳过 ----
def test_load_bound_skills_skips_disabled(tmp_path):
    from karvyloop.crystallize.recall import load_bound_skills
    p = _write_skill(tmp_path, "demo-skill")
    got = load_bound_skills(["demo-skill"], skills_dir=tmp_path)
    assert len(got) == 1
    set_skill_disabled(p, True)
    assert load_bound_skills(["demo-skill"], skills_dir=tmp_path) == []


# ---- AC4: 端点 ----
def test_disable_endpoint_and_listing(tmp_path):
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.crystallize.skill_index import SkillIndex

    p = _write_skill(tmp_path, "demo-skill")
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)

    class _ML:
        pass
    ml = _ML()
    ml.skill_index = SkillIndex()
    ml.skill_index.rebuild_from_disk(tmp_path)
    ml.store = None
    ml.skills_dir = tmp_path
    app.state.main_loop = ml

    client = TestClient(app)
    r = client.get("/api/skills")
    skills = r.json()["skills"]
    assert any(s["name"] == "demo-skill" and s["disabled"] is False for s in skills)

    r = client.post("/api/skill/disable", json={"name": "demo-skill", "disabled": True})
    assert r.json()["ok"] is True
    assert "disabled: true" in p.read_text(encoding="utf-8")

    skills = client.get("/api/skills").json()["skills"]
    assert any(s["name"] == "demo-skill" and s["disabled"] is True for s in skills)

    r = client.post("/api/skill/disable", json={"name": "demo-skill", "disabled": False})
    assert r.json()["ok"] is True
    skills = client.get("/api/skills").json()["skills"]
    assert any(s["name"] == "demo-skill" and s["disabled"] is False for s in skills)

    # 不存在的技能 → ok False
    r = client.post("/api/skill/disable", json={"name": "ghost", "disabled": True})
    assert r.json()["ok"] is False
