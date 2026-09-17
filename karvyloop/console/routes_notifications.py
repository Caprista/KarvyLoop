"""notify_user 通知面 API:列表、待审批、批准/拒绝。

审批通过 → outbox 转 queued → 后台 notification_dispatch loop 自动投递(无需再手动触发)。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/notifications")

_NOT_WIRED = {"ok": True, "notifications": [], "reason": "notification_runtime_not_wired"}


def _store(request: Request) -> Any:
    rt = getattr(request.app.state, "notification_runtime", None)
    return getattr(rt, "store", None) if rt is not None else None


@router.get("")
def api_notifications(request: Request, limit: int = 50) -> dict[str, Any]:
    store = _store(request)
    if store is None:
        return dict(_NOT_WIRED)
    return {"ok": True, "notifications": store.list_notifications(limit=limit)}


@router.get("/pending")
def api_notifications_pending(request: Request) -> dict[str, Any]:
    store = _store(request)
    if store is None:
        return dict(_NOT_WIRED)
    return {"ok": True, "notifications": store.pending_approvals()}


@router.post("/{notification_id}/approve")
def api_notification_approve(notification_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    if store is None:
        return {"ok": False, "reason": "notification_runtime_not_wired"}
    receipt = store.transition_approval(notification_id, approved=True)
    return {"ok": receipt.status == "queued", "status": receipt.status, "reason": receipt.reason}


@router.post("/{notification_id}/reject")
def api_notification_reject(notification_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    if store is None:
        return {"ok": False, "reason": "notification_runtime_not_wired"}
    receipt = store.transition_approval(notification_id, approved=False)
    ok = receipt.status == "rejected" and receipt.reason != "invalid_approval_transition"
    return {"ok": ok, "status": receipt.status, "reason": receipt.reason}


__all__ = ["router"]
