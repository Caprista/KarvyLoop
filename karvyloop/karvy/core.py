"""core — 小卡本体(单实例,observer 角色)。

**核心不变量**(doc §4):
- K1 小卡永远 observer(不接受业务角色)
- K6 小卡单实例(全局唯一)

设计:docs/20 §3.3。
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import uuid
from typing import Optional

from karvyloop.a2a import KARVY_AGENT_ID
from karvyloop.domain import Address

logger = logging.getLogger(__name__)


# 小卡角色名(灵魂级)
KARVY_ROLE: str = "observer"

# 小卡 agent_id
KARVY: str = KARVY_AGENT_ID


class KarvyRoleError(RuntimeError):
    """K1: 小卡永远 observer,不允许其他角色。"""


class KarvyAlreadyInitializedError(RuntimeError):
    """K6: 小卡单实例,重复创建抛。"""


class KarvyCore:
    """小卡本体(单实例,K6)。

    职责:对外只暴露一个身份(Karvy = observer)。
    内部:N 个原子 agent(详见 atoms.py)。

    双锁单例:threading.Lock + 双重检查。
    """

    _instance: Optional["KarvyCore"] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls, *args, **kwargs) -> "KarvyCore":
        if cls._instance is not None:
            return cls._instance
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        role: str = KARVY_ROLE,
        agent_id: str = KARVY,
        timestamp_fn=None,
    ) -> None:
        # 第二次构造(单例)是 noop,但仍校验(防止有人绕开单例)
        if hasattr(self, "_initialized") and self._initialized:
            # 校验 role/agent_id 不变(K1 灵魂级)
            if self._role != role or self._agent_id != agent_id:
                raise KarvyRoleError(
                    f"K1: KarvyCore is singleton, role/agent_id fixed "
                    f"(want={role}/{agent_id}, got={self._role}/{self._agent_id})"
                )
            return

        if role != KARVY_ROLE:
            raise KarvyRoleError(
                f"K1: KarvyCore role must be '{KARVY_ROLE}'(observer), got {role!r}"
            )
        if agent_id != KARVY_AGENT_ID:
            raise KarvyRoleError(
                f"K1: KarvyCore agent_id must be '{KARVY_AGENT_ID}', got {agent_id!r}"
            )
        self._role = role
        self._agent_id = agent_id
        self._initialized = True
        if timestamp_fn is not None:
            self._timestamp_fn = timestamp_fn
        else:
            from datetime import datetime, timezone
            self._timestamp_fn = lambda: datetime.now(timezone.utc).isoformat()

    @property
    def role(self) -> str:
        return self._role

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def is_observer(self) -> bool:
        """K1 灵魂级:永远 observer。"""
        return self._role == KARVY_ROLE

    def address(self, domain_id: str) -> Address:
        """小卡在某业务域的地址(永远是 observer 角色)。"""
        return Address(
            domain_id=domain_id,
            role=KARVY_ROLE,
            agent_id=self._agent_id,
        )

    @classmethod
    def reset_for_test(cls) -> None:
        """测试用:重置单例(锁外)。"""
        with cls._lock:
            cls._instance = None
