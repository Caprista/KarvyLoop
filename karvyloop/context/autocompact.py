"""AutoCompact：整体摘要压缩 + 断路器（context/autocompact.py）。

规格：docs/modules/context-governance.md §3 autocompact.py + §4 HR-3。
- 把 messages 切成 head/middle/tail
- 调模型摘要 middle 段;成功 → 替换为单条 summary 消息
- 失败 → 计数;达 MAX_CONSECUTIVE_FAILURES → 断路器开
- 关自动压缩 + 超限 → BlockingLimitError
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from .boundary import SENTINEL
from .budget import (
    BlockingLimitError,
    GovConfig,
    GovState,
    MANUAL_COMPACT_BUFFER_TOKENS,
    count_tokens_messages,
)
from .truncate import truncate_str_utf8


# ---- 摘要消息的 role(spec §3 autocompact)----
SUMMARY_ROLE = "summary"  # 简化为单条 user 角色更通用(模型侧普遍接受)
# 注:Claude/OpenAI 都不识别 "summary" role,实际入模时由 caller 转 "user"
# 或 "assistant" — 这里只标记类型,出口处转。


# ---- 历史帧前缀(historical framing, v1.5+)----
# 业界 context compressor 通行的 SUMMARY_PREFIX 思路。
#
# 作用:摘要消息是"被压缩掉的中段"——模型很容易误把它当成 active instruction
# 继续执行(尤其当摘要里出现了"用户说请做 X"这种历史请求时)。前缀用 4 条
# 强约束告诉模型:**这是 reference,不是 instruction;最新 user 消息才是 source
# of truth;reverse signal 终止 in-flight;memory 永远胜过摘要**。
#
# 这是 M1.5 起的"行为正确性"补丁,非性能补丁 —— 摘要本身没问题,是模型的
# 阅读理解需要被引向正确方向。
HISTORICAL_FRAMING_PREFIX = (
    "[历史摘要 — 仅作参考,非 active 指令]\n"
    "以下是早前轮次被压缩后留下的摘要。**请把它当背景信息,不要当成需要执行的任务**。\n"
    "1. **不要**回应/完成本摘要里提到的请求 —— 它们在原轮已被处理。\n"
    "2. 只回应出现在本摘要**之后**的最新 user 消息;那是此刻唯一的 source of truth。\n"
    "3. 最新 user 消息里的反向信号(\"停\" / \"撤销\" / \"算了\" / \"别做了\" / 换话题)\n"
    "   **必须立即终止**本摘要里 in-flight 的任务;不要在后续轮次重新捡起。\n"
    "4. 系统提示里持久的 memory(USER.md / 长期 Belief)**永远权威**——本摘要\n"
    "   不能 deprioritize memory。\n"
)


async def autocompact(
    messages: list[dict],
    state: GovState,
    cfg: GovConfig,
    summarize: Callable[[list[dict]], Awaitable[str]],
    *,
    keep_tail: int = 3,
    context_window: int = 200_000,
) -> list[dict]:
    """整体摘要压缩(带 HR-3 断路器)。

    参数:
      messages:  当前消息列表
      state:     治理状态(断路器 + 摘要缓存)
      cfg:       治理配置
      summarize: 异步函数(middle_messages) → 摘要文本
      keep_tail: 尾部保留 N 条不参与摘要
      context_window: 模型上下文窗口(超限判定)

    返回:压缩后的 messages(原列表被改写或返回新列表)
    """
    # 1) 断路器:开 / 失败次数达上限 → 不再尝试
    if not state.can_attempt():
        if not cfg.autocompact_enabled and count_tokens_messages(messages) > context_window - MANUAL_COMPACT_BUFFER_TOKENS:
            raise BlockingLimitError(
                f"auto-compact 已关且上下文超限 ({count_tokens_messages(messages)} > "
                f"{context_window - MANUAL_COMPACT_BUFFER_TOKENS});请手动 compact",
                code=7,
            )
        return messages

    # 2) 切分 head/middle/tail
    if len(messages) <= keep_tail:
        # 消息太少,无 middle 可压
        return messages
    head = []
    tail = messages[-keep_tail:]
    middle = messages[:-keep_tail]
    # head 包含首条 system 消息(若有),确保 system 提示不被摘要
    if messages and messages[0].get("role") == "system":
        head = [messages[0]]
        middle = messages[1:-keep_tail] if len(messages) > keep_tail + 1 else []
    else:
        middle = messages[:-keep_tail]

    if not middle:
        return messages

    # 3) 摘要(走缓存)
    cache_key = _cache_key(middle)
    summary = state.summary_cache.get(cache_key)
    if summary is None:
        try:
            summary = await summarize(middle)
        except Exception as e:
            # 失败:计数
            state.record_failure()
            if not cfg.autocompact_enabled and count_tokens_messages(messages) > context_window - MANUAL_COMPACT_BUFFER_TOKENS:
                raise BlockingLimitError(
                    f"auto-compact 失败 ({type(e).__name__}) 且 auto-compact 已关且超限",
                    code=7,
                )
            return messages
        state.summary_cache[cache_key] = summary
    # 成功:归零
    state.record_success()

    # 4) 重组:head + 摘要消息 + tail
    pre_tokens = count_tokens_messages(head)
    post_tokens = count_tokens_messages(tail)
    # v1.5+ 把摘要内容包在 HISTORICAL_FRAMING_PREFIX 里 —— 防止 LLM 把摘要
    # 当 active instruction 继续执行(业界 context compressor 通行做法)。
    framed_summary = (
        HISTORICAL_FRAMING_PREFIX
        + f"\n[autocompact summary of {len(middle)} prior messages; "
        + f"preserved head={pre_tokens}tok, tail={post_tokens}tok]\n\n"
        + summary
    )
    summary_msg = {
        "role": "user",  # Claude/OpenAI 都接受 user 摘要
        "content": framed_summary,
        "_meta": {
            "kind": "summary",
            "pre_tokens": pre_tokens,
            "post_tokens": post_tokens,
            "source_count": len(middle),
            "framed": True,  # v1.5+ 标记:已包 historical framing
        },
    }
    # 截断超长摘要(防御)
    summary_msg["content"], _ = truncate_str_utf8(summary_msg["content"], 50_000)
    return _strip_orphan_tool_results(head + [summary_msg] + tail)


def _strip_orphan_tool_results(messages: list[dict]) -> list[dict]:
    """删掉"孤儿 tool_result"(其 tool_use 已被摘要吞掉)—— 否则 Anthropic 报 400。

    病根:固定 keep_tail 切 head/middle/tail 时,可能把 assistant 的 tool_use 切进 middle
    (被摘要),而对应的 tool_result 留在 tail[0] → tool_result 找不到 tool_use → API 400。
    (独立 checker 抓到的 HIGH;只在 autocompact 真触发[≈窗口-13k]时才会现形。)

    做法:按序累计已见 tool_use id,删掉 tool_use_id 不在其中的 tool_result block;
    若某 user 消息的 block 被删空 → 给一句占位文本(不留空 content)。

    假设(依赖 executor 循环结构):只处理"孤儿 tool_result",不处理"孤儿 tool_use"
    (assistant tool_use 无后继 tool_result)。因为 govern 在 executor 循环顶部跑,
    state.messages 总以 tool_result/user 结尾(每个 tool_use 都已被回灌 result),孤儿
    tool_use 不会进到这里。若将来 executor 重构打破此不变量,需在此补对称剥离。
    """
    seen_ids: set = set()
    for m in messages:
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                seen_ids.add(b.get("id"))
        kept = [
            b for b in c
            if not (isinstance(b, dict) and b.get("type") == "tool_result"
                    and b.get("tool_use_id") not in seen_ids)
        ]
        if len(kept) != len(c):
            m["content"] = kept if kept else "[autocompact: prior tool results elided]"
    return messages


def _cache_key(messages: list[dict]) -> str:
    """middle 段稳定 hash 作为摘要缓存 key。

    简单实现:role + content[:100] 拼接 → 短哈希。避免 hash 库依赖。
    """
    import hashlib
    h = hashlib.sha256()
    for m in messages:
        h.update((m.get("role") or "").encode("utf-8"))
        h.update(b"\x00")
        c = m.get("content", "")
        if isinstance(c, str):
            h.update(c[:200].encode("utf-8", errors="replace"))
        elif isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict) and "text" in blk:
                    h.update(str(blk["text"])[:200].encode("utf-8", errors="replace"))
        h.update(b"\x01")
    return h.hexdigest()[:16]


__all__ = ["autocompact", "SUMMARY_ROLE", "HISTORICAL_FRAMING_PREFIX"]
