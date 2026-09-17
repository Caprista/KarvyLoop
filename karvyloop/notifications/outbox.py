from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .models import (DeliveryStatus, NotificationCommand, NotificationPolicy,
                     NotificationReceipt, OutboxStatus, PolicyAction)


def default_notification_path() -> Path:
    path = Path.home() / ".karvyloop" / "notifications.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS notification_outbox (
 id TEXT PRIMARY KEY, recipient_ref TEXT NOT NULL, platform_user_id TEXT NOT NULL DEFAULT '', conversation_id TEXT NOT NULL DEFAULT '', preferred_channel TEXT NOT NULL,
 title TEXT NOT NULL, content TEXT NOT NULL, content_type TEXT NOT NULL, urgency TEXT NOT NULL, reason TEXT NOT NULL,
 actor_type TEXT NOT NULL, actor_id TEXT NOT NULL, task_id TEXT NOT NULL, trace_ref TEXT NOT NULL,
 policy_action TEXT NOT NULL, policy_reason TEXT NOT NULL, approval_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
 dedupe_scope TEXT NOT NULL, dedupe_key TEXT NOT NULL, scheduled_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 approved_at REAL, cancelled_at REAL, completed_at REAL, error_redacted TEXT NOT NULL DEFAULT '', UNIQUE(dedupe_scope, dedupe_key), CHECK(urgency IN ('normal','important','urgent'))
);
CREATE TABLE IF NOT EXISTS notification_deliveries (
 id TEXT PRIMARY KEY, notification_id TEXT NOT NULL, binding_id TEXT NOT NULL, platform_user_id TEXT NOT NULL, channel TEXT NOT NULL, address TEXT NOT NULL,
 status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3, next_attempt_at REAL NOT NULL DEFAULT 0,
 lease_owner TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0, provider_message_id TEXT NOT NULL DEFAULT '', provider_request_id TEXT NOT NULL DEFAULT '', error_redacted TEXT NOT NULL DEFAULT '', last_attempt_at REAL, delivered_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 FOREIGN KEY(notification_id) REFERENCES notification_outbox(id)
);
CREATE TABLE IF NOT EXISTS notification_recipient_bindings (
 id TEXT PRIMARY KEY, platform_user TEXT NOT NULL, channel TEXT NOT NULL, tenant TEXT NOT NULL DEFAULT '', address_type TEXT NOT NULL, address_id TEXT NOT NULL, conversation_address TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, consent INTEGER NOT NULL DEFAULT 0, UNIQUE(platform_user, channel, tenant, address_type, address_id)
);
CREATE INDEX IF NOT EXISTS notification_delivery_ready ON notification_deliveries(status, next_attempt_at, lease_until);
CREATE INDEX IF NOT EXISTS notification_binding_lookup ON notification_recipient_bindings(conversation_address, enabled);
"""


class OutboxStore:
    def __init__(self, path: str | Path | None = None, *, clock=time.time, resolver: Callable[[str, NotificationCommand], dict[str, Any] | None] | None = None):
        target = default_notification_path() if path is None else path
        if target != ":memory:":
            Path(target).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(target), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._clock = clock
        self._resolver = resolver
        self._owner_user_id = ""   # 平台所有者映射(runtime 接线时设;current_user 兜底解析用)

    @property
    def owner_user_id(self) -> str:
        return self._owner_user_id

    @owner_user_id.setter
    def owner_user_id(self, value: str) -> None:
        self._owner_user_id = str(value or "").strip()

    def close(self) -> None:
        self._conn.close()

    def now(self) -> float:
        return self._clock()

    def upsert_binding(self, *, platform_user: str, channel: str, tenant: str = "", address_type: str = "user", address_id: str = "", conversation_address: str = "", enabled: bool = True, consent: bool | int = True) -> str:
        """consent 档位:False/0=未同意  True/1=需审批(默认)  2=auto(接收人授权免审直投)。"""
        bid = uuid.uuid4().hex
        consent_val = int(consent)
        with self._lock:
            self._conn.execute("INSERT INTO notification_recipient_bindings VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(platform_user,channel,tenant,address_type,address_id) DO UPDATE SET conversation_address=excluded.conversation_address,enabled=excluded.enabled,consent=excluded.consent", (bid, platform_user, channel, tenant, address_type, address_id, conversation_address, int(enabled), consent_val))
            row = self._conn.execute("SELECT id FROM notification_recipient_bindings WHERE platform_user=? AND channel=? AND tenant=? AND address_type=? AND address_id=?", (platform_user, channel, tenant, address_type, address_id)).fetchone()
            self._conn.commit()
            return row[0]

    def reconcile_tenant_consent(self, *, tenant: str, consent: int) -> int:
        """声明式同步:把某实例(tenant=client_id)名下全部 dingtalk 绑定的 consent
        对齐到配置档位(单聊+群聊)。notify_auto_approve 开→全部升 2(免审直投);
        关→回 1(恢复审批)。返回受影响行数。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE notification_recipient_bindings SET consent=? WHERE channel=? AND tenant=? AND enabled=1",
                (int(consent), "dingtalk", tenant or ""))
            self._conn.commit()
            return cur.rowcount

    def resolve_binding(self, recipient: str, command: NotificationCommand) -> dict[str, Any] | None:
        if self._resolver:
            return self._resolver(recipient, command)
        channel = command.channel
        params: tuple[Any, ...]
        if recipient == "current_user":
            user = command.runtime_context.get("current_user_id", "") or self._owner_user_id
            if not user:
                return None
            where = "platform_user=?"
            params = (user,)
        elif recipient.startswith("conversation:"):
            where = "conversation_address=?"
            params = (recipient.removeprefix("conversation:"),)
        elif recipient.startswith("user:"):
            where = "platform_user=?"
            params = (recipient[5:],)
        else:
            # 裸 ID 兜底:agent 常直接填 staffId(实拍:recipient="635824800")。
            # 只做**精确匹配**(platform_user 或已绑定 address_id),绝不按姓名猜 ——
            # 名字歧义会发错人;匹配不到仍走 recipient_unresolved 拒绝。
            where = "(platform_user=? OR address_id=?)"
            params = (recipient, recipient)
        if channel == "auto":
            channel_sql = "channel IN (?, ?)"
            channels = ("dingtalk", "auto")
        else:
            channel_sql = "channel=?"
            channels = (channel,)
        row = self._conn.execute(
            f"SELECT * FROM notification_recipient_bindings WHERE {where} AND {channel_sql} AND enabled=1 AND consent>=1 "
            "ORDER BY CASE WHEN channel='dingtalk' THEN 0 ELSE 1 END, "
            "CASE WHEN address_type='direct' THEN 0 ELSE 1 END, id LIMIT 1",
            params + channels).fetchone()
        return dict(row) if row else None

    def _insert_delivery(self, nid: str, binding: dict[str, Any], command: NotificationCommand, now: float) -> None:
        address_type = binding.get("address_type", "direct")
        address = binding.get("address_id", "") if address_type == "direct" else binding.get("conversation_address", "")
        self._conn.execute("INSERT INTO notification_deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, nid, binding.get("id", ""), binding.get("platform_user", ""), binding.get("channel", command.channel), address, DeliveryStatus.QUEUED.value, 0, 3, now, "", 0, "", "", "", None, None, now, now))

    def submit(self, command: NotificationCommand) -> NotificationReceipt:
        binding = self.resolve_binding(command.recipient, command)
        action = NotificationPolicy.evaluate(command, binding)
        now = self._clock(); nid = uuid.uuid4().hex
        resolved_channel = binding.get("channel", command.channel) if binding else command.channel
        scope = f"{command.actor_id}:{binding.get('platform_user','') if binding else ''}:{resolved_channel}"
        key = command.dedupe_key or uuid.uuid4().hex
        status = {PolicyAction.ALLOW: OutboxStatus.QUEUED, PolicyAction.REQUIRE_APPROVAL: OutboxStatus.PENDING_APPROVAL, PolicyAction.DENY: OutboxStatus.REJECTED}[action]
        reason = "" if action != PolicyAction.DENY else ("recipient_unresolved" if binding is None else "policy_denied")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute("INSERT INTO notification_outbox VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (nid, command.recipient, binding.get("platform_user", "") if binding else "", command.conversation_id, resolved_channel, command.title, command.content, command.content_type, command.urgency, command.reason, command.actor_type, command.actor_id, command.task_id, command.trace_ref, action.value, reason, "", status.value, scope, key, None, now, now, None, None, None, ""))
                if status == OutboxStatus.QUEUED:
                    if binding is None:
                        raise ValueError("queued notification requires a resolved recipient binding")
                    self._insert_delivery(nid, binding, command, now)
                self._conn.commit(); return NotificationReceipt(status=status.value, notification_id=nid, reason=reason)
            except sqlite3.IntegrityError:
                self._conn.rollback(); row = self._conn.execute("SELECT id FROM notification_outbox WHERE dedupe_scope=? AND dedupe_key=?", (scope, key)).fetchone(); return NotificationReceipt(status="duplicate", notification_id=row[0], duplicate_of=row[0])

    def transition_approval(self, notification_id: str, approved: bool, approval_id: str = "") -> NotificationReceipt:
        now = self._clock()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE"); row = self._conn.execute("SELECT * FROM notification_outbox WHERE id=? AND status=?", (notification_id, OutboxStatus.PENDING_APPROVAL.value)).fetchone()
            if row is None: self._conn.rollback(); return NotificationReceipt(status="rejected", notification_id=notification_id, reason="invalid_approval_transition")
            target = OutboxStatus.QUEUED if approved else OutboxStatus.REJECTED
            self._conn.execute("UPDATE notification_outbox SET status=?,approval_id=?,approved_at=?,updated_at=?,error_redacted=? WHERE id=?", (target.value, approval_id, now if approved else None, now, "" if approved else "rejected_by_actor", notification_id))
            if approved:
                binding = self.resolve_binding(row["recipient_ref"], NotificationCommand(recipient=row["recipient_ref"], content=row["content"], channel=row["preferred_channel"], runtime_context={"current_user_id": row["platform_user_id"]}))
                if binding is None: self._conn.rollback(); return NotificationReceipt(status="rejected", notification_id=notification_id, reason="recipient_unresolved")
                self._insert_delivery(notification_id, binding, NotificationCommand(recipient=row["recipient_ref"], content=row["content"], channel=row["preferred_channel"]), now)
            self._conn.commit(); return NotificationReceipt(status=target.value, notification_id=notification_id)

    def read_delivery(self, delivery_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT d.*,o.title,o.content,o.content_type,o.urgency,o.reason,b.address_type,b.address_id,b.conversation_address FROM notification_deliveries d JOIN notification_outbox o ON o.id=d.notification_id LEFT JOIN notification_recipient_bindings b ON b.id=d.binding_id WHERE d.id=?", (delivery_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        address_type = result.get("address_type", "direct")
        result["address"] = result.get("conversation_address", "") if address_type in {"group", "conversation"} else result.get("address_id", "")
        result["recipient_address"] = result["address"]
        result["platform_user_id"] = result.get("platform_user_id", "")
        result["conversation_address"] = result.get("conversation_address", "")
        return result

    def lease_deliveries(self, owner: str, *, limit: int = 10, lease_seconds: float = 60) -> list[dict[str, Any]]:
        now = self._clock()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE"); rows = self._conn.execute("SELECT * FROM notification_deliveries WHERE (status IN (?,?) AND next_attempt_at<=?) OR (status=? AND lease_until<=?) ORDER BY id LIMIT ?", (DeliveryStatus.QUEUED.value, DeliveryStatus.RETRY_WAIT.value, now, DeliveryStatus.SENDING.value, now, limit)).fetchall(); until=now+lease_seconds
            for row in rows: self._conn.execute("UPDATE notification_deliveries SET status=?,lease_owner=?,lease_until=?,attempt_count=attempt_count+1,updated_at=? WHERE id=?", (DeliveryStatus.SENDING.value, owner, until, now, row["id"]))
            self._conn.commit(); return [dict(r) | {"attempt_count": r["attempt_count"]+1, "lease_owner": owner} for r in rows]

    def mark_success(self, delivery_id: str, owner: str, provider_message_id: str = "", provider_request_id: str = "") -> bool: return self._finish(delivery_id, owner, DeliveryStatus.DELIVERED, "", provider_message_id, provider_request_id)
    def mark_retry(self, delivery_id: str, owner: str, error: str, next_attempt_at: float | None = None) -> bool: return self._finish(delivery_id, owner, DeliveryStatus.RETRY_WAIT, error, next_attempt_at=self._clock()+60 if next_attempt_at is None else next_attempt_at)
    def mark_permanent_failure(self, delivery_id: str, owner: str, error: str) -> bool: return self._finish(delivery_id, owner, DeliveryStatus.FAILED, error)

    def _finish(self, did: str, owner: str, status: DeliveryStatus, error: str, provider_message_id: str = "", provider_request_id: str = "", next_attempt_at: float = 0) -> bool:
        with self._lock:
            cur=self._conn.execute("UPDATE notification_deliveries SET status=?,error_redacted=?,provider_message_id=?,provider_request_id=?,next_attempt_at=?,lease_owner='',lease_until=0,updated_at=? WHERE id=? AND status=? AND lease_owner=?", (status.value,error[:500],provider_message_id,provider_request_id,next_attempt_at,self._clock(),did,DeliveryStatus.SENDING.value,owner)); self._conn.commit()
            if cur.rowcount: self._aggregate(did)
            return cur.rowcount == 1

    def _aggregate(self, did: str) -> None:
        row=self._conn.execute("SELECT notification_id FROM notification_deliveries WHERE id=?",(did,)).fetchone();
        if not row: return
        vals=[r[0] for r in self._conn.execute("SELECT status FROM notification_deliveries WHERE notification_id=?",(row[0],)).fetchall()]
        status=OutboxStatus.DELIVERED.value if vals and all(v==DeliveryStatus.DELIVERED.value for v in vals) else (OutboxStatus.FAILED.value if vals and all(v==DeliveryStatus.FAILED.value for v in vals) else OutboxStatus.PARTIALLY_DELIVERED.value)
        self._conn.execute("UPDATE notification_outbox SET status=?,updated_at=? WHERE id=?",(status,self._clock(),row[0])); self._conn.commit()

    def list_notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM notification_outbox ORDER BY created_at DESC LIMIT ?",
            (max(1, int(limit)),)).fetchall()
        return [dict(r) for r in rows]

    def pending_approvals(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM notification_outbox WHERE status=? ORDER BY created_at ASC",
            (OutboxStatus.PENDING_APPROVAL.value,)).fetchall()
        return [dict(r) for r in rows]
