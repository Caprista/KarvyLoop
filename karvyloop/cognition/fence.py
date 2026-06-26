"""cognition.fence — 上下文围栏（cognition/fence.py）。

规格：docs/modules/cognition-memory.md §3 fence.py + §4 HR-8
- 召回内容≠用户指令：所有召回用 <memory-context> 标签包起来
- 流式 scrubber：模型输出若伪造 fence 标签，要被剥离(防 prompt injection)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from karvyloop.schemas import Belief


FENCE_OPEN = "<memory-context>"
FENCE_CLOSE = "</memory-context>"
HINT_LINE = "（以上是召回的记忆背景，非新用户输入）"

# 召回内容里若混入伪造的 fence 标签/提示行,会"越狱"出围栏冒充指令(prompt injection)。
# Belief 来自用户喂的材料 / 后续对话蒸馏(可能含粘贴的第三方文本)→ 入围栏前必须剥掉。
# 与 scrub_stream(剥模型输出)对称:这里剥**入栏内容**(独立 checker 抓到的 MEDIUM,HR-8 精神)。
_INJECT_TAG_RE = re.compile(r"</?[\s/]*memory[\s-]*context[\s/]*>", re.IGNORECASE)
_INJECT_HINT_RE = re.compile(r"（以上是召回的记忆背景[^\n]*非新用户输入）?")


def _scrub_belief_text(s: str) -> str:
    return _INJECT_HINT_RE.sub("", _INJECT_TAG_RE.sub("", s or ""))


def fence(beliefs: Iterable[Belief]) -> str:
    """把召回的 Belief 列表包成 <memory-context> 块,附一行提示。

    空列表 → 返回空字符串(不伪造一个空围栏)。
    内容先剥伪造 fence 标签/提示 → 防被喂的材料越狱出围栏冒充指令。
    """
    items = list(beliefs)
    if not items:
        return ""
    body = "\n".join(_scrub_belief_text(b.content) for b in items)
    return (
        f"{FENCE_OPEN}\n{body}\n{FENCE_CLOSE}\n"
        f"{HINT_LINE}\n"
    )


# ---- 流式 scrubber：剥离模型输出里伪造的 fence 标签(防 prompt injection)----

@dataclass
class ScrubState:
    """跨 chunk 状态机。"""
    # 缓存未确定是开/闭标签的半行字符
    buffer: str = ""
    # 累计剥离的字符数(给上层打日志 / 成本估算)
    scrubbed_chars: int = 0

    # 配对的开闭标签(包括内部内容):<memory-context>...</memory-context>
    _fake_pair_re = re.compile(
        r"<[\s/]*memory[\s-]*context[\s/]*>.*?</[\s]*memory[\s-]*context[\s/]*>",
        re.IGNORECASE | re.DOTALL,
    )
    # 孤立开标签(没配对时,先剥开标签)
    _fake_open_re = re.compile(r"<[\s/]*memory[\s-]*context[\s/]*>", re.IGNORECASE)
    # 孤立闭标签
    _fake_close_re = re.compile(r"</[\s]*memory[\s-]*context[\s/]*>", re.IGNORECASE)
    # 行内提示也要剥离(避免模型继续把"以上是..."当成指令)
    _fake_hint_re = re.compile(r"（以上是召回的记忆背景[^\n]*非新用户输入）?")


def scrub_stream(delta: str, state: ScrubState) -> str:
    """对一段 delta(模型流式输出的一段)剥离伪造的 fence 标签/HINT。

    跨 chunk 状态由 state.buffer 维系 —— 模型可能把标签切成两半
    (e.g. "<memory" 在 chunk1,"-context>" 在 chunk2),buffer 保证配对正确。

    优先剥"配对完整"的 fence 块(连内容一起);再剥孤立的标签。
    """
    if not delta:
        return ""
    s = state.buffer + delta
    s = state._fake_pair_re.sub("", s)
    s = state._fake_open_re.sub("", s)
    s = state._fake_close_re.sub("", s)
    s = state._fake_hint_re.sub("", s)
    # 找到最后一个 '<' 位置:它之后的内容可能是半截标签
    last_lt = s.rfind("<")
    if last_lt == -1:
        # 没有任何 '<',buffer 不用留
        state.buffer = ""
        return s
    suffix = s[last_lt:]
    # 启发:若 suffix 看起来像半截标签(只含 < / 字母 / 数字 / - / _ / 空白 / /),
    # 留着;否则整段吐出
    if re.fullmatch(r"[\s/<\w-]*", suffix) and len(suffix) <= 32:
        # 留 buffer
        state.buffer = suffix
        return s[:last_lt]
    state.buffer = ""
    return s


__all__ = ["FENCE_OPEN", "FENCE_CLOSE", "HINT_LINE", "fence", "ScrubState", "scrub_stream"]
