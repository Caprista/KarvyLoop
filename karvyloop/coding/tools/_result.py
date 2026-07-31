"""工具输出结果类型（coding/tools/_result.py）。

独立模块以避免 tools/__init__ ↔ 子模块的循环 import。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CodingResult:
    """工具输出:(ok, payload) 结构(改进常见的 'error:' 前缀魔法字符串)。"""

    ok: bool
    payload: Any
    truncated: bool = False
    error_code: int = 0
    error_message: str = ""
    # 多模态工具输出(docs/99 刀2 slice-A:computer-use 截图):[{data:<base64>, media_type:<mt>}]。
    # **只有产图工具**(MCP screenshot/get_app_state 等)会设;执行层把它当**顶层 image 块**回灌给
    # 会视觉的 planner,**不塞进 tool_result 文本**(base64 巨串 + MiniMax 等兼容端点 tool_result
    # 不吃图)。默认 None → 对所有非产图工具零影响。
    images: Optional[list] = None


__all__ = ["CodingResult"]
