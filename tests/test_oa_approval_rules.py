from datetime import datetime, timezone

import pytest

from karvyloop.oa.approval_rules import RuleValidationError, explain_rule_match, matches_rule, normalize_rule


def rule(**overrides):
    value = {
        "schema_version": 2,
        "enabled": True,
        "priority": 100,
        "action": {"type": "approve", "auto_execute": True},
        "conditions": {"all": [
            {"field": "workflow.id", "operator": "eq", "value": "wf-1"},
            {"field": "request.amount", "operator": "lte", "value": 5000},
        ]},
        "limits": {"expires_at": "2030-01-01T00:00:00+00:00"},
    }
    value.update(overrides)
    return value


def test_matches_multidimensional_context():
    context = {"workflow": {"id": "wf-1"}, "request": {"amount": 4500}}
    assert matches_rule(rule(), context, now=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_missing_field_is_unknown_and_never_matches():
    result = explain_rule_match(rule(), {"workflow": {"id": "wf-1"}}, now=datetime.now(timezone.utc))
    assert result["matched"] is False
    assert any(item["known"] is False for item in result["conditions"])


def test_rejects_unsafe_field_and_operator():
    with pytest.raises(RuleValidationError):
        normalize_rule(rule(conditions={"all": [{"field": "request.__class__", "operator": "eq", "value": 1}]}))
    with pytest.raises(RuleValidationError):
        normalize_rule(rule(conditions={"all": [{"field": "request.amount", "operator": "eval", "value": 1}]}))


def test_expired_rule_does_not_match():
    result = explain_rule_match(rule(limits={"expires_at": "2020-01-01T00:00:00+00:00"}),
                                {"workflow": {"id": "wf-1"}, "request": {"amount": 1}},
                                now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert result["matched"] is False
    assert result["reason"] == "规则已过期"
