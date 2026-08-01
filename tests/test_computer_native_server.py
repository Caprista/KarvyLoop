"""docs/99 Windows/macOS computer-use native plumbing —— 平台原语纯函数(mock 输入)+ server 可建.

真跑要真桌面(动鼠标键盘、截真屏),不在自动测;这里锁:原语调对 pyautogui、按键别名/chord 拆对、
缺依赖 fail-loud 指路、server 注册出与 Linux 对齐的 mcp_computer_use_* 工具名。
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from karvyloop.computer import native_server as ns


class TestInputPrimitives:
    def _fake(self, calls):
        return types.SimpleNamespace(
            FAILSAFE=True,
            click=lambda **k: calls.append(("click", k)),
            moveTo=lambda x, y: calls.append(("moveTo", x, y)),
            typewrite=lambda t, interval=0: calls.append(("type", t)),
            press=lambda k: calls.append(("press", k)),
            hotkey=lambda *a: calls.append(("hotkey", a)))

    def test_click_move_type(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(ns, "_pyautogui", lambda: self._fake(calls))
        assert ns.do_click(10, 20, "left")["ok"] is True
        assert ns.do_move(5, 6)["ok"] is True
        assert ns.do_type("hi")["ok"] is True
        assert ("click", {"x": 10, "y": 20, "button": "left"}) in calls
        assert ("moveTo", 5, 6) in calls and ("type", "hi") in calls

    def test_press_single_and_chord_and_alias(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(ns, "_pyautogui", lambda: self._fake(calls))
        ns.do_press("enter")
        ns.do_press("ctrl+c")
        ns.do_press("super")    # 别名 → pyautogui 的 win
        assert ("press", "enter") in calls
        assert ("hotkey", ("ctrl", "c")) in calls
        assert ("press", "win") in calls

    def test_press_empty_is_honest_fail(self, monkeypatch):
        monkeypatch.setattr(ns, "_pyautogui", lambda: types.SimpleNamespace(FAILSAFE=True))
        assert ns.do_press("")["ok"] is False

    def test_key_aliases(self):
        assert ns._norm_key("Super") == "win"
        assert ns._norm_key("Return") == "enter"
        assert ns._norm_key("Ctrl") == "ctrl"

    def test_missing_pyautogui_fail_loud(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pyautogui", None)   # import → ImportError
        with pytest.raises(RuntimeError) as ei:
            ns._pyautogui()
        assert "karvyloop[computer]" in str(ei.value)


class TestServer:
    def test_build_registers_tools_aligned_with_linux(self):
        srv = ns.build_server()
        tools = asyncio.run(srv.list_tools())
        names = sorted(t.name for t in tools)
        assert names == ["click", "get_screen_size", "move", "press_key", "screenshot", "type_text"]
        # server 名 = computer_use → 工具即 mcp_computer_use_*(与 computer-use-linux 对齐,刀1 gate 认得)
        assert srv.name == "computer_use"
