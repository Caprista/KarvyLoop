"""DegradedWindowsSandbox(platform/win/degraded.py)—— Windows Tier 4 兜底。

Windows 上连 RestrictedToken(Tier 3)都探测不到时的降级模式。诚实契约:

  - **第一方路径直通**:agent 对 workspace 的 read/write/exec 走纯 token 闸
    (write_file/read_file 与 bubblewrap/seatbelt 完全同语义;exec 要求 cwd 落在
    token 的 fs 授权内)——**无 OS 级隔离**,如实标注。运行时自身与 agent 的
    工作区操作属同一信任域;没有沙箱不等于连自己的工作区都不能读写
    (旧 StubSandbox 全抛 NotImplementedError,与"仅第三方技能脚本禁用、
    其余全功能"的承诺不符 —— 本类就是修这个 bug)。
  - **第三方技能脚本 fail-closed**:skill_exec 路径签发的 token(见
    _util.is_skill_exec_token)一律拒跑 —— 别人的代码没有笼子绝不裸跑。
  - available() 恒 False:对"本机有没有真隔离"诚实回答没有
    (console 的技能试跑 API 据此在门口拒绝)。

生效范围只有 Windows(selector 只在 win32 分支返回本类);Linux 无 bwrap /
macOS 无 sandbox-exec 仍降到全拒的 StubSandbox,行为不变。
"""

from __future__ import annotations

import asyncio
import os
import subprocess

from karvyloop.capability import is_within_workspace
from karvyloop.sandbox.exec_result import ExecResult
from karvyloop.schemas import CapabilityToken

from ._util import (
    _truncate_utf8,
    is_skill_exec_token,
    resolve_argv,
    token_gated_read,
    token_gated_write,
)

_THIRD_PARTY_REFUSED = (
    "Windows 降级模式(无 OS 级隔离):第三方技能脚本已被 fail-closed 明确拒绝,"
    "绝不退化成无隔离执行别人的代码。影响面仅第三方技能脚本;运行时/控制台/"
    "工作区读写/自有结晶技能(无脚本,知识型)全功能不受影响 —— 这是降级模式,不是故障。"
    "完整沙箱在 Linux(bubblewrap)/ macOS(sandbox-exec);本机未探测到可用的 "
    "Windows RestrictedToken 沙箱(Tier 3)。"
)


class DegradedWindowsSandbox:
    """Windows 降级沙箱:第一方直通(诚实无隔离)+ 第三方技能脚本拒跑。"""

    name = "win-degraded"

    @staticmethod
    def available() -> bool:
        """恒 False —— 本类不提供任何真隔离,探测"有没有沙箱"时诚实答没有。"""
        return False

    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0,
                   max_output_bytes=30_000) -> ExecResult:
        if not argv:
            raise ValueError("argv 必须非空")
        # 第三方技能脚本:fail-closed(唯一被禁用的路径)
        if is_skill_exec_token(token):
            raise PermissionError(_THIRD_PARTY_REFUSED)
        # 第一方 token 闸:cwd 必须落在 token 的 fs 授权内(不接受 ambient cwd)
        covered = any(
            g.resource.startswith("fs:") and is_within_workspace(cwd, g.resource[3:])
            for g in token.grants
        )
        if not covered:
            raise PermissionError(f"token 未覆盖执行目录 {cwd}")

        argv = resolve_argv(argv)
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
            self._kill_tree(proc)
            out, err = await proc.communicate()
        out, truncated = _truncate_utf8(out, max_output_bytes)
        return ExecResult(
            stdout=out, stderr=err, exit_code=proc.returncode or 0,
            timed_out=timed_out, truncated=truncated,
        )

    @staticmethod
    def _kill_tree(proc) -> None:
        """超时杀进程;Windows 用 taskkill /T 杀整棵树(proc.kill 杀不到子孙)。"""
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=15,
                )
                return
            except Exception:
                pass
        try:
            proc.kill()
        except ProcessLookupError:
            pass

    async def write_file(self, path: str, content: bytes, token: CapabilityToken) -> None:
        """纯 token 闸 IO(与 bubblewrap/seatbelt 同语义)—— 第一方 workspace 读写直通。"""
        token_gated_write(path, content, token)

    async def read_file(self, path: str, token: CapabilityToken) -> bytes:
        return token_gated_read(path, token)
