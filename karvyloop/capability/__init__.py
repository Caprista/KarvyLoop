"""capability — 能力 broker：决策链/词法路径归一化/影子分类器

规格（函数级实现架构 + 签名级接口 + 验收标准）：docs/modules/capability.md
里程碑：M0。状态：实现 + 通过 self-acceptance。
"""

from __future__ import annotations

from .broker import check, classify, derive_min_capabilities
from .decision import Allow, Ask, Decision, Deny, Passthrough, authorize
from .pathnorm import is_within_workspace
from .policy import (
    DEFAULT_TOOL_REQUIREMENTS,
    Mode,
    PermissionContext,
    Prompter,
    Rule,
    Verdict,
    required_mode,
)
from .token import has_grant, is_expired, mint, verify

__all__ = [
    # broker
    "check", "classify", "derive_min_capabilities",
    # decision
    "authorize", "Decision", "Allow", "Ask", "Deny", "Passthrough",
    # pathnorm
    "is_within_workspace",
    # policy
    "Mode", "Verdict", "Rule", "PermissionContext", "Prompter",
    "DEFAULT_TOOL_REQUIREMENTS", "required_mode",
    # token
    "mint", "verify", "is_expired", "has_grant",
]
