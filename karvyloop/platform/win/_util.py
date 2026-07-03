"""platform/win/_util.py —— Windows 沙箱层共用纯逻辑(degraded / restricted 共享)。

全部平台无关可单测(不 import 任何 Win32 API):
  - _truncate_utf8:UTF-8 边界截断(与 bubblewrap/seatbelt 同源)
  - is_skill_exec_token:识别"技能脚本执行"token(registry/skill_exec →
    capability/skill_grants.token_for_skill 签发,task_id 固定 "skill-exec";
    tests/test_win_sandbox.py 有契约测试锁住这个 marker,上游改名会红)
  - resolve_argv:把 POSIX 形态的 argv 翻译成本机可执行形态(`sh -c` → cmd /c、
    `python3` → sys.executable)—— 平台适配属于平台层,不改上游调用方
  - token 闸 read/write:纯 token 检查 IO,跨平台一致(照搬 bubblewrap 语义)
"""

from __future__ import annotations

import os
import shutil
import sys

from karvyloop.capability import is_within_workspace
from karvyloop.schemas import CapabilityToken

#: registry/skill_exec.run_skill_script 走 token_for_skill(task_id 默认值)签发的 token。
#: 这是"第三方/外部技能脚本执行"路径在 token 上的确定性指纹。
SKILL_EXEC_TASK_ID = "skill-exec"


def _truncate_utf8(data: bytes, limit: int) -> tuple[bytes, bool]:
    """UTF-8 边界截断(HR-9 同源)。返回 (data, truncated)。"""
    if len(data) <= limit:
        return data, False
    cut = limit
    while cut > 0 and (data[cut] & 0xC0) == 0x80:
        cut -= 1
    return data[:cut], True


def is_skill_exec_token(token: CapabilityToken) -> bool:
    """token 是否来自技能脚本执行路径(skill_grants.token_for_skill 默认 task_id)。"""
    return getattr(token, "task_id", "") == SKILL_EXEC_TASK_ID


def resolve_argv(argv: list[str]) -> list[str]:
    """Windows 上把 POSIX 惯例的 argv 头翻译成本机可用形态。

    - `sh -c <cmd>` / `bash -c <cmd>`:本机有 sh/bash(如 Git Bash)→ 原样;
      没有 → `cmd /d /s /c <cmd>`(语义有差异,降级诚实换壳,好过直接 FileNotFound)。
    - `python3` / `python` → **总是**改成 sys.executable(skill_exec 对 .py 固定发
      python3)。关键:Windows 上裸 `python3` 常被解析成微软商店 App Execution Alias
      (WindowsApps 下的 reparse point)——受限令牌**无法访问**该 alias,会 WinError 1920
      (CANT_ACCESS_FILE)。直接用 sys.executable(真解释器绝对路径)绕开此坑,且更确定。
    非 Windows 原样返回(degraded 类在测试里会被跨平台实例化)。
    """
    argv = list(argv)
    if os.name != "nt" or not argv:
        return argv
    head = argv[0]
    if head in ("sh", "bash") and len(argv) >= 3 and argv[1] == "-c":
        if shutil.which(head) is None:
            return ["cmd", "/d", "/s", "/c", argv[2]] + argv[3:]
        return argv
    if head in ("python3", "python"):
        return [sys.executable] + argv[1:]
    return argv


def token_gated_write(path: str, content: bytes, token: CapabilityToken) -> None:
    """只接受 token 覆盖的 fs 路径;写越界 = 拒绝(与 bubblewrap/seatbelt 同语义)。"""
    for g in token.grants:
        if g.resource.startswith("fs:") and (not g.ops or "write" in g.ops):
            root = g.resource[3:]
            if is_within_workspace(path, root):
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "wb") as f:
                    f.write(content)
                return
    raise PermissionError(f"token 未覆盖写 {path}")


def token_gated_read(path: str, token: CapabilityToken) -> bytes:
    for g in token.grants:
        if g.resource.startswith("fs:"):
            root = g.resource[3:]
            if is_within_workspace(path, root):
                with open(path, "rb") as f:
                    return f.read()
    raise PermissionError(f"token 未覆盖读 {path}")


def rw_ro_paths_with_grants(token: CapabilityToken) -> tuple[list[str], list[str]]:
    """token 的 (ro, rw) 路径 + fs_grants 台账未过期授权(与 bubblewrap/seatbelt 同源)。

    ro = 只读授权路径;rw = 可写授权路径。Windows Tier-3 用它决定:
      - rw → 临时授 write-gate SID 写(白名单)
      - ro → 临时授 write-gate SID 读/遍历(RX):虽然 WRITE_RESTRICTED 名义上读放宽,
        但新建的深层目录在受限令牌下遍历会踩坑,显式授 RX 保证授权路径可靠可读。
    """
    from karvyloop.sandbox.mounts import mounts_from_token
    ro, rw = mounts_from_token(token)
    ro, rw = list(ro), list(rw)
    try:
        from karvyloop.capability.fs_grants import get_store
        _st = get_store()
        if _st is not None:
            for g in _st.list():
                if g.get("expired"):
                    continue
                (rw if "write" in (g.get("ops") or []) else ro).append(g["path"])
    except Exception:
        pass
    return ro, rw


def rw_paths_with_grants(token: CapabilityToken) -> list[str]:
    """向后兼容:仅 rw 路径。"""
    return rw_ro_paths_with_grants(token)[1]
