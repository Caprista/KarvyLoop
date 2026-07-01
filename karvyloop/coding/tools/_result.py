"""工具输出结果类型（coding/tools/_result.py）。

独立模块以避免 tools/__init__ ↔ 子模块的循环 import。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CodingResult:
    """工具输出:(ok, payload) 结构(改进常见的 'error:' 前缀魔法字符串)。"""

    ok: bool
    payload: Any
    truncated: bool = False
    error_code: int = 0
    error_message: str = ""


__all__ = ["CodingResult"]
