"""test_workbench_persistent_log — chat_history ring buffer 持久 + App.get_chat_history 暴露(M3+ 批 8.5-A)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-A。

修 TUI "石沉大海" 缺陷 #3:无 persistent log。

AC 列表:
- AC7: 3 次提交 → get_chat_history() 返 3 条
- AC8: 第 501 次 push → buffer 截断到 500
- WorkbenchApp.get_chat_history() 暴露给 8.5-C console
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
    push_chat_log_line,
    reset_for_test,
)


def _user() -> Address:
    return Address(domain_id="dom-1", role="user", agent_id="ch")


@pytest.fixture(autouse=True)
def _clean():
    reset_for_test()
    yield
    reset_for_test()


# ---------- AC7: 3 次 push → 3 条 ----------

class TestAC7ThreePushesReturnThree:
    def test_three_pushes_three_entries(self):
        push_chat_log_line("user", "a", "t1")
        push_chat_log_line("agent", "b", "t2")
        push_chat_log_line("user", "c", "t3")
        history = get_chat_history()
        assert len(history) == 3
        assert [h["text"] for h in history] == ["a", "b", "c"]
        assert [h["role"] for h in history] == ["user", "agent", "user"]


# ---------- AC8: 500 cap 截断 ----------

class TestAC8MaxlenTrims:
    def test_500_cap_drops_oldest(self):
        """AC8: push 第 501 次时,buffer 截断到 500,最旧被丢。"""
        for i in range(501):
            push_chat_log_line("user", str(i), f"t{i}")
        history = get_chat_history()
        assert len(history) == 500
        # 最旧 0, 1, ..., 500 中的 0 被丢;1, 2 在顶
        assert history[0]["text"] == "1"
        assert history[-1]["text"] == "500"


# ---------- WorkbenchApp 暴露 ----------

class TestAppExposesChatHistory:
    def test_app_get_chat_history_returns_list(self):
        """批 8.5-A: WorkbenchApp.get_chat_history() 暴露给 8.5-C console。"""
        wb = WorkbenchObserver()
        app = WorkbenchApp(workbench=wb, user_address=_user())
        push_chat_log_line("user", "hello", "t1")
        push_chat_log_line("agent", "hi", "t2")
        history = app.get_chat_history()
        assert len(history) == 2
        assert history[0]["text"] == "hello"
        assert history[1]["text"] == "hi"

    def test_app_push_chat_log_line_writes_to_buffer(self):
        """App.push_chat_log_line("user", x) → ring buffer 收到 1 条。"""
        wb = WorkbenchObserver()
        app = WorkbenchApp(workbench=wb, user_address=_user())
        app.push_chat_log_line("user", "via app")
        assert len(get_chat_history()) == 1
        assert get_chat_history()[0]["text"] == "via app"
        # post_message 在 headless 下不会抛(已 try/except)
