"""Windows 降级模式(degraded)验收 —— Windows 是一等「降级」平台。

契约(Task B):
  - 运行时/控制台/自有结晶技能(无脚本,知识型)在 Windows 全功能 —— 全套测试本就在 win32 host 跑绿。
  - 第三方技能脚本执行被 **fail-closed 明确拒绝**(StubSandbox),报错信息说清:
    为什么拒(无沙箱)、影响面(只第三方脚本)、去哪有完整沙箱(Linux/macOS)。
  - 一行安装:scripts/install.ps1(镜像 install.sh 的决策:专属 venv + PATH shim + Python 3.11+ 门)。
  - README 平台表:Windows 从 ⛔ 升 ✅ Supported (degraded),中英一致。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from karvyloop.platform._stub import StubSandbox
from karvyloop.sandbox.selector import default_sandbox
from karvyloop.schemas import Capability, CapabilityToken

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _tok():
    return CapabilityToken(
        task_id="t",
        grants=[Capability(resource="fs:/tmp", ops=["read", "write"])],
        expiry=9_999_999_999.0,
    )


# ============ (a) selector:win32 → fail-closed StubSandbox ============

def test_selector_on_win32_returns_failclosed_stub(monkeypatch):
    """win32 → StubSandbox(available()=False),不静默降成无隔离执行。"""
    monkeypatch.setattr(sys, "platform", "win32")
    sb = default_sandbox()
    assert isinstance(sb, StubSandbox)
    assert sb.available() is False


# ============ (b) stub exec:明确拒绝 + 说清降级语义 ============

@pytest.mark.asyncio
async def test_stub_exec_refuses_with_clear_degraded_message():
    """拒绝信息必须说清:fail-closed、只禁第三方脚本、Linux/macOS 有完整沙箱。"""
    sb = StubSandbox()
    with pytest.raises(NotImplementedError) as ei:
        await sb.exec(["python3", "evil.py"], token=_tok(), cwd="/tmp")
    msg = str(ei.value)
    assert "fail-closed" in msg, "拒绝信息须点名 fail-closed(明确是安全设计不是 bug)"
    assert "第三方技能" in msg, "拒绝信息须说清影响面 = 第三方技能脚本"
    assert "Linux" in msg and "macOS" in msg, "拒绝信息须指路完整沙箱平台"
    assert "降级" in msg, "拒绝信息须点名降级模式(其余功能不受影响)"


@pytest.mark.asyncio
async def test_stub_write_and_read_also_refuse():
    sb = StubSandbox()
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
        assert "Supported (degraded)" in content
        assert "third-party skill scripts disabled" in content

    def test_english_readme_has_ps1_oneliner(self):
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "irm https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/install.ps1 | iex" in content

    def test_chinese_readme_windows_row(self):
        content = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        assert "⛔ 暂不支持" not in content, "中文 README 的 Windows 行还是 ⛔"
        assert "支持(降级)" in content
        assert "第三方技能脚本禁用" in content

    def test_chinese_readme_has_ps1_oneliner(self):
        content = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        assert "irm https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/install.ps1 | iex" in content
