"""test_meeting_notes_skill — 🎙️ 会议纪要系统技能(docs/60 裁决:降级为技能,零代码纯资产)。

照 data-analyst 召回测试形制锁三件事:
1. 中/英会议意图 → recall 真命中 meeting-notes;无关意图不命中;不与其他系统技能互抢。
2. 资产诚实:输入=文字稿(不承诺 ASR/录音转写);方法=三分栏(决策/行动项/待确认)+
   who/what/when + 术语表门(查不到标待确认,绝不臆造)。
3. human-owned 术语表模板随包(会议域的语义层;成长故事=文件变长,零假指标)。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.crystallize.recall import recall  # noqa: E402
from karvyloop.crystallize.skill_index import SkillIndex  # noqa: E402
from karvyloop.registry.skills import parse_frontmatter, system_skills_dir  # noqa: E402

CN_MEETING_INTENTS = [
    "帮我把这份会议转写稿整理成会议纪要",
    "从这个会议记录里提取行动项和决策",
    "帮我写一下今天周会的会议总结",
]
EN_MEETING_INTENTS = [
    "turn this meeting transcript into minutes",
    "extract the action items from these meeting notes",
]
IRRELEVANT_INTENTS = ["写首诗", "帮我整理一下下载文件夹"]


def _index(user_dir) -> SkillIndex:
    idx = SkillIndex()
    idx.rebuild_from_disk(pathlib.Path(user_dir))
    return idx


# ---- 1. 召回可达 ----

def test_chinese_meeting_intents_hit(tmp_path):
    idx = _index(tmp_path)
    for intent in CN_MEETING_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "meeting-notes", \
            f"中文会议意图没召回 meeting-notes: {intent!r} -> {hit and hit.name}"


def test_english_meeting_intents_hit(tmp_path):
    idx = _index(tmp_path)
    for intent in EN_MEETING_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "meeting-notes", \
            f"英文会议意图没召回 meeting-notes: {intent!r} -> {hit and hit.name}"


def test_irrelevant_and_sibling_intents_do_not_hit(tmp_path):
    """无关意图不命中;整理文件的意图归 file-butler,不被会议技能抢。"""
    idx = _index(tmp_path)
    for intent in IRRELEVANT_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is None or hit.name != "meeting-notes", \
            f"无关意图误召回 meeting-notes: {intent!r}"


def test_domain_scope_sees_meeting_notes(tmp_path):
    idx = _index(tmp_path)
    hit = recall("帮我把这份会议转写稿整理成会议纪要", skills_dir=tmp_path,
                 scope="domain", skill_index=idx)
    assert hit is not None and hit.name == "meeting-notes"


# ---- 2. 资产诚实(方法 + 输入契约)----

def test_skill_asset_honest_and_methodical():
    fm, body = parse_frontmatter(system_skills_dir() / "meeting-notes" / "SKILL.md")
    assert fm.name == "meeting-notes"
    assert (fm.raw or {}).get("source") == "system"
    assert fm.result_reuse == "dynamic", "纪要必须每次重跑(存方法不存答案)"
    assert fm.tags, "召回靠 tags(中英双语)"
    # 诚实输入契约:吃文字稿,不承诺转写音频
    assert "transcript" in body
    assert "does **not** transcribe audio" in body, "必须白纸黑字写明不做 ASR"
    # 方法要素:三分栏 + who/what/when + 术语表门
    for marker in ("Decisions", "Action items", "who / what / by-when",
                   "needs confirmation", "glossary"):
        assert marker in body, f"SKILL.md 缺方法要素: {marker}"


# ---- 3. human-owned 术语表模板 ----

def test_glossary_template_ships_human_owned():
    p = system_skills_dir() / "meeting-notes" / "references" / "glossary.template.md"
    assert p.exists(), "术语表模板必须随包(会议域的语义层)"
    text = p.read_text(encoding="utf-8")
    assert "human-owned" in text, "必须标明 human-owned(实例长在用户空间,抄不走)"
    assert "| term |" in text, "术语表要给可直接填的表格骨架"
