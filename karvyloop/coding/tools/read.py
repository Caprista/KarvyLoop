"""Read 工具（coding/tools/read.py）。

规格：docs/modules/forge.md §2.2 Read 行 + HR-4/HR-5。
调用方在 make_coding_tools 时注入 sandbox + file_state + workspace_root;
token 在工具被调时通过 call() 传入(不存进实例,避免过期 token 残留)。
"""

from __future__ import annotations

import os
from typing import Any

from karvyloop.capability import is_within_workspace
from karvyloop.schemas import CapabilityToken

from ..filestate import FileState
from ._result import CodingResult


class ReadTool:
    name = "read_file"
    description = "Read a file with line numbers (HR-4: records snapshot for read-before-write)"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "offset": {"type": "integer", "default": 0},
            "limit": {"type": "integer", "default": 2000},
        },
        "required": ["file_path"],
    }

    def __init__(self, sandbox, file_state: FileState, workspace_root: str, *, token: CapabilityToken):
        self.sandbox = sandbox
        self.fs = file_state
        self.workspace_root = workspace_root
        self.token = token  # 单任务生命周期内不变

    def is_concurrency_safe(self, inp: dict) -> bool:
        return True  # read 只读,always safe

    async def __call__(self, inp: dict) -> CodingResult:
        path = inp.get("file_path", "")
        if not path or not is_within_workspace(path, self.workspace_root):
            return CodingResult(ok=False, payload=None, error_code=1,
                                error_message=f"路径 {path} 越出工作区")
        offset = int(inp.get("offset", 0))
        limit = int(inp.get("limit", 2000))
        try:
            content = await self.sandbox.read_file(path, self.token)
        except PermissionError as e:
            return CodingResult(ok=False, payload=None, error_code=5,
                                error_message=f"未授权: {e}")
        except FileNotFoundError:
            return CodingResult(ok=False, payload=None, error_code=6,
                                error_message=f"文件不存在: {path}")
        except Exception as e:
            return CodingResult(ok=False, payload=None, error_code=4,
                                error_message=f"读取失败: {type(e).__name__}: {e}")
        # 记快照(HR-4 状态机)
        self.fs.record_read(path, content)
        if not content:
            return CodingResult(ok=True,
                                payload={"system_reminder": "文件为空"},
                                truncated=False)
        # sandbox 返回 bytes,统一 decode 为 str 再分行
        text = content.decode("utf-8", errors="replace")
        lines = text.splitlines()
        sliced = lines[offset:offset + limit]
        body = "\n".join(sliced)
        truncated = (offset + limit) < len(lines)
        out = "\n".join(f"{i+1+offset:>6}\t{line}" for i, line in enumerate(body.splitlines()))
        return CodingResult(ok=True, payload=out, truncated=truncated)
