"""test_line_hide — 2c:X 掉 = 隐藏不删;私聊小卡不可隐藏;重开自动恢复显示。"""
from __future__ import annotations

import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.console.routes import _is_line_hidden  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def _client():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None, runtime_kwargs={})
    return app, TestClient(app)


def test_hide_then_unhide():
    app, c = _client()
    r = c.post("/api/line/hide", json={"domain_id": "d1", "role": "engineer", "agent_id": "a1"}).json()
    assert r["ok"] is True and r["hidden"] is True
    assert _is_line_hidden(app, "d1", "engineer", "a1") is True
    # 恢复
    r2 = c.post("/api/line/hide", json={"domain_id": "d1", "role": "engineer", "agent_id": "a1", "hidden": False}).json()
    assert r2["ok"] is True
    assert _is_line_hidden(app, "d1", "engineer", "a1") is False


def test_karvy_private_cannot_be_hidden():
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
    app, c = _client()
    r = c.post("/api/line/hide", json={"domain_id": KARVY_WORLD_DOMAIN, "role": "observer", "agent_id": "karvy"}).json()
    assert r["ok"] is False and r["reason"] == "pinned"
    assert _is_line_hidden(app, KARVY_WORLD_DOMAIN, "observer", "karvy") is False


def test_workflow_line_hidable():
    app, c = _client()
    assert c.post("/api/line/hide", json={"domain_id": "d1", "role": "workflow", "agent_id": "run9"}).json()["ok"]
    assert _is_line_hidden(app, "d1", "workflow", "run9") is True
