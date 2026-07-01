"""snapshot — 数据规整(snapshot → widget 状态)(M3 批 3)。

把 WorkbenchObserver 的只读 API + 4 原子 agent 的输出规整成 widget 友好的 dataclass。
"""
from __future__ import annotations

import dataclasses

from karvyloop.a2a import Envelope
from karvyloop.karvy.atoms import BoardAggregator, DataCourier, Overseer, TaskTracker
from karvyloop.karvy.observer import BoardSnapshot, WorkbenchObserver


@dataclasses.dataclass(frozen=True)
class WidgetSnapshot:
    """一个 widget tree 的输入数据(K4 只读)。"""
    domains: tuple[str, ...]                 # WorkbenchObserver.list_domains()
    current_domain: str                       # 当前选中的 domain(L0 顶导触发)
    broadcasts: tuple[Envelope, ...]          # 当前 domain 的 BROADCAST
    task_count: int                           # TaskTracker 统计
    pursuit_count: int                        # BoardAggregator 统计
    unhealthy: bool                           # Overseer.is_healthy() 反向
    # 批 5(M3+ 批 5):TUI 内 MainLoop 状态(本进程内,跨重启不持久 — 拍 6 解决)
    crystallized_skills: tuple[str, ...] = ()  # 已结晶的 skill name 列表(L0TopBar 显示)
    last_fast_brain_skill: str = ""            # 最近 1 次快脑命中的 skill name
    last_drive_text: str = ""                  # 最近 1 次 drive 的 text(供 L2Board 显示)
    # 批 8.5-A(M3+ 批 8.5):TUI 修"石沉大海" — 错误/输入回显独立通道
    last_error: str = ""                       # 最近 1 次 drive 的 error(供 L2Board 红字渲染,**不**截断)
    last_intent: str = ""                      # 最近 1 次用户提交的 intent(供 L2Board 显示输入回显)


def snapshot_for_widgets(
    workbench: WorkbenchObserver,
    *,
    current_domain: str | None = None,
) -> WidgetSnapshot:
    """拍 3a v0 简化版:无 TaskTracker/BoardAggregator 注入也能跑(返 0/False)。

    Args:
        workbench: 小卡工作台观察者。
        current_domain: 当前选中的 domain_id(None 时取 list_domains() 第一个)。

    Returns:
        WidgetSnapshot — widget tree 的输入数据。
    """
    domains = workbench.list_domains()
    chosen = current_domain or (domains[0] if domains else "")
    broadcasts = workbench.fetch_broadcasts(chosen) if chosen else ()
    return WidgetSnapshot(
        domains=domains,
        current_domain=chosen,
        broadcasts=broadcasts,
        task_count=0,
        pursuit_count=0,
        unhealthy=False,
    )


def make_snapshot_with_mainloop(
    workbench: WorkbenchObserver,
    *,
    current_domain: str | None = None,
    crystallized_skills: tuple[str, ...] = (),
    last_fast_brain_skill: str = "",
    last_drive_text: str = "",
    last_error: str = "",
    last_intent: str = "",
) -> WidgetSnapshot:
    """批 5:TUI 启动时构造 snapshot(包含 MainLoop 状态字段)。

    批 8.5-A:加 `last_error` / `last_intent` 字段供 L2Board 独立渲染。
    """
    domains = workbench.list_domains()
    chosen = current_domain or (domains[0] if domains else "")
    broadcasts = workbench.fetch_broadcasts(chosen) if chosen else ()
    return WidgetSnapshot(
        domains=domains,
        current_domain=chosen,
        broadcasts=broadcasts,
        task_count=0,
        pursuit_count=0,
        unhealthy=False,
        crystallized_skills=crystallized_skills,
        last_fast_brain_skill=last_fast_brain_skill,
        last_drive_text=last_drive_text,
        last_error=last_error,
        last_intent=last_intent,
    )


def snapshot_with_atoms(
    workbench: WorkbenchObserver,
    *,
    current_domain: str | None = None,
    task_tracker: TaskTracker | None = None,
    board_agg: BoardAggregator | None = None,
    overseer: Overseer | None = None,
    data_courier: DataCourier | None = None,
) -> WidgetSnapshot:
    """拍 3b 起的扩展版:原子 agent 注入 → 丰富字段。"""
    base = snapshot_for_widgets(workbench, current_domain=current_domain)
    return WidgetSnapshot(
        domains=base.domains,
        current_domain=base.current_domain,
        broadcasts=base.broadcasts,
        task_count=len(task_tracker.tracked_tasks()) if task_tracker else 0,
        pursuit_count=board_agg.aggregate(base.current_domain)["broadcast_count"] if board_agg else 0,
        unhealthy=(not overseer.is_healthy()) if overseer else False,
    )