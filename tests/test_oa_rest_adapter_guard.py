"""Focused regression tests for the OA request-id guard.

The remote adapter is an optional MCP stage, so this module is skipped when
the optional ``mcp`` dependency is not installed.
"""
import importlib.util
from datetime import datetime
from pathlib import Path

import json

import pytest


pytest.importorskip("mcp")

_PATH = Path(__file__).parents[1] / "karvyloop" / "oa" / "rest_adapter.py"
_SPEC = importlib.util.spec_from_file_location("oa_rest_adapter_guard_test", _PATH)
assert _SPEC and _SPEC.loader
oa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(oa)


@pytest.fixture(autouse=True)
def isolate_rule_state(tmp_path, monkeypatch):
    role_dir = tmp_path / "OA_A"
    monkeypatch.setattr(oa, "ROLE_DIR", role_dir)
    monkeypatch.setattr(oa, "RULES_PATH", role_dir / "approval_rules.json")
    monkeypatch.setattr(oa, "HABITS_PATH", role_dir / "approval_habits.json")
    monkeypatch.setattr(oa, "PREVIEWS_PATH", role_dir / "approval_rule_previews.json")
    oa._PREVIEW_MEMORY.clear()


def _ok_todos(request_id: str):
    return {"data": {"result": {"code": "SUCCESS", "data": [{"requestId": request_id}]}}}


def _ok_oa_list(request_id: str):
    """Actual OA envelope: data.result is the record list, not a result object."""
    return {"success": True, "code": 200, "data": {"result": [{"requestId": request_id}]}}


def _permission():
    return {"data": {"result": {"code": "NO_PERMISSION"}}}


def _detail(amount=100, workflow_id="wf-1"):
    return {"data": {"result": {"code": "SUCCESS", "data": {
        "requestName": "差旅报销", "creatorName": "张三", "amount": amount,
        "workflowBaseInfo": {"workflowId": workflow_id, "workflowName": "差旅报销"},
    }}}}


def _v2_rule(**overrides):
    rule = {
        "schema_version": 2, "rule_id": "rule-v2", "enabled": True, "priority": 10,
        "action": {"type": "approve", "auto_execute": True},
        "conditions": {"all": [
            {"field": "workflow.id", "operator": "eq", "value": "wf-1"},
            {"field": "request.amount", "operator": "lte", "value": 500},
        ]},
        "limits": {},
    }
    rule.update(overrides)
    return rule


def test_detail_rejects_request_id_not_in_current_todos(monkeypatch):
    monkeypatch.setattr(oa, "invoke", lambda method, params: _ok_todos("other") if method == "getDoingWorkflowRequestList" else pytest.fail(method))
    result = oa.get_workflow_request("stale")
    assert result["error_category"] == "STALE_REQUEST"
    assert result["request_id"] == "stale"


def test_detail_permission_is_classified_after_live_todo_check(monkeypatch):
    monkeypatch.setattr(oa, "invoke", lambda method, params: _ok_todos("r1") if method == "getDoingWorkflowRequestList" else _permission())
    result = oa.get_workflow_request("r1")
    assert result["error_category"] == "DETAIL_PERMISSION_DENIED"


def test_actual_oa_list_envelope_does_not_create_false_stale(monkeypatch):
    calls = []

    def fake_invoke(method, params):
        calls.append(method)
        if method == "getDoingWorkflowRequestList":
            return _ok_oa_list("231442")
        return _detail()

    monkeypatch.setattr(oa, "invoke", fake_invoke)
    result = oa.get_workflow_request("231442")
    assert result["data"]["result"]["data"]["requestName"] == "差旅报销"
    assert calls == ["getDoingWorkflowRequestList", "getWorkflowRequest"]


