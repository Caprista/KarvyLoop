"""Stage 4 Deviation —— 长期偏离检测(滑动窗)。

**核心不变量**:
- S4 偏离率 = (missing + forbidden) / total(不含 extra)
- S5 滑动窗 100 + 阈值 0.3

设计:docs/16 §3.5。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Sequence

from .baseline import BehaviorEvent, SyntonosBaseline
from .policy import DriftReport
from .reconcile import reconcile

logger = logging.getLogger(__name__)


# 协议不变量:滑动窗 100 + 阈值 0.3
DEFAULT_WINDOW_SIZE: int = 100
DEFAULT_THRESHOLD: float = 0.3


@dataclasses.dataclass(frozen=True)
class DriftConfig:
    """偏离检测配置(S6 注入式)。"""
    window_size: int = DEFAULT_WINDOW_SIZE
    threshold: float = DEFAULT_THRESHOLD


def default_drift_config() -> DriftConfig:
    return DriftConfig()


def detect_drift(
    events: Sequence[BehaviorEvent],
    baseline: SyntonosBaseline,
    *,
    config: DriftConfig = None,   # type: ignore[assignment]
) -> DriftReport:
    """AC6 入口:滑动窗偏离检测。

    S4:ratio = (missing + forbidden) / total
    S5:window=100 + threshold=0.3(默认)
    """
    if config is None:
        config = default_drift_config()
    window = list(events[-config.window_size:])  # 取最**近** N 条
    if not window:
        return DriftReport(
            drift_ratio=0.0,
            threshold=config.threshold,
            is_drifting=False,
            window_size=config.window_size,
            time_span="<empty>",
            missing_count=0,
            forbidden_count=0,
            extra_count=0,
            total_events=0,
        )

    report = reconcile(baseline, window)

    # S4:偏**离**率 = (missing + forbidden) / total,**不**含 extra
    missing = report.warning_count       # missing 走 warning
    forbidden = report.error_count       # forbidden 走 error
    total = max(1, len(window))
    ratio = (missing + forbidden) / total
    is_drifting = ratio >= config.threshold

    # time_span
    if window:
        start = window[0].timestamp
        end = window[-1].timestamp
        time_span = f"{start}/{end}"
    else:
        time_span = "<empty>"

    return DriftReport(
        drift_ratio=ratio,
        threshold=config.threshold,
        is_drifting=is_drifting,
        window_size=config.window_size,
        time_span=time_span,
        missing_count=missing,
        forbidden_count=forbidden,
        extra_count=report.info_count,
        total_events=len(window),
    )
