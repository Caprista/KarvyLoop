"""L0ObserverAggregator — L0 小卡集合(l0/observer_aggregator.py)。

设计:docs/23 §3.4 K0 灵魂级。
"""
from __future__ import annotations

import logging
from typing import Iterable

from karvyloop.a2a import Envelope
from karvyloop.karvy.observer import WorkbenchObserver

logger = logging.getLogger(__name__)


class L0ObserverAggregator:
    """L0 小卡集合(每**个**小**卡** 都**会**收**到** L0 **广**播**,**内**部**存**到**各**自**的** WorkbenchObserver**)。

    K0:多进程场景下多个小卡都观察同一个 L0 广播。
    """

    def __init__(self, observers: Iterable[WorkbenchObserver]) -> None:
        self._observers = list(observers)

    @property
    def observer_count(self) -> int:
        return len(self._observers)

    def add_observer(self, karvy: WorkbenchObserver) -> None:
        self._observers.append(karvy)

    def on_l0_broadcast(self, env: Envelope) -> None:
        """L0 广播接入:每个小卡都收到(走 WorkbenchObserver.subscribe_to,K3 强制只收 BROADCAST)。"""
        for ob in self._observers:
            try:
                ob.subscribe_to(env)
            except Exception as e:
                logger.warning(f"WorkbenchObserver.subscribe_to raised {e}, skipping")
