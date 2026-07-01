"""test_workbench_error_display — L2Board 错误独立通道 + WidgetSnapshot.last_error(M3+ 批 8.5-A)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-A。

修 TUI "石沉大海" 缺陷 #2:无 error display。

AC 列表:
- AC4: 注入 `DriveOutcome(error="boom")` → snapshot.last_error="⚠ boom", last_drive_text=""
- AC5: L2Board.compose 在错误态含 #last-error,**不**截断
- AC6: 成功态 #last-error 不存在,#last-slow-brain 渲染原 last_drive_text
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
from karvyloop.workbench.snapshot import (  # noqa: E402
    WidgetSnapshot,
    make_snapshot_with_mainloop,
)


def _user() -> Address:
    return Address(domain_id="dom-1", role="user", agent_id="ch")


# ---------- AC4: WidgetSnapshot 字段 + App state 拆分 ----------

class TestAC4SnapshotErrorField:
    def test_widget_snapshot_has_last_error_field(self):
        """AC4: WidgetSnapshot 有 last_error 字段(默认空串)。"""
        snap = WidgetSnapshot(
            domains=(), current_domain="",
            broadcasts=(), task_count=0, pursuit_count=0, unhealthy=False,
        )
        assert snap.last_error == ""
        assert snap.last_intent == ""

    def test_make_snapshot_threads_error_through(self):
        """make_snapshot_with_mainloop 把 last_error 透传到 WidgetSnapshot。"""
        wb = WorkbenchObserver()
        snap = make_snapshot_with_mainloop(
            wb, last_error="⚠ boom", last_intent="hi",
        )
        assert snap.last_error == "⚠ boom"
        assert snap.last_intent == "hi"
        assert snap.last_drive_text == ""  # 错误时不污染慢脑槽

    def test_app_state_separates_error_and_drive_text(self):
        """AC4: App.submit_intent 失败时拆 last_error / last_drive_text 三状态。"""
        wb = WorkbenchObserver()
        app = WorkbenchApp(
            workbench=wb, user_address=_user(),
            main_loop=None,  # 触发 silent-fail 路径
        )
        # 注: AC9 在 test_workbench_silent_fail.py 详细验;这里只验"有 field"
        assert hasattr(app, "_last_error")
        assert hasattr(app, "_last_drive_text")
        assert hasattr(app, "_last_intent")


# ---------- AC5/AC6: L2Board compose 错误分支 ----------

class TestAC5L2BoardErrorBranch:
    def test_l2_board_compose_renders_error_block(self):
        """AC5: L2Board.compose 在 last_error 存在时 yield #last-error Static(不截断)。"""
        from karvyloop.workbench.widgets import L2Board
        from karvyloop.workbench.snapshot import WidgetSnapshot

        long_error = "⚠ " + ("x" * 200)  # 200 字符长错误,**不**应被截断
        snap = WidgetSnapshot(
            domains=(), current_domain="",
            broadcasts=(), task_count=0, pursuit_count=0, unhealthy=False,
            last_error=long_error,
        )
        # compose 返回 generator;收集所有 widget
        widgets = list(L2Board(snap).compose())
        # 找到 #last-error
        err_widgets = [w for w in widgets if getattr(w, "id", None) == "last-error"]
        assert len(err_widgets) == 1
        sw = err_widgets[0]
        # textual Static 存 content 在 _Static__content(名字混淆);用 render() 拿 Rich renderable
        # render() 不需要 app 上下文,可安全在无 pilot 下调
        rendered = sw.render()
        text = str(rendered)
        # 标记 [b reverse red] + 我们的 long_error 都在 — **不**截断到 80 字符
        assert "xxx" in text  # 至少 1 个 x
        # 检查 x 的数量 ≥ 200(说明没被截断)
        assert text.count("x") >= 200

    def test_l2_board_compose_no_error_block_on_success(self):
        """AC6: 成功态无 #last-error,有 #last-slow-brain。"""
        from karvyloop.workbench.widgets import L2Board
        from karvyloop.workbench.snapshot import WidgetSnapshot

        snap = WidgetSnapshot(
            domains=(), current_domain="",
            broadcasts=(), task_count=0, pursuit_count=0, unhealthy=False,
            last_drive_text="ok result",
        )
        widgets = list(L2Board(snap).compose())
        ids = [getattr(w, "id", None) for w in widgets]
        assert "last-error" not in ids
        assert "last-slow-brain" in ids


# ---------- AC: intent echo 在 L2Board ----------

class TestIntentEchoInL2Board:
    def test_l2_board_renders_last_intent(self):
        """批 8.5-A: last_intent 在 L2Board 渲染 "📤 你说: ..." 块。"""
        from karvyloop.workbench.widgets import L2Board
        from karvyloop.workbench.snapshot import WidgetSnapshot

        snap = WidgetSnapshot(
            domains=(), current_domain="",
            broadcasts=(), task_count=0, pursuit_count=0, unhealthy=False,
            last_intent="summarize hello",
        )
        widgets = list(L2Board(snap).compose())
        ids = [getattr(w, "id", None) for w in widgets]
        assert "last-intent" in ids
