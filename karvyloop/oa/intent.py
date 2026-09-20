"""Platform-neutral parsing for concise OA user intents."""

from __future__ import annotations

import re

from .domain import OAIntent, RuleIntent


_KEYWORD_SPLIT = re.compile(r"\s*(?:、|，|,|；|;|/|\\|\s+|和|及|以及)\s*")


def _keyword_values(raw: str) -> list[str]:
    """Extract explicit words without inventing synonyms or expressions."""
    values = []
    for value in _KEYWORD_SPLIT.split(raw.strip(" \t\r\n，,；;。")):
        value = value.strip(" \t\r\n\"'“”‘’")
        if value and value not in values and len(value) <= 80:
            values.append(value)
    return values


def _keyword_clauses(value: str) -> tuple[dict[str, list[str]], tuple[str, ...]]:
    """Read only explicit keyword clauses from Chinese user text.

    The parser deliberately does not infer keywords from arbitrary prose. This
    keeps rule creation reviewable and prevents an accidental sentence from
    becoming an executable approval rule.
    """
    keywords: dict[str, list[str]] = {"all": [], "any": [], "none": []}
    scopes: list[str] = []
    scope_patterns = (
        (r"(?:标题|申请标题)", "request.title"),
        (r"(?:说明|申请说明|描述|备注)", "request.description"),
        (r"(?:项目|项目名称)", "request.project"),
        (r"(?:表单|表单内容|申请内容)", "request.form_text"),
    )
    for pattern, field in scope_patterns:
        if re.search(pattern + r"[^，,；;。]*?(?:包含|含有|关键词)", value):
            if field not in scopes:
                scopes.append(field)

    # Specific forms are checked before the generic ``包含`` form.
    patterns = (
        ("none", r"(?:排除|不包含|不含|禁止包含)\s*(?:关键词|关键字)?\s*([^，,；;。]+)"),
        ("any", r"(?:任一|任意一个|至少一个)\s*(?:关键词|关键字)?\s*(?:为|是|包含|含有)?\s*([^，,；;。]+)"),
        # Avoid re-reading the ``关键词`` part of an ``任一关键词`` clause as
        # an additional ``all`` condition.
        ("all", r"(?<!任一)(?<!任意一个)(?<!至少一个)(?:(?:关键词|关键字)\s*(?:必须)?\s*(?:为|是|包含|包括|含有)|(?:包含|包括|含有)\s*(?:关键词|关键字))\s*[:：]?\s*([^，,；;。]+)"),
    )
    consumed: list[tuple[int, int]] = []
    for kind, pattern in patterns:
        for match in re.finditer(pattern, value):
            consumed.append(match.span())
            for item in _keyword_values(match.group(1)):
                if item not in keywords[kind]:
                    keywords[kind].append(item)

    # Field-scoped wording such as “申请说明包含出差” is an explicit
    # keyword request, but “项目包含华东” remains a structured condition.
    field_pattern = r"(?:标题|申请标题|说明|申请说明|描述|备注|表单内容|申请内容)\s*(?:包含|含有)\s*([^，,；;。]+)"
    for match in re.finditer(field_pattern, value):
        for item in _keyword_values(match.group(1)):
            if item not in keywords["all"]:
                keywords["all"].append(item)

    return {key: values for key, values in keywords.items() if values}, tuple(scopes)


def _indexes(raw: str) -> tuple[int, ...]:
    values: set[int] = set()
    for token in re.findall(r"\d+\s*(?:[-~至]\s*\d+)?", raw):
        parts = re.split(r"\s*[-~至]\s*", token)
        start, end = int(parts[0]), int(parts[-1])
        if start > 0 and end > 0 and end - start <= 100:
            values.update(range(min(start, end), max(start, end) + 1))
    return tuple(sorted(values))


def parse_oa_intent(text: str, *, source: str = "user") -> OAIntent | None:
    """Parse only unambiguous short commands; natural language stays for the agent."""
    value = " ".join(str(text or "").strip().split())
    if value in {"?", "？", "继续", "下一批"}:
        return OAIntent("continue", source=source)
    if value in {"刷新", "刷新列表", "重新查询待办"}:
        return OAIntent("refresh", source=source)
    if value in {"重试", "再试一次"}:
        return OAIntent("retry", source=source)
    match = re.search(r"(批准|同意|通过|拒绝|驳回|转发)", value)
    if not match:
        return None
    word = match.group(1)
    action = "approve" if word in {"批准", "同意", "通过"} else "reject" if word in {"拒绝", "驳回"} else "forward"
    indexes = _indexes(value[:match.start()] or value[match.end():])
    if not indexes:
        return None
    remark = ""
    target = ""
    if action == "reject":
        reason = re.search(r"(?:原因|理由)\s*[:：]?\s*(.+)$", value)
        remark = reason.group(1).strip() if reason else ""
    elif action == "forward":
        recipient = re.search(r"(?:给|至|到)\s*([^，,;；]+)", value)
        target = recipient.group(1).strip() if recipient else ""
    return OAIntent(action, indexes=indexes, remark=remark, target=target, source=source)


