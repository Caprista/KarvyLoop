"""lifecycle — 业务域生命周期的辅助操作(归档/恢复)。

**核心不变量**:
- D6 archived 业务域不接新请求(只读)

设计:docs/18 §3.2。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from .registry import (
    ArchivedDomainError,
    BusinessDomain,
    BusinessDomainRegistry,
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_ACTIVE,
)

logger = logging.getLogger(__name__)


def assert_active(domain: BusinessDomain) -> None:
    """断言业务域 active(D6)。"""
    if domain.lifecycle != LIFECYCLE_ACTIVE:
        raise ArchivedDomainError(
            f"D6: domain {domain.id} is archived, no new operations allowed"
        )


def restore(registry: BusinessDomainRegistry, domain_id: str) -> BusinessDomain:
    """从 archived 恢复为 active(留口,M3+ 用)。"""
    d = registry.get(domain_id)
    if d is None:
        raise ValueError(f"domain {domain_id} not found")
    if d.lifecycle == LIFECYCLE_ACTIVE:
        return d  # 已经是 active
    new = dataclasses.replace(d, lifecycle=LIFECYCLE_ACTIVE)
    # 注入式修改
    object.__setattr__(registry, "_domains", {**registry._domains, domain_id: new})
    return new
