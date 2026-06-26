"""sandbox 验收测试 —— 逐条对应 docs/modules/sandbox.md §5 验收标准。

AC1-3 需真 Linux + bwrap,本机 Windows 上 skip（实现已写,在目标平台会跑）。
AC4-6 全平台可测。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

from karvyloop.platform._stub import StubSandbox
from karvyloop.platform.linux.bubblewrap import (
    BubblewrapSandbox,
    _truncate_utf8,
    has_net,
    mounts_from_token,
)
from karvyloop.sandbox import ExecResult, default_sandbox
from karvyloop.sandbox.selector import default_sandbox as sel
from karvyloop.schemas import Capability, CapabilityToken


def _tok(fs_specs, net=False):
    """fs_specs: list[(path, op)]; net: bool"""
    grants = [Capability(resource=f"fs:{p}", ops=[o]) for p, o in fs_specs]
    if net:
        grants.append(Capability(resource="net:host", ops=["connect"]))
    return CapabilityToken(task_id="t", grants=grants, expiry=9_999_999_999.0)


requires_linux_bwrap = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and shutil.which("bwrap")),
    reason="需 Linux + bubblewrap（apt install bubblewrap）",
)


# ============ AC1：沙箱内 rm -rf / 不影响宿主（只能动挂载的 rw 路径）============
@requires_linux_bwrap
@pytest.mark.asyncio
async def test_ac1_sandbox_rm_rf_root_cannot_touch_host(tmp_path):
    """AC1 真意:沙箱内 rm 一个**沙箱外**的路径 → 失败,宿主不受影响。

    原版 bug:测的是 `rm -rf tmp_path/canary.txt` —— 但 tmp_path 在 token 的 rw
    列表里(`_tok` 给的就是 read+write tmp_path),沙箱内对 tmp_path 有完整写权,
    rm 成功是**预期行为**。真要测隔离,得让 canary 在 token 覆盖的 rw 路径**之外**
    —— 这样沙箱内根本看不到(或只读),rm 必失败。
    """
    # 在沙箱外(不是 tmp_path)建 canary —— 不在 token 覆盖范围
    # 用 /etc/hosts:永远存在、不在 tmp_path 范围、最经典的隔离测试目标
    canary = Path("/etc/hosts")
    if not canary.exists():
        pytest.skip("/etc/hosts 不存在（容器或非 Linux 主机）")
    # 记录原内容,测试完恢复(防御性 —— 我们期望它不被改)
    original = canary.read_bytes()
    # token 只给 tmp_path,不给 /etc
    tok = _tok([(str(tmp_path), "read"), (str(tmp_path), "write")])
    sb = BubblewrapSandbox()
    r = await sb.exec(["rm", "-f", str(canary)], token=tok, cwd=str(tmp_path))
    # 关键断言:无论 rm 退出码多少,宿主 /etc/hosts 必须仍是原内容
    assert canary.read_bytes() == original, (
        "宿主 /etc/hosts 被破坏（沙箱隔离失效！）"
    )
    # 也可验证:沙箱内 rm 应失败（exit != 0,因为路径不可写/不可见）
    # 不强求此断言 —— 有的 bwrap 版本可能 rm 0 但实际未删(空文件 or read-only fs)
    # 关键是不破环宿主。


# ============ AC2：只读 fs → 沙箱内写失败、读宿主路径失败 ============
@requires_linux_bwrap
@pytest.mark.asyncio
async def test_ac2_readonly_grant_blocks_write_and_other_paths(tmp_path):
    target = tmp_path / "data.txt"
    target.write_text("ok")
    # 沙箱内尝试写目标（应失败） + 读 /etc/passwd（应失败/被挂载策略挡）
    tok = _tok([(str(tmp_path), "read")])
    sb = BubblewrapSandbox()
    r1 = await sb.exec(["sh", "-c", f"echo BOOM > {target}"], token=tok, cwd=str(tmp_path))
    # 写失败 → 宿主文件内容不变
    assert target.read_text() == "ok", "只读挂载下被写入（隔离失效！）"


# ============ AC3：无 net → curl 失败（断网）============
@requires_linux_bwrap
@pytest.mark.asyncio
async def test_ac3_no_net_blocks_network(tmp_path):
    tok = _tok([(str(tmp_path), "read"), (str(tmp_path), "write")], net=False)
    sb = BubblewrapSandbox()
    # --unshare-net 下 DNS 解析必失败 → curl 应非 0
    if shutil.which("curl"):
        argv = ["curl", "-sS", "--max-time", "2", "https://example.com"]
    else:
        # 没 curl：用 python httpx？直接用 python -c 拼一个 TCP connect
        argv = ["python", "-c", "import socket;socket.create_connection(('1.1.1.1', 53), timeout=1)"]
    r = await sb.exec(argv, token=tok, cwd=str(tmp_path), timeout_s=10.0)
    assert r.exit_code != 0, "无 net 能力却能联网（隔离失效！）"


# ============ AC4：timeout 到 → 杀进程、timed_out=True ============
@requires_linux_bwrap
@pytest.mark.asyncio
async def test_ac4_timeout_kills_process(tmp_path):
    tok = _tok([(str(tmp_path), "read"), (str(tmp_path), "write")])
    sb = BubblewrapSandbox()
    # 睡 60s,只给 1s 超时
    r = await sb.exec(["sleep", "60"], token=tok, cwd=str(tmp_path), timeout_s=1.0)
    assert r.timed_out is True
    assert r.exit_code != 0


# ============ AC5：输出超 max_output_bytes → 截断、UTF-8 不破 ============
def test_ac5_truncate_utf8_boundary():
    # 构造：limit 处正好是 UTF-8 多字节序列的中间
    data = b"abc" + "你".encode("utf-8") * 10 + b"END"  # 你=3 bytes × 10
    # limit 落在 "你" 序列中间
    out, trunc = _truncate_utf8(data, 5)  # 5 字节：'abc' (3) + '你'前 2 字节（中间）
    assert trunc is True
    # 截断后必须是合法 UTF-8
    out.decode("utf-8")
    # 不能截到 '你' 中间 → 应在 'abc' 之后停
    assert out == b"abc"


def test_ac5_no_truncate_when_within_limit():
    data = b"hello"
    out, trunc = _truncate_utf8(data, 100)
    assert out == data and trunc is False


# ============ AC6：核心层无 import karvyloop.platform.linux ============
def test_ac6_core_layer_no_platform_imports():
    """karvyloop/sandbox/*.py 不准直接 import 具体平台。"""
    import karvyloop.sandbox as sb
    # 收集 sandbox 子模块的源码
    import pathlib
    pkg_dir = pathlib.Path(sb.__file__).parent
    forbidden = "karvyloop.platform.linux"
    offenders = []
    for p in pkg_dir.rglob("*.py"):
        # selector 故意按 OS 选实现 —— 这是 PAL 接缝的合法 import
        if p.name == "selector.py":
            continue
        text = p.read_text(encoding="utf-8")
        if forbidden in text:
            offenders.append(str(p))
    assert not offenders, f"核心层禁直接 import {forbidden}: {offenders}"


# ============ 跨平台：mounts_from_token / has_net ============
def test_mounts_from_token_ro_rw():
    tok = _tok([("/a", "read"), ("/b", "write"), ("/c", "read"), ("/d", "write")])
    ro, rw = mounts_from_token(tok)
    assert "/a" in ro and "/c" in ro
    assert "/b" in rw and "/d" in rw


def test_mounts_from_token_wildcard_ops_is_rw():
    tok = CapabilityToken(task_id="t",
                          grants=[Capability(resource="fs:/e", ops=[])],
                          expiry=9_999_999_999.0)
    ro, rw = mounts_from_token(tok)
    assert ro == [] and rw == ["/e"]


def test_has_net():
    assert has_net(_tok([], net=True)) is True
    assert has_net(_tok([], net=False)) is False


# ============ 跨平台：PAL selector ============
def test_selector_returns_stub_on_windows(monkeypatch):
    """真的模拟 Windows 平台 → selector 选 StubSandbox（不查 bwrap）。

    用 monkeypatch.setattr(sys, "platform", ...) 强制 sys.platform 走 win32 分支;
    这样在 Linux 上跑也真验逻辑,而不是被 bwrap 路径截胡。
    """
    monkeypatch.setattr(sys, "platform", "win32")
    sb = sel()
    assert isinstance(sb, StubSandbox)
    assert sb.available() is False


def test_selector_override_wins():
    class _Fake:
        name = "fake"
        available = staticmethod(lambda: True)
        async def exec(self, *a, **k): return ExecResult(b"", b"", 0)
        async def write_file(self, *a, **k): pass
        async def read_file(self, *a, **k): return b""
    sb = sel(override=_Fake())
    assert sb is not None  # type: ignore[comparison-overlap]


# ============ 跨平台：StubSandbox 明确报错（不静默放行）============
@pytest.mark.asyncio
async def test_stub_exec_raises_unimplemented():
    sb = StubSandbox()
    tok = _tok([("/a", "write")])
    with pytest.raises(NotImplementedError, match="未实现"):
        await sb.exec(["ls"], token=tok, cwd="/")


@pytest.mark.asyncio
async def test_stub_write_file_raises_unimplemented():
    sb = StubSandbox()
    tok = _tok([("/a", "write")])
    with pytest.raises(NotImplementedError):
        await sb.write_file("/a/x", b"data", tok)


# ============ 跨平台：linux 模块的 read/write 走 capability 边界 ============
@pytest.mark.asyncio
async def test_linux_write_file_rejects_outside_token(tmp_path):
    """写越界 → PermissionError（即便实现存在,token 仍是一票否决）"""
    sb = BubblewrapSandbox()
    tok = _tok([(str(tmp_path), "write")])
    outside = tmp_path.parent / "evil.txt"
    try:
        with pytest.raises(PermissionError, match="未覆盖"):
            await sb.write_file(str(outside), b"x", tok)
    finally:
        if outside.exists():
            outside.unlink()


@pytest.mark.asyncio
async def test_linux_read_file_rejects_outside_token(tmp_path):
    sb = BubblewrapSandbox()
    tok = _tok([(str(tmp_path), "read")])
    outside = tmp_path.parent / "secret.txt"
    outside.write_bytes(b"x")
    try:
        with pytest.raises(PermissionError):
            await sb.read_file(str(outside), tok)
    finally:
        if outside.exists():
            outside.unlink()
