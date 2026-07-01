"""inbox — Tier 1 in-process inbox(同进程投递)。

设计:docs/19 §3.5。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from karvyloop.domain import Address

from .envelope import Envelope

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class InboxEntry:
    """inbox 一条记录。"""
    target: Address
    envelope: Envelope


class Inbox:
    """同进程 in-memory inbox(全部注入,无全局)。

    职责:envelope 投递(同进程,dict 存)。
    """

    def __init__(self) -> None:
        # 收件人 → Envelope 列表
        self._boxes: dict[tuple, list[Envelope]] = {}

    def deliver(self, target: Address, env: Envelope) -> None:
        """投递 envelope 到目标。"""
        key = (target.domain_id, target.role, target.agent_id)
        self._boxes.setdefault(key, []).append(env)

    def fetch(self, target: Address) -> tuple[Envelope, ...]:
        """取走目标的所有 envelope(atomic,取走后清空)。"""
        key = (target.domain_id, target.role, target.agent_id)
        items = self._boxes.pop(key, [])
        return tuple(items)

    def peek(self, target: Address) -> tuple[Envelope, ...]:
        """查看(不取走)。"""
        key = (target.domain_id, target.role, target.agent_id)
        return tuple(self._boxes.get(key, []))

    def count(self, target: Optional[Address] = None) -> int:
        if target is None:
            return sum(len(v) for v in self._boxes.values())
        key = (target.domain_id, target.role, target.agent_id)
        return len(self._boxes.get(key, []))
