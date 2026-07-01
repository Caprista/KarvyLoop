"""courier — 小卡的代发模块(K2 灵魂级:from: user, by: (karvy,))。

**核心不变量**(doc §4):
- K2 小卡发起的 envelope = from: user, by: (karvy,)

设计:docs/20 §3.5。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Callable, Optional

from karvyloop.a2a import (
    Envelope,
    EnvelopeType,
    KARVY_AGENT_ID,
    sign_envelope,
)
from karvyloop.domain import Address

from .core import KARVY, KarvyCore

logger = logging.getLogger(__name__)


# 注入式时间戳
TimestampFn = Callable[[], str]


def _default_timestamp_fn() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class Courier:
    """小卡的代发模块(灵魂级 K2)。

    职责:用户说 "小卡,帮我跟 X 说 Y" 时,小卡构造:
      from: user, by: (karvy,)
    永**远**不**是** `from: karvy`(A1 边界)。
    """

    def __init__(
        self,
        karvy: Optional[KarvyCore] = None,
        timestamp_fn: Optional[TimestampFn] = None,
        sign_secret: bytes = b"",
    ) -> None:
        self._karvy = karvy or KarvyCore()
        self._timestamp_fn = timestamp_fn or _default_timestamp_fn
        self._sign_secret = sign_secret

    def send(
        self,
        *,
        user_address: Address,
        to: Address,
        envelope_type: str,
        payload: object,
    ) -> Envelope:
        """代发(灵魂级 K2)。

        强制:
          - user_address.role == "user"
          - to.role != "observer"(小卡不给自己发,也不给其他 observer 发)
          - envelope_type 必须是 docs/19 §3.1 的 11 种之一

        返回 Envelope(已签名),交由 docs/19 EnvelopeRouter 投递。
        """
        if user_address.role != "user":
            raise ValueError(
                f"K2: courier_send user_address.role must be 'user', got {user_address.role!r}"
            )
        if to.is_observer():
            raise ValueError(
                f"K2: courier cannot send to another observer (to={to})"
            )
        if envelope_type not in (
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
        ):
            raise ValueError(
                f"unknown envelope_type {envelope_type!r}; expected one of 11 types"
            )

        # K2 灵魂级:from: user, by: (karvy,)
        env = Envelope(
            type=envelope_type,
            from_=user_address,
            by=(self._karvy.address(user_address.domain_id),),
            to=to,
            payload=payload,
            ts=self._timestamp_fn(),
            signature=b"",
        )
        # 签(A4)
        signed_env = dataclasses.replace(env, signature=sign_envelope(env, self._sign_secret))
        return signed_env