def test_request_summary_omits_large_detail_payload(monkeypatch):
    payload = _detail()
    payload["data"]["result"]["data"]["workflowDetailTableInfos"] = [
        {"filedHtmlShow": "x" * 100_000}
    ]

    monkeypatch.setattr(
        oa,
        "invoke",
        lambda method, _params: _ok_oa_list("r1") if method == "getDoingWorkflowRequestList" else payload,
    )

    result = oa.get_workflow_request_summary("r1")

    assert result["request_id"] == "r1"
    assert result["workflow_name"] == "差旅报销"
    assert "workflowDetailTableInfos" not in result
    assert len(__import__("json").dumps(result, ensure_ascii=False)) < 5_000


def test_submit_permission_is_classified_after_live_todo_check(monkeypatch):
    calls = []

    def fake_invoke(method, params):
        calls.append(method)
        if method == "getDoingWorkflowRequestList":
            return _ok_todos("r1")
        if method == "getWorkflowRequest":
            return {"data": {"result": {"code": "SUCCESS", "data": {"requestName": "流程", "workflowBaseInfo": {"workflowName": "流程"}}}}}
        return _permission()

    monkeypatch.setattr(oa, "invoke", fake_invoke)
    result = oa.submit_request("r1")
    assert result["error_category"] == "ACTION_PERMISSION_DENIED"
    assert calls == ["getDoingWorkflowRequestList", "getWorkflowRequest", "submitRequest"]


def test_request_ids_ignore_envelope_metadata():
    response = {
        "requestId": "envelope-id",
        "data": {"result": {"code": "SUCCESS", "requestId": "result-id",
                              "data": [{"requestId": "todo-1"}]}}
    }
    assert oa._request_ids(response) == ["todo-1"]


def test_request_ids_accept_case_insensitive_nested_todo_identifiers(monkeypatch):
    """One canonical extractor must feed both the snapshot and live guard."""
    response = {
        "data": {"result": {"code": "SUCCESS", "data": [{
            "requestNo": "CFLDLBX202609140993",
            "workflow": {"workflowrequestid": 229016},
        }]}}
    }
    calls = []

    def fake_invoke(method, params):
        calls.append((method, params))
        if method == "getDoingWorkflowRequestList":
            return response
        if method == "getWorkflowRequest":
            return _detail()
        pytest.fail(method)

    monkeypatch.setattr(oa, "invoke", fake_invoke)
    assert oa._request_ids(response) == ["229016"]
    listed = oa.get_doing_workflow_request_list()
    assert listed["index_map"] == {"1": "229016"}
    result = oa.get_workflow_request("229016")
    assert "error_category" not in result
    assert calls[-1] == ("getWorkflowRequest", {"requestId": "229016"})


def test_business_number_is_a_diagnostic_id_error_not_stale(monkeypatch):
    """A displayed business number is not proof that its OA request ID expired."""
    business_no = "CFLDLBX202609140993"
    monkeypatch.setattr(oa, "invoke", lambda method, _params:
                        {"data": {"result": {"code": "SUCCESS", "data": [{
                            "requestNo": business_no, "requestId": "229016",
                        }]}}} if method == "getDoingWorkflowRequestList" else pytest.fail(method))

    result = oa.get_workflow_request(business_no)

    assert result["error_category"] == "TODO_ID_UNRESOLVED"
    assert result["request_id"] == business_no
    assert result["reason"]


@pytest.mark.parametrize(
    ("operation", "args", "action_method"),
    [
        ("submit_request", ("r1",), "submitRequest"),
        ("reject_request", ("r1", "材料不完整", 1), "rejectRequest"),
        ("forward_request", ("r1", "李四"), "forwardRequest"),
    ],
)
def test_detail_failure_blocks_all_manual_actions(monkeypatch, operation, args, action_method):
    """No approval-changing endpoint may run unless details are business-successful."""
    calls = []

    def fake_invoke(method, _params):
        calls.append(method)
        if method == "getDoingWorkflowRequestList":
            return _ok_todos("r1")
        if method == "getWorkflowRequest":
            return {"data": {"result": {"code": "FAILED", "message": "detail unavailable"}}}
        pytest.fail(f"unexpected state-changing call: {method}")

    monkeypatch.setattr(oa, "invoke", fake_invoke)
    result = getattr(oa, operation)(*args)

    assert result["error_category"] == "DETAIL_REQUEST_FAILED"
    assert action_method not in calls


