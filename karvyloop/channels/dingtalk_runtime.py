"""钉钉通道运行时热更新与待授权发送者短期缓存。"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import Any, Iterable

from karvyloop.config_channels import DingTalkChannelConfig
from karvyloop.oa.approval_service import ApprovalSnapshotStore

PENDING_SENDER_TTL_S = 24 * 60 * 60
PENDING_SENDER_MAX_ITEMS = 1000
APPROVAL_SNAPSHOT_TTL_S = 10 * 60


class LegacyApprovalSnapshotStore:
    """Per-chat todo snapshots and execution cursors.

    This is deliberately a state boundary: model text is never used as the
    source of truth for an index -> requestId mapping.
    """

    def __init__(self, ttl_s: float = APPROVAL_SNAPSHOT_TTL_S) -> None:
        self.ttl_s = ttl_s
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.RLock()

    def put(self, instance_id: str, chat_id: str,
            mapping: dict[int, str], *, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else now
        normalized = {int(k): str(v) for k, v in mapping.items() if int(k) > 0 and str(v)}
        item = {
            "snapshot_id": uuid.uuid4().hex,
            "instance_id": instance_id,
            "chat_id": chat_id,
            "mapping": normalized,
            "completed": set(),
            "failed": set(),
            "created_at": current,
            "expires_at": current + self.ttl_s,
        }
        with self._lock:
            self._items[(instance_id, chat_id)] = item
        return self.view(instance_id, chat_id, now=current)

    def resolve(self, instance_id: str, chat_id: str, indexes: tuple[int, ...], *,
                now: float | None = None) -> tuple[dict[int, str], str]:
        current = time.time() if now is None else now
        with self._lock:
            item = self._items.get((instance_id, chat_id))
            if item is None or item["expires_at"] <= current:
                return {}, "EXPIRED"
            missing = [index for index in indexes if index not in item["mapping"]]
            if missing:
                return {}, "UNKNOWN_INDEX"
            selected = {index: item["mapping"][index] for index in indexes
                        if index not in item["completed"]}
            return selected, "OK"

    def mark(self, instance_id: str, chat_id: str, *, completed=(), failed=()) -> None:
        with self._lock:
            item = self._items.get((instance_id, chat_id))
            if item is None:
                return
            item["completed"].update(int(index) for index in completed)
            item["failed"].update(int(index) for index in failed)

    def clear(self, instance_id: str, chat_id: str) -> None:
        with self._lock:
            self._items.pop((instance_id, chat_id), None)

    def view(self, instance_id: str, chat_id: str, *, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else now
        with self._lock:
            item = self._items.get((instance_id, chat_id))
            if item is None or item["expires_at"] <= current:
                return {"status": "EXPIRED"}
            return {
                "status": "OK", "snapshot_id": item["snapshot_id"],
                "mapping": dict(item["mapping"]),
                "completed": sorted(item["completed"]),
                "failed": sorted(item["failed"]),
                "expires_at": item["expires_at"],
            }


class PendingSenderCache:
    def __init__(self, ttl_s: float = PENDING_SENDER_TTL_S,
                 max_items: int = PENDING_SENDER_MAX_ITEMS) -> None:
        self.ttl_s = ttl_s
        self.max_items = max(1, max_items)
        self._items: dict[tuple[str, str], dict] = {}
        self._lock = threading.RLock()

    def put(self, instance_id: str, sender: str, **meta: Any) -> dict:
        now = time.time()
        with self._lock:
            self.prune(now)
            key = (instance_id, sender)
            previous = self._items.get(key, {})
            item = {"instance_id": instance_id, "sender": sender, **meta,
                    "first_seen_at": previous.get("first_seen_at", now),
                    "last_seen_at": now, "expires_at": now + self.ttl_s,
                    "attempt_count": previous.get("attempt_count", 0) + 1}
            self._items[key] = item
            if len(self._items) > self.max_items:
                oldest = min(self._items, key=lambda item_key: self._items[item_key]["last_seen_at"])
                self._items.pop(oldest, None)
            return dict(item)

    def list(self, now: float | None = None) -> list[dict]:
        with self._lock:
            self.prune(time.time() if now is None else now)
            return sorted((dict(v) for v in self._items.values()),
                          key=lambda x: x["last_seen_at"], reverse=True)

    def remove(self, instance_id: str, sender: str) -> None:
        with self._lock:
            self._items.pop((instance_id, sender), None)

    def prune(self, now: float | None = None) -> None:
        current = time.time() if now is None else now
        with self._lock:
            for key, value in list(self._items.items()):
                if value["expires_at"] <= current:
                    self._items.pop(key, None)


def config_instance_id(cfg: DingTalkChannelConfig) -> str:
    return cfg.instance_id or cfg.client_id


def _same_credentials(left: DingTalkChannelConfig, right: DingTalkChannelConfig) -> bool:
    return (left.client_id, left.client_secret) == (right.client_id, right.client_secret)


def reconcile_dingtalk_channels(app: Any, configs: Iterable[DingTalkChannelConfig]) -> dict:
    """按稳定实例 ID 做增删改；仅凭据变化需要无损替换连接。"""
    from karvyloop.channels.dingtalk_channel import DingTalkChannel

    old = list(getattr(app.state, "dingtalk_channels", None) or [])
    old_by_id = {config_instance_id(ch._cfg): ch for ch in old}
    loop = getattr(app.state, "main_event_loop", None)
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

    active = []
    started = stopped = kept = failed = 0
    wanted_ids: set[str] = set()
    for cfg in configs:
        instance_id = config_instance_id(cfg)
        wanted_ids.add(instance_id)
        existing = old_by_id.get(instance_id)
        if existing is not None and _same_credentials(existing._cfg, cfg):
            existing._cfg = cfg
            active.append(existing)
            kept += 1
            continue

        channel = DingTalkChannel(app, cfg)
        if loop is not None and channel.start(loop):
            active.append(channel)
            started += 1
            if existing is not None:
                existing.stop()
                stopped += 1
        else:
            failed += 1
            if existing is not None:
                active.append(existing)
                kept += 1

    for instance_id, channel in old_by_id.items():
        if instance_id not in wanted_ids:
            channel.stop()
            stopped += 1
    app.state.dingtalk_channels = active
    app.state.dingtalk_channel = active[0] if active else None
    return {"active": len(active), "started": started, "stopped": stopped,
            "kept": kept, "failed": failed, "hot_reload": loop is not None}
