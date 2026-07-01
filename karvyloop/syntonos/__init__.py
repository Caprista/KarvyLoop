"""syntonos — M2.0 拍 6 行为审计。

设计:docs/16-syntonos.md。

**4 段流水线**:Baseline → Capture → Reconcile → Deviation
**接 Auditor Attestation (拍 5)**:baseline 身份 = attestation_hash
**3 类对账发现**:missing / forbidden / extra
**长期偏离检测**:滑动窗(默认 100)+ 阈值(默认 0.3)

**核心不变量**(doc §4):
- S1 baseline 来源 = 拍 5 Attestation(不重新生成)
- S2 不抛(severity 走 finding)
- S3 3 类发现协议锁住
- S4 偏离率 = (missing + forbidden) / total(不含 extra)
- S5 滑动窗 100 + 阈值 0.3
- S6 baseline + events = 注入(无全局)
- S7 对账 + 偏离 = 2 独立产物
- S8 不调 LLM(关键词对比)
"""
from __future__ import annotations

from .baseline import EVENT_TYPES, BehaviorEvent, SyntonosBaseline, build_baseline_from_attestation
from .policy import (
    SYNTONOS_CHECK_IDS,
    DriftReport,
    SyntonosFinding,
    SyntonosReport,
)
from .reconcile import reconcile
from .deviation import DEFAULT_THRESHOLD, DEFAULT_WINDOW_SIZE, DriftConfig, detect_drift, default_drift_config

__all__ = [
    "BehaviorEvent",
    "SyntonosBaseline",
    "build_baseline_from_attestation",
    "SYNTONOS_CHECK_IDS",
    "DriftReport",
    "SyntonosFinding",
    "SyntonosReport",
    "reconcile",
    "detect_drift",
    "default_drift_config",
]
