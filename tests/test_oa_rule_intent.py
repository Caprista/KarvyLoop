from karvyloop.oa.domain import RuleIntent
from karvyloop.oa.intent import parse_rule_intent, rule_intent_to_schema


def test_create_rule_is_explicit_only():
    assert parse_rule_intent("创建自动审批规则") == RuleIntent(kind="create_rule")
    assert parse_rule_intent("帮我处理报销") is None


def test_compact_payment_rule_is_not_misclassified_as_start_approval():
    intent = parse_rule_intent("帮我创建一条付款类型的审批金额小于100时候自动审批")
    assert intent is not None
    assert intent.kind == "create_rule"
    assert {item["field"] for item in intent.conditions["all"]} == {"workflow.name", "request.amount"}
    assert {item["value"] for item in intent.conditions["all"]} == {"付款", 100.0}


def test_preview_disable_and_list_rule_intents():
    assert parse_rule_intent("预览自动审批规则").kind == "preview_rule"
    assert parse_rule_intent("暂停所有自动审批").kind == "disable_rule"
    assert parse_rule_intent("查看审批规则").kind == "list_rules"


def test_rule_conditions_are_limited_to_known_fields():
    intent = parse_rule_intent("创建自动审批规则，流程为报销，部门为财务部，金额不超过5000，项目包含华东")
    assert intent.conditions["all"] == [
        {"field": "workflow.name", "operator": "eq", "value": "报销"},
        {"field": "applicant.department", "operator": "eq", "value": "财务部"},
        {"field": "request.amount", "operator": "lte", "value": 5000.0},
        {"field": "request.project", "operator": "contains", "value": "华东"},
    ]


def test_rule_intent_to_schema_requires_explicit_conditions():
    intent = parse_rule_intent("创建自动审批规则，金额不超过5000")
    schema = rule_intent_to_schema(intent)
    assert schema["schema_version"] == 2
    assert schema["action"] == {"type": "approve", "auto_execute": True}


def test_keyword_rule_phrases_are_normalized_into_schema():
    intent = parse_rule_intent(
        "创建自动审批规则，关键词包含出差，任一关键词为上海、杭州，排除补贴、超标"
    )
    assert intent.keywords == {
        "all": ["出差"],
        "any": ["上海", "杭州"],
        "none": ["补贴", "超标"],
    }
    schema = rule_intent_to_schema(intent)
    assert schema["keywords"] == intent.keywords


def test_keyword_only_rule_can_be_previewed():
    intent = parse_rule_intent("创建自动审批规则，申请说明包含出差")
    assert intent.keywords == {"all": ["出差"]}
    assert rule_intent_to_schema(intent)["conditions"] == {}


def test_project_condition_is_not_accidentally_a_keyword_clause():
    intent = parse_rule_intent("创建自动审批规则，项目包含华东")
    assert intent.conditions["all"] == [
        {"field": "request.project", "operator": "contains", "value": "华东"}
    ]
    assert intent.keywords == {}
