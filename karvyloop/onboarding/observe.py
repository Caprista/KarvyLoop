"""Stage 1 Observe —— 从消息 + 历史观察"用户在做什么"。

**本拍**(I4):关键词启发式(0 LLM)。
**拍 3.5 升级**:注入弱模型,Qwen 1.5B / Llama-3.2-1B 之类。

设计:docs/13 §3.5。
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional, Sequence

from .hints import (
    ALL_FLAGS,
    FIRST_ATOM_COMPOSE,
    FIRST_LONG_TOOL,
    FIRST_PURSUIT,
    FIRST_SKILL_USE,
    NO_ROLE_YET,
)

logger = logging.getLogger(__name__)


# 关键词 → flag 映射(本拍默认 fallback)
KEYWORD_MAP: dict[str, tuple[str, ...]] = {
    NO_ROLE_YET: ("没有角色", "want a role", "no role", "角色", "role"),
    FIRST_SKILL_USE: ("第一次用", "this skill", "我的 skill", "my skill"),
    FIRST_PURSUIT: ("pursuit", "goal", "目标", "Pursuit"),
    FIRST_ATOM_COMPOSE: ("atom", "原子", "wizard", "完成", "wrote role"),
    FIRST_LONG_TOOL: ("慢", "long", "跑了好久", "still running"),
}


def observe_message(
    messages: Sequence[str],
    role_files_present: bool = True,
    skill_used_recently: bool = False,
    pursuit_active: bool = False,
    long_tool_running: bool = False,
) -> Optional[str]:
    """从 message + 上下文观察"该触发哪条 hint"。

    简化版(M2+ 升级 LLM):
      - role_files_present = False → 触发 NO_ROLE_YET
      - skill_used_recently = True → 触发 FIRST_SKILL_USE
      - pursuit_active = True → 触发 FIRST_PURSUIT
      - long_tool_running = True → 触发 FIRST_LONG_TOOL
      - 否则关键词扫描

    返:flag 名(在 ALL_FLAGS 里),或 None(不触发)
    """
    # 显式上下文信号优先
    if not role_files_present:
        return NO_ROLE_YET
    if skill_used_recently:
        return FIRST_SKILL_USE
    if pursuit_active:
        return FIRST_PURSUIT
    if long_tool_running:
        return FIRST_LONG_TOOL

    # 关键词启发式
    for flag, kws in KEYWORD_MAP.items():
        for msg in messages:
            for kw in kws:
                if kw.lower() in msg.lower():
                    logger.debug("observe: matched %r → flag=%s", kw, flag)
                    return flag

    return None


def classify_intent(
    messages: Sequence[str],
    model: Optional[Callable[[Sequence[str]], Optional[str]]] = None,
) -> Optional[str]:
    """注入式分类(拍 3.5 接入弱模型用)。

    model:可调用 (messages) -> flag 字符串 / None
    """
    if model is not None:
        try:
            result = model(messages)
            if result in ALL_FLAGS:
                return result
            return None
        except Exception as e:
            logger.warning("classify_intent: injected model failed: %s, fallback", e)
    # fallback:走关键词观察
    return observe_message(messages)
