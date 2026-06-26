"""instance — M2.0 拍 7 Instance Manager(M2.0 最后一拍)。

设计:docs/17-instance-manager.md。

**4 段流水线**:Request → Resolve → Load → Schedule
**4 类场景**:transfer(调岗) / work(上班) / dm(私聊) / share(Share)
**灵魂层子集协议**:transfer=7 文件 / work=5 文件 / dm=1 文件 / share=2 文件
**健康度评估**:拍 5 Auditor Attestation + 拍 6 Syntonos DriftReport

**核心不变量**(doc §4):
- M1 instance 必**须**绑 attestation_hash(身**份**对**账**)
- M2 soul_subset **由** scenario 决**定**(**不**接**受** **外**部**传**)
- M3 4 类**子**集**全**有**时**才**算**协**议**完**整**
- M4 dismissed instance **不**接**受** **新**请**求**
- M5 drift_ratio 升**高** → 强**制**降**级**
- M6 调岗**不**延**用**旧 Attestation
- M7 全**部**依**赖**注**入**
- M8 **不**调 LLM
"""
from __future__ import annotations

from .context import SOUL_SUBSETS, Scenario, get_soul_subset
from .scheduler import (
    Instance,
    InstanceState,
    ScheduleError,
    ScheduleRequest,
    Scheduler,
    default_scheduler,
)
from .health import Health, HealthStatus, demote_if_drifting

__all__ = [
    "SOUL_SUBSETS",
    "Scenario",
    "get_soul_subset",
    "Instance",
    "InstanceState",
    "ScheduleError",
    "ScheduleRequest",
    "Scheduler",
    "default_scheduler",
    "Health",
    "HealthStatus",
    "demote_if_drifting",
]
