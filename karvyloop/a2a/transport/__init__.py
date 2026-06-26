"""A2A 跨进程传输(transport/__init__.py)。

M3.0 批 1 拍 2:把 Inbox 从同进程 dict 升级到可插拔 transport。
Tier 1 行为 = InProcessTransport(默认);Tier 2 = RedisTransport(可选)。

设计:docs/22 §3.1-3.2 + §4 8 不变量。
"""
from __future__ import annotations

from typing import Callable, Optional, Protocol, runtime_checkable

from ..envelope import Envelope


@runtime_checkable
class Transport(Protocol):
    """跨进程传输抽象(只 publish + subscribe 两个动作,无全局状态)。"""
    name: str

    def publish(self, env: Envelope) -> None:
        """发送一个 envelope。同步(redis-py sync client)。"""
        ...

    def subscribe(self, on_message: Callable[[Envelope], None]) -> None:
        """订阅并把消息交给 on_message。同步路径 = 注册 callback(**不**自动启 thread)。"""
        ...


def create_transport(
    *,
    backend: str = "in-process",
    redis_url: Optional[str] = None,
    client_id: str = "karvy-default",
) -> Transport:
    """工厂:根据 backend 创建 transport。

    Args:
        backend: "in-process"(默认) / "redis"。
        redis_url: redis 连接串(backend="redis" 时**必**须,否则降级到 in-process + 警告)。
        client_id: 客户端标识(多实例区分;redis **不**用来分发,只用来 log)。

    Returns:
        Transport 实例。

    5 问硬规则 T7:无 redis 时降级到 in-process,**不**抛。
    """
    if backend == "in-process":
        from .bus_inprocess import InProcessTransport
        return InProcessTransport(client_id=client_id)
    elif backend == "redis":
        if not redis_url:
            # T7: 降级 + 警告(不抛,让系统能跑)
            import logging
            logging.getLogger(__name__).warning(
                "backend='redis' 但 redis_url=None,降级到 in-process"
            )
            from .bus_inprocess import InProcessTransport
            return InProcessTransport(client_id=client_id)
        try:
            from .bus_redis import RedisTransport
            return RedisTransport(redis_url=redis_url, client_id=client_id)
        except ImportError:
            import logging
            logging.getLogger(__name__).warning(
                "redis-py 未安装,降级到 in-process"
            )
            from .bus_inprocess import InProcessTransport
            return InProcessTransport(client_id=client_id)
    else:
        raise ValueError(f"未知 backend: {backend}(仅 in-process / redis)")


__all__ = ["Transport", "create_transport", "InProcessTransport", "RedisTransport"]


def __getattr__(name: str):
    """Lazy import:避免 transport 顶层强制 import redis。"""
    if name == "InProcessTransport":
        from .bus_inprocess import InProcessTransport
        return InProcessTransport
    if name == "RedisTransport":
        from .bus_redis import RedisTransport
        return RedisTransport
    raise AttributeError(f"module 'karvyloop.a2a.transport' has no attribute {name!r}")
