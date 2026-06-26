"""Bash 工具（coding/tools/bash.py）。

规格：docs/modules/forge.md §2.2 Bash 行:
  - is_concurrency_safe 接收 input,解析命令 AST 动态判定(HR-1 最关键实例)
  - MVP 只挡:命令替换 / 重定向写 / rm -rf / .git 写;其余保守交 capability 询问
  - 阻塞超 15s 转后台（spec:run_in_background 字段;M0 简单实现）
  - 输出 >30k 落盘
"""

from __future__ import annotations

import re
import shlex
from typing import Iterable

from karvyloop.schemas import CapabilityToken

from ..filestate import FileState
from ._result import CodingResult


# 写类命令(粗解析;AST 太重,MVP 用 shlex 切 + 子命令集合)
_WRITE_FIRST_WORDS = frozenset({
    "rm", "mv", "cp", "mkdir", "rmdir", "touch", "chmod", "chown", "ln",
    "tee", "dd", "install", "rsync", "sed", "awk", ">",
    # 显式危险
    "shutdown", "reboot", "halt",
})

# 解析失败时保守判写(safe=False)
def _classify(command: str) -> bool:
    """返回 True = 只读,False = 写。"""
    s = command.strip()
    if not s:
        return True
    # 重定向写 → 写
    if re.search(r"(?<!<)>(?!>)", s) or ">>" in s:
        return False
    # 命令替换 → 保守写(可能触发副作用)
    if "`" in s or "$(" in s:
        return False
    # 未匹配的括号/引号 → 解析失败 → 保守
    if s.count("(") != s.count(")"):
        return False
    if s.count('"') % 2 != 0 or s.count("'") % 2 != 0:
        return False
    # pipectl / 子 shell → 保守
    if s.startswith(("sudo ", "exec ")):
        return False
    try:
        tokens = shlex.split(s, posix=True)
    except ValueError:
        return False  # 解析失败 → 保守
    if not tokens:
        return True
    head = tokens[0]
    if head in _WRITE_FIRST_WORDS:
        return False
    # 复合命令(pipeline/chain) → 保守
    if any(t in {"&&", "||", "|", ";"} for t in tokens[1:]):
        return False
    return True


class BashTool:
    name = "run_command"
    description = "Run a shell command (is_read_only derived from AST, conservative on parse failure)"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30},
            "run_in_background": {"type": "boolean", "default": False},
        },
        "required": ["command"],
    }

    def __init__(self, sandbox, file_state: FileState, workspace_root: str, *, token: CapabilityToken):
        self.sandbox = sandbox
        self.fs = file_state  # noqa: F841 — bash 不动 file_state(无读-改-写)
        self.workspace_root = workspace_root
        self.token = token

    def is_concurrency_safe(self, inp: dict) -> bool:
        """接收 input 动态判定(HR-1)。"""
        cmd = inp.get("command", "")
        return _classify(cmd)

    async def __call__(self, inp: dict) -> CodingResult:
        cmd = inp.get("command", "")
        timeout = float(inp.get("timeout", 30))
        cwd = self.workspace_root
        try:
            r = await self.sandbox.exec(["sh", "-c", cmd], token=self.token,
                                        cwd=cwd, timeout_s=timeout,
                                        max_output_bytes=30_000)
        except Exception as e:
            return CodingResult(ok=False, payload=None, error_code=4,
                                error_message=f"exec 失败: {type(e).__name__}: {e}")
        # 输出 >30k → truncated 已由 sandbox 标;额外提供 union(stdout+stderr) 文本
        out = r.stdout.decode("utf-8", errors="replace")
        err = r.stderr.decode("utf-8", errors="replace")
        return CodingResult(
            ok=(r.exit_code == 0),
            payload={
                "exit_code": r.exit_code,
                "timed_out": r.timed_out,
                "stdout": out,
                "stderr": err,
            },
            truncated=r.truncated,
            error_code=0 if r.exit_code == 0 else 10,
            error_message="" if r.exit_code == 0 else f"exit={r.exit_code}",
        )
