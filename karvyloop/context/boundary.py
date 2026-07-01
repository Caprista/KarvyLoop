"""DYNAMIC_BOUNDARY 切分 + cache_control（context/boundary.py）。

规格：docs/modules/context-governance.md §3 boundary.py + §4 HR-9。
- 哨兵 `__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__` 分隔静态 / 动态
- 静态前缀末块打 `cache_control: ephemeral`（HR-9）
- 哨兵发送前被过滤(不进入模型视野)
"""

from __future__ import annotations

from typing import Optional

# 与 KarvyLoop 其它哨兵保持一致风格(参照 coding/prompt.py:⟦KARVYLOOP_BOUNDARY⟧)
SENTINEL = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"

# 与 coding/prompt.py 的 BOUNDARY_MARKER 不同:这里用 `__...__` 命名
# 是因为 DYNAMIC_BOUNDARY 业界通行就是这种 dunder 形式,clean-room
# 保留视觉一致性(业界 coding agent 通行做法)。
CACHE_TYPE = "ephemeral"


def split_static_dynamic(sections: list[str]) -> tuple[list[str], list[str]]:
    """把 sections 列表按哨兵切成 (静态, 动态)。

    哨兵本身不出现在结果中(被过滤)。
    哨兵不存在 → 整段当静态(spec:哨兵必现,但留 fallback)。
    """
    if SENTINEL in sections:
        i = sections.index(SENTINEL)
        return sections[:i], sections[i + 1:]
    # fallback:整段当静态
    return list(sections), []


def find_sentinel_index(sections: list[str]) -> Optional[int]:
    """找哨兵位置(测试/调试用)。"""
    return sections.index(SENTINEL) if SENTINEL in sections else None


def build_system_for_request(
    static: list[str],
    dynamic: Optional[list[str]] = None,
) -> list[dict]:
    """把静态/动态文本段组装成 system blocks。

    - 每段一个 text block
    - 静态最后一块加 cache_control: ephemeral(HR-9)
    - 动态跟在静态后(不缓存)
    - 哨兵已在上一步被过滤,不进 blocks
    """
    blocks: list[dict] = []
    for s in static:
        if not s:
            continue
        blocks.append({"type": "text", "text": s})
    if blocks:
        blocks[-1]["cache_control"] = {"type": CACHE_TYPE}
    for d in (dynamic or []):
        if not d:
            continue
        blocks.append({"type": "text", "text": d})
    return blocks


def is_sentinel(text: str) -> bool:
    """判断文本是否哨兵(测试用)。"""
    return text.strip() == SENTINEL


__all__ = [
    "SENTINEL",
    "CACHE_TYPE",
    "split_static_dynamic",
    "find_sentinel_index",
    "build_system_for_request",
    "is_sentinel",
]
