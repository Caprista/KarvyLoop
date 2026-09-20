"""Safe keyword matching for OA approval rules.

This module is deliberately deterministic and does not call an LLM.  A model
may propose keywords elsewhere, but this matcher is the final textual
candidate check before the structured rule and authorization gates run.
Missing scoped text is ``UNKNOWN`` (fail-closed), never a match.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Mapping


@dataclass(frozen=True)
class KeywordMatchResult:
    status: str
    matched_keywords: tuple[str, ...] = ()
    missing_keywords: tuple[str, ...] = ()
    forbidden_keywords: tuple[str, ...] = ()
    reason: str = ""
    confidence: float = 1.0
    matched_groups: int = 0
    total_groups: int = 0
    threshold: float = 1.0
    score: float = 1.0
    matched_score: float = 1.0
    matched_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"MATCH", "NO_MATCH", "UNKNOWN"}:
            raise ValueError("status must be MATCH, NO_MATCH, or UNKNOWN")
        if not 0 <= float(self.confidence) <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.matched_groups < 0 or self.total_groups < 0:
            raise ValueError("group counts must be non-negative")


_DEFAULT_SCOPE = (
    "request.title",
    "request.description",
    "request.form_text",
    "request.project",
)


def normalize_text(value: Any) -> str:
    """Normalize user/OA text for deterministic contains matching."""

    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    # Keep letters/numbers (including CJK) and turn punctuation/whitespace into
    # one separator.  This makes full-width punctuation and line breaks benign.
    text = "".join(char if (char.isalnum() or char == "_") else " " for char in text)
    return re.sub(r"\s+", " ", text).strip()


def _context_value(context: Any, path: str) -> tuple[bool, Any]:
    if isinstance(context, Mapping):
        current: Any = context
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return False, None
            current = current[part]
        return current is not None and str(current).strip() != "", current
    # ApprovalContext exposes canonical flat attributes.
    aliases = {
        "request.title": "request_title",
        "request.description": "request_description",
        "request.form_text": "form_text",
        "request.project": "project",
    }
    attr = aliases.get(path)
    if attr is None or not hasattr(context, attr):
        return False, None
    value = getattr(context, attr)
    return value is not None and str(value).strip() != "", value


def _keywords(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result = []
    for item in value:
        normalized = normalize_text(item)
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _rule_text(rule: Mapping[str, Any], context: Any) -> tuple[str, bool]:
    keyword_config = rule.get("keywords") if isinstance(rule, Mapping) else None
    if not isinstance(keyword_config, Mapping):
        return "", True
    scope = keyword_config.get("keyword_scope", rule.get("keyword_scope", _DEFAULT_SCOPE))
    if not isinstance(scope, (list, tuple)) or not scope:
        scope = _DEFAULT_SCOPE
    chunks: list[str] = []
    known = False
    for path in scope:
        found, value = _context_value(context, str(path))
        if found:
            known = True
            chunks.append(normalize_text(value))
    return normalize_text(" ".join(chunks)), known


def _match_clause(config: Mapping[str, Any], text: str, known: bool) -> KeywordMatchResult:
    """Evaluate one ``all/any/none`` clause against already scoped text."""
    all_words = _keywords(config.get("all", ()))
    any_words = _keywords(config.get("any", ()))
    none_words = _keywords(config.get("none", ()))
    if not (all_words or any_words or none_words):
        return KeywordMatchResult("MATCH", reason="关键词条件为空")
    if not known or not text:
        return KeywordMatchResult("UNKNOWN", reason="关键词匹配文本缺失")
    matched_all = tuple(word for word in all_words if word in text)
    missing = tuple(word for word in all_words if word not in text)
    matched_any = tuple(word for word in any_words if word in text)
    forbidden = tuple(word for word in none_words if word in text)
    matched = matched_all + matched_any
    if forbidden:
        return KeywordMatchResult("NO_MATCH", matched, missing, forbidden, "命中禁止关键词")
    if missing or (any_words and not matched_any):
        return KeywordMatchResult("NO_MATCH", matched, missing, (), "未满足关键词条件")
    return KeywordMatchResult("MATCH", matched, (), (), "关键词条件满足")


def match_keywords(rule: Mapping[str, Any], context: Any) -> KeywordMatchResult:
    """Evaluate ``keywords.all/any/none`` against an approval context.

    An absent keyword section imposes no textual constraint and matches.  When
    a rule has keywords but all configured text fields are unavailable, the
    result is ``UNKNOWN``.  ``UNKNOWN`` is intentionally not executable.
    """

    config = rule.get("keywords") if isinstance(rule, Mapping) else None
    if not isinstance(config, Mapping):
        return KeywordMatchResult("MATCH", reason="未配置关键词条件")
    groups = config.get("condition_groups")
    if isinstance(groups, list) and groups:
        # A group is an independent all/any/none clause.  The model may emit
        # a bare list as shorthand for ``all``; mappings can also override the
        # text scope.  Only groups are considered when this field is present.
        total = len(groups)
        raw_threshold = config.get("match_threshold", rule.get("match_threshold", 50.0))
        try:
            threshold = max(0.0, float(raw_threshold))
        except (TypeError, ValueError):
            threshold = 1.0
        try:
            min_groups = max(1, int(config.get("min_groups", rule.get("min_groups", 1))))
        except (TypeError, ValueError):
            min_groups = 1
        matched_count = 0
        matched_score = 0.0
        total_score = 0.0
        matched_keywords: list[str] = []
        missing_keywords: list[str] = []
        forbidden_keywords: list[str] = []
        unknown = False
        for group in groups:
            if isinstance(group, (list, tuple)):
                group_config: Mapping[str, Any] = {"all": group}
                scoped_rule = rule
            elif isinstance(group, Mapping):
                group_config = group
                scoped_rule = {"keywords": group} if "keyword_scope" in group else rule
            else:
                unknown = True
                continue
            # Classification is the only model-provided scoring input.  Never
            # trust a model/user supplied weight: the mapping is immutable.
            category = str(group.get("category", "normal")).casefold() if isinstance(group, Mapping) else "normal"
            weight = 50.0 if category == "sensitive" else 100.0
            positive_conditions = len(_keywords(group_config.get("all", ()))) + len(_keywords(group_config.get("any", ())))
            # Weight is per positive condition, not per container group.
            # A ``none`` clause is a safety veto and contributes no score.
            if positive_conditions == 0:
                positive_conditions = 1
            total_score += weight * positive_conditions
            group_text, group_known = _rule_text(scoped_rule, context)
            result = _match_clause(group_config, group_text, group_known)
            matched_keywords.extend(result.matched_keywords)
            missing_keywords.extend(result.missing_keywords)
            forbidden_keywords.extend(result.forbidden_keywords)
            if result.status == "MATCH":
                matched_count += 1
            elif result.status == "UNKNOWN":
                unknown = True
            matched_score += weight * len(result.matched_keywords)
        score = matched_score
        enough = matched_count >= min_groups and score > threshold
        # Unknown source text is fail-closed even when the numeric threshold
        # would otherwise be met; a partial snapshot must not authorize an
        # approval merely because other groups happened to match.
        if enough and not unknown and not forbidden_keywords:
            status = "MATCH"
            reason = f"命中 {matched_count}/{total} 个条件组，得分 {score:.0f}，超过阈值 {threshold:.0f}"
        elif unknown:
            status, reason = "UNKNOWN", "条件组匹配文本缺失，需人工确认"
        else:
            status = "NO_MATCH"
            reason = (f"命中得分 {score:.0f} 未超过阈值 {threshold:.0f}"
                      if score < threshold else f"命中 {matched_count} 组，少于最低要求 {min_groups} 组")
        return KeywordMatchResult(status, tuple(dict.fromkeys(matched_keywords)),
                                  tuple(dict.fromkeys(missing_keywords)),
                                  tuple(dict.fromkeys(forbidden_keywords)), reason,
                                  score / total_score if total_score else 0.0,
                                  matched_count, total, threshold, score, matched_score,
                                  tuple(dict.fromkeys(matched_keywords)))

    all_words = _keywords(config.get("all", ()))
    any_words = _keywords(config.get("any", ()))
    none_words = _keywords(config.get("none", ()))
    text, known = _rule_text(rule, context)
    if not (all_words or any_words or none_words):
        return KeywordMatchResult("MATCH", reason="关键词条件为空")
    if not known or not text:
        return KeywordMatchResult("UNKNOWN", reason="关键词匹配文本缺失")

    return _match_clause(config, text, known)


__all__ = ["KeywordMatchResult", "match_keywords", "normalize_text"]
