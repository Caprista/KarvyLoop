"""a2a — M3 路线 C 拍 2:Agent-to-Agent 协议。

设计:docs/19-a2a-protocol.md。

**核心概念**:
- 11 种 typed envelope(task.*/ask/answer/propose/accept/reject/broadcast/audit.*)
- `by` 字段 = 灵魂级代发链(`from: karvy` 永远不合法,A1)
- 寻址 = domain + role + 可选 agent_id
- Tier 1 in-process router(本拍);Tier 2/3 留 M3+
- 审计链全记录 from_ + by(A5)

**核心不变量**(doc §4):
- A1 from: karvy 永远不合法
- A2 by 不含 from_ 本身
- A3 by 不含 karvy 的 from_(小卡只做中间人)
- A4 任何 envelope 都要签名
- A5 审计链 = from_ + by 全部记录
- A6 小卡是 observer 时只收 BROADCAST
- A7 TASK_ASSIGN 不能跨域
- A8 REJECT 必须带 reason
"""
from __future__ import annotations

from .envelope import (
    EXPECTED_ENVELOPE_TYPES,
    KARVY_AGENT_ID,
    BroadcastPayload,
    ByContainsFromError,
    Envelope,
    EnvelopeType,
    FromKarvyForbiddenError,
    QA,
    ProposePayload,
    RejectMissingReasonError,
    RejectPayload,
    SignatureMissingError,
    TaskPayload,
    sign_envelope,
    verify_envelope,
)
from .address import (
    is_cross_domain,
    is_karvy_observer,
    same_domain,
)
from .audit import AuditChain, AuditEntry
from .inbox import Inbox, InboxEntry
from .router import (
    BROADCAST_TYPE,
    REJECT_BAD_SIGNATURE,
    REJECT_BOUNDARY_VIOLATION,
    REJECT_CROSS_DOMAIN,
    REJECT_DOMAIN_ARCHIVED,
    REJECT_NO_TARGET,
    REJECT_OBSERVER_FILTER,
    AddressResolver,
    DomainLifecycleQuery,
    EnvelopeRouter,
    RouteResult,
    TASK_ASSIGN_TYPE,
)

__all__ = [
    # envelope
    "EXPECTED_ENVELOPE_TYPES",
    "KARVY_AGENT_ID",
    "BroadcastPayload",
    "ByContainsFromError",
    "Envelope",
    "EnvelopeType",
    "FromKarvyForbiddenError",
    "QA",
    "ProposePayload",
    "RejectMissingReasonError",
    "RejectPayload",
    "SignatureMissingError",
    "TaskPayload",
    "sign_envelope",
    "verify_envelope",
    # address
    "is_cross_domain",
    "is_karvy_observer",
    "same_domain",
    # audit
    "AuditChain",
    "AuditEntry",
    # inbox
    "Inbox",
    "InboxEntry",
    # router
    "BROADCAST_TYPE",
    "REJECT_BAD_SIGNATURE",
    "REJECT_BOUNDARY_VIOLATION",
    "REJECT_CROSS_DOMAIN",
    "REJECT_DOMAIN_ARCHIVED",
    "REJECT_NO_TARGET",
    "REJECT_OBSERVER_FILTER",
    "AddressResolver",
    "DomainLifecycleQuery",
    "EnvelopeRouter",
    "RouteResult",
    "TASK_ASSIGN_TYPE",
]
