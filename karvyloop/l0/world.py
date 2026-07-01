"""L0World — 跨域广播大群(l0/world.py)。

设计:docs/23 §3.1-3.2 + §4 8 不变量(L0-L1 ~ L0-L8)。
"""
from __future__ import annotations

import dataclasses
import logging
import threading
from typing import Callable, Optional

from karvyloop.a2a import (
    AuditChain,
    BroadcastPayload,
    Envelope,
    sign_envelope,
)
from karvyloop.a2a.transport import Transport
from karvyloop.domain import Address
from karvyloop.karvy.observer import WorkbenchObserver

logger = logging.getLogger(__name__)

# L0 特殊 domain_id — 标识"跨域广播"接收方(docs/23 §3.3)
L0_DOMAIN_ID: str = "l0"


@dataclasses.dataclass(frozen=True)
class L0Channel:
    """一个 L0 频道(docs/23 §3.1)。"""
    name: str                       # "strategy" / "alert" / ...
    description: str
    requires_acknowledgment: bool   # alert 必读,celebrate 可选


# 5 个 L0 频道(白名单,L0-L5 强制)
L0_DEFAULT_CHANNELS: tuple[L0Channel, ...] = (
    L0Channel(name="strategy", description="战略层:新财年战略、并购、产品方向", requires_acknowledgment=False),
    L0Channel(name="alert", description="安全/合规:全平台告警、零日漏洞", requires_acknowledgment=True),
    L0Channel(name="celebrate", description="庆祝:季度目标达成、发布", requires_acknowledgment=False),
    L0Channel(name="ask-for-help", description="求援:跨域资源请求", requires_acknowledgment=False),
    L0Channel(name="general", description="兜底频道", requires_acknowledgment=False),
)


class UnknownL0ChannelError(ValueError):
    """L0 频道未注册(L0-L5)。"""


class KarvyL0SendForbiddenError(RuntimeError):
    """小卡不能发 L0 广播(L0-L8:小卡只收不发,K1)。"""


