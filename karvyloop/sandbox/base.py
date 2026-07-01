"""Sandbox 抽象（sandbox/base.py）—— PAL 接缝。

规格：docs/modules/sandbox.md §3 / #1 §6.2。
核心层只依赖此模块的 Protocol,禁止 import 具体平台实现。
"""

from __future__ import annotations

from typing import Protocol

from karvyloop.schemas import CapabilityToken

from .exec_result import ExecResult


class Sandbox(Protocol):
    """Sandbox 接口。实现必须：

    - 默认全隔离（fail-closed），按 token 显式放开（HR-1 同精神）
    - 资源上限：超时强杀 + 输出截断（HR-3）
    - 写文件 / 读文件由 token.fs 控制，不接受 ambient path
    """

    async def exec(self, argv: list[str], *, token: CapabilityToken,
                   cwd: str, stdin: bytes = b"", timeout_s: float = 120.0,
                   max_output_bytes: int = 30_000) -> ExecResult: ...

    async def write_file(self, path: str, content: bytes, token: CapabilityToken) -> None: ...

    async def read_file(self, path: str, token: CapabilityToken) -> bytes: ...
