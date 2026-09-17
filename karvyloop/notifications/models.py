from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PolicyAction(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class NotificationPolicy:
    """审批策略(纯函数,可测)。

    放行三档:
    - DENY:channel 非法 / 接收人解析不到;
    - ALLOW(直投):①运行时预授权(小卡)且 current_user;②接收人绑定 consent=auto(2)
      —— 绑定级自动放行:发**给谁**由**谁**授权免审(config notify_auto_approve 建 auto
      绑定),任何 actor(含业务角色)给该接收人发都直投;
    - 其余 → REQUIRE_APPROVAL(出 notification_approval 决策卡)。
    """

    # consent 取值:0=未同意 1=需审批(默认) 2=auto(接收人已授权免审直投)
    CONSENT_AUTO = 2

    @staticmethod
    def evaluate(command: "NotificationCommand", resolved_recipient: dict[str, Any] | None) -> PolicyAction:
        if command.channel not in {"auto", "dingtalk"} or not command.recipient.strip():
            return PolicyAction.DENY
        if resolved_recipient is None:
            return PolicyAction.DENY
        if command.runtime_context.get("preauthorized") is True and command.recipient == "current_user":
            return PolicyAction.ALLOW
        try:
            if int(resolved_recipient.get("consent") or 0) >= NotificationPolicy.CONSENT_AUTO:
                return PolicyAction.ALLOW
        except (TypeError, ValueError):
            pass
        return PolicyAction.REQUIRE_APPROVAL


class OutboxStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    DELIVERED = "delivered"
    PARTIALLY_DELIVERED = "partially_delivered"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class DeliveryStatus(str, Enum):
    QUEUED = "queued"
    SENDING = "sending"
    RETRY_WAIT = "retry_wait"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class NotificationCommand:
    recipient: str
    content: str
    actor_type: str = "agent"
    actor_id: str = ""
    task_id: str = ""
    trace_ref: str = ""
    title: str = ""
    channel: str = "auto"
    conversation_id: str = ""
    urgency: str = "normal"
    reason: str = ""
    dedupe_key: str = ""
    content_type: str = "text"
    runtime_context: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class NotificationReceipt:
    status: str
    notification_id: str | None = None
    reason: str = ""
    duplicate_of: str | None = None


@dataclass(frozen=True)
class PermanentDeliveryError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class RetryableDeliveryError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class RateLimitedError(RetryableDeliveryError):
    retry_after: float = 60.0
