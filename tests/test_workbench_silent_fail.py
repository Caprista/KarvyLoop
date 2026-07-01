"""test_workbench_silent_fail — main_loop=None 时不再静默 swallow(M3+ 批 8.5-A)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-A + CLAUDE.md §"debug 节奏"。

修 TUI "石沉大海" 的 silent-fail death-spiral:
- 修前:WorkbenchApp(main_loop=None).submit_intent("x") → logger.warning + return,UI 不可见
- 修后:显式 `_last_error` + system chat line + 重挂屏,用户立刻看到"请先 karvyloop init"

AC 列表:
- AC9: WorkbenchApp(main_loop=None).submit_intent("x") → last_error 非空 + 含 "karvyloop init"
- AC10: 同样场景下 ring buffer 含 system 角色行
- stderr banner in cli/chat.py(辅助验证)
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.domain import Address  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.workbench.app import WorkbenchApp  # noqa: E402
from karvyloop.workbench.chat_history import (  # noqa: E402
    get_chat_history,
    reset_for_test,
)


def _user() -> Address:
    return Address(domain_id="dom-1", role="user", agent_id="ch")


@pytest.fixture(autouse=True)
def _clean():
    reset_for_test()
    yield
    reset_for_test()


# ---------- AC9: last_error 非空 + 含 "karvyloop init" ----------

class TestAC9SilentFailSurfaced:
    @pytest.mark.asyncio
    async def test_submit_intent_no_main_loop_sets_last_error(self):
        """AC9: main_loop=None → submit_intent 设 _last_error,含 "karvyloop init" 引导。"""
        wb = WorkbenchObserver()
        app = WorkbenchApp(workbench=wb, user_address=_user(), main_loop=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.submit_intent("hello")
            await pilot.pause()
            # 不静默 — 错误写到 _last_error 字段
            assert app._last_error != ""
            assert "karvyloop init" in app._last_error
            # input echo 仍然要记
            assert app._last_intent == "hello"

    @pytest.mark.asyncio
    async def test_snapshot_carries_last_error(self):
        """submit_intent 失败后,_build_snapshot 含 last_error。"""
        wb = WorkbenchObserver()
        app = WorkbenchApp(workbench=wb, user_address=_user(), main_loop=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.submit_intent("hi")
            await pilot.pause()
            snap = app._build_snapshot()
            assert snap.last_error == app._last_error
            assert "karvyloop init" in snap.last_error
            assert snap.last_intent == "hi"


# ---------- AC10: system 角色行进 ring buffer ----------

class TestAC10SystemLineInBuffer:
    @pytest.mark.asyncio
    async def test_silent_fail_writes_system_line(self):
        """AC10: silent-fail 时 ring buffer 收到 1 条 system 角色。"""
        wb = WorkbenchObserver()
        app = WorkbenchApp(workbench=wb, user_address=_user(), main_loop=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.submit_intent("hi")
            await pilot.pause()
            history = get_chat_history()
            # system 行存在
            system_lines = [h for h in history if h["role"] == "system"]
            assert len(system_lines) >= 1
            assert "karvyloop init" in system_lines[0]["text"]


# ---------- stderr banner in cli/chat.py ----------

class TestCLIChatStderrBanner:
    def test_cmd_chat_no_config_writes_stderr_banner(self, capsys, tmp_path):
        """karvyloop chat 无 config 时 stderr 应显式警告(不再静默 logger.info)。"""
        from karvyloop.cli import chat as chat_module

        rc = chat_module.cmd_chat(
            config_path=tmp_path / "nonexistent.yaml",
            headless=True,
        )
        assert rc == 0
        captured = capsys.readouterr()
        # stderr 警告应含 config.yaml + karvyloop init 引导(9.4 双语:默认 en,断言 locale-neutral 子串)
        assert "config.yaml" in captured.err
        assert "karvyloop init" in captured.err
