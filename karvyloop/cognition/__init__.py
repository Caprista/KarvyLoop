"""cognition — 认知与记忆：Trace + Belief + 围栏 + 后台蒸馏 + Pursuit 闭环

规格：docs/modules/cognition-memory.md + docs/modules/pursuit.md
里程碑：M1。状态：实现 + 等待 self-acceptance。

模块结构:
  trace       — append-only 事件底座(HR-7 provenance 来源)
  recall      — agentic 召回(无向量库;M1 v1 内存 + 子串匹配)
  fence       — HR-8 围栏 + 流式 scrubber
  provider    — MemoryProvider Protocol + BuiltinProvider
  conflict    — provenance/freshness 冲突消解 + 矛盾标记
  memory      — MemoryManager 单一集成点(单外部 provider 限制)
  distill     — 后台蒸馏(与 crystallize 共用循环,工具白名单)
  pursuit     — PursuitManager:commitment/revision/verify_gate 闭环
  conversation— 对话(会话内记忆 + 快慢脑共享上下文总线;docs/26,拍 9.1a)
"""

from __future__ import annotations

from .conflict import (
    PROVENANCE_RANK,
    ConflictReport,
    detect_conflict,
    provenance_rank,
    resolve,
)
from .distill import (
    ALLOWED_TOOLS,
    ActionKind,
    DistillAction,
    DistillResult,
    apply_action,
    background_review,
    validate_action,
)
from .fence import (
    FENCE_CLOSE,
    FENCE_OPEN,
    HINT_LINE,
    ScrubState,
    fence,
    scrub_stream,
)
from .memory import Context, MemoryManager, MultipleExternalProvidersError
from .provider import BuiltinProvider, MemoryProvider
from .pursuit import (
    GATE_DISPATCH,
    GateError,
    PursuitManager,
    eval_condition,
    eval_verify_gate,
)
from .recall import MemoryIndex, RecallHit, recall
from .trace import TraceEntry, TraceStore, current_run_id, new_run_id, run_scope
from .sqlite_trace import SqliteTraceStore
from .conversation import (
    BRAIN_FAST,
    BRAIN_SLOW,
    KARVY_WORLD_DOMAIN,
    Conversation,
    ConversationManager,
    ConversationMeta,
    ConversationStore,
    Turn,
    karvy_world_peer,
)


__all__ = [
    # trace
    "TraceEntry", "TraceStore", "SqliteTraceStore",
    "run_scope", "new_run_id", "current_run_id",
    # conversation(拍 9.1a/9.1c + 9.2a 归属)
    "Conversation", "Turn", "ConversationStore", "ConversationMeta",
    "ConversationManager", "BRAIN_FAST", "BRAIN_SLOW",
    "KARVY_WORLD_DOMAIN", "karvy_world_peer",
    # recall
    "MemoryIndex", "RecallHit", "recall",
    # fence
    "FENCE_OPEN", "FENCE_CLOSE", "HINT_LINE", "fence", "ScrubState", "scrub_stream",
    # provider
    "MemoryProvider", "BuiltinProvider",
    # conflict
    "PROVENANCE_RANK", "provenance_rank", "ConflictReport", "resolve", "detect_conflict",
    # memory
    "MemoryManager", "Context", "MultipleExternalProvidersError",
    # distill
    "ActionKind", "DistillAction", "DistillResult",
    "ALLOWED_TOOLS", "validate_action", "apply_action", "background_review",
    # pursuit
    "PursuitManager", "GateError", "GATE_DISPATCH",
    "eval_condition", "eval_verify_gate",
]
