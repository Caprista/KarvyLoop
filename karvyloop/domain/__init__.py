"""domain — M3 路线 C 拍 1:业务域(长期 H2A+A2A 协作场域)。

设计:docs/18-business-domain.md。

**核心概念**:
- 业务域 = 长期场域(像企业),有 value.md(灵魂)+ deontic(强护栏)
- 子业务域 = 父域对话中涌现(继承 value.md + deontic,只能加不能删)
- 成员 = 动态解析(从 member_query),不是静态列表
- 小卡 = observer(只读,从不以自己名义参与业务)

**核心不变量**(doc §4):
- D1 业务域 = 长期场域(不接受临时群)
- D2 value.md 是本域的灵魂,不能为空
- D3 soul_subset 由 deontic 推(不接受外部传)
- D4 成员 = 动态解析(不是静态列表)
- D5 子域继承父域 value.md + deontic(不能删)
- D6 archived 业务域不接受新请求(只读)
- D7 全部依赖注入(无全局单例)
- D8 不调 LLM
"""
from __future__ import annotations

from .value import (
    ValueMd,
    ValueMdFormatError,
    ValueMdRequiredError,
)
from .deontic import (
    SOUL_FILES,
    Deontic,
    DeonticResult,
    DeonticViolationError,
    apply_deontic,
    deontic_guardrail_text,
    derive_soul_subset,
)
from .registry import (
    ADDR_AGENT,
    ADDR_OBSERVER,
    ADDR_USER,
    Address,
    BusinessDomain,
    BusinessDomainRegistry,
    LIFECYCLE_ACTIVE,
    LIFECYCLE_ARCHIVED,
    MemberClause,
    Routine,
    ArchivedDomainError,
    parse_member_query,
)
from .lifecycle import assert_active, restore
from .protocols import (
    AuditChainLike,
    EnvelopeRouterLike,
    RoutineCallback,
    RoutineRunner,
)

__all__ = [
    # value
    "ValueMd",
    "ValueMdFormatError",
    "ValueMdRequiredError",
    # deontic
    "SOUL_FILES",
    "Deontic",
    "DeonticResult",
    "DeonticViolationError",
    "apply_deontic",
    "deontic_guardrail_text",
    "derive_soul_subset",
    # registry
    "ADDR_AGENT",
    "ADDR_OBSERVER",
    "ADDR_USER",
    "Address",
    "BusinessDomain",
    "BusinessDomainRegistry",
    "LIFECYCLE_ACTIVE",
    "LIFECYCLE_ARCHIVED",
    "MemberClause",
    "Routine",
    "ArchivedDomainError",
    "parse_member_query",
    # lifecycle
    "assert_active",
    "restore",
    # protocols
    "AuditChainLike",
    "EnvelopeRouterLike",
    "RoutineCallback",
    "RoutineRunner",
]