class L0World:
    """L0 大群(单实例,K0 灵魂级)。

    职责:跨域广播 + 频道注册 + L0 小卡集合观察。
    """

    _singleton: Optional["L0World"] = None
    _singleton_lock = threading.Lock()

    def __new__(cls, *args, **kwargs) -> "L0World":
        """K0 单实例(双重检查锁)。"""
        if cls._singleton is None:
            with cls._singleton_lock:
                if cls._singleton is None:
                    cls._singleton = super().__new__(cls)
        return cls._singleton

    def __init__(
        self,
        transport: Transport,
        channels: tuple[L0Channel, ...] = L0_DEFAULT_CHANNELS,
        audit_chain: Optional[AuditChain] = None,
        auth_policy: Optional["ChannelAuthPolicy"] = None,
        ack_tracker: Optional["AckTracker"] = None,
        broadcast_fallback: Optional["BroadcastFallback"] = None,
    ) -> None:
        # 二次构造时**不**重复初始化(单例语义)
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._transport = transport
        self._channels: dict[str, L0Channel] = {c.name: c for c in channels}
        self._audit = audit_chain or AuditChain()
        self._subscribers: dict[str, list[Callable[[Envelope], None]]] = {
            c.name: [] for c in channels
        }
        # 批 1.5 3 件套(全**注**入**式**,**不**传**走**默**认**/**走**空**实**现**)
        from .channel_auth import ChannelAuthPolicy
        from .ack_tracker import AckTracker
        from .fallback import BroadcastFallback
        self._auth = auth_policy or ChannelAuthPolicy()
        self._ack_tracker = ack_tracker or AckTracker()
        self._fallback = broadcast_fallback or BroadcastFallback(transport)

    @classmethod
    def reset_for_test(cls) -> None:
        """测试用:重置单例。"""
        with cls._singleton_lock:
            cls._singleton = None

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(self._channels.keys())

    def subscribe(self, channel: str, on_message: Callable[[Envelope], None]) -> None:
        """订阅 L0 频道(每个频道独立 subscriber 列表)。"""
        if channel not in self._channels:
            raise UnknownL0ChannelError(
                f"频道 '{channel}' 未注册;已注册: {list(self._channels)}"
            )
        self._subscribers[channel].append(on_message)

    def broadcast(
        self,
        channel: str,
        message: str,
        from_: Address,
        *,
        tag: str = "",
    ) -> Envelope:
        """跨域广播:发到对应 L0 channel(L0-L1, L0-L2 强制)。

        批 1.5 加 3 道**关**:
          1. ChannelAuthPolicy.can_send (F1-F3)
          2. AckTracker.track (F4: alert 类必**读**)
          3. BroadcastFallback.publish (F6: 主**失**败**回**退**)

        Args:
            channel: L0 频道名(必须在 self._channels)。
            message: 广播文本。
            from_: 发件人 Address(必**须**非 observer,L0-L8)。
            tag: BroadcastPayload 的 tag 字段(默认 = channel 名)。

        Returns:
            构造并发送的 Envelope。

        Raises:
            UnknownL0ChannelError: 频道未注册。
            KarvyL0SendForbiddenError: from_.role == "observer"。
            ChannelAuthError: 频道发送权限不足。
        """
        # 0. 拍 3: L0-L5 频道白名单
        if channel not in self._channels:
            raise UnknownL0ChannelError(
                f"频道 '{channel}' 未注册;已注册: {list(self._channels)}"
            )
        # 1. 拍 3: L0-L8 小卡不能发
        if from_.role == "observer":
            raise KarvyL0SendForbiddenError(
                f"L0-L8: 小卡 (role=observer) 只能收 L0 广播,不能发;from_={from_}"
            )
        # 2. 批 1.5: F1-F3 频道权限(role-based 白名单)
        if not self._auth.can_send(channel, from_):
            from .channel_auth import ChannelAuthError
            perm = self._auth.permissions().get(channel)
            allowed = perm.allowed_sender_roles if perm else ()
            raise ChannelAuthError(
                f"F1-F3: role='{from_.role}' 不**在**频**道** '{channel}' **白**名**单** {allowed}"
            )

        # 构造 envelope: type=BROADCAST, to=Address(l0, observer, karvy)
        l0_to = Address(domain_id=L0_DOMAIN_ID, role="observer", agent_id="karvy")
        payload = BroadcastPayload(message=message, tag=tag or channel)

        env = Envelope(
            type="broadcast",
            from_=from_,
            by=(),
            to=l0_to,
            payload=payload,
            ts=_now_ts(),
            signature=b"",
        )
        signed = Envelope(
            type=env.type,
            from_=env.from_,
            by=env.by,
            to=env.to,
            payload=env.payload,
            ts=env.ts,
            signature=sign_envelope(env),
        )

        # 3. 拍 3: L0-L7 写审计
        self._audit.append(signed)

        # 4. 批 1.5: F4 ack 跟踪(仅 requires_acknowledgment 频**道**才**跟**踪**)
        ch_def = self._channels[channel]
        if ch_def.requires_acknowledgment:
            self._ack_tracker.track(signed, required=True)

        # 5. 批 1.5: F6 降级回退(主 transport 失**败** **自**动**回**退**)
        self._fallback.publish(signed)

        # 6. 拍 3: fan-out 给**同**进**程** **订**阅**者**(InProcess transport **走** callback,Redis **走** listener)
        for cb in list(self._subscribers.get(channel, [])):
            try:
                cb(signed)
            except Exception as e:
                logger.warning(f"L0 subscriber raised {e}, skipping (channel={channel})")

        return signed

    def audit_entries(self) -> tuple:
        """测试用:拿所有 L0 审计条目。"""
        return self._audit.all()

    @property
    def auth_policy(self) -> "ChannelAuthPolicy":
        return self._auth

    @property
    def ack_tracker(self) -> "AckTracker":
        return self._ack_tracker

    @property
    def broadcast_fallback(self) -> "BroadcastFallback":
        return self._fallback


def _now_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# 测试用:默认 observer 聚合器(单 WorkbenchObserver)
L0_DEFAULT_OBSERVER_AGGREGATOR = None
