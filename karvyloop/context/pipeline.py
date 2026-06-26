"""治理管线（context/pipeline.py）。

规格：docs/modules/context-governance.md §3 pipeline.py + §4 约束。
每轮调模型前由 atom-executor 调用。MVP 只两层:
  1) microcompact(超 MICROCOMPACT 缓冲) → 旧工具结果占位
  2) autocompact(超 AUTOCOMPACT 缓冲) → 摘要中段(带 HR-3 断路器)
关自动压缩 + 超限 → BlockingLimitError
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from .autocompact import autocompact
from .boundary import build_system_for_request, split_static_dynamic
from .budget import (
    BlockingLimitError,
    GovConfig,
    GovState,
    autocompact_threshold,
    count_tokens_messages,
    count_tokens_text,
    microcompact_threshold,
)
from .microcompact import microcompact


# 系统提示组装(给 forge 等 caller 用,避免每处重复写)
def prepare_system_prompt(
    sections: list[str],
) -> tuple[list[dict], int]:
    """DYNAMIC_BOUNDARY 切分 + cache_control。

    返回 (system_blocks, static_token_est)。
    - static/dynamic 都进 system_blocks,静态最后一块带 cache_control
    - static_token_est 给 caller 判断 cache 命中率
    """
    static, dynamic = split_static_dynamic(sections)
    blocks = build_system_for_request(static, dynamic)
    static_text = "".join(static)
    static_tokens = max(1, len(static_text) // 4)
    return blocks, static_tokens


def _tool_result_tokens(messages: list[dict]) -> int:
    """累计所有工具结果体的粗略 token 数(Anthropic tool_result block + legacy role:tool)。
    用于 microcompact 的成本触发口(累积工具输出多大 = 每轮重发烧多少)。"""
    total = 0
    for m in messages:
        c = m.get("content")
        if m.get("role") == "tool":  # legacy 形态
            if isinstance(c, str):
                total += count_tokens_text(c)
        elif isinstance(c, list):    # Anthropic 形态
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    total += count_tokens_text(str(b.get("content", "")))
    return total


async def govern(
    messages: list[dict],
    cfg: GovConfig,
    state: GovState,
    summarize: Optional[Callable[[list[dict]], Awaitable[str]]] = None,
    *,
    context_window: int = 200_000,
) -> list[dict]:
    """每轮调模型前跑。MVP:先 microcompact,再 autocompact。

    参数:
      messages:      当前消息
      cfg, state:    治理配置 + 状态
      summarize:     摘要函数(middle 段 → 摘要文本);autocompact 启用时必传
      context_window:模型上下文窗口(触发阈值用)
    """
    used = count_tokens_messages(messages)
    state.last_governed_at = 0.0  # 由调用方 stamp;这里只示意

    # 1) microcompact(轻;不调模型)。两个触发口:
    #    a) 接近窗口(window-1.5k)—— 防溢出(原行为)
    #    b) 累积工具结果体超 tool_result_budget —— **成本控制**(loop step4a 关键:
    #       长 coding 任务每轮重发全部 tool 输出 = O(n²) 烧钱;不必等接近窗口才裁,
    #       早裁旧工具结果体即可把"用得起"做实。tool_result_budget 这个旋钮本就是为此设的)。
    if used > microcompact_threshold(context_window) or \
            _tool_result_tokens(messages) > cfg.tool_result_budget:
        messages = microcompact(messages, keep_recent=cfg.keep_recent_tool_results)
        used = count_tokens_messages(messages)

    # 2) autocompact(重;可能调模型;HR-3 断路器)
    threshold = autocompact_threshold(context_window)
    if used > threshold and cfg.autocompact_enabled:
        if summarize is None:
            # caller 没给摘要函数 → 跳过(等价于微调无效)
            return messages
        messages = await autocompact(
            messages, state, cfg, summarize,
            context_window=context_window,
        )

    # 3) 关自动压缩 + 超限 → BlockingLimitError(AC7)
    if not cfg.autocompact_enabled and used > context_window - 3_000:
        raise BlockingLimitError(
            f"auto-compact 已关且上下文超限 ({used} > {context_window - 3_000});"
            f"请手动 compact 或启用 autocompact",
            code=7,
        )

    return messages


__all__ = ["govern", "prepare_system_prompt"]
