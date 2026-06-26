"""Bootstrapper 升级 —— 答案解析 + 推荐灵魂层 3 问。

**核心不变量**(doc §4):
- E1 model_fn=None 走关键词 fallback
- E7 解析 = InsightDict typed dataclass(**不**返裸 dict)
- E8 强模型 = 注入 Callable

设计:docs/15 §3.1。
"""
from __future__ import annotations

import dataclasses
import logging
import re
from typing import Any, Callable, Mapping, Optional, Sequence

from .auditor import Auditor, AuditorReport, SOUL_SLOTS

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class InsightDict:
    """Bootstrapper 解析用户答案的产物(E7 typed)。"""
    keywords: tuple[str, ...]             # 提取的关键词
    detected_themes: tuple[str, ...]      # 命中的主题
    conflicts: tuple[str, ...]            # 与已有灵魂层的冲突描述
    summary: str                          # 一句话总结


# 灵魂层主题关键词(拍 5 v0 关键词库)
THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "产品": ("产品", "pm", "product", "PRD", "需求", "user story"),
    "工程": ("代码", "code", "工程", "engineer", "架构", "系统"),
    "设计": ("设计", "design", "UX", "UI", "视觉"),
    "战略": ("战略", "strategy", "OKR", "愿景", "vision"),
    "运营": ("运营", "ops", "运营", "增长", "growth"),
    "研究": ("研究", "research", "调研", "user research"),
}


def _extract_keywords(answers: Sequence[str]) -> tuple[str, ...]:
    """关键词启发式(本拍 v0 fallback)。"""
    text = " ".join(answers).lower()
    words: set[str] = set()
    for theme, kws in THEME_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in text:
                words.add(kw)
    return tuple(sorted(words))


def _detect_themes(answers: Sequence[str]) -> tuple[str, ...]:
    """主题识别 —— 按出现频次取 Top 3。"""
    text = " ".join(answers).lower()
    scores: list[tuple[str, int]] = []
    for theme, kws in THEME_KEYWORDS.items():
        cnt = sum(1 for kw in kws if kw.lower() in text)
        if cnt:
            scores.append((theme, cnt))
    scores.sort(key=lambda x: -x[1])
    return tuple(t for t, _ in scores[:3])


def _detect_conflicts(
    answers: Sequence[str],
    existing_soul_dir: Optional[str] = None,
) -> tuple[str, ...]:
    """冲突检测 —— 对比 IDENTITY/SOUL 关键词(简化版)。"""
    if not existing_soul_dir:
        return ()
    import pathlib
    base = pathlib.Path(existing_soul_dir)
    identity = ""
    soul = ""
    if (base / "IDENTITY.md").exists():
        identity = (base / "IDENTITY.md").read_text(encoding="utf-8").lower()
    if (base / "SOUL.md").exists():
        soul = (base / "SOUL.md").read_text(encoding="utf-8").lower()
    if not (identity and soul):
        return ()
    from .auditor import CONFLICT_PAIRS
    text = " ".join(answers).lower()
    conflicts = []
    for pair_name, a_kws, b_kws in CONFLICT_PAIRS:
        # 简化:答案含 a_kws 但 IDENTITY 已有 b_kws(或反之)
        a_in_answer = any(kw.lower() in text for kw in a_kws)
        b_in_identity = any(kw.lower() in identity for kw in b_kws)
        if a_in_answer and b_in_identity:
            conflicts.append(f"{pair_name}: 答案倾向 a 但 IDENTITY 倾向 b")
    return tuple(conflicts)


def interpret_answers(
    answers: Sequence[str],
    model_fn: Optional[Callable[[Sequence[str], Optional[str]], Mapping[str, Any]]] = None,
    existing_soul_dir: Optional[str] = None,
) -> InsightDict:
    """E1 + E2 入口:解析用户答案 → InsightDict。

    model_fn = None → 走关键词启发式(E1)
    model_fn 注入 → 调它,失败 fallback(E1/E2)
    """
    if model_fn is not None:
        try:
            result = model_fn(answers, existing_soul_dir)
            if isinstance(result, Mapping):
                # 注入模型必须返可识别的 InsightDict-ish 字段,**不**则 fallback
                if "keywords" in result or "summary" in result:
                    return InsightDict(
                        keywords=tuple(result.get("keywords", ())),
                        detected_themes=tuple(result.get("detected_themes", ())),
                        conflicts=tuple(result.get("conflicts", ())),
                        summary=str(result.get("summary", "")),
                    )
        except Exception as e:
            logger.warning("Bootstrapper model_fn failed: %s, fallback", e)

    # Fallback 关键词启发式
    keywords = _extract_keywords(answers)
    themes = _detect_themes(answers)
    conflicts = _detect_conflicts(answers, existing_soul_dir)
    summary = f"主题={themes[:2] or '未识别'}; 关键词={len(keywords)} 个"
    return InsightDict(
        keywords=keywords,
        detected_themes=themes,
        conflicts=conflicts,
        summary=summary,
    )


def recommend_three_questions(
    audit_report: AuditorReport,
    model_fn: Optional[Callable[[AuditorReport], Sequence[str]]] = None,
) -> tuple[str, ...]:
    """根据 AuditorReport 推荐灵魂层 3 问。

    拍 5 v0:从 error/warning finding 抽 message 拼 3 问
    """
    if model_fn is not None:
        try:
            result = model_fn(audit_report)
            if result:
                return tuple(result[:3])
        except Exception as e:
            logger.warning("recommend model_fn failed: %s, fallback", e)

    # Fallback:从 severity 排序的 finding 抽 3 个
    sorted_f = audit_report.findings_sorted()
    questions = []
    for f in sorted_f[:3]:
        slot = f.slot or "灵魂层"
        questions.append(f"({slot}) {f.message}?")
    # 不够 3 个补默认
    defaults = (
        "IDENTITY: 这个 role 的核心职责是什么?",
        "SOUL: 你最看重什么原则?",
        "USER: 谁是你的目标用户?",
    )
    i = 0
    while len(questions) < 3 and i < len(defaults):
        if defaults[i] not in questions:
            questions.append(defaults[i])
        i += 1
    return tuple(questions[:3])


class Bootstrapper:
    """Bootstrapper 升级主类 —— 整合 interpret_answers + recommend_three_questions。"""

    def __init__(
        self,
        model_fn: Optional[Callable[[Sequence[str], Optional[str]], Mapping[str, Any]]] = None,
        recommend_fn: Optional[Callable[[AuditorReport], Sequence[str]]] = None,
    ) -> None:
        self._model_fn = model_fn
        self._recommend_fn = recommend_fn

    def interpret(
        self,
        answers: Sequence[str],
        existing_soul_dir: Optional[str] = None,
    ) -> InsightDict:
        return interpret_answers(answers, self._model_fn, existing_soul_dir)

    def recommend(self, audit_report: AuditorReport) -> tuple[str, ...]:
        return recommend_three_questions(audit_report, self._recommend_fn)


def default_bootstrapper() -> Bootstrapper:
    return Bootstrapper()
