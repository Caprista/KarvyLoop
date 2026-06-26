"""audit — A2A 审计链(A5 不变量)。

**核心不变量**(doc §4):
- A5 审计链 = from_ + by 全部记录
- 全部依赖注入

设计:docs/19 §3.5。
"""
from __future__ import annotations

import dataclasses
import logging
import uuid
from typing import Callable, Optional

from .envelope import Envelope

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class AuditEntry:
    """审计链一条记录。"""
    entry_id: str
    envelope_type: str
    from_: object  # Address(避免循环 import)
    by: tuple[object, ...]
    to: object
    timestamp: str
    sequence: int


def _default_id_factory() -> str:
    return f"audit-{uuid.uuid4().hex[:8]}"


def _default_timestamp_fn() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class AuditChain:
    """A2A 审计链(全注入,无全局单例)。

    职责:每次 route 成功后 append 一条记录(from_ + by 全记录,A5)。
    """

    def __init__(
        self,
        id_factory: Optional[Callable[[], str]] = None,
        timestamp_fn: Optional[Callable[[], str]] = None,
    ) -> None:
        self._id_factory = id_factory or _default_id_factory
        self._timestamp_fn = timestamp_fn or _default_timestamp_fn
        self._entries: list[AuditEntry] = []
        self._seq: int = 0

    def append(self, env: Envelope) -> AuditEntry:
        """追加一条审计记录(A5)。"""
        self._seq += 1
        entry = AuditEntry(
            entry_id=self._id_factory(),
            envelope_type=env.type,
            from_=env.from_,
            by=env.by,
            to=env.to,
            timestamp=self._timestamp_fn(),
            sequence=self._seq,
        )
        self._entries.append(entry)
        return entry

    def all(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)

    def by_type(self, envelope_type: str) -> tuple[AuditEntry, ...]:
        return tuple(e for e in self._entries if e.envelope_type == envelope_type)

    def by_from(self, from_addr: object) -> tuple[AuditEntry, ...]:
        return tuple(e for e in self._entries if e.from_ == from_addr)

    def __len__(self) -> int:
        return len(self._entries)
