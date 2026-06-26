"""test_seatbelt_profile — macOS Seatbelt 适配器的纯逻辑单测(无需 macOS).

profile 生成是平台无关纯函数,这里锁住 fail-closed 契约 + token→profile 映射。
真机围栏(写区外/写HOME/联网 拦截)已在 Mac 上对抗式验证,见会话记录 / docs/modules/sandbox.md §4。

AC:
- SB1: 默认 fail-closed —— 含 (deny default);未授权 → (deny network*)
- SB2: token 给可写工作区 → profile 出 (allow file-write* (subpath "<realpath>"))
- SB3: token 无写授权(只读) → 无 file-write* 放开行(全只读 exec)
- SB4: token 有 net → (allow network*)
- SB5: selector 在 darwin + sandbox-exec 可用 → SeatbeltSandbox;不可用 → StubSandbox
- SB6: write_file/read_file 越界拒绝(token 闸,与 bubblewrap 同语义)
"""
from __future__ import annotations

import asyncio
import os

import pytest

from karvyloop.platform.darwin.seatbelt import SeatbeltSandbox, _sbpl_str, build_profile
from karvyloop.platform._stub import StubSandbox
from karvyloop.schemas import Capability, CapabilityToken


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