def test_todo_list_reads_all_pages(monkeypatch):
    calls = []

    def fake_invoke(method, params):
        calls.append(params["pageNo"])
        if params["pageNo"] == "1":
            return {"data": {"result": {"code": "SUCCESS", "hasMore": True,
                                          "data": [{"requestId": "r1"}]}}}
        return {"data": {"result": {"code": "SUCCESS", "hasMore": False,
                                      "data": [{"requestId": "r2"}]}}}

    monkeypatch.setattr(oa, "invoke", fake_invoke)
    result = oa.get_doing_workflow_request_list(page_size="1")
    assert calls == ["1", "2"]
    assert result["index_map"] == {"1": "r1", "2": "r2"}
    assert result["data"]["result"]["data"] == [{"requestId": "r1"}, {"requestId": "r2"}]


def test_todo_pagination_parses_string_boolean(monkeypatch):
    calls = []

    def fake_invoke(_method, params):
        calls.append(params["pageNo"])
        return {"data": {"result": {"code": "SUCCESS", "hasMore": "false",
                                      "data": [{"requestId": "r1"}]}}}

    monkeypatch.setattr(oa, "invoke", fake_invoke)
    oa.get_doing_workflow_request_list(page_size="1")
    assert calls == ["1"]


def test_auto_approve_todo_permission_is_structured(monkeypatch):
    monkeypatch.setattr(oa, "_load_rules", lambda: {"rules": [{
        "rule_id": "rule-1", "enabled": True, "auto_execute": True,
        "action": "批准", "workflow_name": "流程", "workflow_id": "", "applicant": "",
    }]})
    monkeypatch.setattr(oa, "invoke", lambda *_args, **_kwargs: _permission())
    result = oa.auto_approve_by_rule("r1", "rule-1")
    assert result["error_category"] == "TODO_PERMISSION_DENIED"
    assert result["status"] == "skipped"


def test_set_natural_language_rule_persists_plain_text_fields(monkeypatch):
    stored = {"version": 3, "updated_at": None, "rules": []}
    persisted = []
    monkeypatch.setattr(oa, "_load_rules", lambda: stored)
    monkeypatch.setattr(oa, "_save_rules", lambda data: persisted.append(data))

    result = oa.set_approval_rule(
        name="小额差旅自动批准",
        condition="申请金额不超过500元，且属于正常出差",
        examples_json=json.dumps([{"summary": "客户拜访"}], ensure_ascii=False),
    )

    rule = result["rule"]
    assert result["schema_version"] == 3
    assert rule["name"] == "小额差旅自动批准"
    assert rule["condition"] == "申请金额不超过500元，且属于正常出差"
    assert rule["examples"] == [{"summary": "客户拜访"}]
    assert rule["action"] == "approve"
    assert persisted[0]["rules"][0] == rule


def test_summary_returns_natural_language_rule_candidates(monkeypatch):
    rules = {"version": 3, "rules": [{
        "rule_id": "rule-nl", "name": "出差自动批准", "condition": "属于正常出差申请",
        "examples": [{"request_id": "old-1", "summary": "客户拜访"}],
        "action": "approve", "enabled": True,
    }]}
    monkeypatch.setattr(oa, "_load_rules", lambda: rules)
    monkeypatch.setattr(
        oa, "invoke",
        lambda method, _params: _ok_todos("r1") if method == "getDoingWorkflowRequestList" else _detail(),
    )

    result = oa.get_workflow_request_summary("r1")

    assert result["approval_rule_candidates"] == [{
        "rule_id": "rule-nl", "name": "出差自动批准", "condition": "属于正常出差申请",
        "examples": [{"request_id": "old-1", "summary": "客户拜访"}], "action": "approve",
    }]


