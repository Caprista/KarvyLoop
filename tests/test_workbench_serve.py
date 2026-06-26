"""textual-serve + 安全审计测试(M3 批 3c — 收官)。

设计:plans/snoopy-singing-sunbeam.md §5。

边界:
- launch_serve 默认 host=127.0.0.1(CLAUDE.md 安全地基)
- 绑 0.0.0.0 必显式 opt-in(警告)
- 不打印 headers / api_key / Authorization(grep 锁)
- textual-serve 是 subprocess 启动 chat CLI(K 铁律 grep 全过)
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
from unittest.mock import patch

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.workbench.serve import launch_serve  # noqa: E402


# ---------- AC1: launch_serve 参数 ----------

class TestAC1LaunchServeSignature:
    """AC1: launch_serve 参数签名 + 默认值安全。"""

    def test_default_host_is_loopback(self):
        """CLAUDE.md 安全地基:launch_serve 默认 host='127.0.0.1'(不绑 LAN)。"""
        import inspect
        sig = inspect.signature(launch_serve)
        assert sig.parameters["host"].default == "127.0.0.1"

    def test_default_port_is_8765(self):
        import inspect
        sig = inspect.signature(launch_serve)
        assert sig.parameters["port"].default == 8765


# ---------- AC2: 0.0.0.0 显式 opt-in 警告 ----------

class TestAC2PublicHostWarning:
    """AC2: 绑 0.0.0.0 必须显式 opt-in + 警告日志。"""

    def test_public_host_logs_warning(self, caplog):
        """0.0.0.0 应触发安全警告(不阻塞启动)。"""
        # 用 monkey patch 避免真启动 server
        from textual_serve.server import Server

        with patch.object(Server, "serve") as mock_serve:
            with caplog.at_level("WARNING"):
                launch_serve(
                    command="python -c 'print(1)'",
                    host="0.0.0.0",
                    port=9999,
                )
            # 0.0.0.0 应触发警告
            assert any("0.0.0.0" in r.message for r in caplog.records), \
                f"0.0.0.0 应触发警告,但 caplog={[(r.levelname, r.message) for r in caplog.records]}"
            mock_serve.assert_called_once()


# ---------- AC3: serve 模式子命令路由 ----------

class TestAC3ServeCliRouting:
    """AC3: `karvyloop chat --serve` 走 textual-serve 路径,不进 App.run()。"""

    def test_serve_flag_calls_launch_serve(self):
        """--serve 应调 launch_serve(不调 WorkbenchApp.run())。"""
        from textual_serve.server import Server

        with patch.object(Server, "serve") as mock_serve:
            # 直接调 cmd_chat --serve 路径
            from karvyloop.cli.chat import cmd_chat
            rc = cmd_chat(serve=True, host="127.0.0.1", port=9998)
            mock_serve.assert_called_once()
            assert rc == 0

    def test_serve_flag_uses_command_subprocess(self):
        """textual-serve 调 subprocess 跑 `python -m karvyloop.cli.chat`。"""
        from textual_serve.server import Server
        with patch.object(Server, "__init__", return_value=None) as mock_init:
            with patch.object(Server, "serve") as mock_serve:
                from karvyloop.cli.chat import cmd_chat
                cmd_chat(serve=True, host="127.0.0.1", port=9997)
                # Server 应被以 command 参数构造
                assert mock_init.called
                kwargs = mock_init.call_args.kwargs
                # command 应包含 'python' 或 'karvyloop.cli.chat'
                assert "python" in kwargs.get("command", "") or "karvyloop.cli.chat" in kwargs.get("command", "")


# ---------- AC4: K 铁律源码扫描 + 安全 grep ----------

class TestAC4SecurityAndKLockScan:
    """AC4: K 铁律 + CLAUDE.md 安全地基全 grep 锁。"""

    def test_no_print_headers_or_api_key_in_workbench(self):
        """workbench/ 不应 print headers / api_key / Authorization。"""
        result = subprocess.run(
            ["grep", "-rEn", "--include=*.py", "--exclude-dir=__pycache__",
             r"print.*(headers|api_key|Authorization|ANTHROPIC_API_KEY)",
             str(ROOT / "karvyloop" / "workbench")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert not lines, f"安全违规:print 敏感字段\n{chr(10).join(lines)}"

    def test_no_real_key_handling_in_workbench(self):
        """workbench/ 不应 import / 读 config.yaml 的 api_key(留 LLM 层)。"""
        result = subprocess.run(
            ["grep", "-rEn", "--include=*.py", "--exclude-dir=__pycache__",
             r"(sk-ant-|sk-[a-z]+-[A-Za-z0-9]{10,})",
             str(ROOT / "karvyloop" / "workbench")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert not lines, f"安全违规:发现真 key\n{chr(10).join(lines)}"

    def test_no_cloud_endpoint_in_workbench(self):
        """workbench/ 不应拼 cloud endpoint / api.minimax.chat / api.anthropic.com。"""
        result = subprocess.run(
            ["grep", "-rEn", "--include=*.py", "--exclude-dir=__pycache__",
             r"(api\.minimax\.chat|api\.anthropic\.com|api\.openai\.com|https?://[a-z]+\.anthropic\.com)",
             str(ROOT / "karvyloop" / "workbench")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert not lines, f"0 LLM 违规\n{chr(10).join(lines)}"

    def test_no_apply_or_courier_in_workbench(self):
        """K4 + K5 复检:workbench/ 仍不**含** apply_ 或 Courier.send。"""
        result = subprocess.run(
            ["grep", "-rEn", "--include=*.py", "--exclude-dir=__pycache__",
             r"(apply_deontic\(|domain\.apply_\w+\(|courier\.send\(|Courier\.send\()",
             str(ROOT / "karvyloop" / "workbench")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert not lines, f"K 铁律违规\n{chr(10).join(lines)}"


# 已移除 AC5「docs/20 §6 边界已改写」—— 那是测**文档内容**(doc-lint),不是测代码;
# 测试该保证代码完整度/工程可用性,且开源时不依赖 docs/ 在场。文档边界审查另走文档审计,不进测试套件。


# ---------- AC6: textual-serve import 验证 ----------

class TestAC6TextualServeAvailable:
    """AC6: textual-serve 已装好(MIT clean-room 借)。"""

    def test_textual_serve_import(self):
        from textual_serve.server import Server
        assert Server is not None

    def test_launch_serve_imports(self):
        from karvyloop.workbench.serve import launch_serve
        assert callable(launch_serve)