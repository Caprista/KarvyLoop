"""BroadcastFallback — L0 降级回退(l0/fallback.py)。

F6 不变量:主 transport 失败不抛给调用方(自动回退到次级 transport)。

设计:docs/24 §3.3。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from karvyloop.a2a import Envelope
from karvyloop.a2a.transport import InProcessTransport, Transport

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class FallbackLog:
    """一次降级记录(审计用)。"""
    channel: str
    primary_transport: str
    fallback_transport: str
    error: str
    timestamp: str


class BroadcastFallback:
    """降级回退(主 transport 失败 → 回退到次级 transport)。

    v0: 1 次失败直接回退(不重试;多路重试留 M3+ 1.6)。
    """

    def __init__(
        self,
        primary: Transport,
        fallback: Optional[Transport] = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback or InProcessTransport()
        self._logs: list[FallbackLog] = []

    def publish(self, env: Envelope) -> tuple[Transport, Optional[Exception]]:
        """发送 env(主 → 失败 → 回退)。

        Returns:
            (实际走的 transport, 原始错误)。
            - 主成功: (primary, None)
            - 主失败回退: (fallback, exception)
        """
        try:
            self._primary.publish(env)
            return self._primary, None
        except Exception as e:
            # F6: 不抛给调用方
            self._fallback.publish(env)
            self._logs.append(FallbackLog(
                channel=env.payload.tag if hasattr(env.payload, "tag") else "unknown",
                primary_transport=self._primary.name,
                fallback_transport=self._fallback.name,
                error=str(e),
                timestamp=env.ts,
            ))
            logger.warning(
                f"L0 broadcast fallback: {self._primary.name} -> {self._fallback.name} "
                f"(channel={env.payload.tag if hasattr(env.payload, 'tag') else 'unknown'}): {e}"
            )
            return self._fallback, e

    def logs(self) -> tuple[FallbackLog, ...]:
        return tuple(self._logs)

    def log_count(self) -> int:
        return len(self._logs)

    @property
    def primary(self) -> Transport:
        return self._primary

    @property
    def fallback(self) -> Transport:
        return self._fallback
