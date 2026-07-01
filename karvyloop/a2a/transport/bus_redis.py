"""RedisTransport(transport/bus_redis.py)。

redis pub/sub 跨进程 transport(可选,**依**赖** redis-py **包**)。
T7:redis 不**可**用**时降级(在 `__init__.py` create_transport 里处理,这**里**不**重**复**)。

设计:docs/22 §3.3。
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from ..envelope import Envelope
from .envelope_codec import decode, encode

logger = logging.getLogger(__name__)

# Channel 模板(docs/22 §3.3)
CHANNEL_DOMAIN: str = "karvy:a2a:domain:{domain_id}"
CHANNEL_BROADCAST: str = "karvy:a2a:broadcast:all"


def channel_for(env: Envelope) -> str:
    """根据 envelope 选 channel:
      - BROADCAST → 全局
      - 其他 → 目标 domain
    """
    if env.type == "broadcast":
        return CHANNEL_BROADCAST
    return CHANNEL_DOMAIN.format(domain_id=env.to.domain_id)


def channels_to_subscribe(domain_id: str) -> tuple[str, ...]:
    """订阅:目标 domain + 全局 broadcast。"""
    return (CHANNEL_DOMAIN.format(domain_id=domain_id), CHANNEL_BROADCAST)


class RedisTransport:
    """redis pub/sub 跨进程 transport(同步 publish,**背**景** thread **订**阅**)。

    **不**在** __init__ **起** thread(**让**测试可**控**制**);**调** `start()` **才**起**。
    """

    name = "redis"

    def __init__(self, redis_url: str, client_id: str = "karvy-default") -> None:
        # **延**迟 import:redis-py 是可选依赖
        try:
            import redis  # type: ignore
        except ImportError as e:
            raise ImportError(
                "redis-py 未安装。pip install redis 或降级到 in-process"
            ) from e

        self._redis_mod = redis
        self._url = redis_url
        self.client_id = client_id
        # 同步 client(PUBLISH)
        self._client = redis.Redis.from_url(redis_url, decode_responses=False)
        # pubsub client(SUBSCRIBE),懒构造
        self._pubsub = None
        self._listener_thread: Optional[threading.Thread] = None
        self._callbacks: list[Callable[[Envelope], None]] = []
        self._stop_flag = threading.Event()

    # ---------- publish(同步)----------

    def publish(self, env: Envelope) -> None:
        """PUBLISH 到对应 channel(同步,redis-py 是同步 client)。"""
        ch = channel_for(env)
        raw = encode(env)
        self._client.publish(ch, raw)

    # ---------- subscribe(后台 thread)----------

    def subscribe(self, on_message: Callable[[Envelope], None]) -> None:
        """注册 callback。**不**启 thread — 调 `start()` 才启。"""
        self._callbacks.append(on_message)

    def start(self, domain_id: str) -> None:
        """起**后**台**订**阅** thread(**订**阅**目**标** domain + 全**局** broadcast**)。

        调**用**方**负**责**调** `stop()`(**关**线**程**)。"""
        if self._listener_thread is not None and self._listener_thread.is_alive():
            return  # 已起
        # 懒构造 pubsub
        self._pubsub = self._client.pubsub()
        for ch in channels_to_subscribe(domain_id):
            self._pubsub.subscribe(ch)
        self._stop_flag.clear()
        self._listener_thread = threading.Thread(
            target=self._listen_loop,
            name=f"karvy-redis-listener-{self.client_id}",
            daemon=True,
        )
        self._listener_thread.start()

    def stop(self) -> None:
        """停后台 thread(测试 / 进程退出用)。"""
        self._stop_flag.set()
        if self._pubsub is not None:
            try:
                self._pubsub.close()
            except Exception:
                pass
        if self._listener_thread is not None:
            self._listener_thread.join(timeout=2.0)

    def _listen_loop(self) -> None:
        """后台 thread:从 pubsub 收消息 → fan-out 给 callbacks。"""
        assert self._pubsub is not None
        try:
            for message in self._pubsub.listen():
                if self._stop_flag.is_set():
                    break
                if message is None:
                    continue
                if message.get("type") != "message":
                    continue
                try:
                    env = decode(message["data"])
                except Exception as e:
                    logger.warning(f"decode envelope failed: {e}, skipping")
                    continue
                for cb in list(self._callbacks):
                    try:
                        cb(env)
                    except Exception as e:
                        logger.warning(f"subscriber raised {e}, skipping")
        except Exception as e:
            if not self._stop_flag.is_set():
                logger.error(f"redis listener crashed: {e}")
