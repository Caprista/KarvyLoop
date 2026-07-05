"""test_study_buddy_skill — 📚 学伴系统技能(docs/60 第三步的资产先备,角色入住后程再开)。

照 meeting-notes / data-analyst 召回测试形制锁三件事:
1. 中/英学习意图 → recall 真命中 study-buddy;无关意图不命中;不与其他系统技能互抢。
2. 资产诚实:方法=主动回忆/间隔复习(1/3/7/14/30 阶梯)+ 费曼 + 康奈尔 + Bloom;
   来源点名(Dunlosky 2013 / SM-2);以学习者自己的材料为真理源,不臆造答案;
   不承诺自动定时(提醒由用户/Karvy 排,技能自己不起 timer)。
3. human-owned 学习台账模板随包(间隔变长+错题不再复发 = 诚实成长指标,零假百分比)。

注意:只有技能资产,**没有** system_residents/study-buddy(引荐不接,下程再开)——
这里顺带锁住这一点,防止有人提前把角色塞进包里。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.crystallize.recall import recall  # noqa: E402
from karvyloop.crystallize.skill_index import SkillIndex  # noqa: E402
from karvyloop.registry.skills import parse_frontmatter, system_skills_dir  # noqa: E402

CN_STUDY_INTENTS = [
    "帮我复习一下这章知识点,考考我",
    "用费曼学习法给我讲讲这个概念检查我是否真懂",
    "帮我做几张闪卡背单词",
    "给我安排一个间隔复习的学习计划",
]
EN_STUDY_INTENTS = [
    "quiz me on this chapter before the exam",
    "make flashcards from my notes and test me",
    "help me study with spaced repetition",
]
IRRELEVANT_INTENTS = ["写首诗", "帮我整理一下下载文件夹",
                      "帮我把这份会议转写稿整理成会议纪要"]


def _index(user_dir) -> SkillIndex:
    idx = SkillIndex()
    idx.rebuild_from_disk(pathlib.Path(user_dir))
    return idx


# ---- 1. 召回可达 ----

def test_chinese_study_intents_hit(tmp_path):
    idx = _index(tmp_path)
    for intent in CN_STUDY_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "study-buddy", \
            f"中文学习意图没召回 study-buddy: {intent!r} -> {hit and hit.name}"


def test_english_study_intents_hit(tmp_path):
    idx = _index(tmp_path)
    for intent in EN_STUDY_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "study-buddy", \
            f"英文学习意图没召回 study-buddy: {intent!r} -> {hit and hit.name}"


def test_irrelevant_and_sibling_intents_do_not_hit(tmp_path):
    """无关意图不命中;会议纪要/整理文件的意图归各自技能,不被学伴抢。"""
    idx = _index(tmp_path)
    for intent in IRRELEVANT_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is None or hit.name != "study-buddy", \
            f"无关意图误召回 study-buddy: {intent!r}"


def test_siblings_still_win_their_own_intents(tmp_path):
    """0 回归:新技能加入后,兄弟系统技能仍赢下自己的主场意图。"""
    idx = _index(tmp_path)
    own = {
        "帮我把这份会议转写稿整理成会议纪要": "meeting-notes",
        "帮我整理一下下载文件夹": "file-butler",
        "帮我分析一下 report.pdf 这份数据": "data-analyst",
    }
    for intent, want in own.items():
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == want, \
            f"{want} 的主场意图被抢: {intent!r} -> {hit and hit.name}"


def test_domain_scope_sees_study_buddy(tmp_path):
    idx = _index(tmp_path)
    hit = recall("帮我做几张闪卡背单词", skills_dir=tmp_path,
                 scope="domain", skill_index=idx)
    assert hit is not None and hit.name == "study-buddy"


# ---- 2. 资产诚实(方法 + 来源 + 边界)----

def test_skill_asset_honest_and_methodical():
    fm, body = parse_frontmatter(system_skills_dir() / "study-buddy" / "SKILL.md")
    assert fm.name == "study-buddy"
    assert (fm.raw or {}).get("source") == "system"
    assert fm.result_reuse == "dynamic", "学习会话必须每次重跑(存方法不存答案)"
    assert fm.tags, "召回靠 tags(中英双语)"
    # 方法要素:主动回忆 + 间隔阶梯 + 费曼 + 康奈尔 + Bloom,来源点名
    for marker in ("Dunlosky", "1 → 3 → 7 → 14 → 30", "Feynman", "Cornell",
                   "Bloom", "SM-2"):
        assert marker in body, f"SKILL.md 缺方法要素: {marker}"
    # 诚实边界:材料是真理源、不臆造;不自动起 timer
    assert "check the source" in body, "材料不裁决时必须标注查源,不臆造"
    assert "does not set timers" in body, "必须白纸黑字写明技能自己不起定时"


def test_no_resident_shipped_yet():
    """docs/60 排程:学伴只先落技能资产;原住民镜像/引荐**下程**再开,不许提前塞包。"""
    from karvyloop.karvy.residents import system_residents_dir
    assert not (system_residents_dir() / "study-buddy").exists(), \
        "study-buddy 原住民镜像不该在这个阶段出现(引荐未接)"


# ---- 3. human-owned 学习台账模板 ----

def test_study_ledger_template_ships_human_owned():
    p = system_skills_dir() / "study-buddy" / "references" / "study-ledger.template.md"
    assert p.exists(), "学习台账模板必须随包(学习域的 human-owned 记忆)"
    text = p.read_text(encoding="utf-8")
    assert "human-owned" in text, "必须标明 human-owned(实例长在用户空间,抄不走)"
    assert "| concept / item |" in text, "台账要给可直接填的表格骨架"
    assert "错题" in text, "错题重现是方法的一半,模板必须有位置"


def test_learning_methods_reference_ships_with_sources():
    p = system_skills_dir() / "study-buddy" / "references" / "learning-methods.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    for src in ("Dunlosky", "Woźniak", "Pauk", "Anderson & Krathwohl"):
        assert src in text, f"方法参考缺来源署名: {src}"
