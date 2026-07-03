"""Windows 降级模式(degraded)验收 —— Windows 是一等「降级」平台。

契约(v1 Tier 4 DegradedWindowsSandbox,替代旧全拒 StubSandbox):
  - 运行时/控制台/自有结晶技能(无脚本,知识型)在 Windows 全功能。
  - **第一方 workspace 读写/exec 直通**(诚实无隔离)—— 修旧 bug:旧 StubSandbox 连
    第一方 workspace 文件都抛 NotImplementedError,与"仅第三方技能脚本禁用、其余全功能"
    承诺不符。现在 agent 在 Windows host 上能读写 workspace 文件。
  - **第三方技能脚本执行仍 fail-closed 明确拒绝**,报错信息说清:为什么拒(无隔离)、
    影响面(只第三方脚本)、去哪有完整沙箱(Linux/macOS)。
  - selector win32 分支:Tier 3 RestrictedToken 可用则用,否则降 Tier 4 Degraded;
    **绝不返回旧全拒 StubSandbox**。
  - 一行安装:scripts/install.ps1(镜像 install.sh 的决策:专属 venv + PATH shim + Python 3.11+ 门)。
  - README 平台表:Windows 从 ⛔ 升 ✅ Supported (degraded),中英一致。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from karvyloop.platform._stub import StubSandbox
from karvyloop.platform.win.degraded import DegradedWindowsSandbox
from karvyloop.sandbox.selector import default_sandbox
from karvyloop.schemas import Capability, CapabilityToken

ROOT = pathlib.Path(__file__).resolve().parents[1]

# skill_exec 路径签发的 token 指纹(第三方脚本执行)
from karvyloop.capability.skill_grants import token_for_skill  # noqa: E402


def _tok(ws="/tmp"):
    return CapabilityToken(
        task_id="t",
        grants=[Capability(resource=f"fs:{ws}", ops=["read", "write"]),
                Capability(resource=f"fs:{ws}", ops=["exec"])],
        expiry=9_999_999_999.0,
    )


class _FM:
    """假 frontmatter:第三方 untrusted。"""
    raw = {"trust": "untrusted", "source": "third-party"}
    allowed_tools = ["Read"]


def _third_party_token(ws):
    return token_for_skill(_FM(), skill_dir=ws, workspace=ws)


# ============ (a) selector:win32 → Tier3/Tier4,绝不全拒 Stub ============

def test_selector_on_win32_is_degraded_not_failclosed_stub(monkeypatch):
    """win32 强制 degraded 分支 → DegradedWindowsSandbox(第一方直通),不是旧全拒 Stub。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("KARVYLOOP_SANDBOX", "degraded")
    sb = default_sandbox()
    assert isinstance(sb, DegradedWindowsSandbox)
    assert not isinstance(sb, StubSandbox)


# ============ (b) degraded:第一方 workspace 读写直通(修 bug)============

@pytest.mark.asyncio
async def test_degraded_first_party_workspace_read_write_passthrough(tmp_path):
    """核心 bug 修复锁:Windows host 上 agent 能读写 workspace 文件(不再 NotImplemented 崩)。"""
    sb = DegradedWindowsSandbox()
    tok = _tok(str(tmp_path))
    target = str(tmp_path / "note.txt")
    await sb.write_file(target, b"hello karvy", tok)          # 写直通
    assert (tmp_path / "note.txt").read_bytes() == b"hello karvy"
    data = await sb.read_file(target, tok)                    # 读直通
    assert data == b"hello karvy"


@pytest.mark.asyncio
async def test_degraded_write_read_reject_outside_token(tmp_path):
    """第一方直通仍走 token 闸:越界 = 拒(与 bwrap/seatbelt 同语义)。"""
    sb = DegradedWindowsSandbox()
    tok = _tok(str(tmp_path))
    outside = str(tmp_path.parent / "evil.txt")
    with pytest.raises(PermissionError):
        await sb.write_file(outside, b"x", tok)
    with pytest.raises(PermissionError):
        await sb.read_file(outside, tok)


# ============ (c) degraded:第三方技能脚本仍 fail-closed ============

