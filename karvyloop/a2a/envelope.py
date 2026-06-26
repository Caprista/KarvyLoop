"""envelope — A2A 协议 typed message(11 种)+ by 字段灵魂级。

**核心不变量**(doc §4):
- A1 `from: karvy` 永远不合法
- A2 `by` 不含 `from_` 本身
- A3 `by` 不含 `karvy` 的 `from_`(小卡只做中间人)
- A4 任何 envelope 都要签名(M3 v0:用 sha256 hash 模拟)
- A8 `REJECT` 必须带 `reason`

设计:docs/19 §3.1 + §3.2。
"""
from __future__ import annotations

import dataclasses
import hashlib
from enum import Enum
from typing import Optional

from karvyloop.domain import Address


# 11 种 envelope 类型(docs/19 §3.1)
class EnvelopeType(str, Enum):
    """A2A 协议 envelope 类型(11 种)。"""
    # 任务派发
    TASK_ASSIGN = "task.assign"
    TASK_PROGRESS = "task.progress"
    TASK_DONE = "task.done"
    # 问答
    ASK = "ask"
    ANSWER = "answer"
    # 协商
    PROPOSE = "propose"
    ACCEPT = "accept"
    REJECT = "reject"
    # 广播
    BROADCAST = "broadcast"
    # 审计
    AUDIT_REQUEST = "audit.request"
    AUDIT_RESPONSE = "audit.response"


# 11 种类型全有才协议完整(AC1)
EXPECTED_ENVELOPE_TYPES: tuple[str, ...] = (
    EnvelopeType.TASK_ASSIGN.value,
    EnvelopeType.TASK_PROGRESS.value,
    EnvelopeType.TASK_DONE.value,
    EnvelopeType.ASK.value,
    EnvelopeType.ANSWER.value,
    EnvelopeType.PROPOSE.value,
    EnvelopeType.ACCEPT.value,
    EnvelopeType.REJECT.value,
    EnvelopeType.BROADCAST.value,
    EnvelopeType.AUDIT_REQUEST.value,
    EnvelopeType.AUDIT_RESPONSE.value,
)


# 寻址常量
KARVY_AGENT_ID: str = "karvy"  # 小卡 agent_id(灵魂级)


# ---- 三边界错误 ----
class FromKarvyForbiddenError(RuntimeError):
    """A1: from: karvy 永远不合法。"""


class ByContainsFromError(RuntimeError):
    """A2: by 不能含 from_ 本身。"""


class RejectMissingReasonError(RuntimeError):
    """A8: REJECT 必须带 reason。"""


class SignatureMissingError(RuntimeError):
    """A4: 任何 envelope 都要签名。"""


# ---- Payload 基础 ----
@dataclasses.dataclass(frozen=True)
class TaskPayload:
    """TASK_* 类共用 payload。"""
    task_id: str
    description: str
    context: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class QA:
    """ASK / ANSWER 共用。"""
    question: str
    question_id: str = ""


@dataclasses.dataclass(frozen=True)
class ProposePayload:
    """PROPOSE。"""
    proposal_id: str
    summary: str
    options: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class RejectPayload:
    """REJECT(必须带 reason,A8)。"""
    reason: str
    proposal_id: str = ""


@dataclasses.dataclass(frozen=True)
class BroadcastPayload:
    """BROADCAST。"""
    message: str
    tag: str = ""


# ---- Envelope 根类型 ----
@dataclasses.dataclass(frozen=True)
class Envelope:
    """A2A 协议 envelope(docs/19 §3.1)。

    字段:
      type: 11 种之一
      from_: 发起方(必填,不能是 karvy,A1)
      by: 代发链(不含 from_ 本身,A2)
      to: 目标寻址
      payload: 类型相关
      ts: ISO 时间戳
      signature: 拍 5 签(M3 v0 用 sha256 hash 模拟)
    """
    type: str  # EnvelopeType value
    from_: Address
    by: tuple[Address, ...]
    to: Address
    payload: object
    ts: str
    signature: bytes = b""

    def __post_init__(self) -> None:
        # A1: from: karvy 永远不合法
        if self.from_.agent_id == KARVY_AGENT_ID:
            raise FromKarvyForbiddenError(
                f"A1: from_: karvy 永远不合法(from_={self.from_});小卡只做中间人,不在 by 也不在 from_"
            )
        # A2: by 不含 from_ 本身
        if self.from_ in self.by:
            raise ByContainsFromError(
                f"A2: by 不能含 from_ 本身(by={self.by}, from_={self.from_})"
            )
        # A8: REJECT 必须带 reason
        if self.type == EnvelopeType.REJECT.value:
            if not isinstance(self.payload, RejectPayload):
                raise RejectMissingReasonError(
                    "A8: REJECT payload must be RejectPayload instance"
                )
            if not self.payload.reason or not self.payload.reason.strip():
                raise RejectMissingReasonError(
                    "A8: REJECT must have non-empty reason"
                )


# ---- 签(M3 v0 用 sha256 模拟)----
def sign_envelope(env: Envelope, secret: bytes = b"") -> bytes:
    """A4: 任何 envelope 都要签名(M3 v0 用 sha256 模拟,无 LLM)。

    生产:接拍 5 Auditor 的 Ethos Attestation。
    """
    h = hashlib.sha256()
    h.update(env.type.encode("utf-8"))
    h.update(repr(env.from_).encode("utf-8"))
    h.update(repr(env.by).encode("utf-8"))
    h.update(repr(env.to).encode("utf-8"))
    h.update(repr(env.payload).encode("utf-8"))
    h.update(env.ts.encode("utf-8"))
    h.update(secret)
    return h.digest()


def verify_envelope(env: Envelope, secret: bytes = b"") -> bool:
    """A4: 验签。"""
    return env.signature == sign_envelope(env, secret)
