"""router — A2A envelope router(Tier 1 + Tier 2 跨进程)。

**核心不变量**(doc §4):
- A6 小卡是 observer 时只收 BROADCAST
- A7 TASK_ASSIGN 不能跨域
- 全部依赖注入

设计:docs/19 §3.5 + docs/22 §3.1(Tier 2 跨进程分流)。
"""
from __future__ import annotations

import dataclasses
import logging
import uuid
from typing import Callable, Optional

from karvyloop.domain import Address

from .address import is_cross_domain, is_karvy_observer
from .audit import AuditChain
from .envelope import (
    Envelope,
    EnvelopeType,
    KARVY_AGENT_ID,
    sign_envelope,
    verify_envelope,
)
from .inbox import Inbox
from .transport import Transport

logger = logging.getLogger(__name__)


# 用字符串占位(避免循环 import 风险)
BROADCAST_TYPE: str = EnvelopeType.BROADCAST.value
TASK_ASSIGN_TYPE: str = EnvelopeType.TASK_ASSIGN.value


# 路由结果
@dataclasses.dataclass(frozen=True)
class RouteResult:
    """路由结果。"""
    rejected: bool
    reason: str = ""
    target: Optional[Address] = None
    entry_id: str = ""  # 审计链 entry_id(成功时)


# 拒绝原因(标准化)
REJECT_BAD_SIGNATURE: str = "bad_signature"
REJECT_BOUNDARY_VIOLATION: str = "boundary_violation"
REJECT_NO_TARGET: str = "no_target"
REJECT_OBSERVER_FILTER: str = "observer_filter"  # A6
REJECT_CROSS_DOMAIN: str = "cross_domain_forbidden"  # A7
REJECT_DOMAIN_ARCHIVED: str = "domain_archived"


# 寻址解析器(注入式)
AddressResolver = Callable[[Address], Optional[Address]]


def _round_robin_resolver(to: Address, counter: dict[str, int]) -> Optional[Address]:
    """最简寻址解析:轮询(测试用默认,M3+ 升级负载均衡)。

    M3 v0 简化:to 已经是目标地址时直接返回(同域内)。
    """
    return to


# 域生命周期查询(注入式)
DomainLifecycleQuery = Callable[[str], Optional[str]]  # domain_id → "active" / "archived"

# is_local 查询(注入式,Tier 2 用,T5)
IsLocalQuery = Callable[[Address], bool]


class EnvelopeRouter:
    """Envelope router(Tier 1 in-process + Tier 2 跨进程,docs/19 + docs/22)。

    5 步:
      1. 签验
      2. 三边界检查(A1-A8)
      3. 寻址解析(domain + role → 目标 agent)
      4. 投递 — is_local?→ Inbox(Tier 1 快路径) : Transport.publish(Tier 2 跨进程)
      5. 写审计链

    **向**后**兼**容**:不传 transport = 走 InProcessTransport(等价 Tier 1,M3 v0 行为)。
    """

    def __init__(
        self,
        inbox: Inbox,
        audit_chain: AuditChain,
        domain_lifecycle: Optional[DomainLifecycleQuery] = None,
        address_resolver: Optional[AddressResolver] = None,
        sign_secret: bytes = b"",
        transport: Optional[Transport] = None,
        is_local: Optional[IsLocalQuery] = None,
    ) -> None:
        self._inbox = inbox
        self._audit = audit_chain
        self._domain_lifecycle = domain_lifecycle
        self._address_resolver = address_resolver or (lambda to: to)
        self._sign_secret = sign_secret
        # Tier 2:跨进程 transport(**不**传 = 退化 in-process,等价 M3 v0)
        from .transport.bus_inprocess import InProcessTransport
        self._transport: Transport = transport or InProcessTransport()
        # is_local 查询:默认全 True(向**后**兼**容** M3 v0)
        self._is_local: IsLocalQuery = is_local or (lambda to: True)
        # 轮询计数器(按 role)
        self._rr_counter: dict[str, int] = {}

    def route(self, env: Envelope) -> RouteResult:
        """投递 envelope(5 步流水线)。"""
        # 1. 签验
        if not verify_envelope(env, self._sign_secret):
            return RouteResult(rejected=True, reason=REJECT_BAD_SIGNATURE)

        # 2. 三边界检查
        # A1: from: karvy 已在 Envelope.__post_init__ 抛(构造时)
        # A2: by 含 from_ 已在 Envelope.__post_init__ 抛(构造时)
        # A6: 小卡 observer 只收 BROADCAST
        if is_karvy_observer(env.to):
            if env.type != BROADCAST_TYPE:
                return RouteResult(rejected=True, reason=REJECT_OBSERVER_FILTER)
        # A7: TASK_ASSIGN 不能跨域
        if env.type == TASK_ASSIGN_TYPE:
            if is_cross_domain(env.from_.domain_id, env.to.domain_id):
                return RouteResult(rejected=True, reason=REJECT_CROSS_DOMAIN)

        # 域生命周期检查(注入式)
        if self._domain_lifecycle is not None:
            target_lc = self._domain_lifecycle(env.to.domain_id)
            if target_lc == "archived":
                return RouteResult(rejected=True, reason=REJECT_DOMAIN_ARCHIVED)

        # 3. 寻址解析
        target = self._address_resolver(env.to)
        if target is None:
            return RouteResult(rejected=True, reason=REJECT_NO_TARGET)

        # 4. 投递(Tier 1 快路径 vs Tier 2 跨进程)
        if self._is_local(target):
            self._inbox.deliver(target, env)
        else:
            self._transport.publish(env)

        # 5. 写审计链
        entry = self._audit.append(env)
        return RouteResult(rejected=False, target=target, entry_id=entry.entry_id)