def test_natural_language_rule_requires_explicit_match(monkeypatch):
    stored = {"version": 3, "rules": [{
        "rule_id": "rule-nl", "name": "出差自动批准", "condition": "属于正常出差申请",
        "examples": [], "action": "approve", "enabled": True,
    }]}
    calls = []
    monkeypatch.setattr(oa, "_load_rules", lambda: stored)
    monkeypatch.setattr(oa, "invoke", lambda method, _params: calls.append(method) or pytest.fail(method))

    result = oa.auto_approve_by_rule("r1", "rule-nl", decision="UNCERTAIN", match_reason="信息不足")

    assert result["status"] == "skipped"
    assert calls == []


def test_natural_language_rule_match_approves_and_records_reason(monkeypatch):
    stored = {"version": 3, "updated_at": None, "rules": [{
        "rule_id": "rule-nl", "name": "出差自动批准", "condition": "属于正常出差申请",
        "examples": [], "action": "approve", "enabled": True,
    }]}
    calls = []
    todo_reads = 0

    def fake_invoke(method, _params):
        nonlocal todo_reads
        calls.append(method)
        if method == "getDoingWorkflowRequestList":
            todo_reads += 1
            return _ok_todos("r1") if todo_reads == 1 else _ok_todos("other")
        if method == "getWorkflowRequest":
            return _detail()
        return {"code": "SUCCESS"}

    monkeypatch.setattr(oa, "_load_rules", lambda: stored)
    monkeypatch.setattr(oa, "_save_rules", lambda _data: None)
    monkeypatch.setattr(oa, "_record_decision", lambda *_args: {})
    monkeypatch.setattr(oa, "invoke", fake_invoke)

    result = oa.auto_approve_by_rule(
        "r1", "rule-nl", decision="MATCH", match_reason="审批内容明确属于正常出差申请"
    )

    assert result["status"] == "approved"
    assert calls == ["getDoingWorkflowRequestList", "getWorkflowRequest", "submitRequest", "getDoingWorkflowRequestList"]
    assert stored["rules"][0]["executions"][0]["match_reason"] == "审批内容明确属于正常出差申请"


def test_preview_v2_rule_is_read_only_and_explains_match(monkeypatch):
    calls = []

    def fake_invoke(method, _params):
        calls.append(method)
        return _ok_todos("r1") if method == "getDoingWorkflowRequestList" else _detail()

    monkeypatch.setattr(oa, "invoke", fake_invoke)
    result = oa.preview_approval_rule(__import__("json").dumps(_v2_rule()), "r1")
    assert result["preview"]["matched"] is True
    assert result["preview"]["explanation"]["reason"] == "全部条件满足"
    assert calls == ["getDoingWorkflowRequestList", "getWorkflowRequest"]


def test_auto_approve_v2_uses_matcher_and_records_execution(monkeypatch):
    stored = {"version": 2, "updated_at": None, "rules": [_v2_rule()]}
    calls = []
    todo_reads = 0

    def fake_invoke(method, _params):
        nonlocal todo_reads
        calls.append(method)
        if method == "getDoingWorkflowRequestList":
            todo_reads += 1
            return _ok_todos("r1") if todo_reads == 1 else _ok_oa_list("other")
        if method == "getWorkflowRequest":
            return _detail()
        return {"data": {"result": {"code": "SUCCESS", "data": {}}}}

    monkeypatch.setattr(oa, "_load_rules", lambda: stored)
    monkeypatch.setattr(oa, "_save_rules", lambda data: None)
    monkeypatch.setattr(oa, "_record_decision", lambda *_args: {})
    monkeypatch.setattr(oa, "invoke", fake_invoke)
    result = oa.auto_approve_by_rule("r1", "rule-v2")
    assert result["status"] == "approved"
    assert calls == ["getDoingWorkflowRequestList", "getWorkflowRequest", "submitRequest", "getDoingWorkflowRequestList"]
    assert stored["rules"][0]["executions"][0]["request_id"] == "r1"


@pytest.mark.parametrize(
    "response",
    [
        {"code": "SUCCESS"},
        {"success": True, "code": 200},
        {"data": {"result": {"code": "SUCCESS"}}},
    ],
)
def test_business_success_accepts_known_oa_envelopes(response):
    assert oa._business_success(response) is True


