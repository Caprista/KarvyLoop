#!/Users/enjoy/Karvyloop/.venv/bin/python
from __future__ import annotations
import hashlib
import json, logging, os, sys, tempfile, urllib.error, urllib.request
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from mcp.server.fastmcp import FastMCP

# The MCP adapter is deployed outside the KarvyLoop package.  Prefer the
# installed project, but keep its location configurable for standalone runs.
_PROJECT_ROOT = Path(os.environ.get("KARVYLOOP_ROOT", str(Path.home() / "KarvyLoop")))
if _PROJECT_ROOT.is_dir() and str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
try:
    from karvyloop.oa.approval_rules import (RuleValidationError, explain_rule_match,
                                             matches_rule, normalize_rule)
    from karvyloop.oa.keyword_matcher import match_keywords
except ImportError as error:  # pragma: no cover - exercised by remote startup
    raise RuntimeError(
        "OA rule engine is unavailable. Set KARVYLOOP_ROOT to the KarvyLoop project "
        "directory before starting the OA MCP adapter."
    ) from error

BASE_URL = "http://127.0.0.1:3000"
ROLE_DIR = Path.home() / ".karvyloop/roles/OA_A"
IDENTITY_PATH = ROLE_DIR / "oa_identity.json"
HABITS_PATH = ROLE_DIR / "approval_habits.json"
RULES_PATH = ROLE_DIR / "approval_rules.json"
PREVIEWS_PATH = ROLE_DIR / "approval_rule_previews.json"
PREVIEW_TTL_SECONDS = 10 * 60
_PREVIEW_MEMORY: dict[str, Any] = {}
ACTIONS = ("批准", "拒绝", "转发")
mcp = FastMCP("oa")
logger = logging.getLogger("karvyloop.oa")


class OARequestError(RuntimeError):
    """A structured, user-safe OA request failure."""

    def __init__(self, message: str, category: str):
        super().__init__(message)
        self.category = category


def _post_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}: {error.read().decode(errors='replace')}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Connection failed: {error.reason}") from error


def _oa_user_id() -> str:
    try:
        user_id = str(json.loads(IDENTITY_PATH.read_text()).get("user_id") or "").strip()
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"OA identity file is invalid: {error}") from error
    if not user_id:
        raise RuntimeError("OA user_id is not configured in OA_A/oa_identity.json")
    return user_id


def invoke(method: str, params: dict[str, Any]) -> dict[str, Any]:
    response = _post_json(f"{BASE_URL}/api/oa/invoke", {"method": method, "user_id": _oa_user_id(), "params": params})
    request_id = str(params.get("requestId") or "")
    logger.info("oa_invoke method=%s request_id=%s result_code=%s",
                method, request_id or "-", _result_code(response) or "UNKNOWN")
    return response


_SUCCESS_CODES = {"SUCCESS", "OK", "200", "0"}


def _response_failure_message(response: Any) -> str:
    """Extract a short OA failure message without returning the raw payload."""
    if not isinstance(response, dict):
        return ""
    failure = response.get("reqFailMsg")
    if isinstance(failure, dict):
        info = failure.get("msgInfo")
        if isinstance(info, dict):
            for key in ("errMessage", "detail", "message", "msg"):
                value = info.get(key)
                if isinstance(value, (str, int)) and str(value).strip():
                    return str(value).strip()[:500]
        for key in ("message", "msg", "error"):
            value = failure.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()[:500]
    for key in ("error", "message", "msg"):
        value = response.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()[:500]
    return ""


def _response_shape(response: Any) -> dict[str, Any]:
    """Return safe diagnostics for an OA response, never the response body."""
    if not isinstance(response, dict):
        return {"response_type": type(response).__name__}
    data = response.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    return {
        "top_level_keys": sorted(str(key) for key in response.keys()),
        "data_type": type(data).__name__,
        "result_type": type(result).__name__,
        "business_code": _result_code(response) or None,
        "failure_message": _response_failure_message(response) or None,
    }


def _business_success(response: dict[str, Any]) -> bool:
    """Recognize OA success envelopes while rejecting explicit failure details."""
    if not isinstance(response, dict):
        return False
    if response.get("reqFailMsg"):
        return False
    code = _result_code(response)
    if code in _SUCCESS_CODES:
        return True
    if _response_failure_message(response):
        return False
    # An explicit non-success code must not be overridden by success=true.
    has_explicit_code = (
        isinstance(response.get("code"), (str, int))
        or isinstance(((response.get("data") or {}).get("result")), dict)
        and isinstance(((response.get("data") or {}).get("result") or {}).get("code"), (str, int))
    )
    if has_explicit_code and code:
        return False
    # Some OA deployments return only success=true for submitRequest.
    return response.get("success") is True


def _result_code(response: Any) -> str:
    """Return the OA business code without assuming a particular envelope."""
    if not isinstance(response, dict):
        return ""
    result = ((response.get("data") or {}).get("result"))
    if isinstance(result, dict):
        code = result.get("code")
        if isinstance(code, (str, int)) and str(code).strip():
            return str(code).strip().upper()
    # submitRequest commonly places the business code at the top level even
    # when data.result is an object (or is omitted entirely).
    # The OA list endpoint uses a successful top-level HTTP-style code while
    # putting the actual records directly in data.result as a list.
    if isinstance(response.get("code"), (str, int)):
        return str(response["code"]).strip().upper()
    return ""


def _permission_category(method: str) -> str:
    if method in {"getWorkflowRequest", "getRequestOperatorInfo"}:
        return "DETAIL_PERMISSION_DENIED" if method == "getWorkflowRequest" else "OPERATOR_INFO_PERMISSION_DENIED"
    return "ACTION_PERMISSION_DENIED"


def _error_result(request_id: str, category: str, reason: str) -> dict[str, Any]:
    return {"status": "failed", "error_category": category, "request_id": str(request_id), "reason": reason}


def _todo_items(value: Any) -> list[dict[str, Any]]:
    """Extract only actual todo records, excluding envelope metadata."""
    if not isinstance(value, dict):
        return []
    result = value.get("data")
    if isinstance(result, dict):
        result = result.get("result")
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if not isinstance(result, dict):
        return []
    data = result.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "rows", "list", "records"):
            items = data.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


