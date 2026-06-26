"""NDJSON 事件输出（coding/ndjson.py）。

规格：docs/modules/forge.md §2.5。
每行一 JSON,带 schema + format_version;可被 CLI / 上层消费。
事件种类:
  run_start / turn_start / assistant_text_delta / assistant_turn
  / tool_result / run_end
工具输出 >32KB → 截断 + truncated:true(UTF-8 char 边界,避免破坏编码)。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional


SCHEMA = "karvyloop-forge-ndjson"
FORMAT_VERSION = 1


def _now() -> float:
    return time.time()


def _truncate_utf8_bytes(text: str, limit_bytes: int) -> tuple[str, bool]:
    """UTF-8 字节边界截断(HR-9 同源)。

    切到 limit_bytes 字节以下,且落在 char 边界(不破多字节序列)。
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return text, False
    cut = limit_bytes
    # 回退到上一个 ASCII / 完整多字节边界
    # UTF-8 起始字节:0xxxxxxx(ASCII) / 110xxxxx(2字节) / 1110xxxx(3字节) / 11110xxx(4字节)
    # 续字节:10xxxxxx
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    return encoded[:cut].decode("utf-8", errors="replace"), True


# 保留旧名,默认字节
_truncate_utf8 = _truncate_utf8_bytes


@dataclass
class NdjsonEmitter:
    """事件发射器。sink 可注入(sys.stdout / file / io.Writer)。"""

    sink: Any  # 接受 .write(str) 即可
    session_id: str = ""
    turn: int = 0
    _lock: bool = field(default=False, init=False, repr=False)

    def emit(self, kind: str, **fields) -> None:
        ev = {
            "schema": SCHEMA,
            "v": FORMAT_VERSION,
            "ts": _now(),
            "kind": kind,
            "session_id": self.session_id,
            "turn": self.turn,
        }
        ev.update(fields)
        # 工具输出 32KB 截断(字节级,UTF-8 边界;HR-9 同源)
        # 关键:直接对 ev["output"] 截断(改 ev),再 json.dumps。
        # 留 ~512B 余量给 header / escaped 字符。
        if "output" in ev and isinstance(ev["output"], str) and not ev.get("truncated"):
            if len(ev["output"].encode("utf-8")) > 31 * 1024:
                ev["output"], _ = _truncate_utf8_bytes(ev["output"], 31 * 1024)
                ev["truncated"] = True
        line = json.dumps(ev, ensure_ascii=False, default=str)
        # 兜底:header 自身意外超 32K(几乎不可能,防御性)
        if len(line.encode("utf-8")) > 32 * 1024:
            line, _ = _truncate_utf8_bytes(line, 32 * 1024)
        self.sink.write(line + "\n")

    # 便捷包装
    def run_start(self, *, workspace: str, model: str, permission_mode: str) -> None:
        self.emit("run_start", workspace=workspace, model=model, permission_mode=permission_mode)

    def turn_start(self) -> None:
        self.turn += 1
        self.emit("turn_start")

    def tool_call(self, *, id: str = "", name: str = "", input: Any = None) -> None:
        """工具调用详情(9.4:NDJSON 也带 tool call 名/输入,与渲染层一致)。"""
        self.emit("tool_call", tool_use_id=id, name=name, input=input or {})

    def assistant_text_delta(self, text: str) -> None:
        if text:
            self.emit("assistant_text_delta", text=text)

    def assistant_thinking_delta(self, text: str) -> None:
        if text:
            self.emit("assistant_thinking_delta", text=text)   # P4:推理增量(NDJSON 一致)

    def assistant_turn(self, *, stop_reason: str, usage: dict, tool_calls: list) -> None:
        self.emit("assistant_turn", stop_reason=stop_reason, usage=usage,
                  tool_calls=tool_calls)

    def tool_result(self, *, tool_use_id: str, is_error: bool, output: Any,
                    truncated: bool = False) -> None:
        # 截断（字节级 UTF-8；HR-9 同源）
        # 阈值用 31K（留 1K 给 header + JSON 转义），避免 emit() 二次截断破 JSON
        truncated_flag = truncated
        output_repr = output
        if not is_error and isinstance(output, str) and len(output.encode("utf-8")) > 31 * 1024:
            output_repr, truncated_flag = _truncate_utf8_bytes(output, 31 * 1024)
        self.emit("tool_result", tool_use_id=tool_use_id, is_error=is_error,
                  output=output_repr, truncated=truncated_flag)

    def run_end(self, *, ok: bool, reason: str = "") -> None:
        self.emit("run_end", ok=ok, reason=reason)


__all__ = ["NdjsonEmitter", "SCHEMA", "FORMAT_VERSION", "_truncate_utf8"]
