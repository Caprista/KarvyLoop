"""docs/99 刀2:computer-use 真机 E2E 驱动脚本 —— 可导入 + 事件摘要纯函数锁。

真跑需 Linux 图形桌面(headless/CI/Windows 都跑不了),不在自动测里跑;这里只锁脚本**能导入**、
参数面在、事件→人话摘要正确(回看日志靠它),防脚本悄悄烂在那儿。
"""
from __future__ import annotations

from types import SimpleNamespace as N

import pytest

from karvyloop.cli.computer_use_e2e import main, summarize_event


class TestSummarizeEvent:
    def test_tool_call(self):
        ev = type("ToolCallEvent", (),
                  {"block": N(name="mcp_computer_use_get_app_state", input={"app": "Files"})})()
        line = summarize_event(ev)
        assert line.startswith("→ CALL mcp_computer_use_get_app_state")
        assert "Files" in line

    def test_tool_result_screenshot_marked(self):
        """slice-A 的回看信号:结果带 images → 标"截图真到 planner"。"""
        r = N(name="mcp_computer_use_screenshot", is_error=False, images=[{"data": "X"}], content="ok")
        line = summarize_event(type("ToolResultEvent", (), {"result": r})())
        assert "screenshot delivered" in line and "[ok]" in line

    def test_tool_result_no_image_no_marker(self):
        r = N(name="mcp_computer_use_list_windows", is_error=False, images=None, content="[]")
        line = summarize_event(type("ToolResultEvent", (), {"result": r})())
        assert "[ok]" in line and "screenshot delivered" not in line

    def test_tool_result_error(self):
        r = N(name="mcp_computer_use_click", is_error=True, images=None,
              error_reason="capability_denied", content=None)
        line = summarize_event(type("ToolResultEvent", (), {"result": r})())
        assert "[ERROR]" in line and "capability_denied" in line and "screenshot" not in line

    def test_text_and_terminal(self):
        assert summarize_event(type("TextEvent", (), {"text": "hi"})()).startswith("💭")
        assert "completed" in summarize_event(type("TerminalEvent", (), {"reason": "completed"})())

    def test_empty_text_and_unknown_event_are_none(self):
        assert summarize_event(type("TextEvent", (), {"text": "  "})()) is None
        assert summarize_event(type("ThinkingEvent", (), {"text": "x"})()) is None


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0
    assert "computer-use" in capsys.readouterr().out


class TestEnsureYdotoolSocket:
    """回归锁:_ensure_ydotool_socket 用到 os —— 缺 import os 会在**运行期**炸(纯 import
    smoke 逮不到,真机 E2E 才现形)。这两测直接调它,把运行期路径覆盖住。"""

    def test_no_socket_no_crash(self, monkeypatch):
        from karvyloop.cli.computer_use_e2e import _ensure_ydotool_socket
        monkeypatch.delenv("YDOTOOL_SOCKET", raising=False)
        _ensure_ydotool_socket(lambda s: None)   # 无真 socket(CI/Win)→ 优雅返回不抛

    def test_respects_existing_env(self, monkeypatch):
        import os as _os

        from karvyloop.cli.computer_use_e2e import _ensure_ydotool_socket
        monkeypatch.setenv("YDOTOOL_SOCKET", "/preset/sock")
        _ensure_ydotool_socket(lambda s: None)
        assert _os.environ["YDOTOOL_SOCKET"] == "/preset/sock"   # 已设 → 不覆盖
