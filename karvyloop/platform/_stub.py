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
    f"KarvyLoop sandbox 在此平台（{sys.platform}）未实现。"
    "v1 仅支持 Linux（bubblewrap）。macOS/Windows/Android 适配器按 docs/modules/sandbox.md §4 推迟。"
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