_TODO_REQUEST_ID_KEYS = frozenset({
    "requestid", "workflowrequestid", "workflow_request_id", "request_id",
})
_TODO_BUSINESS_NUMBER_KEYS = frozenset({
    "requestno", "requestcode", "requestnumber", "applyno", "billno",
})
_TODO_REQUEST_ID_CONTAINERS = ("request", "workflowrequest", "workflow", "data", "detail")


def _todo_request_id(item: dict[str, Any], depth: int = 0) -> str:
    """Extract the OA request ID from one todo item without using business numbers.

    Different OA deployments vary the casing and occasionally nest the request
    object. Keep the accepted key set deliberately narrow so that fields such
    as requestNo / billNo can never be mistaken for an action-capable ID.
    """
    for key, value in item.items():
        normalized = str(key).replace("-", "_").lower()
        if normalized in _TODO_REQUEST_ID_KEYS and value is not None and str(value).strip():
            return str(value).strip()
    if depth >= 2:
        return ""
    for key, value in item.items():
        normalized = str(key).replace("-", "_").lower()
        if normalized in _TODO_REQUEST_ID_CONTAINERS and isinstance(value, dict):
            request_id = _todo_request_id(value, depth + 1)
            if request_id:
                return request_id
    return ""


def _todo_request_ids(items: list[dict[str, Any]]) -> tuple[list[str], int]:
    """Return deduplicated IDs and the number of todo items lacking a safe ID."""
    ids: list[str] = []
    unresolved = 0
    for item in items:
        request_id = _todo_request_id(item)
        if not request_id:
            unresolved += 1
        elif request_id not in ids:
            ids.append(request_id)
    return ids, unresolved


def _todo_business_numbers(items: list[dict[str, Any]]) -> list[str]:
    """Read display-only business numbers so they can be rejected as action IDs."""
    found: list[str] = []
    for item in items:
        for key, value in item.items():
            normalized = str(key).replace("-", "_").lower()
            if normalized in _TODO_BUSINESS_NUMBER_KEYS and value is not None and str(value).strip():
                number = str(value).strip()
                if number not in found:
                    found.append(number)
    return found


def _todo_page_has_more(response: Any, item_count: int, page_size: int) -> bool:
    """Honor explicit OA pagination metadata, otherwise use a full-page heuristic."""
    result = response.get("data", {}).get("result", {}) if isinstance(response, dict) else {}
    if not isinstance(result, dict):
        result = {}
    def as_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                return True
            if normalized in {"false", "0", "no", "n"}:
                return False
        return None

    for key in ("hasMore", "hasNext", "more"):
        if key in result:
            parsed = as_bool(result[key])
            if parsed is not None:
                return parsed
    data = result.get("data")
    if isinstance(data, dict):
        for key in ("hasMore", "hasNext", "more"):
            if key in data:
                parsed = as_bool(data[key])
                if parsed is not None:
                    return parsed
        for key in ("totalCount", "total", "count"):
            if key in data:
                try:
                    return item_count < int(data[key])
                except (TypeError, ValueError):
                    pass
    for key in ("totalCount", "total", "count"):
        if key in result:
            try:
                return item_count < int(result[key])
            except (TypeError, ValueError):
                pass
    return item_count >= page_size


def _all_todo_snapshot_ids(page_size: int = 100) -> tuple[list[str], int, list[str]]:
    """Read all OA todo pages, returning IDs, unresolved count and business numbers."""
    ids: list[str] = []
    unresolved = 0
    business_numbers: list[str] = []
    page = 1
    while page <= 100:
        response = invoke("getDoingWorkflowRequestList", {"pageNo": str(page), "pageSize": str(page_size)})
        if _result_code(response) == "NO_PERMISSION":
            raise OARequestError("当前身份无权查询待办列表", "TODO_PERMISSION_DENIED")
        items = _todo_items(response)
        page_ids, page_unresolved = _todo_request_ids(items)
        unresolved += page_unresolved
        for number in _todo_business_numbers(items):
            if number not in business_numbers:
                business_numbers.append(number)
        for request_id in page_ids:
            if request_id not in ids:
                ids.append(request_id)
        if not _todo_page_has_more(response, len(items), page_size):
            break
        page += 1
    return ids, unresolved, business_numbers


def _all_todo_request_ids(page_size: int = 100) -> list[str]:
    """Read all OA todo pages, returning safely resolved IDs in server order."""
    return _all_todo_snapshot_ids(page_size)[0]


def _current_todo_check(request_id: str) -> dict[str, Any] | None:
    """Return a structured failure when request_id is not in the live todo set."""
    try:
        todo_ids, unresolved, business_numbers = _all_todo_snapshot_ids()
    except OARequestError as error:
        return _error_result(request_id, error.category, str(error))
    except RuntimeError as error:
        return _error_result(request_id, "TODO_CHECK_FAILED", f"无法确认当前待办：{error}")
    if str(request_id) not in todo_ids:
        if str(request_id) in business_numbers:
            return _error_result(
                request_id,
                "TODO_ID_UNRESOLVED",
                "传入的是申请单号而非 OA requestId；请使用当前待办快照中的序号重新选择",
            )
        if unresolved:
            return _error_result(
                request_id,
                "TODO_ID_UNRESOLVED",
                "当前待办列表含有无法识别 requestId 的记录，已停止详情或审批操作",
            )
        return _error_result(request_id, "STALE_REQUEST", "该审批已不在当前待办，编号或 requestId 可能已过期")
    return None


def _detail_data(response: dict[str, Any]) -> dict[str, Any]:
    return ((((response.get("data") or {}).get("result") or {}).get("data")) or {})


def _find_text(value: Any, keys: tuple[str, ...]) -> str:
    if isinstance(value, dict):
        lowered = {str(k).lower(): v for k, v in value.items()}
        for key in keys:
            found = lowered.get(key.lower())
            if isinstance(found, (str, int)) and str(found).strip():
                return str(found).strip()
        for child in value.values():
            found = _find_text(child, keys)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_text(child, keys)
            if found:
                return found
    return ""