def parse_rule_intent(text: str, *, source: str = "user") -> RuleIntent | None:
    """Parse only explicit rule-management phrases into a safe intent shell.

    Detailed conditions remain for the agent to normalize and preview; this
    parser never invents an approval rule from a vague sentence.
    """
    value = " ".join(str(text or "").strip().split())
    if not value:
        return None
    if re.search(r"(?:预览|模拟).*(?:自动审批|审批规则)|(?:自动审批|审批规则).*(?:预览|模拟)", value):
        return RuleIntent(kind="preview_rule", source=source)
    if re.search(r"(?:创建|新增|设置|建立).*(?:自动审批|审批规则)", value):
        conditions: list[dict[str, object]] = []
        # Accept both explicit forms ("审批类型为付款") and the compact
        # conversational form ("付款类型的审批").
        workflow = re.search(r"(?:流程|审批类型)\s*[为是:：]\s*([^，,；;]+)", value)
        if not workflow:
            compact_workflow = re.search(
                r"(?:创建(?:一条)?|新增(?:一条)?|设置(?:一条)?)\s*"
                r"([\u4e00-\u9fffA-Za-z0-9_-]{1,30}?)类型(?:的)?(?:审批|申请)?",
                value,
            )
            if compact_workflow:
                workflow = compact_workflow
        if workflow:
            workflow_value = workflow.group(1).strip()
            conditions.append({"field": "workflow.name", "operator": "eq", "value": workflow_value})
        department = re.search(r"(?:部门|申请人部门)\s*[为是:]\s*([^，,；;]+)", value)
        if department:
            conditions.append({"field": "applicant.department", "operator": "eq", "value": department.group(1).strip()})
        amount = re.search(r"(?:金额|费用)\s*(?:小于|不超过|低于|<=)\s*([0-9]+(?:\.[0-9]+)?)", value)
        if amount:
            conditions.append({"field": "request.amount", "operator": "lte", "value": float(amount.group(1))})
        project = re.search(r"项目\s*(?:包含|含)\s*([^，,；;]+)", value)
        if project:
            conditions.append({"field": "request.project", "operator": "contains", "value": project.group(1).strip()})
        keywords, keyword_scope = _keyword_clauses(value)
        return RuleIntent(
            kind="create_rule",
            conditions={"all": conditions} if conditions else {},
            keywords=keywords,
            keyword_scope=keyword_scope,
            source=source,
        )
    if re.search(r"(?:暂停|停用|关闭).*(?:自动审批|审批规则)", value):
        return RuleIntent(kind="disable_rule", source=source)
    if re.search(r"(?:查看|列出).*(?:自动审批|审批规则)", value):
        return RuleIntent(kind="list_rules", source=source)
    return None


def rule_intent_to_schema(intent: RuleIntent) -> dict[str, object]:
    """Convert a RuleIntent into the restricted schema-v2 draft for preview."""
    if intent.kind not in {"create_rule", "preview_rule"}:
        raise ValueError("only create_rule and preview_rule intents produce rule drafts")
    conditions = dict(intent.conditions)
    keywords = dict(intent.keywords)
    if not conditions.get("all") and not any(keywords.values()):
        raise ValueError("at least one explicit rule condition or keyword is required")
    schema: dict[str, object] = {
        "schema_version": 2,
        "enabled": bool(intent.enabled),
        "priority": int(intent.priority),
        "action": {"type": intent.action, "auto_execute": True},
        "conditions": conditions,
        "limits": dict(intent.limits),
        "unknown_policy": intent.unknown_policy,
        "conflict_policy": intent.conflict_policy,
    }
    if keywords:
        schema["keywords"] = keywords
    if intent.keyword_scope:
        schema["keyword_scope"] = list(intent.keyword_scope)
    return schema


__all__ = ["parse_oa_intent", "parse_rule_intent", "rule_intent_to_schema"]
