"""adapter — M2.0 拍 4 外部 agent adapter。

设计:docs/14-external-agent-adapter.md。

**5 段流水线**:Source → Map → Plan → Apply → Validate
**plan/apply 模式**:借 openclaw migrate-hermes plan.ts/apply.ts 思想
**7 槽位映射**:IDENTITY/SOUL/USER/MEMORY/COMMITMENT/VERIFY/COMPOSITION.yaml
**4 类 source adapter**(本拍 v0):claude / codex / openclaw-hermes / generic-json

**核心不变量**(doc §4):
- J1 缺 system_prompt/tools → 拒收
- J2 7 槽位全部有 Plan
- J3 can_apply=False → 不写盘
- J4 写到注入的 target dir,不写 cwd
- J5 目标存在不覆盖 (SKIP_EXISTS)
- J6 Paradigm Loader 烟测不通过 = validation_errors 不抛不回滚
- J7 全注入,无 LLM 调用(本拍)
"""
from __future__ import annotations

from .source import (
    EXTERNAL_SOURCES,
    ExternalManifest,
    ManifestError,
    SourceAdapter,
    discover_manifest,
    parse_claude_manifest,
    parse_codex_manifest,
    parse_generic_manifest,
    parse_openclaw_hermes_manifest,
)
from .planner import (
    SLOT_NAMES,
    AdapterPlan,
    PlanError,
    SlotAction,
    SlotPlan,
    build_plan,
)
from .applier import ApplyResult, apply_plan
from .validator import validate_with_loader
from .registry import AdapterRegistry, adapter_registry

__all__ = [
    # source
    "EXTERNAL_SOURCES",
    "ExternalManifest",
    "ManifestError",
    "SourceAdapter",
    "discover_manifest",
    "parse_claude_manifest",
    "parse_codex_manifest",
    "parse_generic_manifest",
    "parse_openclaw_hermes_manifest",
    # planner
    "SLOT_NAMES",
    "AdapterPlan",
    "PlanError",
    "SlotAction",
    "SlotPlan",
    "build_plan",
    # applier
    "ApplyResult",
    "apply_plan",
    # validator
    "validate_with_loader",
    # registry
    "AdapterRegistry",
    "adapter_registry",
]
