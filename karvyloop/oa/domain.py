"""Platform-neutral contracts used by the OA agent.

These objects describe business messages and outcomes only.  In particular,
they must not expose a channel payload or an MCP implementation detail to the
OA domain.  Transport adapters are responsible for constructing them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _copy_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Make a shallow copy so callers cannot mutate an input dictionary."""

    return dict(value or {})


@dataclass(frozen=True)
class ChannelEnvelope:
    """A normalized inbound message from any channel.

    ``metadata`` is intentionally opaque to the domain.  Adapters may keep
    provider-specific values there, but business code should use only the
    fields declared on this object.
    """

    channel: str
    instance_id: str
    conversation_key: str
    sender_id: str
    text: str = ""
    message_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for name in ("channel", "instance_id", "conversation_key", "sender_id"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} must not be empty")
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class OAIntent:
    """A channel-independent OA operation requested by a user."""

    kind: str
    indexes: tuple[int, ...] = ()
    remark: str = ""
    target: str = ""
    source: str = "user"
    snapshot_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not str(self.kind or "").strip():
            raise ValueError("kind must not be empty")
        indexes = tuple(int(index) for index in self.indexes)
        if any(index <= 0 for index in indexes):
            raise ValueError("indexes must contain positive integers")
        object.__setattr__(self, "indexes", indexes)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class RuleIntent:
    """A normalized request to inspect or mutate an approval rule.

    Conditions remain data (not executable expressions).  Validation of field
    names and operators belongs to the rule engine, not the channel parser.
    """

    kind: str = "create_rule"
    conditions: Mapping[str, Any] = field(default_factory=dict)
    keywords: Mapping[str, Any] = field(default_factory=dict)
    keyword_scope: tuple[str, ...] = ()
    action: str = "approve"
    enabled: bool = True
    priority: int = 0
    expires_at: str = ""
    limits: Mapping[str, Any] = field(default_factory=dict)
    unknown_policy: str = "require_human"
    conflict_policy: str = "require_human"
    source: str = "user"
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not str(self.kind or "").strip():
            raise ValueError("kind must not be empty")
        if self.priority < 0:
            raise ValueError("priority must be non-negative")
        object.__setattr__(self, "conditions", _copy_mapping(self.conditions))
        object.__setattr__(self, "keywords", _copy_mapping(self.keywords))
        object.__setattr__(self, "keyword_scope", tuple(str(item) for item in self.keyword_scope))
        object.__setattr__(self, "limits", _copy_mapping(self.limits))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class ApprovalContext:
    """Canonical fields presented to the approval-rule matcher.

    ``extra`` carries adapter fields that are not yet part of the stable
    schema.  Missing values stay ``None`` and must be treated as unknown by
    automatic approval.
    """

    workflow_id: str = ""
    workflow_name: str = ""
    applicant_id: str = ""
    applicant_name: str = ""
    applicant_department: str = ""
    amount: int | float | None = None
    currency: str = ""
    project: str = ""
    cost_center: str = ""
    submitted_at: str = ""
    attachment_count: int | None = None
    risk_level: str = ""
    request_title: str = ""
    request_description: str = ""
    form_text: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.attachment_count is not None and self.attachment_count < 0:
            raise ValueError("attachment_count must be non-negative")
        object.__setattr__(self, "extra", _copy_mapping(self.extra))


@dataclass(frozen=True)
class ToolExecutionEvent:
    """Normalized result of an OA capability/MCP invocation."""

    tool_key: str
    outcome: str
    call_id: str = ""
    request_id: str = ""
    error_category: str | None = None
    result: Any = None
    occurred_at: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not str(self.tool_key or "").strip():
            raise ValueError("tool_key must not be empty")
        if not str(self.outcome or "").strip():
            raise ValueError("outcome must not be empty")
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


__all__ = [
    "ApprovalContext",
    "ChannelEnvelope",
    "OAIntent",
    "RuleIntent",
    "ToolExecutionEvent",
]
