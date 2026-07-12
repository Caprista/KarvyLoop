"""mesh/tasks — 共享日志上的去中心任务板(散的承重梁 + 调度的认领,docs/74 §6.2/§6.3)。

任务 = MeshLog 上的一串事件:**offer(报价)→ claim(认领)→ heartbeat(续租)→ done(完成)**。
没有中央任务队列、没有主设备:各设备本地把日志折叠成任务态,靠 **HLC 定序裁并发认领**、
**lease 过期任一在线可行设备重认领**(去中心 failover)。这就是"丢一台设备只降资源、活不丢"。

- **认领竞争**:两台同时 claim 同一任务 → HLC 最早的赢(所有设备日志 HLC 同序 → 一致同意赢家)。
- **lease 过期重排**:claimer 不再心跳续租,`lease_until < now` → 任务转"可再认领",别的在线设备接。
- **幂等/at-least-once**:重排可能重跑(雷达 A);有副作用的活从 checkpoint 续或走 H2A(上层保证)。

事件 payload 带 `at`(创建时墙钟,给 lease 时间语义);HLC 只管排序/裁决。feasibility(谁能认领)
由 [[mesh/schedule]] 的 `feasible` 提供,不在此。
"""
from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional

from karvyloop.mesh.synclog import MeshEvent, MeshLog

# 任务态
ST_OFFERED = "offered"        # 已报价,待认领
ST_CLAIMED = "claimed"        # 已认领且 lease 有效
ST_RECLAIMABLE = "reclaimable"  # 认领过但 lease 过期(claimer 掉线没续租)→ 可再认领(散:活不丢)
ST_DONE = "done"              # 已完成

K_OFFER = "task-offer"
K_CLAIM = "task-claim"
K_HEARTBEAT = "task-heartbeat"
K_DONE = "task-done"


@dataclasses.dataclass
class TaskState:
    task_id: str
    needs: list = dataclasses.field(default_factory=list)   # 要求的执行能力(feasibility 用)
    payload: dict = dataclasses.field(default_factory=dict)
    lease_s: float = 60.0
    status: str = ST_OFFERED
    claimer: str = ""
    lease_until: float = 0.0
    result: Optional[dict] = None


# ---- 往日志写任务事件(各设备本地调,同步后全设备可见)----

def offer_task(log: MeshLog, task_id: str, needs: list, payload: dict, *,
               wall: int, lease_s: float = 60.0) -> MeshEvent:
    return log.append(K_OFFER, {"task_id": task_id, "needs": list(needs or []),
                                "payload": dict(payload or {}), "lease_s": float(lease_s),
                                "at": wall}, wall=wall)


def claim_task(log: MeshLog, task_id: str, *, wall: int) -> MeshEvent:
    """本设备(log.device_id)认领一个任务;是否真赢由 materialize 按 HLC/lease 裁。"""
    return log.append(K_CLAIM, {"task_id": task_id, "at": wall}, wall=wall)


def heartbeat_task(log: MeshLog, task_id: str, *, wall: int) -> MeshEvent:
    return log.append(K_HEARTBEAT, {"task_id": task_id, "at": wall}, wall=wall)


def complete_task(log: MeshLog, task_id: str, result: dict, *, wall: int) -> MeshEvent:
    return log.append(K_DONE, {"task_id": task_id, "result": dict(result or {}), "at": wall}, wall=wall)


# ---- 把日志折叠成任务态(物化视图;各设备算得一致 —— 日志 HLC 同序)----

def materialize_tasks(events: List[MeshEvent], now: int) -> Dict[str, TaskState]:
    """按 HLC 全序折叠任务事件 → 每任务当前态。now = 当前墙钟(判 lease 是否过期)。"""
    tasks: Dict[str, TaskState] = {}
    for ev in sorted(events, key=lambda e: (e.hlc, e.device_id)):   # 与 MeshLog.entries 同序
        p = ev.payload or {}
        tid = str(p.get("task_id") or "")
        if not tid:
            continue
        if ev.kind == K_OFFER:
            t = tasks.setdefault(tid, TaskState(task_id=tid))
            t.needs = list(p.get("needs") or [])
            t.payload = dict(p.get("payload") or {})
            t.lease_s = float(p.get("lease_s") or 60.0)
            if t.status == ST_OFFERED and not t.claimer:
                t.status = ST_OFFERED
        elif ev.kind == K_CLAIM:
            t = tasks.get(tid)
            if t is None or t.status == ST_DONE:
                continue
            at = float(p.get("at") or ev.hlc.wall)
            # 可认领 = 没 claimer,或前一个 claimer 的 lease 到本次认领时已过期(散:重认领)
            if not t.claimer or t.lease_until <= at:
                t.claimer = ev.device_id                # HLC 最早的先到,先占;晚到的这条进不来
                t.lease_until = at + t.lease_s
                t.status = ST_CLAIMED
        elif ev.kind == K_HEARTBEAT:
            t = tasks.get(tid)
            if t is not None and t.status == ST_CLAIMED and t.claimer == ev.device_id:
                t.lease_until = float(p.get("at") or ev.hlc.wall) + t.lease_s   # 续租
        elif ev.kind == K_DONE:
            t = tasks.get(tid)
            if t is not None:
                t.status = ST_DONE
                t.result = dict(p.get("result") or {})
    # 收尾:已认领但 lease 过期(claimer 掉线没续)→ 转可再认领(承重梁:活不丢)
    for t in tasks.values():
        if t.status == ST_CLAIMED and t.lease_until < now:
            t.status = ST_RECLAIMABLE
    return tasks


def claimable_for(events: List[MeshEvent], now: int, my_caps: list) -> List[TaskState]:
    """本设备现在能认领的任务:状态 offered/reclaimable **且** 我的能力满足它的 needs(feasibility)。"""
    from karvyloop.mesh.schedule import feasible
    out = []
    for t in materialize_tasks(events, now).values():
        if t.status in (ST_OFFERED, ST_RECLAIMABLE) and feasible(t.needs, my_caps):
            out.append(t)
    return out


__all__ = ["TaskState", "offer_task", "claim_task", "heartbeat_task", "complete_task",
           "materialize_tasks", "claimable_for",
           "ST_OFFERED", "ST_CLAIMED", "ST_RECLAIMABLE", "ST_DONE"]
