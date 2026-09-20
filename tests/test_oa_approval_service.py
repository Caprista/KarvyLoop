from karvyloop.oa.approval_service import ApprovalSnapshotStore, OAApprovalService
from karvyloop.oa.domain import ToolExecutionEvent


def test_service_records_and_applies_write_event():
    service = OAApprovalService(ApprovalSnapshotStore(ttl_s=60))
    snapshot = service.record_todo_snapshot("oa", "chat", {1: "r1", 2: "r2"})
    assert snapshot["status"] == "OK"
    service.apply_event("oa", "chat", ToolExecutionEvent(
        tool_key="oa.submit_request", outcome="approved", request_id="r1"))
    assert service.view("oa", "chat")["completed"] == [1]
    assert service.resolve("oa", "chat", (1, 2))[0] == {2: "r2"}


def test_listing_event_replaces_snapshot_and_malformed_write_is_ignored():
    service = OAApprovalService(ApprovalSnapshotStore(ttl_s=60))
    result = service.apply_event("oa", "chat", {
        "name": "mcp_oa_get_doing_workflow_request_list",
        "output": '{"index_map": {"3": "r3"}}',
    })
    assert result["mapping"] == {3: "r3"}
    service.apply_event("oa", "chat", {
        "tool_key": "oa.submit_request", "outcome": "NO_PERMISSION", "request_id": "r3",
    })
    assert service.view("oa", "chat")["completed"] == []
    assert service.view("oa", "chat")["failed"] == []


def test_snapshot_id_can_reject_stale_resolution():
    service = OAApprovalService(ApprovalSnapshotStore(ttl_s=60))
    first = service.put("oa", "chat", {1: "r1"})
    service.put("oa", "chat", {1: "r2"})
    assert service.resolve("oa", "chat", (1,), snapshot_id=first["snapshot_id"]) == ({}, "STALE_SNAPSHOT")
