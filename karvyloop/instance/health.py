"""Health —— 健康度评估 + 自动降级。

**核心不变量**:
- M5 drift_ratio 升**高** → 强**制**降**级**
- M7 全**部**注**入**

设计:docs/17 §3.5。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from .scheduler import (
    STATE_DISMISSED,
    STATE_SLEEPING,
    Instance,
    ScheduleError,
    Scheduler,
)

logger = logging.getLogger(__name__)


# 默**认**降**级**阈**值**(拍 6 Syntonos **默**认**阈**值** 一**致** — S5 拍 6)
DEFAULT_DRIFT_DEMOTE_THRESHOLD: float = 0.3


@dataclasses.dataclass(frozen=True)
class HealthStatus:
    """一**个** instance 的**健**康**度**状**态**。"""
    instance_id: str
    is_healthy: bool
    state: str
    drift_ratio: float
    recommended_action: str          # "keep" / "dismiss" / "sleep"


@dataclasses.dataclass(frozen=True)
class Health:
    """Health 评**估**器**(全**部**注**入**)**。"""
    scheduler: Scheduler
    drift_demote_threshold: float = DEFAULT_DRIFT_DEMOTE_THRESHOLD

    def check(self, instance_id: str) -> HealthStatus:
        """单**个** instance 健康度。"""
        inst = self.scheduler.get(instance_id)
        if inst is None:
            raise ScheduleError(f"instance {instance_id} not found")
        is_drifting = inst.drift_ratio >= self.drift_demote_threshold
        if inst.state == STATE_DISMISSED:
            action = "dismiss"
            healthy = False
        elif is_drifting:
            action = "dismiss"
            healthy = False
        else:
            action = "keep"
            healthy = True
        return HealthStatus(
            instance_id=instance_id,
            is_healthy=healthy,
            state=inst.state,
            drift_ratio=inst.drift_ratio,
            recommended_action=action,
        )


def demote_if_drifting(
    scheduler: Scheduler,
    instance_id: str,
    threshold: float = DEFAULT_DRIFT_DEMOTE_THRESHOLD,
) -> Optional[Instance]:
    """AC5 入口:drift_ratio ≥ 阈**值** → 强**制** dismiss。

    返**新**的 instance(**已** dismiss),或 None(**未**触**发**降**级**)。
    """
    inst = scheduler.get(instance_id)
    if inst is None:
        raise ScheduleError(f"instance {instance_id} not found")
    if inst.drift_ratio >= threshold:
        logger.warning(
            "demote_if_drifting: instance %s drift_ratio=%.3f ≥ %.3f → 强**制** dismiss",
            instance_id, inst.drift_ratio, threshold,
        )
        return scheduler.dismiss(instance_id)
    return None
