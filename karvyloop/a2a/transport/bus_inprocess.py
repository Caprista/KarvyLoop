"""InProcessTransport(transport/bus_inprocess.py)。

M3 v0 行为:同进程 publish + subscribe(等价 Tier 1 Inbox 行为,**向**后**兼**容**)。
不依赖任何外部包。

设计:docs/22 §3.2。
"""
from __future__ import annotations

import logging
from typing import Callable

from ..envelope import Envelope

logger = logging.getLogger(__name__)


class InProcessTransport:
    """同进程 transport(默认,等价 Tier 1)。"""

    name = "in-process"

    def __init__(self, client_id: str = "karvy-default") -> None:
        self.client_id = client_id
        # subscriber callbacks
        self._callbacks: list[Callable[[Envelope], None]] = []

    def publish(self, env: Envelope) -> None:
        """同进程 fan-out:每个 subscriber callback 各拿一份。"""
        for cb in list(self._callbacks):
            try:
                cb(env)
            except Exception as e:
                # 8 不变量 T7 兜底:一个 subscriber 失败**不**影响其他人
                logger.warning(f"subscriber raised {e}, skipping (env.type={env.type})")

    def subscribe(self, on_message: Callable[[Envelope], None]) -> None:
        """注册 callback。**不**自动启 thread(纯同步,调用方负责调度)。"""
        self._callbacks.append(on_message)

    def clear(self) -> None:
        """测试用:清空 subscribers。"""
        self._callbacks.clear()
