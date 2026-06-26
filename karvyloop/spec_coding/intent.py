"""Stage 1: Intent Extractor —— 从对话 context 提取"用户想做 X"。

**本拍**用启发式 + 关键词(LLM 留给 M2+ 升级)。

设计:docs/12 §3.1 5 段流水线 Stage 1。
"""

from __future__ import annotations

import dataclasses
import re
from typing import Optional, Sequence


# 中文 + 英文 intent 触发词(覆盖常见"我想做 X"句式)
INTENT_TRIGGERS: tuple[str, ...] = (
    "我想做", "我想要", "我想写", "帮我做", "帮我写",
    "做个", "写个", "做一下", "写一下",
    "I want to", "let's", "help me", "can you",
)

# 否定词(用户**不**想做 → intent 为空)
NEGATION_MARKERS: tuple[str, ...] = (
    "不想", "不要", "别", "算了", "no", "don't",
)


@dataclasses.dataclass(frozen=True)
class Intent:
    """提取出的用户 intent。

    字段:
      raw:        原始用户消息(完整保留)
      goal:       提炼出的"用户想做 X"短句
      confidence: 启发式置信度 0.0..1.0
    """
    raw: str
    goal: str
    confidence: float


def extract_intent(
    messages: Sequence[str],
) -> Optional[Intent]:
    """从对话消息序列里提取用户 intent。

    算法(启发式,M2+ 升级 LLM):
      1. 取最近的 user 消息
      2. 检 INTENT_TRIGGERS 触发词
      3. 检 NEGATION_MARKERS → 否定
      4. 提取触发词之后的"我想做的内容"作为 goal
      5. confidence = 触发词权重(中 0.8 / 英 0.6)
    """
    if not messages:
        return None
    # 取最近一条 user 消息(简化:不区分 user/assistant,只看触发词)
    last = messages[-1]
    if not last:
        return None
    last_l = last.lower()
    # 否定 → 跳过
    for neg in NEGATION_MARKERS:
        if neg in last_l:
            return None
    # 找触发词
    for trigger in INTENT_TRIGGERS:
        if trigger in last_l:
            goal = _extract_goal(last, trigger)
            if goal:
                conf = 0.8 if any(ord(c) > 127 for c in trigger) else 0.6
                return Intent(raw=last, goal=goal, confidence=conf)
    return None


def _extract_goal(message: str, trigger: str) -> str:
    """从用户消息里提取触发词之后的"想做内容"。"""
    idx = message.lower().find(trigger.lower())
    if idx < 0:
        return ""
    after = message[idx + len(trigger):].strip()
    # 截到第一个句号 / 问号 / 换行
    for stop in ("。", "?", "?", "!", "!", "\n"):
        if stop in after:
            after = after.split(stop)[0]
    return after.strip(" .,;:;:")


# 反向:intent 反向校验(给 AC1 测用)
def has_intent_marker(message: str) -> bool:
    """检消息是否含 intent 触发词(不含否定)。"""
    if not message:
        return False
    msg_l = message.lower()
    if any(n in msg_l for n in NEGATION_MARKERS):
        return False
    return any(t in msg_l for t in INTENT_TRIGGERS)
