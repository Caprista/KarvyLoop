"""context — 上下文/Token 治理：多层压缩 + DYNAMIC_BOUNDARY + 断路器

规格（函数级实现架构 + 签名级接口 + 验收标准）：docs/modules/context-governance.md
里程碑：M0 贯穿。状态：实现 + 通过 self-acceptance。
"""

from __future__ import annotations

from .autocompact import SUMMARY_ROLE, autocompact
from .boundary import (
    CACHE_TYPE,
    SENTINEL,
    build_system_for_request,
    find_sentinel_index,
    is_sentinel,
    split_static_dynamic,
)
from .budget import (
    AUTOCOMPACT_BUFFER_TOKENS,
    MANUAL_COMPACT_BUFFER_TOKENS,
    MAX_CONSECUTIVE_FAILURES,
    MICROCOMPACT_BUFFER_TOKENS,
    BlockingLimitError,
    GovConfig,
    GovState,
    LLM_MATERIAL_TOKENS,
    autocompact_threshold,
    clip_to_tokens,
    count_tokens_messages,
    count_tokens_text,
    microcompact_threshold,
)
from .microcompact import COMPACTABLE, PLACEHOLDER, microcompact
from .pipeline import govern, prepare_system_prompt
from .truncate import truncate_str_utf8, truncate_utf8

__all__ = [
    # budget
    "MAX_CONSECUTIVE_FAILURES", "AUTOCOMPACT_BUFFER_TOKENS",
    "MICROCOMPACT_BUFFER_TOKENS", "MANUAL_COMPACT_BUFFER_TOKENS",
    "GovState", "GovConfig", "BlockingLimitError",
    "count_tokens_text", "count_tokens_messages", "clip_to_tokens", "LLM_MATERIAL_TOKENS",
    "autocompact_threshold", "microcompact_threshold",
    # boundary
    "SENTINEL", "CACHE_TYPE",
    "split_static_dynamic", "build_system_for_request",
    "find_sentinel_index", "is_sentinel",
    # microcompact
    "COMPACTABLE", "PLACEHOLDER", "microcompact",
    # autocompact
    "SUMMARY_ROLE", "autocompact",
    # pipeline
    "govern", "prepare_system_prompt",
    # truncate
    "truncate_utf8", "truncate_str_utf8",
]
