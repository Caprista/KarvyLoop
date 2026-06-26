"""Microcompact：按 tool_use_id 删旧工具结果（context/microcompact.py）。

规格：docs/modules/context-governance.md §3 microcompact.py。
- 保留最近 N 个工具结果;更早的同类 → 占位
- 不动消息结构(只清 content),tool_use_id 配对不破
- 占位文本明确说"已截断",给模型看
"""

from __future__ import annotations

from typing import Any

# CC microCompact.ts:313 内的可压缩工具集合
COMPACTABLE = frozenset({
    "Read", "Bash", "Grep", "Glob", "WebSearch", "WebFetch", "Edit", "Write",
})

# 占位文本(短,1 行,告诉模型"这里曾经有过输出但被压了")
PLACEHOLDER = "[microcompact: prior output trimmed to save context window; " \
              "re-run tool if you need the data again]"


def _is_tool_result(msg: dict) -> bool:
    return msg.get("role") == "tool"


def _tool_name(msg: dict) -> str:
    return msg.get("name", "")


def _set_content_placeholder(msg: dict) -> None:
    """把消息 content 设为占位(只动 content 字段,不动 role/tool_use_id)。"""
    msg["content"] = PLACEHOLDER
    # 标记(便于调试)
    msg.setdefault("_meta", {})["microcompacted"] = True


def _collect_compactable(messages: list[dict]) -> list:
    """按出现顺序收集"可压缩工具结果"的清除器(closure),支持两种消息形态:

    1) **Anthropic 真实形态**(executor 实际产出,`_serialize_results_for_model`):
       `{"role":"user","content":[{"type":"tool_result","tool_use_id":..,"content":<str>}]}`
       —— 逐 tool_result block 收(无 name 字段 → 不按 COMPACTABLE 过滤,KarvyLoop 四件套
       输出全部可裁;只改 block 的 content,tool_use_id 留着 → 配对不破)。
    2) **legacy `role:"tool"` 形态**(规格/老测试):按 name ∈ COMPACTABLE 过滤(保持兼容)。

    病根复盘:原实现只认 ②,而 executor 只产 ①,导致 microcompact 接进 loop 后**永远空转**
    (独立 checker 抓到的 CRITICAL)。
    """
    setters: list = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "user" and isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    setters.append(_mk_block_clear(blk))
        elif role == "tool" and _tool_name(m) in COMPACTABLE:
            setters.append(_mk_msg_clear(m))
    return setters


def _mk_block_clear(blk: dict):
    def _clear() -> None:
        if blk.get("content") != PLACEHOLDER:
            blk["content"] = PLACEHOLDER
            blk["_microcompacted"] = True
    return _clear


def _mk_msg_clear(msg: dict):
    def _clear() -> None:
        if msg.get("content") != PLACEHOLDER:
            _set_content_placeholder(msg)
    return _clear


def microcompact(messages: list[dict], *, keep_recent: int = 5) -> list[dict]:
    """保留最近 keep_recent 个工具结果,更早的 content 替成占位(tool_use_id/配对不破)。

    规则:
      1) 按出现顺序收集所有工具结果(Anthropic tool_result block + legacy role=tool)
      2) 若总数 ≤ keep_recent → 全部保留
      3) 否则除最后 keep_recent 个外,其余 content 替成 PLACEHOLDER(只清 content,不删块)
      4) 不动其他消息 / 不动 message 顺序 / tool_use_id 留存 → API 配对不破
    """
    if keep_recent < 0:
        raise ValueError(f"keep_recent 必须 >= 0 (got {keep_recent})")
    if not messages:
        return messages
    setters = _collect_compactable(messages)
    if len(setters) <= keep_recent:
        return messages
    to_clear = setters if keep_recent == 0 else setters[:-keep_recent]
    for clear in to_clear:
        clear()
    return messages


__all__ = ["COMPACTABLE", "PLACEHOLDER", "microcompact"]