def test_business_success_rejects_explicit_req_fail_message():
    response = {
        "success": True,
        "code": 200,
        "reqFailMsg": {"msgInfo": {"errMessage": "当前节点不可操作"}},
    }
    assert oa._business_success(response) is False
    assert oa._response_shape(response)["failure_message"] == "当前节点不可操作"


def test_auto_approve_does_not_claim_success_when_todo_remains(monkeypatch):
    stored = {"version": 2, "updated_at": None, "rules": [_v2_rule()]}
    todo_reads = 0

    def fake_invoke(method, _params):
        nonlocal todo_reads
        if method == "getDoingWorkflowRequestList":
            todo_reads += 1
            return _ok_todos("r1")
        if method == "getWorkflowRequest":
            return _detail()
        return {"code": "SUCCESS"}

    monkeypatch.setattr(oa, "_load_rules", lambda: stored)
    monkeypatch.setattr(oa, "_save_rules", lambda data: None)
    monkeypatch.setattr(oa, "_record_decision", lambda *_args: {})
    monkeypatch.setattr(oa, "invoke", fake_invoke)
    result = oa.auto_approve_by_rule("r1", "rule-v2")
    assert result["status"] == "failed"
    assert result["error_category"] == "ACTION_UNCONFIRMED"


def test_auto_approve_v2_missing_condition_field_never_submits(monkeypatch):
    stored = {"version": 2, "updated_at": None, "rules": [_v2_rule(
        conditions={"all": [{"field": "applicant.department", "operator": "eq", "value": "财务部"}]}
    )]}
    calls = []

    def fake_invoke(method, _params):
        calls.append(method)
        return _ok_todos("r1") if method == "getDoingWorkflowRequestList" else _detail()

    monkeypatch.setattr(oa, "_load_rules", lambda: stored)
    monkeypatch.setattr(oa, "invoke", fake_invoke)
    result = oa.auto_approve_by_rule("r1", "rule-v2")
    assert result["status"] == "skipped"
    assert "submitRequest" not in calls


def test_auto_approve_v2_respects_daily_limit(monkeypatch):
    current = datetime.now().astimezone().isoformat(timespec="seconds")
    stored = {"version": 2, "updated_at": None, "rules": [_v2_rule(
        limits={"max_per_day": 1}, executions=[{"request_id": "old", "amount": 1, "occurred_at": current}]
    )]}
    calls = []

    def fake_invoke(method, _params):
        calls.append(method)
        return _ok_todos("r1") if method == "getDoingWorkflowRequestList" else _detail()

    monkeypatch.setattr(oa, "_load_rules", lambda: stored)
    monkeypatch.setattr(oa, "invoke", fake_invoke)
    result = oa.auto_approve_by_rule("r1", "rule-v2")
    assert result["status"] == "skipped"
    assert result["reason"] == "已达到规则每日执行数量上限"
    assert "submitRequest" not in calls


def test_set_v2_rule_json_persists_normalized_rule(monkeypatch):
    stored = {"version": 2, "updated_at": None, "rules": []}
    persisted = []
    monkeypatch.setattr(oa, "_load_rules", lambda: stored)
    monkeypatch.setattr(oa, "_save_rules", lambda data: persisted.append(data))
    rule = _v2_rule(rule_id="")
    token = oa._issue_preview_token(oa.normalize_rule(rule), source="test")
    result = oa.set_approval_rule(rule_json=__import__("json").dumps(rule), preview_token=token)
    assert result["schema_version"] == 2
    assert persisted[0]["rules"][0]["schema_version"] == 2
    assert persisted[0]["rules"][0]["rule_id"]


def test_preview_token_cannot_cross_scope():
    rule = oa.normalize_rule(_v2_rule(rule_id=""))
    token = oa._issue_preview_token(rule, source="test", scope_key="instance/chat-a/user-a")
    with __import__("pytest").raises(ValueError, match="another session"):
        oa._consume_preview_token(token, rule, scope_key="instance/chat-b/user-a")
