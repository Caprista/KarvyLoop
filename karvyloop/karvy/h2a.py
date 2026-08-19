"""h2a — H2A 决策层(K5 灵魂级:用户拍板,AI 不产生 ACCEPT)。

**核心不变量**(doc §4):
- K5 H2A 决策 = 用户拍板(AI 不产生 ACCEPT)

设计:docs/20 §3.7。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Callable, Optional

from karvyloop.a2a import (
    Envelope,
    EnvelopeType,
    RejectPayload,
)
from karvyloop.domain import Address

logger = logging.getLogger(__name__)


# H2A 决策类型
H2A_ACCEPT: str = "ACCEPT"
H2A_REJECT: str = "REJECT"
H2A_DEFER: str = "DEFER"  # 用户没拍板(继续等)

# H2A 拍板模式
H2A_MODE_BLOCKING: str = "blocking"
H2A_MODE_NON_BLOCKING: str = "non_blocking"

# H2A outcome 封闭枚举(借 DeepSeek Harness approval 设计:fail-closed + 一锤定音)。
# 空字符串 = 未指定,由 decision_to_envelope 按 decision 推导(向后兼容)。
H2A_OUTCOME_ALLOWED_ONCE: str = "allowed-once"
H2A_OUTCOME_AUTO_ALLOWED: str = "auto-allowed"   # 非阻塞模式下系统先自动推进
H2A_OUTCOME_REJECTED: str = "rejected"
H2A_OUTCOME_CANCELLED: str = "cancelled"
H2A_OUTCOME_UNAVAILABLE: str = "unavailable"
H2A_OUTCOME_PENDING: str = "pending"

H2A_OUTCOMES: frozenset[str] = frozenset({
    H2A_OUTCOME_ALLOWED_ONCE,
    H2A_OUTCOME_AUTO_ALLOWED,
    H2A_OUTCOME_REJECTED,
    H2A_OUTCOME_CANCELLED,
    H2A_OUTCOME_UNAVAILABLE,
    H2A_OUTCOME_PENDING,
})


def _derive_outcome(decision: str, outcome: str = "") -> str:
    """由 decision 推导 outcome(向后兼容:旧调用不传 outcome 时自动补)。"""
    if outcome and outcome in H2A_OUTCOMES:
        return outcome
    if decision == H2A_ACCEPT:
        return H2A_OUTCOME_ALLOWED_ONCE
    if decision == H2A_REJECT:
        return H2A_OUTCOME_REJECTED
    if decision == H2A_DEFER:
        return H2A_OUTCOME_PENDING
    return H2A_OUTCOME_UNAVAILABLE


@dataclasses.dataclass(frozen=True)
class H2ADecision:
    """H2A 决策结果。"""
    user_address: Address
    proposal_id: str
    decision: str  # ACCEPT / REJECT / DEFER
    reason: str = ""  # REJECT 时必填
    timestamp: str = ""
    mode: str = H2A_MODE_BLOCKING  # blocking / non_blocking
    outcome: str = ""  # 空 = 由 decision 推导;非空必须是 H2A_OUTCOMES 之一

    def __post_init__(self):
        object.__setattr__(self, "outcome", _derive_outcome(self.decision, self.outcome))
        # frozen 校验 mode
        if self.mode not in (H2A_MODE_BLOCKING, H2A_MODE_NON_BLOCKING):
            raise ValueError(f"H2ADecision mode must be blocking/non_blocking, got {self.mode!r}")


# 注入式用户输入(测试可 mock)
UserInputFn = Callable[[str, Address], H2ADecision]


def _default_user_input(prompt: str, user: Address) -> H2ADecision:
    """默认:阻塞等用户输入(本拍 v0 简化:直接 DEFER)。

    真实环境:接 KarvyChat 前端(用户点击按钮)。
    测试:用 mock 替代。
    """
    return H2ADecision(
        user_address=user,
        proposal_id="",
        decision=H2A_DEFER,
        reason="default_user_input_no_real_input",
    )


def h2a_decide(
    *,
    user: Address,
    proposal_id: str,
    proposal_summary: str,
    user_input: Optional[UserInputFn] = None,
    timestamp_fn: Optional[Callable[[], str]] = None,
) -> H2ADecision:
    """H2A 决策(K5:等用户拍板,AI 不产生 ACCEPT)。

    返回 H2ADecision(decision 字段是 ACCEPT/REJECT/DEFER)。
    调用方根据 decision 决定是否经 A2A 投递。
    """
    if user.role != "user":
        raise ValueError(f"K5: H2A 决策必须是 user, got role={user.role!r}")
    inp = user_input or _default_user_input
    decision_obj = inp(proposal_summary, user)
    # K5 强化校验:AI 不能产生 ACCEPT(M3+ 用 audit 拦截;M3 v0 仅校验语义)
    # 本拍 v0:user_input 是注入的,默认 _default_user_input 返回 DEFER,自然安全
    if decision_obj.decision == H2A_REJECT and not decision_obj.reason.strip():
        raise ValueError("K5: H2A REJECT must have non-empty reason")
    return decision_obj


def decision_to_envelope(
    decision: H2ADecision,
    to: Address,
    *,
    timestamp_fn: Optional[Callable[[], str]] = None,
    sign_secret: bytes = b"",
) -> Envelope:
    """把 H2A 决策转成 A2A envelope(供 docs/19 EnvelopeRouter 投递)。

    强制:
      - 必须是 ACCEPT 或 REJECT(DEFER 不发 envelope)
      - REJECT 必须带 reason(A8 边界)
    """
    from .courier import _default_timestamp_fn
    ts = (timestamp_fn or _default_timestamp_fn)()

    if decision.decision == H2A_ACCEPT:
        payload = {"proposal_id": decision.proposal_id, "summary": "ACCEPT"}
        env_type = EnvelopeType.ACCEPT.value
    elif decision.decision == H2A_REJECT:
        payload = RejectPayload(reason=decision.reason, proposal_id=decision.proposal_id)
        env_type = EnvelopeType.REJECT.value
    else:
        raise ValueError(f"K5: DEFER cannot be converted to envelope; decision={decision}")

    env = Envelope(
        type=env_type,
        from_=decision.user_address,
        by=(),  # H2A 是用户直接发(不是小卡代发),by 空
        to=to,
        payload=payload,
        ts=ts,
        signature=b"",
    )
    from karvyloop.a2a import sign_envelope
    return dataclasses.replace(env, signature=sign_envelope(env, sign_secret))
