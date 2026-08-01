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

    def test_click_and_move(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(ns, "_pyautogui", lambda: self._fake(calls))
        assert ns.do_click(10, 20, "left")["ok"] is True
        assert ns.do_move(5, 6)["ok"] is True
        assert ("click", {"x": 10, "y": 20, "button": "left"}) in calls
        assert ("moveTo", 5, 6) in calls

    def test_type_via_clipboard_not_typewrite(self, monkeypatch):
        """门到门实测:typewrite 被中文 IME 打乱 → do_type 改走剪贴板粘贴(copy + Ctrl+V),
        绕开 IME、原样进 Unicode,粘完还原剪贴板。"""
        calls: list = []
        monkeypatch.setattr(ns, "_pyautogui", lambda: self._fake(calls))
        clip = {"v": "orig-clipboard"}
        monkeypatch.setitem(sys.modules, "pyperclip", types.SimpleNamespace(
            paste=lambda: clip["v"], copy=lambda s: clip.__setitem__("v", s)))
        r = ns.do_type("héllo 你好")   # 含 Unicode
        assert r["ok"] is True and r["via"] == "clipboard"
        assert ("hotkey", ("ctrl", "v")) in calls          # 走粘贴
        assert not any(c[0] == "type" for c in calls)      # 不再逐字 typewrite
        assert clip["v"] == "orig-clipboard"               # 粘完还原了你原来的剪贴板

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