def _find_value(value: Any, keys: tuple[str, ...]) -> Any:
    """Find a named OA field without coercing a missing value into a matchable value."""
    if isinstance(value, dict):
        lowered = {str(k).lower(): v for k, v in value.items()}
        for key in keys:
            if key.lower() in lowered and lowered[key.lower()] is not None:
                return lowered[key.lower()]
        for child in value.values():
            found = _find_value(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_value(child, keys)
            if found is not None:
                return found
    return None


def _workflow_context(request_id: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    detail = detail if detail is not None else invoke("getWorkflowRequest", {"requestId": request_id})
    if _result_code(detail) == "NO_PERMISSION":
        raise OARequestError("OA 详情接口拒绝访问", _permission_category("getWorkflowRequest"))
    if not _business_success(detail):
        raise OARequestError("OA 详情接口未返回业务成功", "DETAIL_REQUEST_FAILED")
    data = _detail_data(detail)
    base = data.get("workflowBaseInfo") or {}
    applicant = _find_text(data, ("creatorName", "createrName", "applicantName", "requestCreatorName", "createName")) or "未识别申请人"
    workflow_id = str(base.get("workflowId") or "")
    workflow_name = str(base.get("workflowName") or data.get("requestName") or "未识别流程")
    applicant_id = _find_text(data, ("creatorId", "createrId", "applicantId", "requestCreatorId"))
    department = _find_text(data, ("creatorDepartment", "applicantDepartment", "departmentName", "deptName"))
    rule_context = {
        "workflow": {"id": workflow_id or None, "name": workflow_name or None},
        "applicant": {"id": applicant_id or None, "name": applicant or None, "department": department or None},
        "request": {
            "title": str(data.get("requestName") or data.get("title") or "") or None,
            "description": _find_value(data, ("description", "requestDescription", "remark", "reason")),
            "form_text": _find_value(data, ("formText", "formContent", "content", "detail")),
            "amount": _find_value(data, ("amount", "money", "totalAmount", "applyAmount")),
            "currency": _find_value(data, ("currency", "currencyCode")),
            "project": _find_value(data, ("project", "projectName")),
            "cost_center": _find_value(data, ("costCenter", "costCenterName")),
            "created_at": _find_value(data, ("createTime", "createdAt", "submitTime", "submittedAt")),
            "tags": _find_value(data, ("tags", "tagList")),
        },
        "attachment": {"count": _find_value(data, ("attachmentCount", "attachCount", "fileCount"))},
        "risk": {"level": _find_value(data, ("riskLevel", "risk"))},
    }
    return {
        "request_id": str(request_id),
        "request_name": str(data.get("requestName") or ""),
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "applicant": applicant,
        "rule_context": rule_context,
    }


def _empty_habits() -> dict[str, Any]:
    return {"version": 2, "updated_at": None, "workflow_types": {}, "type_applicants": {}}


def _load_habits() -> dict[str, Any]:
    if not HABITS_PATH.exists():
        return _empty_habits()
    try:
        data = json.loads(HABITS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return _empty_habits()
    data["version"] = 2
    data.setdefault("updated_at", None)
    data.setdefault("workflow_types", {})
    data.setdefault("type_applicants", {})
    return data


def _save_habits(data: dict[str, Any]) -> None:
    HABITS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="approval_habits.", suffix=".tmp", dir=HABITS_PATH.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(tmp, HABITS_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _empty_rules() -> dict[str, Any]:
    return {"version": 3, "updated_at": None, "rules": []}


def _load_rules() -> dict[str, Any]:
    if not RULES_PATH.exists():
        return _empty_rules()
    try:
        data = json.loads(RULES_PATH.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"approval rules file is invalid: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("rules", []), list):
        raise RuntimeError("approval rules file has an invalid schema")
    # The container can hold legacy rules while natural-language rules use schema v3.
    data["version"] = max(3, int(data.get("version") or 1))
    data.setdefault("updated_at", None)
    return data


def _save_rules(data: dict[str, Any]) -> None:
    """Atomically persist only user-declared rules, separate from learned habits."""
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="approval_rules.", suffix=".tmp", dir=RULES_PATH.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(tmp, RULES_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load_previews() -> dict[str, Any]:
    try:
        data = json.loads(PREVIEWS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return dict(_PREVIEW_MEMORY)
    return data if isinstance(data, dict) else {}


def _save_previews(data: dict[str, Any]) -> None:
    _PREVIEW_MEMORY.clear()
    _PREVIEW_MEMORY.update(data)
    try:
        ROLE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="approval_rule_previews.", suffix=".tmp", dir=ROLE_DIR)
    except OSError:
        return
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(tmp, PREVIEWS_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _rule_digest(rule: dict[str, Any]) -> str:
    payload = json.dumps(rule, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _issue_preview_token(rule: dict[str, Any], *, source: str, scope_key: str = "") -> str:
    token = uuid.uuid4().hex
    now = datetime.now().timestamp()
    previews = {key: value for key, value in _load_previews().items()
                if isinstance(value, dict) and float(value.get("expires_at", 0)) > now}
    previews[token] = {"digest": _rule_digest(rule), "created_at": now,
                       "expires_at": now + PREVIEW_TTL_SECONDS, "source": source,
                       "scope_key": str(scope_key or "")}
    _save_previews(previews)
    return token


def _consume_preview_token(token: str, rule: dict[str, Any], *, scope_key: str = "") -> None:
    token = str(token or "").strip()
    item = _load_previews().get(token)
    if not isinstance(item, dict) or float(item.get("expires_at", 0)) <= datetime.now().timestamp():
        raise ValueError("rule must be previewed and confirmed before saving")
    if item.get("digest") != _rule_digest(rule):
        raise ValueError("rule changed after preview; run preview again")
    if str(item.get("scope_key") or "") != str(scope_key or ""):
        raise ValueError("preview token belongs to another session")
    previews = _load_previews()
    previews.pop(token, None)
    _save_previews(previews)


def _rule_id() -> str:
    return f"rule-{datetime.now().astimezone().strftime('%Y%m%d%H%M%S%f')}"


def _validate_rule(*, workflow_name: str, workflow_id: str, applicant: str,
                   action: str, auto_execute: bool) -> None:
    if not workflow_name.strip() and not workflow_id.strip():
        raise ValueError("workflow_name or workflow_id is required")
    if action not in ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(ACTIONS)}")
    # Only approval has a safe, complete write path. Rejection and forwarding
    # require a reason/node or a recipient and therefore cannot be unattended.
    if auto_execute and action != "批准":
        raise ValueError("only 批准 rules may enable auto_execute")
    if applicant and applicant == "未识别申请人":
        raise ValueError("applicant must be a real, unambiguous name")


def _is_v2_rule(rule: dict[str, Any]) -> bool:
    return rule.get("schema_version") == 2


def _is_natural_language_rule(rule: dict[str, Any]) -> bool:
    return bool(str(rule.get("condition") or "").strip())


def _active_natural_language_rules() -> list[dict[str, Any]]:
    return [
        rule for rule in _load_rules()["rules"]
        if _is_natural_language_rule(rule)
        and rule.get("enabled")
        and not rule.get("deleted")
        and rule.get("action") in {"approve", "批准"}
    ]


def _rule_for_agent(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": str(rule.get("rule_id") or ""),
        "name": str(rule.get("name") or "自动审批规则"),
        "condition": str(rule.get("condition") or ""),
        "examples": rule.get("examples") if isinstance(rule.get("examples"), list) else [],
        "action": "approve",
    }


def _natural_language_rule_candidates() -> list[dict[str, Any]]:
    return [_rule_for_agent(rule) for rule in _active_natural_language_rules()]


def _legacy_rule_matches_context(rule: dict[str, Any], context: dict[str, str]) -> bool:
    if not rule.get("enabled") or not rule.get("auto_execute") or rule.get("action") != "批准":
        return False
    expires_at = str(rule.get("expires_at") or "").strip()
    if expires_at and datetime.fromisoformat(expires_at) <= datetime.now().astimezone():
        return False
    workflow_id = str(rule.get("workflow_id") or "").strip()
    workflow_name = str(rule.get("workflow_name") or "").strip()
    applicant = str(rule.get("applicant") or "").strip()
    return ((not workflow_id or workflow_id == context["workflow_id"])
            and (not workflow_name or workflow_name == context["workflow_name"])
            and (not applicant or applicant == context["applicant"]))


def _rule_matches_context(rule: dict[str, Any], context: dict[str, Any]) -> bool:
    """Match v2 through the domain matcher; retain v1 exact-match compatibility."""
    if _is_v2_rule(rule):
        try:
            keyword_result = match_keywords(rule, context["rule_context"])
            return bool(rule.get("enabled") and rule.get("action", {}).get("auto_execute")
                        and keyword_result.status == "MATCH"
                        and matches_rule(rule, context["rule_context"]))
        except (RuleValidationError, TypeError, ValueError):
            # Malformed persisted rules must never authorize an approval.
            return False
    return _legacy_rule_matches_context(rule, context)


def _matching_rule(context: dict[str, Any]) -> dict[str, Any] | None:
    """Return one highest-priority match; equal-priority matches are conflicts."""
    matched = [rule for rule in _load_rules()["rules"] if _rule_matches_context(rule, context)]
    if not matched:
        return None
    highest = max(int(item.get("priority") or 0) for item in matched)
    top = [item for item in matched if int(item.get("priority") or 0) == highest]
    if len(top) > 1:
        return {"conflict": True, "rule_ids": [str(item.get("rule_id") or "") for item in top], "priority": highest}
    return top[0]


def _v2_limit_reason(rule: dict[str, Any], context: dict[str, Any]) -> str:
    """Return a reason when an active v2 quota would be exceeded; otherwise empty."""
    if not _is_v2_rule(rule):
        return ""
    limits = rule.get("limits") or {}
    if not limits:
        return ""
    today = datetime.now().astimezone().date()
    executions = []
    for entry in rule.get("executions") or []:
        try:
            if datetime.fromisoformat(str(entry.get("occurred_at"))).astimezone().date() == today:
                executions.append(entry)
        except (TypeError, ValueError):
            continue
    if "max_per_day" in limits and len(executions) >= int(Decimal(str(limits["max_per_day"]))):
        return "已达到规则每日执行数量上限"
    if "max_amount_per_day" in limits:
        try:
            amount = Decimal(str(context["rule_context"]["request"]["amount"]))
            spent = sum((Decimal(str(item.get("amount"))) for item in executions), Decimal("0"))
            maximum = Decimal(str(limits["max_amount_per_day"]))
        except (InvalidOperation, KeyError, TypeError, ValueError):
            return "金额字段缺失或格式无效，不能确认规则金额上限"
        if spent + amount > maximum:
            return "将超过规则每日金额上限"
    return ""


def _record_rule_execution(data: dict[str, Any], rule: dict[str, Any], context: dict[str, Any],
                           *, match_reason: str = "") -> None:
    if not (_is_v2_rule(rule) or _is_natural_language_rule(rule)):
        return
    executions = rule.setdefault("executions", [])
    executions.append({
        "request_id": context["request_id"],
        "amount": context["rule_context"]["request"].get("amount"),
        "match_reason": match_reason.strip(),
        "occurred_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    rule["executions"] = executions[-500:]
    data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _save_rules(data)


def _request_ids(value: Any) -> list[str]:
    """Extract request IDs only from actual todo records."""
    return _todo_request_ids(_todo_items(value))[0]


def _detail_has_obvious_anomaly(data: Any) -> str:
    """Fail closed on the explicit missing-material/error signals returned by OA."""
    signals = ("材料缺失", "附件缺失", "资料缺失", "接口异常", "审批异常", "状态异常")
    if isinstance(data, str) and any(signal in data for signal in signals):
        return "审批详情包含材料缺失或异常标记"
    if isinstance(data, dict):
        for item in data.values():
            reason = _detail_has_obvious_anomaly(item)
            if reason:
                return reason
    elif isinstance(data, list):
        for item in data:
            reason = _detail_has_obvious_anomaly(item)
            if reason:
                return reason
    return ""


def _normal_counts(entry: dict[str, Any] | None) -> dict[str, int]:
    raw = (entry or {}).get("action_counts") or {}
    return {
        "批准": int(raw.get("批准", 0)),
        "拒绝": int(raw.get("拒绝", raw.get("退回", 0))),
        "转发": int(raw.get("转发", 0)),
    }


def _habit_summary(entry: dict[str, Any] | None, dimension: str) -> dict[str, Any]:
    counts = _normal_counts(entry)
    total = sum(counts.values())
    ranked = sorted(ACTIONS, key=lambda action: (-counts[action], ACTIONS.index(action)))
    action = ranked[0] if total else None
    support = counts[action] if action else 0
    return {
        "has_habit": bool(total),
        "dimension": dimension,
        "preselected_action": action,
        "rule": f"历史上通常选择{action}（{support}/{total}）" if action else "暂无真实审批样本",
        "sample_count": total,
        "confidence": round(support / total, 4) if total else 0.0,
        "support_count": support,
        "counterexamples": [{"action": item, "count": counts[item]} for item in ACTIONS if item != action and counts[item]],
        "updated_at": (entry or {}).get("updated_at"),
    }


def _dimension_key(context: dict[str, str]) -> str:
    workflow = context.get("workflow_id") or f"name:{context['workflow_name']}"
    return json.dumps([workflow, context.get("applicant") or "未识别申请人"], ensure_ascii=False, separators=(",", ":"))


def _increment(entry: dict[str, Any], context: dict[str, str], action: str, evidence: dict[str, Any], now: str) -> None:
    entry.update({"workflow_id": context.get("workflow_id"), "workflow_name": context["workflow_name"]})
    entry.setdefault("action_counts", {item: 0 for item in ACTIONS})
    counts = _normal_counts(entry)
    counts[action] += 1
    entry["action_counts"] = counts
    entry.setdefault("evidence", []).append({
        "request_id": context["request_id"], "request_name": context["request_name"],
        "applicant": context["applicant"], "action": action, "occurred_at": now, **evidence,
    })
    entry["evidence"] = entry["evidence"][-100:]
    entry["updated_at"] = now


def _record_decision(context: dict[str, str], action: str, evidence: dict[str, Any]) -> dict[str, Any]:
    data = _load_habits()
    workflow_key = context.get("workflow_id") or f"name:{context['workflow_name']}"
    exact_key = _dimension_key(context)
    exact = data["type_applicants"].setdefault(exact_key, {"applicant": context["applicant"]})
    aggregate = data["workflow_types"].setdefault(workflow_key, {})
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    _increment(exact, context, action, evidence, now)
    _increment(aggregate, context, action, evidence, now)
    data["updated_at"] = now
    _save_habits(data)
    return {
        "exact": _habit_summary(exact, "审批类型 × 申请人"),
        "workflow_type": _habit_summary(aggregate, "审批类型汇总"),
    }


def _habits_for_detail(response: dict[str, Any]) -> dict[str, Any]:
    data = _detail_data(response)
    base = data.get("workflowBaseInfo") or {}
    context = {
        "workflow_id": str(base.get("workflowId") or ""),
        "workflow_name": str(base.get("workflowName") or data.get("requestName") or "未识别流程"),
        "applicant": _find_text(data, ("creatorName", "createrName", "applicantName", "requestCreatorName", "createName")) or "未识别申请人",
    }
    habits = _load_habits()
    workflow_key = context["workflow_id"] or f"name:{context['workflow_name']}"
    exact = _habit_summary(habits["type_applicants"].get(_dimension_key(context)), "审批类型 × 申请人")
    aggregate = _habit_summary(habits["workflow_types"].get(workflow_key), "审批类型汇总")
    preferred = exact if exact["has_habit"] else aggregate
    return {"applicant": context["applicant"], "exact": exact, "workflow_type": aggregate, "preferred": preferred}


@mcp.tool()
def get_approval_habits() -> dict[str, Any]:
    """查询 OA_A 的真实历史审批偏好汇总。只读，不包含或创建任何用户规则。"""
    data = _load_habits()
    return {
        "version": data["version"],
        "updated_at": data["updated_at"],
        "workflow_types": [
            _habit_summary(entry, "审批类型汇总") | {
                "workflow_id": str(entry.get("workflow_id") or workflow_id),
                "workflow_name": str(entry.get("workflow_name") or "未识别流程"),
            }
            for workflow_id, entry in data["workflow_types"].items()
        ],
        "type_applicants": [
            _habit_summary(entry, "审批类型 × 申请人") | {
                "workflow_id": str(entry.get("workflow_id") or ""),
                "workflow_name": str(entry.get("workflow_name") or "未识别流程"),
                "applicant": str(entry.get("applicant") or "未识别申请人"),
            }
            for entry in data["type_applicants"].values()
        ],
    }


@mcp.tool()
def list_approval_rules(include_disabled: bool = True) -> dict[str, Any]:
    """查看 OA_A 用户主动创建的审批规则。只读；不返回系统学习的历史偏好。"""
    rules = _load_rules()
    items = rules["rules"] if include_disabled else [rule for rule in rules["rules"] if rule.get("enabled")]
    return {"version": rules["version"], "updated_at": rules["updated_at"], "rules": items}


@mcp.tool()
def set_approval_rule(workflow_name: str = "", workflow_id: str = "", applicant: str = "",
                      action: str = "批准", auto_execute: bool = False, enabled: bool = True,
                      expires_at: str = "", rule_id: str = "", rule_json: str = "",
                      preview_token: str = "", scope_key: str = "", name: str = "",
                      condition: str = "", examples_json: str = "") -> dict[str, Any]:
    """新增或更新自然语言规则；rule_json 仅用于兼容旧 schema-v2 规则。"""
    if condition.strip() or name.strip() or examples_json.strip():
        if rule_json.strip():
            raise ValueError("natural-language fields cannot be combined with rule_json")
        if not name.strip() or not condition.strip():
            raise ValueError("name and condition are required for a natural-language rule")
        if action not in {"批准", "approve"}:
            raise ValueError("natural-language auto rules must use approve action")
        try:
            examples = json.loads(examples_json) if examples_json.strip() else []
        except json.JSONDecodeError as error:
            raise ValueError("examples_json must be valid JSON") from error
        if not isinstance(examples, list):
            raise ValueError("examples_json must be a JSON array")
        data = _load_rules()
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        existing = next((item for item in data["rules"] if item.get("rule_id") == rule_id), None) if rule_id else None
        if rule_id and existing is None:
            raise ValueError("rule_id does not exist")
        rule = existing or {"rule_id": _rule_id(), "created_by": "OA_A用户", "created_at": now}
        rule.update({"schema_version": 3, "name": name.strip(), "condition": condition.strip(),
                     "examples": examples, "action": "approve", "enabled": bool(enabled),
                     "deleted": False, "updated_at": now})
        if existing is None:
            data["rules"].append(rule)
        data["updated_at"] = now
        _save_rules(data)
        return {"rule": rule, "schema_version": 3}

    if rule_json.strip():
        try:
            supplied = json.loads(rule_json)
        except json.JSONDecodeError as error:
            raise ValueError("rule_json must be valid JSON") from error
        if not isinstance(supplied, dict):
            raise ValueError("rule_json must be a JSON object")
        try:
            normalized = normalize_rule(supplied)
        except RuleValidationError as error:
            raise ValueError(f"invalid schema-v2 rule: {error}") from error
        supplied_id = str(normalized.get("rule_id") or "").strip()
        if rule_id and supplied_id and rule_id != supplied_id:
            raise ValueError("rule_id must match rule_json.rule_id when both are supplied")
        _consume_preview_token(preview_token, normalized, scope_key=scope_key)
        requested_id = rule_id or supplied_id
        data = _load_rules()
        existing = next((item for item in data["rules"] if item.get("rule_id") == requested_id), None) if requested_id else None
        if requested_id and existing is None:
            raise ValueError("rule_id does not exist")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        rule = normalized
        rule["rule_id"] = requested_id or _rule_id()
        rule["created_by"] = (existing or {}).get("created_by", "OA_A用户")
        rule["created_at"] = (existing or {}).get("created_at", now)
        rule["updated_at"] = now
        rule["revision"] = int((existing or {}).get("revision") or 0) + 1
        if existing and isinstance(existing.get("executions"), list):
            rule["executions"] = existing["executions"]
        if existing is None:
            data["rules"].append(rule)
        else:
            data["rules"][data["rules"].index(existing)] = rule
        data["updated_at"] = now
        _save_rules(data)
        return {"rule": rule, "schema_version": 2}

    workflow_name, workflow_id, applicant, action = (value.strip() for value in (workflow_name, workflow_id, applicant, action))
    expires_at = expires_at.strip()
    if expires_at:
        try:
            expires_value = datetime.fromisoformat(expires_at)
        except ValueError as error:
            raise ValueError("expires_at must be an ISO-8601 datetime") from error
        if expires_value.tzinfo is None:
            raise ValueError("expires_at must include a timezone offset")
    _validate_rule(workflow_name=workflow_name, workflow_id=workflow_id, applicant=applicant,
                   action=action, auto_execute=auto_execute)
    data = _load_rules()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    existing = next((item for item in data["rules"] if item.get("rule_id") == rule_id), None) if rule_id else None
    if rule_id and existing is None:
        raise ValueError("rule_id does not exist")
    rule = existing or {"rule_id": _rule_id(), "created_by": "OA_A用户", "created_at": now}
    rule.update({"enabled": bool(enabled), "workflow_name": workflow_name, "workflow_id": workflow_id,
                 "applicant": applicant, "action": action, "auto_execute": bool(auto_execute),
                 "expires_at": expires_at,
                 "updated_at": now})
    if existing is None:
        data["rules"].append(rule)
    data["updated_at"] = now
    _save_rules(data)
    return {"rule": rule}


def _parse_preview_rule(rule_json: str) -> dict[str, Any]:
    try:
        raw = json.loads(rule_json)
    except json.JSONDecodeError as error:
        raise ValueError("rule_json must be valid JSON") from error
    if not isinstance(raw, dict):
        raise ValueError("rule_json must be a JSON object")
    try:
        return normalize_rule(raw)
    except RuleValidationError as error:
        raise ValueError(f"invalid schema-v2 rule: {error}") from error


def _preview_for_request(rule: dict[str, Any], request_id: str) -> dict[str, Any]:
    stale = _current_todo_check(request_id)
    if stale:
        return stale
    try:
        detail = invoke("getWorkflowRequest", {"requestId": request_id})
        context = _workflow_context(request_id, detail)
    except OARequestError as error:
        return _error_result(request_id, error.category, str(error))
    except RuntimeError as error:
        return _error_result(request_id, "DETAIL_REQUEST_FAILED", f"无法查询审批详情：{error}")
    explanation = explain_rule_match(rule, context["rule_context"])
    keyword_result = match_keywords(rule, context["rule_context"])
    return {
        "request_id": request_id,
        "workflow_id": context["workflow_id"],
        "workflow_name": context["workflow_name"],
        "applicant": context["applicant"],
        "matched": explanation["matched"],
        "explanation": explanation,
        "keyword_result": keyword_result.__dict__,
    }


@mcp.tool()
def preview_approval_rule(rule_json: str, request_id: str, scope_key: str = "") -> dict[str, Any]:
    """兼容工具：仅预览历史 schema-v2 规则，不参与自然语言规则流程。"""
    rule = _parse_preview_rule(rule_json)
    return {"rule": rule, "preview": _preview_for_request(rule, request_id),
            "preview_token": _issue_preview_token(rule, source="preview", scope_key=scope_key)}


@mcp.tool()
def simulate_approval_rule(rule_json: str, max_requests: int = 20, scope_key: str = "") -> dict[str, Any]:
    """兼容工具：仅模拟历史 schema-v2 规则，不参与自然语言规则流程。"""
    rule = _parse_preview_rule(rule_json)
    max_requests = max(1, min(int(max_requests), 50))
    try:
        request_ids = _all_todo_request_ids()[:max_requests]
    except OARequestError as error:
        return _error_result("", error.category, str(error))
    except RuntimeError as error:
        return _error_result("", "TODO_CHECK_FAILED", f"无法查询当前待办：{error}")
    items = [_preview_for_request(rule, request_id) for request_id in request_ids]
    matched = [item for item in items if item.get("matched")]
    return {
        "rule": rule,
        "preview_token": _issue_preview_token(rule, source="simulate", scope_key=scope_key),
        "checked_count": len(items),
        "matched_count": len(matched),
        "matches": matched,
        "non_matches": [item for item in items if not item.get("matched")],
        "truncated": len(request_ids) == max_requests,
    }


@mcp.tool()
def disable_approval_rule(rule_id: str) -> dict[str, Any]:
    """停用一条审批规则；保留记录以便审计和恢复。"""
    data = _load_rules()
    rule = next((item for item in data["rules"] if item.get("rule_id") == rule_id), None)
    if rule is None:
        raise ValueError("rule_id does not exist")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    rule["enabled"] = False
    rule["updated_at"] = now
    data["updated_at"] = now
    _save_rules(data)
    return {"rule": rule}


@mcp.tool()
def delete_approval_rule(rule_id: str) -> dict[str, Any]:
    """删除一条审批规则。此操作不会修改 approval_habits.json。"""
    data = _load_rules()
    remaining = [item for item in data["rules"] if item.get("rule_id") != rule_id]
    if len(remaining) == len(data["rules"]):
        raise ValueError("rule_id does not exist")
    data["rules"] = remaining
    data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _save_rules(data)
    return {"deleted_rule_id": rule_id}


@mcp.tool()
def get_doing_workflow_request_list(page_no: str = "1", page_size: str = "50") -> dict[str, Any]:
    """查询当前固定 OA 用户的待办审批列表。只读。"""
    response = invoke("getDoingWorkflowRequestList", {"pageNo": page_no, "pageSize": page_size})
    first_response = response
    all_items = list(_todo_items(response))
    try:
        requested_size = max(1, min(int(page_size), 100))
    except (TypeError, ValueError):
        requested_size = 50
    ids = _request_ids(response)
    try:
        start_page = max(1, int(page_no))
    except (TypeError, ValueError):
        start_page = 1
    page = start_page + 1
    while _todo_page_has_more(response, len(_todo_items(response)), requested_size) and page <= start_page + 99:
        response_page = invoke("getDoingWorkflowRequestList", {"pageNo": str(page), "pageSize": str(requested_size)})
        all_items.extend(_todo_items(response_page))
        ids.extend(_request_ids(response_page))
        page += 1
        response = response_page
    ids = list(dict.fromkeys(ids))
    result = ((first_response.get("data") or {}).get("result")) if isinstance(first_response, dict) else None
    if isinstance(result, dict) and isinstance(result.get("data"), list):
        result["data"] = all_items
    first_response["snapshot_id"] = uuid.uuid4().hex
    first_response["index_map"] = {str(index): request_id for index, request_id in enumerate(ids, 1)}
    return first_response


@mcp.tool()
def get_workflow_request(request_id: str) -> dict[str, Any]:
    """查询审批详情，并返回审批类型及申请人维度的真实历史决策偏好。只读。"""
    stale = _current_todo_check(request_id)
    if stale:
        return stale
    try:
        response = invoke("getWorkflowRequest", {"requestId": request_id})
    except RuntimeError as error:
        return _error_result(request_id, "DETAIL_REQUEST_FAILED", f"无法查询审批详情：{error}")
    if _result_code(response) == "NO_PERMISSION":
        return _error_result(request_id, _permission_category("getWorkflowRequest"), "OA 详情接口拒绝访问")
    response["decision_habits"] = _habits_for_detail(response)
    try:
        context = _workflow_context(request_id, response)
    except OARequestError as error:
        return _error_result(request_id, error.category, str(error))
    response["approval_rule_candidates"] = _natural_language_rule_candidates()
    response["auto_approval_boundary"] = (
        "Compare the real approval detail with each natural-language rule. Return MATCH only when "
        "the condition is clearly satisfied; otherwise use NO_MATCH or UNCERTAIN."
    )
    return response


@mcp.tool()
def get_workflow_request_summary(request_id: str) -> dict[str, Any]:
    """查询审批判断摘要；不返回表单 HTML、明细表或附件正文。"""
    stale = _current_todo_check(request_id)
    if stale:
        return stale
    try:
        response = invoke("getWorkflowRequest", {"requestId": request_id})
        context = _workflow_context(request_id, response)
    except OARequestError as error:
        return _error_result(request_id, error.category, str(error))
    except RuntimeError as error:
        return _error_result(request_id, "DETAIL_REQUEST_FAILED", f"无法查询审批摘要：{error}")
    rule_context = context["rule_context"]
    return {
        "request_id": context["request_id"],
        "request_name": context["request_name"],
        "workflow_id": context["workflow_id"],
        "workflow_name": context["workflow_name"],
        "applicant": context["applicant"],
        "department": rule_context["applicant"].get("department"),
        "amount": rule_context["request"].get("amount"),
        "currency": rule_context["request"].get("currency"),
        "approval_rule_candidates": _natural_language_rule_candidates(),
        "decision_habits": _habits_for_detail(response),
    }


@mcp.tool()
def get_request_operator_info(request_id: str) -> dict[str, Any]:
    """查询审批流转配置和可退回节点。只读。"""
    stale = _current_todo_check(request_id)
    if stale:
        return stale
    response = invoke("getRequestOperatorInfo", {"requestId": request_id})
    if _result_code(response) == "NO_PERMISSION":
        return _error_result(request_id, _permission_category("getRequestOperatorInfo"), "OA 操作节点接口拒绝访问")
    return response


@mcp.tool()
def auto_approve_by_rule(request_id: str, rule_id: str, decision: str = "",
                         match_reason: str = "") -> dict[str, Any]:
    """执行自然语言规则批准；必须由 Agent 明确提交 MATCH 判断和简短依据。"""
    rules_data = _load_rules()
    rule = next((item for item in rules_data["rules"] if item.get("rule_id") == rule_id), None)
    if rule is None:
        return {"status": "skipped", "reason": "规则不存在"}
    if _is_natural_language_rule(rule):
        normalized_decision = decision.strip().upper()
        if normalized_decision != "MATCH":
            return {"status": "skipped", "reason": "自然语言规则未明确判定为 MATCH", "rule_id": rule_id}
        if not match_reason.strip():
            return {"status": "skipped", "reason": "缺少自然语言规则命中依据", "rule_id": rule_id}
        if not rule.get("enabled") or rule.get("deleted") or rule.get("action") not in {"approve", "批准"}:
            return {"status": "skipped", "reason": "规则未启用或不允许自动批准", "rule_id": rule_id}
    todo_check = _current_todo_check(request_id)
    if todo_check:
        return todo_check | {"status": "skipped"}
    try:
        detail = invoke("getWorkflowRequest", {"requestId": request_id})
    except RuntimeError as error:
        return {"status": "skipped", "reason": f"无法查询审批详情：{error}"}
    if not _business_success(detail):
        if _result_code(detail) == "NO_PERMISSION":
            return _error_result(request_id, _permission_category("getWorkflowRequest"), "OA 详情接口拒绝访问") | {"status": "skipped"}
        return _error_result(request_id, "DETAIL_REQUEST_FAILED", "审批详情接口未返回业务成功") | {"status": "skipped"}
    data = _detail_data(detail)
    try:
        context = _workflow_context(request_id, detail)
    except OARequestError as error:
        return _error_result(request_id, error.category, str(error)) | {"status": "skipped"}
    if not context["workflow_id"] and not context["workflow_name"]:
        return {"status": "skipped", "reason": "无法识别审批工作流"}
    if not _is_natural_language_rule(rule):
        if not _rule_matches_context(rule, context):
            return {"status": "skipped", "reason": "规则与当前审批不完全匹配"}
        conflicts = [item for item in _load_rules()["rules"]
                     if item.get("rule_id") != rule_id and _rule_matches_context(item, context)
                     and int(item.get("priority") or 0) == int(rule.get("priority") or 0)]
        if conflicts:
            return {"status": "skipped", "reason": "存在同优先级冲突规则，需人工确认",
                    "rule_id": rule_id, "conflict_rule_ids": [str(item.get("rule_id") or "") for item in conflicts]}
    limit_reason = _v2_limit_reason(rule, context)
    if limit_reason:
        return {"status": "skipped", "reason": limit_reason, "rule_id": rule_id}
    anomaly = _detail_has_obvious_anomaly(data)
    if anomaly:
        return {"status": "skipped", "reason": anomaly, "rule_id": rule_id}
    response = invoke("submitRequest", {"requestId": request_id, "remark": "", "otherParams": {"src": "auto_rule"}})
    if not _business_success(response):
        if _result_code(response) == "NO_PERMISSION":
            return _error_result(request_id, "ACTION_PERMISSION_DENIED", "OA 批准接口拒绝访问") | {"rule_id": rule_id, "status": "failed"}
        return {
            "status": "failed",
            "error_category": "ACTION_REQUEST_FAILED",
            "reason": _response_failure_message(response) or "批准接口未返回业务成功",
            "rule_id": rule_id,
            "request_id": str(request_id),
            "response_shape": _response_shape(response),
        }
    # A transport/HTTP success is not proof that the workflow transitioned.
    # Confirm that the request disappeared from the live todo snapshot before
    # recording the rule execution or reporting approval to the agent.
    try:
        remaining_ids, _unresolved, _business_numbers = _all_todo_snapshot_ids()
    except (OARequestError, RuntimeError) as error:
        return {
            "status": "failed",
            "error_category": "ACTION_UNCONFIRMED",
            "reason": f"批准接口返回成功，但无法刷新待办确认状态：{error}",
            "rule_id": rule_id,
            "request_id": str(request_id),
            "response_shape": _response_shape(response),
        }
    if str(request_id) in remaining_ids:
        return {
            "status": "failed",
            "error_category": "ACTION_UNCONFIRMED",
            "reason": "批准接口返回成功，但审批仍在当前待办中，未确认批准",
            "rule_id": rule_id,
            "request_id": str(request_id),
            "response_shape": _response_shape(response),
        }
    _record_rule_execution(rules_data, rule, context, match_reason=match_reason)
    response["decision_habits"] = _record_decision(context, "批准", {"remark": "", "rule_id": rule_id, "source": "auto_rule"})
    return {"status": "approved", "rule_id": rule_id, "request_id": request_id, "response": response}


@mcp.tool()
def submit_request(request_id: str, remark: str = "") -> dict[str, Any]:
    """批准指定审批。仅可在用户对明确对象给出明确批准指令后调用。"""
    stale = _current_todo_check(request_id)
    if stale:
        return stale
    try:
        context = _workflow_context(request_id)
    except OARequestError as error:
        return _error_result(request_id, error.category, str(error))
    except RuntimeError as error:
        return _error_result(request_id, "DETAIL_REQUEST_FAILED", f"无法查询审批详情：{error}")
    response = invoke("submitRequest", {"requestId": request_id, "remark": remark, "otherParams": {"src": "submit"}})
    if _result_code(response) == "NO_PERMISSION":
        return _error_result(request_id, "ACTION_PERMISSION_DENIED", "OA 批准接口拒绝访问")
    if _business_success(response):
        response["decision_habits"] = _record_decision(context, "批准", {"remark": remark})
    return response


@mcp.tool()
def reject_request(request_id: str, remark: str, reject_to_nodeid: int | str, is_submit_direct: int = 1) -> dict[str, Any]:
    """拒绝并退回指定审批。仅可在用户明确指定对象、拒绝动作和原因后调用。"""
    stale = _current_todo_check(request_id)
    if stale:
        return stale
    try:
        context = _workflow_context(request_id)
    except OARequestError as error:
        return _error_result(request_id, error.category, str(error))
    except RuntimeError as error:
        return _error_result(request_id, "DETAIL_REQUEST_FAILED", f"无法查询审批详情：{error}")
    response = invoke("rejectRequest", {"requestId": request_id, "remark": remark, "otherParams": {"RejectToNodeid": reject_to_nodeid, "isSubmitDirect": is_submit_direct}})
    if _result_code(response) == "NO_PERMISSION":
        return _error_result(request_id, "ACTION_PERMISSION_DENIED", "OA 拒绝接口拒绝访问")
    if _business_success(response):
        response["decision_habits"] = _record_decision(context, "拒绝", {"remark": remark, "reject_to_nodeid": str(reject_to_nodeid)})
    return response


@mcp.tool()
def forward_request(request_id: str, forward_name: str, remark: str = "") -> dict[str, Any]:
    """转发指定审批。仅可在用户明确指定对象、转发动作和目标姓名后调用。"""
    stale = _current_todo_check(request_id)
    if stale:
        return stale
    try:
        context = _workflow_context(request_id)
    except OARequestError as error:
        return _error_result(request_id, error.category, str(error))
    except RuntimeError as error:
        return _error_result(request_id, "DETAIL_REQUEST_FAILED", f"无法查询审批详情：{error}")
    response = invoke("forwardRequest", {"requestId": request_id, "forwardName": forward_name, "remark": remark})
    if _result_code(response) == "NO_PERMISSION":
        return _error_result(request_id, "ACTION_PERMISSION_DENIED", "OA 转发接口拒绝访问")
    if _business_success(response):
        response["decision_habits"] = _record_decision(context, "转发", {"remark": remark, "forward_name": forward_name})
    return response


if __name__ == "__main__":
    mcp.run()
