"""test_expense_skill — 🧾 票据员(Receipt Reader):报销识别的第 4 位原住民。

Hardy 定范围:报销先只做**发票/小票识别 → 结构化输出**,不是完整报销助手 —— 所以这是
"识别+结构化"能力,做成一位原住民(prose 方法 + 7 文件镜像),不是 bespoke Python 特性。
照 meeting-notes 召回测试形制锁三件事:
1. 中/英报销·票据意图 → recall 真命中 expense;无关意图不命中;不与其他系统技能互抢。
2. 资产诚实:方法=识别→校准脏OCR→抽字段→**行项之和核对总额**→科目只给 hint;宁空勿毒(拿不准
   留 null 绝不编金额/税号);图片走可选 [ocr] extra(装了才识别,不装诚实要文字);范围=识别不裁定。
3. human-owned 科目表模板随包(报销域的语义层;成长故事=文件变长,零假指标);OCR 并进 file_extract。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.crystallize.recall import recall  # noqa: E402
from karvyloop.crystallize.skill_index import SkillIndex  # noqa: E402
from karvyloop.registry.skills import parse_frontmatter, system_skills_dir  # noqa: E402

CN_EXPENSE_INTENTS = [
    "帮我把这张发票识别一下抽成结构化",
    "这张小票读一下,商家日期金额明细列出来",
    "把这张收据整理成报销用的记录",
]
EN_EXPENSE_INTENTS = [
    "read this receipt into structured fields",
    "extract the merchant date and total from this invoice",
]
IRRELEVANT_INTENTS = ["写首诗", "帮我把这份会议转写稿整理成纪要"]


def _index(user_dir) -> SkillIndex:
    idx = SkillIndex()
    idx.rebuild_from_disk(pathlib.Path(user_dir))
    return idx


# ---- 1. 召回可达 ----

def test_chinese_expense_intents_hit(tmp_path):
    idx = _index(tmp_path)
    for intent in CN_EXPENSE_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "expense", \
            f"中文报销意图没召回 expense: {intent!r} -> {hit and hit.name}"


def test_english_expense_intents_hit(tmp_path):
    idx = _index(tmp_path)
    for intent in EN_EXPENSE_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "expense", \
            f"英文报销意图没召回 expense: {intent!r} -> {hit and hit.name}"


def test_irrelevant_and_sibling_intents_do_not_hit(tmp_path):
    """无关意图不命中;整理会议纪要的意图归 meeting-notes,不被报销技能抢。"""
    idx = _index(tmp_path)
    for intent in IRRELEVANT_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is None or hit.name != "expense", \
            f"无关意图误召回 expense: {intent!r}"


def test_domain_scope_sees_expense(tmp_path):
    idx = _index(tmp_path)
    hit = recall("帮我把这张发票识别一下抽成结构化", skills_dir=tmp_path,
                 scope="domain", skill_index=idx)
    assert hit is not None and hit.name == "expense"


# ---- 2. 资产诚实(方法 + 范围 + 宁空勿毒 + 诚实图片契约)----

def test_skill_asset_honest_and_methodical():
    fm, body = parse_frontmatter(system_skills_dir() / "expense" / "SKILL.md")
    assert fm.name == "expense"
    assert (fm.raw or {}).get("source") == "system"
    assert fm.result_reuse == "dynamic", "识别每次重跑(存方法不存答案)"
    assert fm.tags, "召回靠 tags(中英双语)"
    # 诚实图片契约:图片走可选本地 [ocr] extra(装了才识别,不装诚实要文字)
    assert 'karvyloop[ocr]' in body, "图片识别必须指向可选 [ocr] extra,不许无条件承诺"
    assert "paste the text" in body, "没 OCR/视觉模型时必须诚实回退到'贴文字',不许假装读过图"
    # 方法要素:识别→校准→抽取→算术对账→科目 hint
    for marker in ("Identify what the document is", "calibrate", "sum",
                   "arithmetic", "null", "category"):
        assert marker in body, f"SKILL.md 缺方法要素: {marker}"
    # 宁空勿毒:拿不准留 null 绝不编造金额/税号
    assert "never invent" in body.lower() or "invent nothing" in body, \
        "必须白纸黑字:读不出的数字绝不编造"
    # 范围诚实:识别+结构化,不裁定报销/不提交
    assert "not judgment" in body or "does not decide" in body, \
        "范围必须写明:只识别结构化,不裁定能否报销"


# ---- 3. human-owned 科目表模板 ----

def test_categories_template_ships_human_owned():
    p = system_skills_dir() / "expense" / "references" / "categories.template.md"
    assert p.exists(), "科目表模板必须随包(报销域的语义层)"
    text = p.read_text(encoding="utf-8")
    assert "human-owned" in text, "必须标明 human-owned(实例长在用户空间,抄不走)"
    assert "科目" in text, "科目表要给公司科目骨架"


# ---- 4. 票据员是完整原住民(活跃·只读·成长魂=公司科目表) ----

def test_receipt_reader_resident_shipped_and_wellformed():
    """报销识别做成完整原住民(识别+结构化,只读不写你的东西):镜像 7 文件齐、引用 expense 技能、
    MEMORY 承载成长(公司科目表)—— 这是它当 role 而非静态 skill 的理由。"""
    from karvyloop.karvy.residents import load_resident, system_residents_dir
    d = system_residents_dir() / "expense"
    assert d.exists(), "expense 原住民镜像应已随包"
    for f in ("resident.json", "IDENTITY.md", "SOUL.md", "USER.md",
              "COMMITMENT.md", "VERIFY.md", "MEMORY.md"):
        assert (d / f).exists() and (d / f).read_text(encoding="utf-8").strip(), \
            f"原住民镜像缺/空: {f}"
    r = load_resident("expense")
    assert r and r["id"] == "expense"
    assert r["nickname"].get("en") and r["nickname"].get("zh"), "花名要中英双语"
    assert r["pitch"].get("en") and r["pitch"].get("zh"), "pitch 要中英双语"
    assert r["skills"] == ["expense"], "必须引用 expense 技能(方法在技能里)"
    for slot in ("identity", "soul", "user", "commitment_own", "verify", "memory"):
        assert len(r[slot]) > 80, f"灵魂槽太空,打样不合格: {slot}"
    # 成长魂:MEMORY 明写会长的公司科目表 —— 否则它就该是静态 skill 不是 role
    assert "category sheet" in r["memory"], \
        "MEMORY 必须承载成长(公司科目表),这是它当 role 的理由"
    # 只读票据,不写盘(纯好用型,只识别不改你的东西)
    assert r["grant_ops"] == ["read"], "票据员只读票据,不写盘"


# ---- 5. OCR 并进 file_extract(图片走同一条产线,不另建工具) ----

def test_image_wired_into_file_extract():
    """报销识别的图片输入 = read_file/files 面板遇图自动 OCR(照 ASR 并进 file_extract),
    不是 bespoke 工具。缺 [ocr] → 诚实 missing_dependency,绝不崩。"""
    from karvyloop.file_extract import extract_kind, extract_text
    for name in ("receipt.jpg", "invoice.PNG", "scan.webp"):
        assert extract_kind(name) == "image", f"{name} 应判为 image 走 OCR 分支"
    assert extract_kind("notes.txt") is None, "文本不走解析分支"
    # 坏图 magic 不符 → bad_file(宁空勿毒,不吐垃圾);缺依赖 → missing_dependency,都不崩
    r = extract_text(b"not really an image", "image")
    assert r.ok is False and r.text == "" and r.error in ("bad_file", "missing_dependency")
