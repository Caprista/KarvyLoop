"""test_seatbelt_profile — macOS Seatbelt 适配器:纯逻辑单测 + macOS 真对抗.

分两块(与 test_landlock.py / test_win_sandbox.py 同构):
  1) 纯逻辑(跨平台,ubuntu/windows CI 也跑):profile 生成是平台无关纯函数,
     锁住 fail-closed 契约 + token→profile 映射。
  2) macOS 真对抗(真跑 sandbox-exec):非 darwin / 无 sandbox-exec 自动 skip;
     darwin 但 runner 环境禁真沙箱(嵌套沙箱/受限 CI)→ skip 不 fail
     (同 tests/test_landlock.py 的 skip-not-fail 策略)。
     首次真机围栏验证在 Mac(macOS 26.5.1 / Apple Silicon)上做过,
     见会话记录 / docs/modules/sandbox.md §4;这里把它锁成 CI 回归。

AC:
- SB1: 默认 fail-closed —— 含 (deny default);未授权 → (deny network*)
- SB2: token 给可写工作区 → profile 出 (allow file-write* (subpath "<realpath>"))
- SB3: token 无写授权(只读) → 无 file-write* 放开行(全只读 exec)
- SB4: token 有 net → (allow network*)
- SB5: selector 在 darwin + sandbox-exec 可用 → SeatbeltSandbox;不可用 → StubSandbox
- SB6: write_file/read_file 越界拒绝(token 闸,与 bubblewrap 同语义)
- SB7(真对抗): 工作区内写通过;工作区外写 / 写 $HOME 被真沙箱拦
- SB8(真对抗): 未授权联网被真沙箱拦
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys

import pytest

from karvyloop.platform.darwin.seatbelt import SeatbeltSandbox, _sbpl_str, build_profile
from karvyloop.platform._stub import StubSandbox
from karvyloop.schemas import Capability, CapabilityToken


pytestmark = pytest.mark.security   # 安全套件:macOS Seatbelt fail-closed profile / 越界拒


def _tok(fs_specs, net=False):
    grants = [Capability(resource=f"fs:{p}", ops=[o]) for p, o in fs_specs]
    if net:
        grants.append(Capability(resource="net:host", ops=["connect"]))
    return CapabilityToken(task_id="t", grants=grants, expiry=9_999_999_999.0)


# ---- SB1 / SB2 ----
def test_profile_failclosed_and_workspace_write(tmp_path):
    ws = str(tmp_path / "ws")
    p = build_profile(_tok([(ws, "write")], net=False))
    assert "(deny default)" in p                      # fail-closed
    assert "(deny network*)" in p                     # 未授权 → 拒网
    assert "(allow network*)" not in p
    # 工作区按 realpath 放开写(用 SBPL 转义形式比对,跨平台稳:macOS 是 / 路径无转义)
    assert f"(subpath {_sbpl_str(os.path.realpath(ws))})" in p
    assert "(allow file-write*" in p


# ---- SB3:只读 token → 无写放开 ----
def test_profile_readonly_no_write_rule(tmp_path):
    ro = str(tmp_path / "ro")
    p = build_profile(_tok([(ro, "read")], net=False))
    assert "(deny default)" in p
    assert "(allow file-write*" not in p              # 只读:一行写放开都没有
    assert "(deny network*)" in p


# ---- SB4:net 授权 → 放网 ----
def test_profile_net_grant(tmp_path):
    ws = str(tmp_path / "ws")
    p = build_profile(_tok([(ws, "write")], net=True))
    assert "(allow network*)" in p
    assert "(deny network*)" not in p


# ---- SB5:selector darwin 分支 ----
def test_selector_darwin_picks_seatbelt(monkeypatch):
    from karvyloop.sandbox import selector
    monkeypatch.setattr(selector.sys, "platform", "darwin")
    # sandbox-exec 可用 → SeatbeltSandbox
    monkeypatch.setattr(SeatbeltSandbox, "available", staticmethod(lambda: True))
    assert isinstance(selector.default_sandbox(), SeatbeltSandbox)
    # 不可用(被裁剪的系统)→ 诚实降级 StubSandbox,不静默无隔离
    monkeypatch.setattr(SeatbeltSandbox, "available", staticmethod(lambda: False))
    assert isinstance(selector.default_sandbox(), StubSandbox)


# ---- SB6:write/read 越界拒绝(token 闸)----
def test_write_read_token_gated(tmp_path):
    sb = SeatbeltSandbox()
    ws = tmp_path / "ws"
    ws.mkdir()
    tok = _tok([(str(ws), "write")])
    inside = str(ws / "a.txt")
    asyncio.run(sb.write_file(inside, b"hi", tok))
    assert (ws / "a.txt").read_bytes() == b"hi"
    assert asyncio.run(sb.read_file(inside, tok)) == b"hi"
    # 越界写 → 拒
    with pytest.raises(PermissionError):
        asyncio.run(sb.write_file(str(tmp_path / "escape.txt"), b"x", tok))


# ============================================================================
# 2) macOS 真对抗(darwin + sandbox-exec 才跑;runner 禁真沙箱 → skip 不 fail)
# ============================================================================

requires_darwin_seatbelt = pytest.mark.skipif(
    not (sys.platform == "darwin" and SeatbeltSandbox.available()),
    reason="需 macOS + sandbox-exec(纯逻辑部分已在上面全平台跑过)",
)


def _skip_if_runner_forbids(r):
    """sandbox-exec **启动器自身**被环境拒(受限 runner / 嵌套沙箱)→ skip 不 fail。

    参照 tests/test_landlock.py:环境限制 ≠ 代码 bug,只在「环境真能强制」时验强制。
    只认启动器错误行(`sandbox-exec: ... sandbox_apply/Operation not permitted`);
    profile 编译错(compile error)是确定性代码 bug —— 不吞,让它红。
    """
    err = r.stderr.decode("utf-8", "replace")
    launcher_lines = [ln for ln in err.splitlines()
                      if ln.lstrip().startswith("sandbox-exec:")]
    blocked = any(
        ("sandbox_apply" in ln or "not permitted" in ln.lower()) and "compile" not in ln
        for ln in launcher_lines
    )
    if r.exit_code != 0 and blocked:
        pytest.skip("runner 不允许真 sandbox-exec,跳过强制验证:"
                    + launcher_lines[-1].strip())


def _canary(sb, tok, ws):
    """profile 下先跑 /usr/bin/true:验证「这个环境能真起 Seatbelt 沙箱」。"""
    r = asyncio.run(sb.exec(["/usr/bin/true"], token=tok, cwd=str(ws), timeout_s=60.0))
    _skip_if_runner_forbids(r)
    assert r.exit_code == 0, f"canary 在沙箱内失败(profile 回归?):{r.stderr!r}"
    return r


# ---- SB7:真沙箱写隔离(工作区内成 / 区外拦 / $HOME 拦)----
@requires_darwin_seatbelt
def test_real_seatbelt_write_isolation(tmp_path):
    sb = SeatbeltSandbox()
    ws = tmp_path / "ws"; ws.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    tok = _tok([(str(ws), "write")])
    _canary(sb, tok, ws)

    # 工作区内写 → 成
    r1 = asyncio.run(sb.exec(["/bin/sh", "-c", "echo ok > inside.txt"],
                             token=tok, cwd=str(ws), timeout_s=60.0))
    assert r1.exit_code == 0, f"工作区内写不该失败:{r1.stderr!r}"
    assert (ws / "inside.txt").read_text().strip() == "ok"

    # 工作区外写 → 拦(fail-closed 核心)
    evil = outside / "evil.txt"
    r2 = asyncio.run(sb.exec(["/bin/sh", "-c", f"echo BOOM > '{evil}'"],
                             token=tok, cwd=str(ws), timeout_s=60.0))
    assert r2.exit_code != 0, "工作区外写竟成功(Seatbelt 门失效!)"
    assert not evil.exists()

    # $HOME 写 → 拦(不能篡改用户面)
    home_canary = os.path.join(os.path.expanduser("~"),
                               f"karvy_seatbelt_canary_{os.getpid()}.txt")
    r3 = asyncio.run(sb.exec(["/bin/sh", "-c", f"echo BOOM > '{home_canary}'"],
                             token=tok, cwd=str(ws), timeout_s=60.0))
    leaked = os.path.exists(home_canary)
    if leaked:
        os.unlink(home_canary)   # 万一失效别把 canary 留在 runner 的 $HOME
    assert r3.exit_code != 0 and not leaked, "写 $HOME 竟成功(Seatbelt 门失效!)"


# ---- SB8:真沙箱网络门(未授权联网拦)----
@requires_darwin_seatbelt
def test_real_seatbelt_net_denied_without_grant(tmp_path):
    sb = SeatbeltSandbox()
    ws = tmp_path / "ws"; ws.mkdir()
    tok = _tok([(str(ws), "write")], net=False)
    _canary(sb, tok, ws)
    curl = shutil.which("curl")
    if not curl:
        pytest.skip("无 curl(macOS 系统自带,理论上不会走到)")
    # (deny network*) 下 socket 创建即拒 → 立刻非 0 退出,不依赖真实出网
    r = asyncio.run(sb.exec([curl, "-sS", "--max-time", "8", "https://example.com/"],
                            token=tok, cwd=str(ws), timeout_s=30.0))
    assert r.exit_code != 0, "未授权联网竟成功(网络门失效!)"
