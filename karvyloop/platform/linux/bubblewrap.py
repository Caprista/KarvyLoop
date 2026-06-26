"""Bubblewrap 沙箱（platform/linux/bubblewrap.py）。

规格：docs/modules/sandbox.md §3。
实现要点：
  1) --unshare-all 起步，按 token.fs 显式放开（fail-closed）
  2) token.net 缺 → --unshare-net 兜底（v1 仅二元网络）
  3) 超时强杀 + 输出字节截断（UTF-8 边界，HR-9 同源）
  4) 不接 L7 过滤（v1 范围外，留 P1）
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Optional

from karvyloop.capability import is_within_workspace
from karvyloop.sandbox.exec_result import ExecResult
from karvyloop.sandbox.mounts import has_net, mounts_from_token
from karvyloop.schemas import CapabilityToken


def _truncate_utf8(data: bytes, limit: int) -> tuple[bytes, bool]:
    """UTF-8 边界截断（HR-9 同源）。返回 (data, truncated)。"""
    if len(data) <= limit:
        return data, False
    # 找到 limit 之前最后一个完整 UTF-8 字符边界
    cut = limit
    while cut > 0 and (data[cut] & 0xC0) == 0x80:
        cut -= 1
    return data[:cut], True


def mounts_from_token(token: CapabilityToken) -> tuple[list[str], list[str]]:
    """兼容 re-export:见 karvyloop.sandbox.mounts.mounts_from_token。"""
    from karvyloop.sandbox.mounts import mounts_from_token as _impl
    return _impl(token)


def has_net(token: CapabilityToken) -> bool:
    """兼容 re-export:见 karvyloop.sandbox.mounts.has_net。"""
    from karvyloop.sandbox.mounts import has_net as _impl
    return _impl(token)


def _bind_binary_parents(bwrap: list[str], argv: list[str]) -> None:
    """解析 argv[0] 并把它的父目录 ro-bind 进沙箱,让 `sleep` / `sh` 等可见。

    处理三处:
      1. argv[0] 是绝对路径 → 直接 bind 父目录
      2. argv[0] 是裸名(无 /) → shutil.which 解析,可能命中 /usr/bin/<x>、/bin/<x>
      3. 解析不到 → 不 bind(让 bwrap 报 "No such file" 给上层,可观测)

    同时确保:
      - /bin 和 /usr/bin 至少有一个被 bind(常见工具集)
      - ld 解析需要的 /lib /lib64 也被 bind
    """
    if not argv:
        return
    candidate_paths: set[str] = set()
    first = argv[0]
    if "/" in first:
        candidate_paths.add(first)
    else:
        resolved = shutil.which(first)
        if resolved:
            candidate_paths.add(resolved)

    # 把候选路径的父目录 + 一些基础库目录加入
    bind_dirs: set[str] = set()
    for p in candidate_paths:
        bind_dirs.add(os.path.dirname(p))
    # ld / glibc 解析需要的目录(常见)
    for d in ("/bin", "/usr/bin", "/lib", "/lib64", "/usr/lib", "/usr/lib64"):
        if os.path.isdir(d):
            bind_dirs.add(d)

    for d in sorted(bind_dirs):
        bwrap += ["--ro-bind", d, d]

    # 让 PATH 在沙箱内仍可用(很多脚本/子进程靠它)
    path_env = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    bwrap += ["--setenv", "PATH", path_env]


class BubblewrapSandbox:
    """Linux bwrap 沙箱。需要 bwrap 在 PATH（apt: bubblewrap）。"""

    name = "bubblewrap"

    @staticmethod
    def available() -> bool:
        return shutil.which("bwrap") is not None

    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0,
                   max_output_bytes=30_000) -> ExecResult:
        if not argv:
            raise ValueError("argv 必须非空")
        if not self.available():
            raise RuntimeError(
                "bubblewrap 不可用（PATH 中没有 bwrap）—— Linux 上 `apt install bubblewrap`"
            )

        ro, rw = mounts_from_token(token)
        net = has_net(token)

        bwrap: list[str] = [
            "bwrap",
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--chdir", cwd,
        ]
        if not net:
            bwrap.append("--unshare-net")
        for p in ro:
            bwrap += ["--ro-bind", p, p]
        for p in rw:
            bwrap += ["--bind", p, p]

        # 默认只挂 /proc /dev /tmp —— `sleep` / `sh` / `cat` 等
        # 都在 /usr/bin、/bin,沙箱里直接调会 "No such file or directory"。
        # 用 shutil.which 解析 argv[0] 并 ro-bind 它的父目录 —— 标准 bubblewrap
        # 实战做法。token 决定哪些 fs 路径可写,这一步只让 binary 可见,不放开写。
        _bind_binary_parents(bwrap, argv)

        bwrap += ["--"] + list(argv)

        proc = await asyncio.create_subprocess_exec(
            *bwrap, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            out, err = await proc.communicate()
        out, truncated = _truncate_utf8(out, max_output_bytes)
        return ExecResult(
            stdout=out, stderr=err, exit_code=proc.returncode or 0,
            timed_out=timed_out, truncated=truncated,
        )

    async def write_file(self, path: str, content: bytes, token: CapabilityToken) -> None:
        """只接受 token 覆盖的 fs 路径；写越界 = 拒绝。"""
        for g in token.grants:
            if g.resource.startswith("fs:") and (not g.ops or "write" in g.ops):
                root = g.resource[3:]
                if is_within_workspace(path, root):
                    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(content)
                    return
        raise PermissionError(f"token 未覆盖写 {path}")

    async def read_file(self, path: str, token: CapabilityToken) -> bytes:
        for g in token.grants:
            if g.resource.startswith("fs:"):
                root = g.resource[3:]
                if is_within_workspace(path, root):
                    with open(path, "rb") as f:
                        return f.read()
        raise PermissionError(f"token 未覆盖读 {path}")
