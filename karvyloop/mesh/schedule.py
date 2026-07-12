"""mesh/schedule — 异构调度地基:feasibility 能力硬过滤 + 删除能力 delta(docs/74 §6.3/§6.2)。

调度三段(feasibility→ranking→claim)里**先做对的一件 = feasibility**:任务声明 `needs:` 能力,
用设备**能力指纹**布尔过滤出可行设备集。纯布尔、零模型、确定性——雷达点名最省最咬合的第一刀。
一步同时解决:能力匹配 / 不硬派做不了的活 / 移动端只接它独占的能力。

**feasibility 只 gate【执行】**(谁能跑这活),不 gate【参与】(发起/决策/旁观人人有,见 §6.1)。
ranking(局部性/别冷启/负载)、claim(MeshLog+HLC 裁决)、lease/心跳/重排是后续 slice。
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Set


def feasible(needs: Iterable[str], caps: Iterable[str]) -> bool:
    """一台设备能不能执行这活:它的能力集 ⊇ 任务要求的能力(布尔硬过滤)。needs 空 → 人人可行。"""
    return set(needs or ()) <= set(caps or ())


def feasible_devices(needs: Iterable[str], devices: List, *, now: Optional[float] = None,
                     require_online: bool = True) -> List:
    """可行设备集:能力满足 `needs` **且在线**(require_online=False 则不看在线,给"提前预警"用)。

    这是失败/调度一切判断的地基:重排找可行(在线)/ 阻塞判无可行 / 定时提前查(看全体不看在线)。
    """
    out = []
    for d in devices:
        if require_online and not d.online(now):
            continue
        if feasible(needs, getattr(d, "capabilities", [])):
            out.append(d)
    return out


def capability_delta_on_remove(target, all_devices: List) -> Set[str]:
    """删掉 target 设备会**永久失去**的能力 = target 独占的(其它设备都没有的)。

    非空 = 能力边界收窄 → 删除前该警告 + 要求再确认(§6.2 "知情的 H2A");空 = 只降资源不降能力。
    """
    others: Set[str] = set()
    tid = getattr(target, "device_id", "")
    for d in all_devices:
        if getattr(d, "device_id", "") != tid:
            others |= set(getattr(d, "capabilities", []) or ())
    return set(getattr(target, "capabilities", []) or ()) - others


def scheduled_task_at_risk(needs: Iterable[str], devices: List,
                           now: Optional[float] = None) -> bool:
    """定时任务提前预警判据(§6.4):当前**没有任何在线设备能执行**它 → 有风险(该警告)。

    定时任务是计划的,能力需求已知 → 提前交叉比对(needs × 在线设备)可预见不可用,咬人前先发现。
    """
    return len(feasible_devices(needs, devices, now=now, require_online=True)) == 0


__all__ = ["feasible", "feasible_devices", "capability_delta_on_remove", "scheduled_task_at_risk"]
