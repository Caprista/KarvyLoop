"""observer — 小卡的工作台观察者层(K2 灵魂级)。

**核心不变量**(doc §4):
- K3 小卡只收 BROADCAST(不收其他类型)
- K4 工作台只读(不能直接修改业务域状态)
- K7 原子 agent 不参与 A2A(它们是小卡的内部)

设计:docs/20 §3.6 + M3 批 3 plans/snoopy-singing-sunbeam.md。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import AsyncIterator, Callable, Optional

from karvyloop.a2a import (
    BROADCAST_TYPE,
    Envelope,
    REJECT_OBSERVER_FILTER,
    RouteResult,
)
from karvyloop.domain import Address

from .core import KARVY, KARVY_ROLE, KarvyCore

logger = logging.getLogger(__name__)


# 工作台数据源
@dataclasses.dataclass(frozen=True)
class BoardSnapshot:
    """工作台一帧(只读,K4)。"""
    domain_id: str
    karvy_role: str
    broadcasts: tuple[Envelope, ...]  # 已过滤:小卡收到的 BROADCAST
    unread_count: int


class WorkbenchObserver:
    """小卡的工作台观察者层(K2 灵魂级)。

    职责:
      - 订阅 L1 A2A 的 BROADCAST(K3 边界)
      - 渲染工作台视图(只读,K4)
      - 不发 A2A 信号(用户没决策,不发)
      - 不调用 H2A(用户没决策,不发)
    """

    def __init__(
        self,
        karvy: Optional[KarvyCore] = None,
        envelope_router_filter: Optional[Callable[[Envelope], RouteResult]] = None,
    ) -> None:
        """
        envelope_router_filter: 注入式过滤函数(M3+ 接 docs/19 EnvelopeRouter.route)。
        M3 v0:用一个简单 lambda 替代。
        """
        self._karvy = karvy or KarvyCore()
        self._filter = envelope_router_filter or self._default_filter
        # 工作台缓存:domain_id → BROADCAST 列表
        self._boards: dict[str, list[Envelope]] = {}

    @staticmethod
    def _default_filter(env: Envelope) -> RouteResult:
        """默认过滤:小卡 observer 只收 BROADCAST(K3)。"""
        if env.to.is_observer() and env.to.agent_id == KARVY:
            if env.type == BROADCAST_TYPE:
                return RouteResult(rejected=False, target=env.to)
            return RouteResult(rejected=True, reason=REJECT_OBSERVER_FILTER)
        return RouteResult(rejected=False, target=env.to)

    # ---- 订阅 ----
    def subscribe_to(self, envelope: Envelope) -> RouteResult:
        """订阅一条 envelope(K3 边界检查)。"""
        result = self._filter(envelope)
        if not result.rejected:
            # 只缓存 BROADCAST 给工作台
            if envelope.type == BROADCAST_TYPE and envelope.to.is_observer():
                domain_id = envelope.to.domain_id
                self._boards.setdefault(domain_id, []).append(envelope)
        return result

    # ---- 工作台视图(K4 只读)----
    def snapshot(self, domain_id: str) -> BoardSnapshot:
        """取工作台一帧视图(只读,K4)。"""
        items = tuple(self._boards.get(domain_id, []))
        return BoardSnapshot(
            domain_id=domain_id,
            karvy_role=self._karvy.role,
            broadcasts=items,
            unread_count=len(items),
        )

    def list_domains(self) -> tuple[str, ...]:
        """列出小卡已订阅的域。"""
        return tuple(self._boards.keys())

    # ---- 原子 agent 的只读接口(K7 边界:原子 agent 不参与 A2A)----
    def fetch_broadcasts(self, domain_id: str) -> tuple[Envelope, ...]:
        """TaskTracker / BoardAggregator 原子 agent 用:取 BROADCAST 列表(只读)。"""
        return tuple(self._boards.get(domain_id, []))

    # ---- M3 批 3 UI 增量:异步事件流(K3 强过滤必须继承)----
    async def subscribe_async(self) -> AsyncIterator[Envelope]:
        """异步事件流(M3 批 3 workbench UI 主循环驱动)。

        实现:`subscribe_to()` 的副作用是写 `self._boards`;
        这里 **不** 重写 K3 过滤,而是把"已通过的 BROADCAST"打包成 `AsyncIterator`。
        边界契约:K3 永不变 — `subscribe_async` 必须通过 `subscribe_to` 才能 emit。

        Yields:
            通过 K3 过滤、被 `subscribe_to` 接受的 BROADCAST。
        """
        # 简化 v0:把当前缓存的所有 envelope 一次性 emit(后续 M3+ 1.6 接入 asyncio.Queue 真事件流)
        for envs in self._boards.values():
            for env in envs:
                yield env


# 模块级 default_filter 引用(供 K3 协议测试 + 注入)
default_filter = WorkbenchObserver._default_filter
