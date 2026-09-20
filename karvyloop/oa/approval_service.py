"""Channel-neutral approval snapshot and execution state service.

The service owns the mapping between a user-facing todo index and an OA
request id.  Channels may keep using the small ``ApprovalSnapshotStore``
interface, while adapters report normalized :class:`ToolExecutionEvent`
objects through :meth:`OAApprovalService.apply_event`.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Mapping

from .domain import ToolExecutionEvent

APPROVAL_SNAPSHOT_TTL_S = 10 * 60


class ApprovalSnapshotStore:
    """Thread-safe per-scope todo snapshots and execution cursors.

    ``scope`` is intentionally represented by two strings for compatibility
    with the original channel store: ``instance_id`` and ``conversation_key``.
    """

    def __init__(self, ttl_s: float = APPROVAL_SNAPSHOT_TTL_S) -> None:
        self.ttl_s = max(1.0, float(ttl_s))
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(instance_id: str, conversation_key: str) -> tuple[str, str]:
        return str(instance_id or ""), str(conversation_key or "")

    @staticmethod
    def _mapping(mapping: Mapping[Any, Any] | None) -> dict[int, str]:
        result: dict[int, str] = {}
        for index, request_id in (mapping or {}).items():
            try:
                number = int(index)
            except (TypeError, ValueError):
                continue
            request = str(request_id or "").strip()
            if number > 0 and request:
                result[number] = request
        return result

    def put(self, instance_id: str, conversation_key: str,
            mapping: Mapping[Any, Any], *, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        item = {
            "snapshot_id": uuid.uuid4().hex,
            "instance_id": str(instance_id),
            "chat_id": str(conversation_key),
            "conversation_key": str(conversation_key),
            "mapping": self._mapping(mapping),
            "completed": set(),
            "failed": set(),
            "created_at": current,
            "expires_at": current + self.ttl_s,
        }
        with self._lock:
            self._items[self._key(instance_id, conversation_key)] = item
        return self.view(instance_id, conversation_key, now=current)

    def resolve(self, instance_id: str, conversation_key: str,
                indexes: tuple[int, ...] | list[int], *, now: float | None = None,
                snapshot_id: str = "") -> tuple[dict[int, str], str]:
        current = time.time() if now is None else float(now)
        with self._lock:
            item = self._items.get(self._key(instance_id, conversation_key))
            if item is None or item["expires_at"] <= current:
                return {}, "EXPIRED"
            if snapshot_id and snapshot_id != item["snapshot_id"]:
                return {}, "STALE_SNAPSHOT"
            requested = tuple(int(index) for index in indexes)
            if any(index <= 0 for index in requested):
                return {}, "UNKNOWN_INDEX"
            if any(index not in item["mapping"] for index in requested):
                return {}, "UNKNOWN_INDEX"
            selected = {
                index: item["mapping"][index] for index in requested
                if index not in item["completed"]
            }
            return selected, "OK"

    def mark(self, instance_id: str, conversation_key: str, *,
             completed=(), failed=()) -> None:
        with self._lock:
            item = self._items.get(self._key(instance_id, conversation_key))
            if item is None:
                return
            item["completed"].update(int(index) for index in completed if int(index) > 0)
            item["failed"].update(int(index) for index in failed if int(index) > 0)

    def clear(self, instance_id: str, conversation_key: str) -> None:
        with self._lock:
            self._items.pop(self._key(instance_id, conversation_key), None)

    def view(self, instance_id: str, conversation_key: str,
             *, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        with self._lock:
            item = self._items.get(self._key(instance_id, conversation_key))
            if item is None or item["expires_at"] <= current:
                return {"status": "EXPIRED"}
            return {
                "status": "OK",
                "snapshot_id": item["snapshot_id"],
                "instance_id": item["instance_id"],
                "conversation_key": item["conversation_key"],
                "mapping": dict(item["mapping"]),
                "completed": sorted(item["completed"]),
                "failed": sorted(item["failed"]),
                "expires_at": item["expires_at"],
            }


class OAApprovalService:
    """Domain service shared by channel adapters and OA protocol adapters."""

    def __init__(self, store: ApprovalSnapshotStore | None = None) -> None:
        self.store = store or ApprovalSnapshotStore()

    def record_todo_snapshot(self, instance_id: str, conversation_key: str,
                             mapping: Mapping[Any, Any], **kwargs: Any) -> dict[str, Any]:
        return self.store.put(instance_id, conversation_key, mapping, **kwargs)

    # Short alias useful to adapters that already call their result a snapshot.
    put = record_todo_snapshot

    def resolve(self, instance_id: str, conversation_key: str,
                indexes: tuple[int, ...] | list[int], **kwargs: Any):
        return self.store.resolve(instance_id, conversation_key, indexes, **kwargs)

    def view(self, instance_id: str, conversation_key: str, **kwargs: Any):
        return self.store.view(instance_id, conversation_key, **kwargs)

    def clear(self, instance_id: str, conversation_key: str) -> None:
        self.store.clear(instance_id, conversation_key)

    def apply_event(self, instance_id: str, conversation_key: str,
                    event: ToolExecutionEvent | Mapping[str, Any]) -> dict[str, Any]:
        """Apply one normalized tool result and return the new snapshot view.

        Listing results create a new snapshot. Write results advance the
        cursor by matching ``request_id`` to the current snapshot. Unknown or
        malformed results are ignored, never treated as successful writes.
        """
        if isinstance(event, ToolExecutionEvent):
            tool_key, outcome, request_id, result = event.tool_key, event.outcome, event.request_id, event.result
        else:
            tool_key = str(event.get("tool_key") or event.get("name") or "")
            outcome = str(event.get("outcome") or event.get("status") or "")
            request_id = str(event.get("request_id") or "")
            result = event.get("result", event.get("output"))
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (TypeError, ValueError):
                    result = None
        result = result if isinstance(result, Mapping) else {}
        normalized_tool = tool_key.rsplit(".", 1)[-1]
        if normalized_tool.endswith("get_doing_workflow_request_list"):
            mapping = result.get("index_map")
            if isinstance(mapping, Mapping):
                return self.store.put(instance_id, conversation_key, mapping)
        if normalized_tool.endswith(("submit_request", "reject_request", "forward_request")):
            request_id = request_id or str(result.get("request_id") or "")
            status = str(outcome or result.get("status") or "").lower()
            if request_id and status in {"approved", "success", "completed", "failed", "skipped", "rejected", "forwarded"}:
                current = self.store.view(instance_id, conversation_key)
                mapping = current.get("mapping", {}) if current.get("status") == "OK" else {}
                indexes = [index for index, rid in mapping.items() if str(rid) == request_id]
                if indexes:
                    if status in {"approved", "success", "completed", "rejected", "forwarded"}:
                        self.store.mark(instance_id, conversation_key, completed=indexes)
                    else:
                        self.store.mark(instance_id, conversation_key, failed=indexes)
        return self.store.view(instance_id, conversation_key)


ApprovalStateService = OAApprovalService

__all__ = ["APPROVAL_SNAPSHOT_TTL_S", "ApprovalSnapshotStore", "OAApprovalService", "ApprovalStateService"]
