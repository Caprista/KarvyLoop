"""Write 工具（coding/tools/write.py）。

规格：docs/modules/forge.md §2.2 Write 行:
  - 写前必须读过(HR-4 read-before-write 状态机拦)
  - 全量覆盖
  - 强制 LF
  - 写前 mkdir
  - mtime > 上次读 → 拒"自读取后被修改"
"""

from __future__ import annotations

import os

from karvyloop.capability import is_within_workspace
from karvyloop.schemas import CapabilityToken

from ..filestate import CHANGED_SINCE_READ, READ_REQUIRED, FileState, ReadBeforeWriteError
from ._result import CodingResult


class WriteTool:
    name = "write_file"
    description = "Write file (HR-4: read-before-write enforced)"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["file_path", "content"],
    }

    def __init__(self, sandbox, file_state: FileState, workspace_root: str, *, token: CapabilityToken):
        self.sandbox = sandbox
        self.fs = file_state
        self.workspace_root = workspace_root
        self.token = token

    def is_concurrency_safe(self, inp: dict) -> bool:
        return False  # 写永远非并发安全

    async def __call__(self, inp: dict) -> CodingResult:
        path = inp.get("file_path", "")
        from karvyloop.capability.fs_grants import note_denied, path_allowed
        if not path or not path_allowed(path, "write", workspace_root=self.workspace_root):
            if path:
                note_denied(path, "write")
            return CodingResult(ok=False, payload=None, error_code=1,
                                error_message=f"路径 {path} 越出工作区")
        content = inp.get("content", "")
        # 强制 LF
        if "\r\n" in content:
            content = content.replace("\r\n", "\n")
        # HR-4 前置校验
        try:
            self.fs.assert_writable(path)
        except ReadBeforeWriteError as e:
            return CodingResult(ok=False, payload=None, error_code=e.code,
                                error_message=str(e))
        # 写前 mkdir
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            await self.sandbox.write_file(path, content.encode("utf-8"), self.token)
        except PermissionError as e:
            return CodingResult(ok=False, payload=None, error_code=5,
                                error_message=f"未授权: {e}")
        except Exception as e:
            return CodingResult(ok=False, payload=None, error_code=4,
                                error_message=f"写失败: {type(e).__name__}: {e}")
        return CodingResult(ok=True, payload={"wrote": path, "bytes": len(content.encode("utf-8"))})
