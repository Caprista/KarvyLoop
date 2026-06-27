"""improve — 结晶后进化（crystallize/improve.py）。

规格:docs/modules/crystallize.md §3 improve.py + §4 HR-7
- 每 5 轮检测纠正,写回 SKILL.md
- 一次性回答/闲聊被忽略
- HR-7:improve 写回的纠正校验来源(steered_by_user 来自 UsageStats)

M1.5 升级 — 分类写回:
  v1 把 steered_by_user 一股脑全塞 ## Corrections。
  v1.1 把纠正按 5 类分桶,各写一段:
    ## Add         (用户补了新用法/参数)
    ## Remove      (用户说"以后别 X")
    ## Modify      (用户修正了一个步骤)
    ## Preferences (用户表达偏好,长期适用)
    ## Corrections (用户说"上次 X 是错的")
  分类规则:关键词白名单启发式 —— M1 v1 不调 LLM 做分类(成本/延迟不值);
  后续 P1 接入小模型精化。

设计意图:分类后下次 recall 时主循环可按 ## Preferences 改行为(例如
"输出用 markdown 表格"),让技能越用越贴用户。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from karvyloop.schemas import UsageStats

from .store import UsageStore


TURN_BATCH_SIZE = 5  # 每 5 轮检测一次(spec §3)


# ---- 5 个分类 ----

class CorrectionKind(str, Enum):
    ADD = "add"
    REMOVE = "remove"
    MODIFY = "modify"
    PREFERENCE = "preference"
    CORRECTION = "correction"


# 5 类对应的 SKILL.md section 标题
KIND_TO_HEADER: dict[CorrectionKind, str] = {
    CorrectionKind.ADD: "## Add",
    CorrectionKind.REMOVE: "## Remove",
    CorrectionKind.MODIFY: "## Modify",
    CorrectionKind.PREFERENCE: "## Preferences",
    CorrectionKind.CORRECTION: "## Corrections",
}


# 关键词触发器(中英双语,小写匹配)
# 注意:re.IGNORECASE + \b 对中文无效(中文没有"单词边界"),所以中文那一半
# 直接用子串匹配;英文仍走 \b 防止误命中("abefore" 不算 "before")
_REMOVE_PAT = re.compile(
    r"(?:\b(?:don'?t|do not|never|stop|remove|delete)\b"
    r"|不要|别|不许|禁止|以后别|以后不|移除|删除|去掉|删掉)",
    re.IGNORECASE,
)
_PREFERENCE_PAT = re.compile(
    r"(?:\b(?:prefer|always|usually|i (?:like|love|hate|want)|my (?:style|way))\b"
    r"|以后都|总是|一直|习惯|偏好|喜欢|用\s*\S+)",
    re.IGNORECASE,
)
_ADD_PAT = re.compile(
    r"(?:\b(?:also|add(?:ed)?|include(?:d)?|plus|remember to|additionally)\b"
    r"|还有|另外|补充|加上|加一句|记得|别忘了|也要)",
    re.IGNORECASE,
)
_MODIFY_PAT = re.compile(
    r"(?:\b(?:change(?:d)?|update(?:d)?|instead|switch(?:ed)? to|revise|modify)\b"
    r"|改一下|改成|换成|调整|改用|改为)",
    re.IGNORECASE,
)
_CORRECTION_PAT = re.compile(
    r"(?:\b(?:wrong|incorrect|that'?s (?:wrong|not right)|bug|fix)\b"
    r"|错了|不对|有误|纠正|改回来|修正)",
    re.IGNORECASE,
)


def classify_correction(text: str) -> CorrectionKind:
    """启发式分类:按关键词命中优先级决定归类。

    优先级(从高到低):correction > remove > preference > modify > add > correction
    理由:用户表达"上次是错的"最强烈,优先归 Corrections(便于审计回滚);
    "不要" 强烈负向,优先归 Remove;preference 是长期偏好,优先归 Preferences;
    modify/add 是中性补充。
    """
    if not text or not text.strip():
        return CorrectionKind.CORRECTION  # 空归默认兜底
    t = text.strip()
    if _CORRECTION_PAT.search(t):
        return CorrectionKind.CORRECTION
    if _REMOVE_PAT.search(t):
        return CorrectionKind.REMOVE
    if _PREFERENCE_PAT.search(t):
        return CorrectionKind.PREFERENCE
    if _MODIFY_PAT.search(t):
        return CorrectionKind.MODIFY
    if _ADD_PAT.search(t):
        return CorrectionKind.ADD
    # 没命中任何关键词 → 保守归 CORRECTION(中性,不假设意图)
    return CorrectionKind.CORRECTION


@dataclass
class ClassifiedCorrection:
    kind: CorrectionKind
    text: str


def classify_batch(corrections: list[str]) -> list[ClassifiedCorrection]:
    """对一批纠正逐条分类,返回带标签的列表。"""
    return [ClassifiedCorrection(kind=classify_correction(c), text=c) for c in corrections]


# ---- 写回 SKILL.md ----

def _fmt_ts(now) -> str:
    if now is None:
        now = datetime.now().timestamp()
    return datetime.fromtimestamp(now).strftime("%Y-%m-%d")


def _bullet(c: ClassifiedCorrection, ts: str) -> str:
    return f"- ({ts}) [{c.kind.value}] {c.text}"


def _insert_into_section(text: str, section_header: str, bullets: list[str]) -> str:
    """把 bullets 插入到指定 section(已有则追加,没有则在文末新建)。

    - 已存在 section → 找到段尾,在末尾追加(段以"## 下一个 section"或文末为界)
    - 不存在 → 在文末新建
    """
    lines = text.splitlines()
    if section_header in lines:
        # 找到 section_header 位置;在它之后找下一个 "## " 开头(同级别),中间就是段
        idx = lines.index(section_header)
        end = len(lines)
        for j in range(idx + 1, len(lines)):
            if lines[j].startswith("## "):
                end = j
                break
        # 在 end 前插入
        out = lines[:end] + bullets + lines[end:]
        return "\n".join(out) + "\n"
    # 不存在:在文末新建
    block = [section_header, ""] + bullets
    return text.rstrip() + "\n\n" + "\n".join(block) + "\n"


def write_corrections_to_skill_md(
    skill_path: Path,
    classified: list[ClassifiedCorrection],
    *,
    now: Optional[float] = None,
) -> bool:
    """把已分类的纠正按 5 段写回 SKILL.md。

    返回 True 表示有写;False 表示没纠正可写(或文件不存在)。
    """
    if not classified:
        return False
    if not skill_path.exists():
        return False
    text = skill_path.read_text(encoding="utf-8")
    ts = _fmt_ts(now)

    # 按 kind 分组
    by_kind: dict[CorrectionKind, list[ClassifiedCorrection]] = {}
    for c in classified:
        by_kind.setdefault(c.kind, []).append(c)

    # 写 5 段(空的不写)
    for kind, items in by_kind.items():
        header = KIND_TO_HEADER[kind]
        bullets = [_bullet(c, ts) for c in items]
        text = _insert_into_section(text, header, bullets)

    skill_path.write_text(text, encoding="utf-8")
    return True


# ---- atom 层 improve:由 role 的质量评语驱动(docs/02 §14,取代死的 steered_by_user 路)----

ROLE_CRITIQUE_HEADER = "## Role critique (atom 自评)"


def write_critiques_to_skill_md(
    skill_path: Path,
    critiques: list[str],
    *,
    now: Optional[float] = None,
) -> bool:
    """把 role 对 atom 的质量评语(满意度评判的 critique)写回 SKILL.md。

    docs/02 §14:atom 的 improve 由 **role 的客观评判** 驱动,不是人的纠正
    (人的纠正归 role 层决策偏好 §11)。返回 True 表示有写。
    """
    from .atom_critic import sanitize_critique
    # 防御性消毒:评语进技能库前压成安全单行(防 `## Steps`/`---` 结构性投毒,对抗验收 C1)。
    cs = [c for c in (sanitize_critique(x) for x in (critiques or [])) if c]
    if not cs or not skill_path.exists():
        return False
    text = skill_path.read_text(encoding="utf-8")
    # 幂等:已作为评语 bullet 写过的不重复(按 bullet 上下文精确匹配,**不是裸子串** — 对抗验收 M3:
    # 短评语"先查缓存"恰好是别处正文子串时不该被误判已写)。
    def _already(c: str) -> bool:
        return re.search(r"- \([^)]*\) " + re.escape(c) + r"\s*$", text, re.MULTILINE) is not None
    cs = [c for c in cs if not _already(c)]
    if not cs:
        return False
    ts = _fmt_ts(now)
    bullets = [f"- ({ts}) {c}" for c in cs]
    text = _insert_into_section(text, ROLE_CRITIQUE_HEADER, bullets)
    skill_path.write_text(text, encoding="utf-8")
    return True


ROLE_LESSON_HEADER = "## Lessons (cross-run 经验)"


def write_lessons_to_skill_md(
    skill_path: Path,
    lessons: list[str],
    *,
    now: Optional[float] = None,
) -> bool:
    """把跨-run 蒸出的规律(lessons.py)折进 SKILL.md 的 Lessons 段(docs/40 §6 丙)。

    与 critiques 同纪律:防御性消毒成安全单行 + 按 bullet 精确幂等(后台可反复跑)。
    """
    from .atom_critic import sanitize_critique
    ls = [c for c in (sanitize_critique(x) for x in (lessons or [])) if c]
    if not ls or not skill_path.exists():
        return False
    text = skill_path.read_text(encoding="utf-8")

    def _already(c: str) -> bool:
        return re.search(r"- \([^)]*\) " + re.escape(c) + r"\s*$", text, re.MULTILINE) is not None

    ls = [c for c in ls if not _already(c)]
    if not ls:
        return False
    ts = _fmt_ts(now)
    bullets = [f"- ({ts}) {c}" for c in ls]
    text = _insert_into_section(text, ROLE_LESSON_HEADER, bullets)
    skill_path.write_text(text, encoding="utf-8")
    return True


def remove_lesson_from_skill_md(skill_path: Path, lesson: str) -> bool:
    """从 SKILL.md 移除一条 lesson bullet(戊:被拒的自我编辑要撤回,不留在技能里误导)。"""
    from .atom_critic import sanitize_critique
    les = sanitize_critique(lesson)
    if not les or not skill_path.exists():
        return False
    text = skill_path.read_text(encoding="utf-8")
    pat = re.compile(r"^- \([^)]*\) " + re.escape(les) + r"\s*$")
    lines = text.splitlines()
    kept = [ln for ln in lines if not pat.match(ln)]
    if len(kept) == len(lines):
        return False
    skill_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return True


# ---- 主入口(每 5 轮触发)----

def maybe_improve(
    skill_name: str,
    *,
    skills_dir: Path,
    store: UsageStore,
    sig: str,
    turn_count: int,
    now: Optional[float] = None,
    force: bool = False,
) -> bool:
    """每 5 轮触发一次;从 UsageStats.steered_by_user 取纠正,分类后写回 SKILL.md。

    ⚠️ docs/02 §14(slice-b):此函数**已不再接进 background_review**——它走的是
    `steered_by_user`(人的纠正),那是"人训 atom"接反问责链、且全代码库无写入者的死路。
    atom 层的 improve 现由 `write_critiques_to_skill_md`(role 满意度评语)驱动。本函数暂留
    (测试覆盖 + 未来若把人的纠正正式接到 role 层时可复用其分类器),但**不在生产改进路径上**。


    返回 True 表示有写回;False 表示本轮跳过(turn_count % TURN_BATCH_SIZE != 0
    或没有纠正可写)。`force=True` 跳过轮次门(后台维护 background_review 自定节奏用)。
    """
    if not force and (turn_count <= 0 or turn_count % TURN_BATCH_SIZE != 0):
        return False
    stats = store.get(sig)
    if stats is None:
        return False
    corrections = list(stats.steered_by_user or [])
    if not corrections:
        return False
    skill_path = skills_dir / skill_name / "SKILL.md"
    classified = classify_batch(corrections)
    return write_corrections_to_skill_md(skill_path, classified, now=now)


__all__ = [
    "TURN_BATCH_SIZE",
    "CorrectionKind", "KIND_TO_HEADER",
    "ClassifiedCorrection",
    "classify_correction", "classify_batch",
    "write_corrections_to_skill_md",
    "ROLE_CRITIQUE_HEADER", "write_critiques_to_skill_md",
    "ROLE_LESSON_HEADER", "write_lessons_to_skill_md", "remove_lesson_from_skill_md",
    "maybe_improve",
]
