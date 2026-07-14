"""Edit 工具（coding/tools/edit.py）。

规格：docs/modules/forge.md §2.2 Edit 行:
  - old_string 须存在且唯一(多匹配且非 replace_all → 拒)
  - 必须先读(HR-4)
  - 引号风格归一(保持源风格;MVP 简化:不主动改引号)
"""

from __future__ import annotations

from karvyloop.capability import is_within_workspace, resolve_in_workspace
from karvyloop.schemas import CapabilityToken

from ..filestate import FileState, ReadBeforeWriteError
from ._result import CodingResult


class EditTool:
    name = "edit_file"
    description = "Edit file by replacing old_string with new_string (HR-4 read-before-write)"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    def __init__(self, sandbox, file_state: FileState, workspace_root: str, *, token: CapabilityToken):
        self.sandbox = sandbox
        self.fs = file_state
        self.workspace_root = workspace_root
        self.token = token

    def is_concurrency_safe(self, inp: dict) -> bool:
        return False  # 写

    async def __call__(self, inp: dict) -> CodingResult:
        # 相对路径按 workspace 解析(与 read/write 同基准纪律,防按进程 CWD 读写)
        path = resolve_in_workspace(inp.get("file_path", ""), self.workspace_root)
        from karvyloop.capability.fs_grants import note_denied, path_allowed
        if not path or not path_allowed(path, "write", workspace_root=self.workspace_root):
            if path:
                note_denied(path, "write")
            return CodingResult(ok=False, payload=None, error_code=1,
                                error_message=f"路径 {path} 越出工作区")
        old = inp.get("old_string", "")
        new = inp.get("new_string", "")
        replace_all = bool(inp.get("replace_all", False))
        if not old:
            return CodingResult(ok=False, payload=None, error_code=7,
                                error_message="old_string 不可为空")
        # HR-4 前置校验
        try:
            self.fs.assert_writable(path)
        except ReadBeforeWriteError as e:
            return CodingResult(ok=False, payload=None, error_code=e.code,
                                error_message=str(e))
        # 读当前内容(再次过 sandbox,确认 mtime 未变)
        try:
            content_bytes = await self.sandbox.read_file(path, self.token)
        except Exception as e:
            return CodingResult(ok=False, payload=None, error_code=4,
                                error_message=f"读取失败: {type(e).__name__}: {e}")
        text = content_bytes.decode("utf-8", errors="replace")
        count = text.count(old)
        if count == 0:
            return CodingResult(ok=False, payload=None, error_code=8,
                                error_message="old_string 在文件中未找到")
        if count > 1 and not replace_all:
            return CodingResult(ok=False, payload=None, error_code=9,
                                error_message=f"old_string 匹配 {count} 次(非 replace_all 不可继续)")
        if replace_all:
            new_text = text.replace(old, new)
        else:
            new_text = text.replace(old, new, 1)
        try:
            await self.sandbox.write_file(path, new_text.encode("utf-8"), self.token)
        except Exception as e:
            return CodingResult(ok=False, payload=None, error_code=4,
                                error_message=f"写失败: {type(e).__name__}: {e}")
        return CodingResult(ok=True, payload={"edited": path, "replaced": count if replace_all else 1})
