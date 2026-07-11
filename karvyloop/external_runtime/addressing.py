"""external_runtime/addressing — citizen-aware 寻址解析(#71 §3.3 R2 接线)。

**病根(接基建前核真实数据形态,R2)**:a2a router 第 3 步 `target = resolver(env.to)`,
target is None → REJECT_NO_TARGET。生产态注入的是**域成员弱引用解析器**(member_query 那套),
它只认域内 role/agent,**不认 ExternalCitizenRegistry 里的 citizen** → 派给外部公民的
`to=Address(域, role="external", agent_id=citizen_id)` 解析为 None → 被拒。

**接线方案**:给 router 传一个 citizen-aware resolver 装饰器 —— 先查内层业务解析器,
miss 再回退查 citizen 注册表(复合键 (域, citizen_id))。citizen 存在 → 返回该地址(=桥)。
这样 (域, citizen_id) 复合键真能解析到"桥"(对齐 §2.1 "解析到的是桥不是 role")。
"""
from __future__ import annotations

from typing import Callable, Optional

from karvyloop.domain import Address

from .citizen import EXTERNAL_ROLE, ExternalCitizenRegistry


def make_citizen_aware_resolver(
    inner_resolver: Optional[Callable[[Address], Optional[Address]]],
    citizen_registry: ExternalCitizenRegistry,
) -> Callable[[Address], Optional[Address]]:
    """包住内层业务解析器,让 (域, citizen_id) 复合键可解析到外部公民桥。

    - inner_resolver:域内 role/agent 弱引用解析器(None → 恒等,测试态放行任意地址)。
    - 命中顺序:先内层(域内 role/agent),miss 且 to.role=="external" 且注册表有该 citizen → 放行。
    """
    inner = inner_resolver or (lambda to: to)

    def resolve(to: Address) -> Optional[Address]:
        hit = inner(to)                    # 先走域内 role/agent 弱引用解析
        if hit is not None:
            return hit
        if getattr(to, "role", "") == EXTERNAL_ROLE:
            citizen = citizen_registry.resolve_in(
                getattr(to, "domain_id", "") or "",
                getattr(to, "agent_id", "") or "")
            if citizen is not None:
                return to                  # citizen 存在 → 目标即该外部公民地址(桥)
        return None

    return resolve


def citizen_address(domain_id: str, citizen_id: str) -> Address:
    """构造派给外部公民的目标地址(#71 §3.3)。"""
    return Address(domain_id=domain_id or "", role=EXTERNAL_ROLE, agent_id=citizen_id)


__all__ = ["make_citizen_aware_resolver", "citizen_address"]
