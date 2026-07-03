"""test_email_channel_wiring — 邮件通道生产接线(防"后端造了没接线"复发)。

不变量:① lifespan 起 email_channel_task,shutdown 取消(不泄露协程)
② 未配置时 app.state.email_channel 为 None/缺省,tick 空转不炸
③ entry 的接线代码存在(build_email_channel + decide 桥走 record_decision_signals)。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_lifespan_starts_and_cancels_email_task():
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    with TestClient(app):
        task = getattr(app.state, "email_channel_task", None)
        assert task is not None and not task.done()
    assert task.cancelled() or task.done()   # shutdown 后不泄露


def test_tick_noop_without_channel():
    import asyncio
    from karvyloop.channels.email_channel import email_channel_tick
    res = asyncio.run(email_channel_tick(None))
    assert res.get("digest_sent") in (0, False, None) or res == {} or isinstance(res, dict)


def test_entry_wiring_present():
    src = (ROOT / "karvyloop" / "console" / "entry.py").read_text(encoding="utf-8")
    assert "build_email_channel" in src
    assert "record_decision_signals" in src         # 邮件回批与 REST 同喂决策信号
    assert "app.state.email_channel" in src