@pytest.mark.asyncio
async def test_degraded_third_party_skill_exec_fail_closed(tmp_path):
    """第三方技能脚本(skill_exec token)在 degraded 下 fail-closed,信息说清降级语义。"""
    sb = DegradedWindowsSandbox()
    tok = _third_party_token(str(tmp_path))
    with pytest.raises(PermissionError) as ei:
        await sb.exec(["python3", "evil.py"], token=tok, cwd=str(tmp_path))
    msg = str(ei.value)
    assert "fail-closed" in msg
    assert "第三方技能" in msg
    assert "Linux" in msg and "macOS" in msg
    assert "降级" in msg


@pytest.mark.asyncio
async def test_degraded_available_is_false():
    """degraded 对'有没有真隔离'诚实答否 —— console 技能试跑门据此在门口拒第三方。"""
    assert DegradedWindowsSandbox.available() is False


# ============ (d) 旧 StubSandbox 仍全拒(其他平台/Linux 无 bwrap 兜底)============

@pytest.mark.asyncio
async def test_stub_still_fail_closed_everywhere():
    """StubSandbox 仍是最终兜底(非 win32 且无隔离):exec/write/read 全拒。"""
    sb = StubSandbox()
    with pytest.raises(NotImplementedError):
        await sb.exec(["ls"], token=_tok(), cwd="/tmp")
    with pytest.raises(NotImplementedError):
        await sb.write_file("/tmp/x", b"d", _tok())
    with pytest.raises(NotImplementedError):
        await sb.read_file("/tmp/x", _tok())


# ============ (c) install.ps1:存在 + 关键决策镜像 install.sh ============

class TestInstallPs1:
    PS1 = ROOT / "scripts" / "install.ps1"

    def test_exists(self):
        assert self.PS1.is_file(), "scripts/install.ps1 缺失(Windows 一行安装)"

    def test_dedicated_venv_under_localappdata(self):
        content = self.PS1.read_text(encoding="utf-8")
        assert "LOCALAPPDATA" in content, "venv 应落在 %LOCALAPPDATA%\\karvyloop(专属 venv,不碰系统 Python)"
        assert "'karvyloop'" in content and "'venv'" in content

    def test_creates_cmd_shim_on_path(self):
        content = self.PS1.read_text(encoding="utf-8")
        assert "karvyloop.cmd" in content, "缺 karvyloop.cmd shim(镜像 install.sh 的 ~/.local/bin symlink)"
        assert "SetEnvironmentVariable" in content, "缺 user PATH 持久化"

    def test_python_311_guard_with_py_launcher_fallback(self):
        content = self.PS1.read_text(encoding="utf-8")
        assert "(3, 11)" in content, "缺 Python 3.11+ 版本门(镜像 install.sh 的 version_info 检查)"
        assert "-3.11" in content, "缺 `py -3.11` launcher 兜底"
        assert "Python 3.11+ is required" in content, "缺清晰的失败提示"

    def test_installs_from_github_main_like_install_sh(self):
        content = self.PS1.read_text(encoding="utf-8")
        assert "https://github.com/Caprista/KarvyLoop.git" in content, "安装源须与 install.sh 一致"
        assert "git+" in content

    def test_ascii_safe_no_emoji(self):
        """PowerShell 5.1 兼容:脚本本体 ASCII-safe,无 emoji。"""
        raw = self.PS1.read_bytes()
        assert all(b < 128 for b in raw), "install.ps1 含非 ASCII 字节(PS 5.1 无 BOM 时会读错编码)"


# ============ (d) README:平台表已升级为 Supported (degraded) ============

class TestReadmePlatformTable:
    def test_english_readme_windows_row(self):
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "⛔ Not yet" not in content, "英文 README 的 Windows 行还是 ⛔"
        # v1:Windows 从"降级/无沙箱"升为真沙箱(restricted-token + Job Object)
        assert "restricted-token" in content, "英文 README 应写明 Windows 用 restricted-token 沙箱"
        # 诚实边界:degraded 兜底仍禁第三方脚本、网络 fail-close
        assert "third-party skill scripts disabled" in content
        assert "fail-close" in content or "fail-closes" in content

    def test_english_readme_has_ps1_oneliner(self):
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "irm https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/install.ps1 | iex" in content

    def test_chinese_readme_windows_row(self):
        content = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        assert "⛔ 暂不支持" not in content, "中文 README 的 Windows 行还是 ⛔"
        assert "受限令牌" in content, "中文 README 应写明 Windows 用受限令牌沙箱"
        assert "fail-close" in content, "中文 README 应写明 Windows 网络 fail-close 边界"

    def test_chinese_readme_has_ps1_oneliner(self):
        content = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        assert "irm https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/install.ps1 | iex" in content
