"""test_skill_tags — 技能语义标签层(P3-c:三层匹配的语义层,LLM 打一次,无向量)。

不变量:① frontmatter `tags:` 解析进 SkillFm/IndexEntry ② 召回/建议把 tags 并进匹配集
(词面 miss、标签 hit → 命中)③ daily 回填:只补没标签的自家技能,untrusted 跳过(护完整性锁),
已有 tags 不覆盖,抽空记冷却 ④ 解析失败宁空勿毒(concepts 层已锁,此处锁写入端)。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.registry.skills import parse_frontmatter  # noqa: E402


def _write_skill(dir_, name, *, desc="发周报", when="每周一汇总工作发给团队", tags=None, extra=""):
    d = dir_ / name
    d.mkdir(parents=True, exist_ok=True)
    tag_line = f"tags: [{', '.join(tags)}]\n" if tags else ""
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\nwhen_to_use: {when}\n"
        f"signature: sig-{name}\n{tag_line}{extra}---\n# body\n", encoding="utf-8")
    return d


def test_tags_parse_and_index(tmp_path):
    _write_skill(tmp_path, "weekly", tags=["报告", "自动化"])
    fm, _ = parse_frontmatter(tmp_path / "weekly" / "SKILL.md")
    assert fm.tags == ["报告", "自动化"]
    # 无 tags → 空列表(旧 SKILL.md 兼容)
    _write_skill(tmp_path, "old")
    fm2, _ = parse_frontmatter(tmp_path / "old" / "SKILL.md")
    assert fm2.tags == []
    # 进索引
    from karvyloop.crystallize.skill_index import SkillIndex
    idx = SkillIndex()
    idx.rebuild_from_disk(tmp_path)
    assert idx._by_name["weekly"].tags == ("报告", "自动化")


def test_recall_hits_via_tags(tmp_path):
    """词面 miss(when/desc 都不含 intent 词)、语义标签 hit → 召回命中。"""
    from karvyloop.crystallize.recall import recall
    _write_skill(tmp_path, "weekly", desc="发周报", when="每周一汇总",
                 tags=["report", "automation"])
    # intent 用英文 token,与 when/desc(中文短语,词面 token 化后不含)不重叠,只撞 tags
    hit = recall("automation report please", skills_dir=tmp_path, scope="user")
    assert hit is not None and hit.name == "weekly"
    # 无标签时同 intent 召回不到(反证:命中确实来自标签层)
    (tmp_path / "weekly" / "SKILL.md").unlink()
    _write_skill(tmp_path, "weekly2", desc="发周报", when="每周一汇总")
    assert recall("automation report please", skills_dir=tmp_path, scope="user") is None


def test_tags_tick_backfills_only_untagged_own(tmp_path):
    from karvyloop.console.skill_tags_tick import skill_tags_tick
    sd = tmp_path / "skills"
    _write_skill(sd, "untagged")                                   # 该补
    _write_skill(sd, "tagged", tags=["已有"])                       # 不动
    _write_skill(sd, "third", extra="trust: untrusted\n")          # untrusted 跳过(护锁)

    class _GW:
        pass

    class _State:
        runtime_kwargs = {"gateway": _GW(), "model_ref": ""}
        main_loop = None

    class _App:
        state = _State()

    async def fake_extract(contents, *, gateway, model_ref=""):
        return [["周报", "汇总"] for _ in contents]

    import karvyloop.console.skill_tags_tick as mod
    import karvyloop.cognition.concepts as concepts_mod
    orig = concepts_mod.extract_concepts_batch
    concepts_mod.extract_concepts_batch = fake_extract
    try:
        res = asyncio.run(skill_tags_tick(_App(), skills_dir=sd,
                                          state_path=tmp_path / "st.json"))
    finally:
        concepts_mod.extract_concepts_batch = orig
    assert res["ran"] and res["tagged"] == 1
    fm, _ = parse_frontmatter(sd / "untagged" / "SKILL.md")
    assert fm.tags == ["周报", "汇总"]
    fm_t, _ = parse_frontmatter(sd / "tagged" / "SKILL.md")
    assert fm_t.tags == ["已有"]                                    # 不覆盖
    fm_3, _ = parse_frontmatter(sd / "third" / "SKILL.md")
    assert fm_3.tags == []                                          # untrusted 未动
    # 第二轮:全打过/跳过 → watermark,零 LLM
    res2 = asyncio.run(skill_tags_tick(_App(), skills_dir=sd,
                                       state_path=tmp_path / "st.json"))
    assert not res2["ran"] and "watermark" in res2["reason"]


def test_tags_tick_empty_result_cooldown(tmp_path):
    """LLM 抽空 → 记冷却,窗口内第二轮不再烧(不反复骚扰同一个)。"""
    from karvyloop.console.skill_tags_tick import skill_tags_tick
    sd = tmp_path / "skills"
    _write_skill(sd, "hollow")
    calls = {"n": 0}

    async def empty_extract(contents, *, gateway, model_ref=""):
        calls["n"] += 1
        return [[] for _ in contents]

    class _State:
        runtime_kwargs = {"gateway": object(), "model_ref": ""}
        main_loop = None

    class _App:
        state = _State()

    import karvyloop.cognition.concepts as concepts_mod
    orig = concepts_mod.extract_concepts_batch
    concepts_mod.extract_concepts_batch = empty_extract
    try:
        r1 = asyncio.run(skill_tags_tick(_App(), skills_dir=sd, state_path=tmp_path / "st.json"))
        r2 = asyncio.run(skill_tags_tick(_App(), skills_dir=sd, state_path=tmp_path / "st.json"))
    finally:
        concepts_mod.extract_concepts_batch = orig
    assert r1["ran"] and r1["tagged"] == 0
    assert calls["n"] == 1                       # 第二轮冷却,没再调 LLM
    assert not r2["ran"]


def test_inject_tags_idempotent(tmp_path):
    from karvyloop.console.skill_tags_tick import inject_tags
    _write_skill(tmp_path, "s")
    p = tmp_path / "s" / "SKILL.md"
    assert inject_tags(p, ["a", "b"]) is True
    assert inject_tags(p, ["c"]) is False        # 已有 tags 键 → 不覆盖
    fm, body = parse_frontmatter(p)
    assert fm.tags == ["a", "b"] and body.strip() == "# body"
    assert inject_tags(p, []) is False           # 空标签不写
