"""Stage 3 Reconcile —— baseline ↔ events 对账。

**核心不变量**:
- S2 不抛(severity 走 finding)
- S3 3 类发现协议
- S8 不调 LLM(关键词对比)

设计:docs/16 §3.4。
"""
from __future__ import annotations

import logging
from typing import Sequence

from .baseline import BehaviorEvent, SyntonosBaseline
from .policy import (
    CHECK_EXTRA,
    CHECK_FORBIDDEN,
    CHECK_MISSING,
    SyntonosFinding,
    SyntonosReport,
)

logger = logging.getLogger(__name__)


def reconcile(
    baseline: SyntonosBaseline,
    events: Sequence[BehaviorEvent],
) -> SyntonosReport:
    """AC2/AC3/AC4 入口:baseline ↔ events 对账。

    逻辑:
      1. missing    = 预期关键词 ∖ observed
      2. forbidden  = 禁忌 ∩ observed
      3. extra      = observed ∖ (预期 ∪ 禁忌)
    """
    from karvyloop.ethos import ERROR, INFO, WARNING

    expected = set(baseline.all_expected_keywords())
    forbidden = set(baseline.forbidden_keywords)
    observed: set[str] = set()
    for e in events:
        observed.update(e.extract_keywords())

    findings: list[SyntonosFinding] = []

    # missing
    for kw in expected - observed:
        findings.append(SyntonosFinding(
            check_id=CHECK_MISSING,
            severity=WARNING,
            message=f"基**准**线**预**期** {kw!r} 但**行**为**中** **没**出**现**",
            baseline_keyword=kw,
        ))

    # forbidden
    for kw in forbidden & observed:
        findings.append(SyntonosFinding(
            check_id=CHECK_FORBIDDEN,
            severity=ERROR,
            message=f"基**准**线**禁**忌** {kw!r} 但**行**为**出**现**了",
            baseline_keyword=kw,
        ))

    # extra
    expected_or_forbidden = expected | forbidden
    for kw in observed - expected_or_forbidden:
        findings.append(SyntonosFinding(
            check_id=CHECK_EXTRA,
            severity=INFO,
            message=f"行**为**有** {kw!r} 基**准**线**没**有**预**期**",
            baseline_keyword=kw,
        ))

    return SyntonosReport(
        ok=not any(f.severity == ERROR for f in findings),
        baseline_hash=baseline.baseline_hash,
        events_examined=len(events),
        findings=tuple(findings),
    )
