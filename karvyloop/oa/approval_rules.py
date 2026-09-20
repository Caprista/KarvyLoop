"""Pure validation and matching for version 2 OA approval rules.

This module deliberately has no MCP, filesystem, clock-global, or channel
dependencies.  The adapter owns persistence and execution; this module only
normalizes a rule and evaluates it against a normalized approval context.
Missing values are ``unknown`` and never satisfy an automatic rule.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

ALLOWED_FIELDS = frozenset({
    "workflow.id", "workflow.name", "applicant.id", "applicant.name",
    "applicant.department", "request.amount", "request.currency",
    "request.project", "request.cost_center", "request.created_at",
    "attachment.count", "risk.level", "request.tags",
})
ALLOWED_OPERATORS = frozenset({
    "eq", "neq", "in", "not_in", "contains", "starts_with",
    "gt", "gte", "lt", "lte", "between", "exists",
})


class RuleValidationError(ValueError):
    """Raised when a rule is unsafe or does not conform to schema v2."""


@dataclass(frozen=True)
class ConditionResult:
    field: str
    operator: str
    matched: bool
    known: bool
    reason: str


def _path_get(value: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return current is not None, current


def _as_number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _condition_result(condition: Mapping[str, Any], context: Mapping[str, Any]) -> ConditionResult:
    field = str(condition.get("field", ""))
    operator = str(condition.get("operator", ""))
    known, actual = _path_get(context, field)
    expected = condition.get("value")
    if operator == "exists":
        wanted = bool(expected) if "value" in condition else True
        return ConditionResult(field, operator, known == wanted, True,
                               "字段存在" if known == wanted else "字段存在性不符合条件")
    if not known:
        return ConditionResult(field, operator, False, False, "字段缺失或值为 null")
    try:
        if operator == "eq":
            matched = actual == expected
        elif operator == "neq":
            matched = actual != expected
        elif operator == "in":
            matched = actual in expected
        elif operator == "not_in":
            matched = actual not in expected
        elif operator == "contains":
            matched = expected in actual
        elif operator == "starts_with":
            matched = isinstance(actual, str) and actual.startswith(str(expected))
        elif operator in {"gt", "gte", "lt", "lte"}:
            left, right = _as_number(actual), _as_number(expected)
            if left is None or right is None:
                return ConditionResult(field, operator, False, False, "数值字段格式无效")
            matched = {"gt": left > right, "gte": left >= right,
                       "lt": left < right, "lte": left <= right}[operator]
        elif operator == "between":
            left = _as_number(actual)
            bounds = expected if isinstance(expected, (list, tuple)) and len(expected) == 2 else None
            low, high = (_as_number(x) for x in bounds) if bounds else (None, None)
            if left is None or low is None or high is None:
                return ConditionResult(field, operator, False, False, "区间或数值字段格式无效")
            matched = low <= left <= high
        else:
            return ConditionResult(field, operator, False, False, "未知操作符")
    except (TypeError, ValueError):
        return ConditionResult(field, operator, False, False, "字段类型不匹配")
    return ConditionResult(field, operator, bool(matched), True,
                           "条件满足" if matched else "条件不满足")


def validate_rule(rule: Mapping[str, Any]) -> None:
    """Validate a schema v2 rule; raise :class:`RuleValidationError` on error."""
    if not isinstance(rule, Mapping):
        raise RuleValidationError("rule must be an object")
    if rule.get("schema_version") != 2:
        raise RuleValidationError("schema_version must be 2")
    action = rule.get("action")
    if not isinstance(action, Mapping) or action.get("type") != "approve":
        raise RuleValidationError("only action.type=approve is supported for automatic rules")
    if not isinstance(action.get("auto_execute"), bool):
        raise RuleValidationError("action.auto_execute must be boolean")
    conditions = rule.get("conditions")
    keywords = rule.get("keywords", {})
    has_structured = isinstance(conditions, Mapping) and isinstance(conditions.get("all"), list) and bool(conditions["all"])
    has_groups = isinstance(keywords, Mapping) and isinstance(keywords.get("condition_groups"), list) and bool(keywords["condition_groups"])
    has_keywords = isinstance(keywords, Mapping) and (has_groups or any(isinstance(keywords.get(key), list) and keywords.get(key) for key in ("all", "any", "none")))
    if not has_structured and not has_keywords:
        raise RuleValidationError("conditions.all or keywords must be non-empty")
    if not isinstance(conditions, Mapping):
        conditions = {"all": []}
    for condition in conditions.get("all", []):
        if not isinstance(condition, Mapping):
            raise RuleValidationError("each condition must be an object")
        field, operator = condition.get("field"), condition.get("operator")
        if field not in ALLOWED_FIELDS:
            raise RuleValidationError(f"field is not allowed: {field}")
        if operator not in ALLOWED_OPERATORS:
            raise RuleValidationError(f"operator is not allowed: {operator}")
        if operator != "exists" and "value" not in condition:
            raise RuleValidationError(f"value is required for operator {operator}")
        if operator in {"in", "not_in"} and (not isinstance(condition.get("value"), list) or not condition["value"]):
            raise RuleValidationError(f"value for {operator} must be a non-empty list")
        if operator == "between" and (not isinstance(condition.get("value"), list) or len(condition["value"]) != 2):
            raise RuleValidationError("between requires exactly two values")
    if not isinstance(keywords, Mapping):
        raise RuleValidationError("keywords must be an object")
    for key in ("all", "any", "none"):
        values = keywords.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise RuleValidationError(f"keywords.{key} must be a list of non-empty strings")
    if has_groups:
        groups = keywords["condition_groups"]
        for group in groups:
            values = group if isinstance(group, list) else group.get("all", []) if isinstance(group, Mapping) else None
            if isinstance(group, Mapping):
                category = str(group.get("category", "normal")).casefold()
                if category not in {"normal", "sensitive"}:
                    raise RuleValidationError("condition group category must be normal or sensitive")
                for key in ("all", "any", "none"):
                    entries = group.get(key, [])
                    if not isinstance(entries, list) or any(not isinstance(value, str) or not value.strip() for value in entries):
                        raise RuleValidationError(f"keywords.condition_groups.{key} must be a list of non-empty strings")
                if not any(group.get(key) for key in ("all", "any", "none")):
                    raise RuleValidationError("each condition group must contain all, any, or none")
            elif isinstance(group, list):
                if any(not isinstance(value, str) or not value.strip() for value in group):
                    raise RuleValidationError("keyword condition group values must be non-empty strings")
            else:
                raise RuleValidationError("each condition group must be an object or list")
        threshold = keywords.get("match_threshold", rule.get("match_threshold", 50.0))
        try:
            if isinstance(threshold, bool) or float(threshold) < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise RuleValidationError("keywords.match_threshold must be a non-negative number")
        min_groups = keywords.get("min_groups", rule.get("min_groups", 1))
        if isinstance(min_groups, bool) or not isinstance(min_groups, int) or min_groups < 1 or min_groups > len(groups):
            raise RuleValidationError("keywords.min_groups must be between 1 and total condition groups")
    limits = rule.get("limits", {})
    if not isinstance(limits, Mapping):
        raise RuleValidationError("limits must be an object")
    if "expires_at" in limits and _parse_time(limits["expires_at"]) is None:
        raise RuleValidationError("limits.expires_at must be ISO-8601 with timezone")
    for key in ("max_per_day", "max_amount_per_day"):
        if key in limits and (isinstance(limits[key], bool) or _as_number(limits[key]) is None or _as_number(limits[key]) < 0):
            raise RuleValidationError(f"limits.{key} must be a non-negative number")
    if rule.get("unknown_policy", "require_human") not in {"require_human", "no_match"}:
        raise RuleValidationError("unknown_policy must be require_human or no_match")
    if rule.get("conflict_policy", "require_human") not in {"require_human", "no_match"}:
        raise RuleValidationError("conflict_policy must be require_human or no_match")


def normalize_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    """Return a defensive normalized copy after schema validation."""
    result = deepcopy(dict(rule))
    # Accept a small, explicit compatibility set for model-produced drafts,
    # then validate the canonical paths.  Unknown/free-form fields remain
    # rejected; this is not an escape hatch from the schema allowlist.
    aliases = {
        "workflow_name": "workflow.name",
        "workflow_id": "workflow.id",
        "applicant_name": "applicant.name",
        "department": "applicant.department",
        "applicant_department": "applicant.department",
        "amount": "request.amount",
        "currency": "request.currency",
        "project": "request.project",
        "cost_center": "request.cost_center",
    }
    raw_conditions = result.get("conditions")
    if isinstance(raw_conditions, dict) and isinstance(raw_conditions.get("all"), list):
        for condition in raw_conditions["all"]:
            if isinstance(condition, dict):
                field = str(condition.get("field") or "")
                if field in aliases:
                    condition["field"] = aliases[field]
    validate_rule(result)
    result.setdefault("conditions", {})
    result["conditions"].setdefault("all", [])
    result.setdefault("enabled", True)
    result.setdefault("priority", 0)
    result.setdefault("unknown_policy", "require_human")
    result.setdefault("conflict_policy", "require_human")
    result.setdefault("limits", {})
    # Freeze scoring policy at persistence time.  Any model-provided weight is
    # discarded and rebuilt from the only allowed categories.
    keywords = result.get("keywords")
    if isinstance(keywords, dict) and isinstance(keywords.get("condition_groups"), list):
        canonical_groups = []
        for group in keywords["condition_groups"]:
            if isinstance(group, dict):
                canonical = dict(group)
                category = str(canonical.get("category", "normal")).casefold()
                canonical["category"] = category
                canonical["weight"] = 50 if category == "sensitive" else 100
                canonical_groups.append(canonical)
            else:
                canonical_groups.append(group)
        keywords["condition_groups"] = canonical_groups
        keywords["match_threshold"] = float(keywords.get("match_threshold", 50))
    return result


def explain_rule_match(rule: Mapping[str, Any], context: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Evaluate a rule and return an audit-friendly explanation."""
    normalized = normalize_rule(rule)
    current = now or datetime.now(timezone.utc)
    expiration = _parse_time(normalized.get("limits", {}).get("expires_at"))
    if not normalized.get("enabled"):
        return {"matched": False, "reason": "规则已停用", "conditions": []}
    if expiration and expiration <= current:
        return {"matched": False, "reason": "规则已过期", "conditions": []}
    results = [_condition_result(c, context) for c in normalized["conditions"]["all"]]
    matched = all(item.matched and item.known for item in results)
    return {"matched": matched, "reason": "全部条件满足" if matched else "存在不满足或未知条件",
            "conditions": [item.__dict__ for item in results]}


def matches_rule(rule: Mapping[str, Any], context: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    """Return true only when every condition is known and satisfied."""
    return bool(explain_rule_match(rule, context, now=now)["matched"])
