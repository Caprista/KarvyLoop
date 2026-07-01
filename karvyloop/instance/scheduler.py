"""Scheduler —— instance 调**度**主**类**。

**核心不变量**(doc §4):
- M1 attestation_hash 强**类**型
- M2 soul_subset **由** scenario 决**定**
- M4 dismissed instance **不**接**新**请**求**
- M6 调岗**不**延**用**旧 Attestation
- M7 全**部**注**入**

设计:docs/17 §3.3 + §3.4。
"""
from __future__ import annotations

import dataclasses
import logging
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from karvyloop.ethos import AuditorAttestation, AuditorReport

from .context import SOUL_SUBSETS, Scenario, get_soul_subset

logger = logging.getLogger(__name__)


# 3 个 instance state
STATE_ACTIVE: str = "active"
STATE_SLEEPING: str = "sleeping"
STATE_DISMISSED: str = "dismissed"


class InstanceState(str):
    """类型化 state。"""
    pass


class ScheduleError(RuntimeError):
    """schedule 阶段错误(dismissed / auditor not ok / 缺 attestation)。"""


@dataclasses.dataclass(frozen=True)
class Instance:
    """一**个**调**度**实**例**。"""
    instance_id: str                # 唯一 ID
    agent_id: str                   # 灵**魂**层**绑**定**
    scenario: str                   # 4 类**之**一**
    soul_subset: tuple[str, ...]    # 加**载**的**灵**魂**层**子**集**
    attestation_hash: str           # 拍 5 身**份**对**账**
    created_at: str                 # ISO
    state: str = STATE_ACTIVE
    drift_ratio: float = 0.0        # 拍 6 Syntonos **偏**离**率**


@dataclasses.dataclass(frozen=True)
class ScheduleRequest:
    """调**度**请**求**。"""
    agent_id: str
    scenario: str
    attestation: AuditorAttestation
    instance_id: Optional[str] = None   # 调岗时**传**旧**的**(M6 重**新**生**成** hash,**不**延**用**)


# ---- 注入式**生**成**器** ----
def _default_id_factory() -> str:
    return f"inst-{uuid.uuid4().hex[:8]}"


def _default_timestamp_fn() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- Scheduler 主类** ----

class Scheduler:
    """调**度**主**类**。**全部**注**入**(M7)。"""

    def __init__(
        self,
        id_factory: Optional[Callable[[], str]] = None,
        timestamp_fn: Optional[Callable[[], str]] = None,
    ) -> None:
        self._id_factory = id_factory or _default_id_factory
        self._timestamp_fn = timestamp_fn or _default_timestamp_fn
        self._instances: dict[str, Instance] = {}

    # ---- AC2/AC4:schedule 入口** ----

    def schedule(self, req: ScheduleRequest) -> Instance:
        """主**入口**:从请**求**生**成** Instance。

        拒**收**(抛 ScheduleError):
          - Auditor ok=False (AC6)
          - 调岗时**旧** instance 已 dismissed (M4)
        """
        if not req.attestation.ok:
            raise ScheduleError(
                f"AC6: attestation.ok=False, cannot create new instance "
                f"(attestation_hash={req.attestation.attestation_hash})"
            )
        if req.scenario not in SOUL_SUBSETS:
            raise ScheduleError(
                f"M3: unknown scenario {req.scenario!r}; expected {list(SOUL_SUBSETS)}"
            )

        # 调岗时**旧** instance 必**须** active (M4)
        if req.instance_id is not None:
            old = self._instances.get(req.instance_id)
            if old is None:
                raise ScheduleError(f"M4: instance_id {req.instance_id} not found")
            if old.state == STATE_DISMISSED:
                raise ScheduleError(
                    f"M4: instance {req.instance_id} already dismissed, cannot transfer"
                )
            # M6:**新** attestation hash(不**延**用**旧**)
            # req.attestation 已经**是**新**的,直**接**用

        instance = Instance(
            instance_id=self._id_factory(),
            agent_id=req.agent_id,
            scenario=req.scenario,
            soul_subset=get_soul_subset(req.scenario),
            attestation_hash=req.attestation.attestation_hash,
            created_at=self._timestamp_fn(),
            state=STATE_ACTIVE,
        )
        self._instances[instance.instance_id] = instance
        # 旧** instance **标**记 dismissed(调岗)
        if req.instance_id is not None and req.instance_id in self._instances:
            old = self._instances[req.instance_id]
            self._instances[req.instance_id] = dataclasses.replace(
                old, state=STATE_DISMISSED
            )
        return instance

    # ---- 查询** ----

    def get(self, instance_id: str) -> Optional[Instance]:
        return self._instances.get(instance_id)

    def all_instances(self) -> tuple[Instance, ...]:
        return tuple(self._instances.values())

    def by_agent(self, agent_id: str) -> tuple[Instance, ...]:
        return tuple(i for i in self._instances.values() if i.agent_id == agent_id)

    # ---- 健康度**更**新**(拍 6 接**入**)----

    def update_drift(self, instance_id: str, drift_ratio: float) -> Instance:
        """拍 6 Syntonos **接**入**后**调**用**,**更**新** drift_ratio。"""
        inst = self._instances.get(instance_id)
        if inst is None:
            raise ScheduleError(f"instance {instance_id} not found")
        new = dataclasses.replace(inst, drift_ratio=drift_ratio)
        self._instances[instance_id] = new
        return new

    # ---- AC3:dismissed **不**接**新**请**求**(M4)----

    def dismiss(self, instance_id: str) -> Instance:
        inst = self._instances.get(instance_id)
        if inst is None:
            raise ScheduleError(f"instance {instance_id} not found")
        new = dataclasses.replace(inst, state=STATE_DISMISSED)
        self._instances[instance_id] = new
        return new


def default_scheduler() -> Scheduler:
    return Scheduler()
