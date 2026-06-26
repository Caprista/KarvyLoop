"""render_events — 把 forge 事件流收成结构化"渲染事件"(给 UI 按类型渲染,M3+ 拍 9.4-显示层)。

forge.generate_and_run 已按事件类型 dispatch 到 emitter(forge.py:108-143)。本 collector 复用
**同一 emitter 契约**(方法名对齐 `NdjsonEmitter`),但不写 NDJSON,而是把每个事件 append 成
`{seq, type, ...}` dict(**顺序保真**),供 console `drive_done` 一次性下发 → 前端按 type 渲染
(text→markdown / tool_call→折叠卡 / tool_result→输出面板 / terminal→status)。

为什么不复用 NdjsonEmitter:它写 NDJSON 字符串到 sink;这里要的是结构化 dict 列表 + delta 合并。
契约一致(duck-type),forge 不用改判断逻辑。
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable, List, Optional

# 输出转字符串上限(UI 再做折叠;这里防超大 dict 撑爆 payload)
_MAX_OUTPUT_CHARS = 8000


def _to_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    try:
        import json
        return json.dumps(output, ensure_ascii=False, default=str)
    except Exception:
        return str(output)


@dataclasses.dataclass
class RenderEventCollector:
    """emitter 形状的事件收集器:forge 调它的方法,它攒成有序 render-event 列表。"""

    events: List[dict] = dataclasses.field(default_factory=list)
    session_id: str = ""
    turn: int = 0
    # P4 逐字流式:每个事件实时回调(由 worker 线程触发;console 用它桥回 loop 推 WS)。
    # 默认 None = 旧批量行为(0 回归);.events 仍攒全量供 drive_done 终态渲染/持久。
    on_event: Optional[Callable[[dict], None]] = None
    _seq: int = 0

    def _live(self, ev: dict) -> None:
        if self.on_event is not None:
            try:
                self.on_event(dict(ev))
            except Exception:
                pass  # 流式推送失败绝不拖垮 drive

    def _add(self, type_: str, **fields) -> None:
        self._seq += 1
        ev = {"seq": self._seq, "type": type_}
        ev.update(fields)
        self.events.append(ev)
        self._live(ev)   # 流式:tool_call/tool_result/terminal 实时推

    # ---- forge 调用的 emitter 契约(方法名对齐 NdjsonEmitter)----

    def run_start(self, *, workspace: str = "", model: str = "", permission_mode: str = "") -> None:
        pass  # 渲染不需要 run_start

    def turn_start(self) -> None:
        self.turn += 1

    def assistant_text_delta(self, text: str) -> None:
        if not text:
            return
        # 连续 text delta 合并进上一条 text 事件(顺序保真 + 减碎片);
        # 中间夹了 tool 事件就另起新 text 块 → 正文/工具交替顺序正确。
        if self.events and self.events[-1]["type"] == "text":
            self.events[-1]["text"] += text          # 批量:合并进上一条(行为不变)
        else:
            self._seq += 1
            self.events.append({"seq": self._seq, "type": "text", "text": text})
        # 流式:每个 delta 单独实时推(不合并)→ 前端逐字追加;批量 .events 仍合并供终态。
        self._live({"type": "text_delta", "text": text})

    def assistant_thinking_delta(self, text: str) -> None:
        """P4:推理增量 —— 独立 thinking 事件(批量合并供折叠渲染),流式单推 thinking_delta。"""
        if not text:
            return
        if self.events and self.events[-1]["type"] == "thinking":
            self.events[-1]["text"] += text
        else:
            self._seq += 1
            self.events.append({"seq": self._seq, "type": "thinking", "text": text})
        self._live({"type": "thinking_delta", "text": text})

    def tool_call(self, *, id: str = "", name: str = "", input: Any = None) -> None:
        self._add("tool_call", id=id, name=name or "tool", input=input or {})

    def tool_result(self, *, tool_use_id: str = "", is_error: bool = False,
                    output: Any = None, truncated: bool = False) -> None:
        out = _to_text(output)
        trunc = bool(truncated)
        if len(out) > _MAX_OUTPUT_CHARS:
            out = out[:_MAX_OUTPUT_CHARS]
            trunc = True
        self._add("tool_result", tool_use_id=tool_use_id, is_error=bool(is_error),
                  output=out, truncated=trunc)

    def assistant_turn(self, *, stop_reason: str = "", usage: Any = None, tool_calls: Any = None) -> None:
        pass  # 汇总信息;渲染不需要(text/tool 事件已逐条捕获)

    def run_end(self, *, ok: bool = True, reason: str = "") -> None:
        self._add("terminal", ok=bool(ok), reason=reason)


__all__ = ["RenderEventCollector"]
