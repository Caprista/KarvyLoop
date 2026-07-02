"""非 Linux 平台兑底（platform/_stub.py）。

规格：docs/modules/sandbox.md §PAL。
职责：明确报错,告诉用户"沙箱在该平台未实现"，防止静默"无隔离"执行。
"""

from __future__ import annotations

import os
import sys

from karvyloop.schemas import CapabilityToken
from karvyloop.sandbox.exec_result import ExecResult


_UNSUPPORTED = (
    f"KarvyLoop sandbox 在此平台（{sys.platform}）未实现：第三方技能脚本已禁用（fail-closed），"
    "不会退化成无隔离执行。运行时/控制台/自有结晶技能（无脚本，知识型）不受影响——这是降级模式，"
    "不是故障。完整沙箱在 Linux（bubblewrap）/ macOS（sandbox-exec）可用。"
)


class StubSandbox:
    name = "stub"

    @staticmethod
    def available() -> bool:
        return False

    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0,
                   max_output_bytes=30_000) -> ExecResult:
        raise NotImplementedError(_UNSUPPORTED)

    async def write_file(self, path: str, content: bytes, token: CapabilityToken) -> None:
        raise NotImplementedError(_UNSUPPORTED)

    async def read_file(self, path: str, token: CapabilityToken) -> bytes:
        raise NotImplementedError(_UNSUPPORTED)
