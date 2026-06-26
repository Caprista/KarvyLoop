"""L0 karvy world 大群(l0/__init__.py)。

M3.0 批 1 拍 3:跨域广播协作场(L0 = 最大协作半径,docs/20 §3)。
**复**用** Tier 2 transport + A2A BROADCAST,**不**重**新**发**明**。

批 1.5 加 3 件套:channel_auth + ack_tracker + fallback(docs/24)。

设计:docs/23 §3.1-3.3 + §4 8 不变量 + docs/24 §3-4。
"""
from .world import (
    L0World,
    L0Channel,
    L0_DEFAULT_CHANNELS,
    L0_DOMAIN_ID,
    UnknownL0ChannelError,
    KarvyL0SendForbiddenError,
    L0_DEFAULT_OBSERVER_AGGREGATOR,
)
from .observer_aggregator import L0ObserverAggregator
from .channel_auth import (
    ChannelAuthPolicy,
    ChannelPermission,
    ChannelAuthError,
    DEFAULT_PERMISSIONS,
)
from .ack_tracker import AckTracker, AckState
from .fallback import BroadcastFallback, FallbackLog

__all__ = [
    # 拍 3
    "L0World",
    "L0Channel",
    "L0_DEFAULT_CHANNELS",
    "L0_DOMAIN_ID",
    "UnknownL0ChannelError",
    "KarvyL0SendForbiddenError",
    "L0ObserverAggregator",
    "L0_DEFAULT_OBSERVER_AGGREGATOR",
    # 批 1.5
    "ChannelAuthPolicy",
    "ChannelPermission",
    "ChannelAuthError",
    "DEFAULT_PERMISSIONS",
    "AckTracker",
    "AckState",
    "BroadcastFallback",
    "FallbackLog",
]
