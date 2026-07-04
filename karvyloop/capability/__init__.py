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

from .fs_grants import (  # noqa: E402
    FsGrantsStore, is_sensitive_path, path_allowed, register_store as register_fs_grants,
    get_store as get_fs_grants,
)

# deontic 确定性硬闸(docs/54 B1):域 forbid 的工具/命令级真拦(authorize step 6.5)
from .deontic_gate import (  # noqa: E402
    DeonticScope, DeonticHit, build_scope, classify_forbid,
    deontic_scope, scope_from_system, check_active as check_deontic,
)
