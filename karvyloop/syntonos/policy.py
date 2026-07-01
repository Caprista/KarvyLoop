"""Syntonos 数据模型 + 3 类发现协议 + 对账/偏离报告。

设计:docs/16 §3.6。
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# 3 类**发**现**名**(S3 协**议**锁**住**)
CHECK_MISSING: str = "syntonos.missing"     # 基**准**线**有** X,行**为**没**有
CHECK_FORBIDDEN: str = "syntonos.forbidden"  # 基**准**线**禁**忌 X,行**为**出**现
CHECK_EXTRA: str = "syntonos.extra"          # 行**为**有 X,基**准**线**没**预**期

SYNTONOS_CHECK_IDS: tuple[str, ...] = (
    CHECK_MISSING, CHECK_FORBIDDEN, CHECK_EXTRA,
)


@dataclasses.dataclass(frozen=True)
class SyntonosFinding:
    """一条**对**账**发**现。"""
    check_id: str
    severity: str                  # error / warning / info
    message: str
    baseline_keyword: str
    event_type: str = ""


@dataclasses.dataclass(frozen=True)
class SyntonosReport:
    """对账报**告**。"""
    ok: bool                       # 无 error finding = True
    baseline_hash: str             # 拍 5 attestation 哈希(身**份**对**账**)
    events_examined: int
    findings: tuple[SyntonosFinding, ...]

    @property
    def error_count(self) -> int:
        from karvyloop.ethos import ERROR
        return sum(1 for f in self.findings if f.severity == ERROR)

    @property
    def warning_count(self) -> int:
        from karvyloop.ethos import WARNING
        return sum(1 for f in self.findings if f.severity == WARNING)

    @property
    def info_count(self) -> int:
        from karvyloop.ethos import INFO
        return sum(1 for f in self.findings if f.severity == INFO)


@dataclasses.dataclass(frozen=True)
class DriftReport:
    """**长**期**偏**离**检**测**报**告**。"""
    drift_ratio: float             # (missing+forbidden)/total
    threshold: float
    is_drifting: bool
    window_size: int
    time_span: str                 # "ISO_start/ISO_end"
    missing_count: int
    forbidden_count: int
    extra_count: int
    total_events: int

    def __bool__(self) -> bool:
        return self.is_drifting
