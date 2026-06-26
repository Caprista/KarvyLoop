"""test_cli_intent_pump — 小卡 IntentAnalyst → console 接线(M3+ 拍 9.0e)。

设计:docs/20 §3.10 + plans/snoopy-singing-sunbeam.md。

AC 列表:
- AC1-AC3: build_proposal_pump 拼装(无 config → llm off / 有 fake provider → llm on / close 幂等)
- AC4-AC5: 端到端(写 trace → boot pump → 推 h2a_proposal)+ LLM off 时静默
- AC6-AC7: daily 调度(lifespan 起 task / interval=None 不起)+ shutdown close 调用
- AC8: K5/K7 铁律(intent_pump 不碰 decision_to_envelope / Courier)
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cli.intent_pump import build_proposal_pump, PumpBundle  # noqa: E402
from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.fastbrain.trace_habit import Habit  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


# ---- AC1-AC3: build_proposal_pump 拼装 ----


def test_build_pump_no_config_llm_off(tmp_path) -> None:
    """无 config → llm_client=None → has_llm=False(优雅退化)。"""
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    bundle = build_proposal_pump(
        app,
        workbench=WorkbenchObserver(),
        config_path=tmp_path / "nonexistent.yaml",
        trace_db=tmp_path / "t.db",
        habit_db=tmp_path / "h.db",
    )
    try:
        assert isinstance(bundle, PumpBundle)
        assert bundle.has_llm is False  # 无 config → LLM off
        assert bundle.pump is not None
    finally:
        bundle.close()


def test_build_pump_close_idempotent(tmp_path) -> None:
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    bundle = build_proposal_pump(
        app,
        workbench=WorkbenchObserver(),
        config_path=tmp_path / "none.yaml",
        trace_db=tmp_path / "t.db",
        habit_db=tmp_path / "h.db",
    )
    bundle.close()
    bundle.close()  # 第二次不报错


def test_build_pump_opens_real_stores(tmp_path) -> None:
    """trace_db / habit_db 真被打开(可写)。"""
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    bundle = build_proposal_pump(
        app,
        workbench=WorkbenchObserver(),
        config_path=tmp_path / "none.yaml",
        trace_db=tmp_path / "t.db",
        habit_db=tmp_path / "h.db",
    )
    try:
        # trace_index 可写
        bundle.trace_index.append_summary({"kind": "intent", "text": "hi"})
        assert bundle.trace_index.summary_bytes() > 0
        # habit_store 可写
        bundle.habit_store.upsert("用户习惯 X", strength=0.5)
        assert bundle.habit_store.count() == 1
    finally:
        bundle.close()


# ---- AC4-AC5: 端到端 ----


def test_end_to_end_boot_pushes_proposal(tmp_path, monkeypatch) -> None:
    """写 signal trace → pump.boot → 推 h2a_proposal(用 fake LLM 经 monkeypatch)。"""
    # monkeypatch _try_build_llm_client 返一个凝强 habit 的假 client
    import karvyloop.cli.intent_pump as ip_mod

    class _FakeLlmClient:
        def chat(self, model, messages, *, temperature=0.3):
            return '[{"pattern": "用户常驻足看衣服 — 可能想试穿", "strength": 0.88}]'

    monkeypatch.setattr(ip_mod, "_try_build_llm_client", lambda cfg, **kw: _FakeLlmClient())

    workbench = WorkbenchObserver()
    app = build_console_app(workbench=workbench, main_loop=None)
    bundle = build_proposal_pump(
        app,
        workbench=workbench,
        trace_db=tmp_path / "t.db",
        habit_db=tmp_path / "h.db",
    )
    app.state.proposal_pump = bundle.pump
    try:
        assert bundle.has_llm is True
        # 写一条 signal 摘要
        bundle.trace_index.append_summary({"kind": "intent", "text": "看衣服"})
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # snapshot
            ws.send_json({"type": "propose", "payload": {}})
            msg = ws.receive_json()
            assert msg["type"] == "h2a_proposal"
            assert "试穿" in msg["payload"]["summary"]
            assert msg["payload"]["strength"] == 0.88
    finally:
        bundle.close()


def test_end_to_end_llm_off_stays_silent(tmp_path) -> None:
    """无 LLM(无 config)→ analyzer 静默 → propose 返 null payload。"""
    workbench = WorkbenchObserver()
    app = build_console_app(workbench=workbench, main_loop=None)
    bundle = build_proposal_pump(
        app,
        workbench=workbench,
        config_path=tmp_path / "none.yaml",
        trace_db=tmp_path / "t.db",
        habit_db=tmp_path / "h.db",
    )
    app.state.proposal_pump = bundle.pump
    try:
        bundle.trace_index.append_summary({"kind": "intent", "text": "看衣服"})
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "propose", "payload": {}})
            msg = ws.receive_json()
            assert msg["type"] == "h2a_proposal"
            assert msg["payload"] is None  # 无 LLM → 静默
            assert msg["sent"] == 0
    finally:
        bundle.close()


# ---- AC6-AC7: daily 调度 + shutdown close ----


def test_daily_scheduler_starts_when_interval_set(tmp_path, monkeypatch) -> None:
    """lifespan 在 interval 设了时起 daily_task;TestClient 上下文触发 lifespan。"""
    import karvyloop.cli.intent_pump as ip_mod
    monkeypatch.setattr(ip_mod, "_try_build_llm_client", lambda cfg, **kw: None)

    workbench = WorkbenchObserver()
    app = build_console_app(workbench=workbench, main_loop=None)
    bundle = build_proposal_pump(
        app, workbench=workbench,
        config_path=tmp_path / "none.yaml",
        trace_db=tmp_path / "t.db", habit_db=tmp_path / "h.db",
    )
    app.state.proposal_pump = bundle.pump
    app.state.proposal_close = bundle.close
    app.state.proposal_daily_interval_s = 3600  # 1h(不会在测试内 fire)
    # TestClient 上下文 = 触发 lifespan startup + shutdown
    with TestClient(app) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        # daily_task 已起
        assert getattr(app.state, "daily_task", None) is not None
    # 退出 with → lifespan shutdown → close 调用(daily_task cancel)
    # bundle.close 已被 lifespan 调,这里再调验证幂等
    bundle.close()


def test_no_daily_scheduler_when_interval_none(tmp_path) -> None:
    """interval=None → 不起 daily_task。"""
    workbench = WorkbenchObserver()
    app = build_console_app(workbench=workbench, main_loop=None)
    bundle = build_proposal_pump(
        app, workbench=workbench,
        config_path=tmp_path / "none.yaml",
        trace_db=tmp_path / "t.db", habit_db=tmp_path / "h.db",
    )
    app.state.proposal_pump = bundle.pump
    # interval 不设(默认 None)
    try:
        with TestClient(app) as client:
            client.get("/healthz")
            assert getattr(app.state, "daily_task", None) is None
    finally:
        bundle.close()


def test_shutdown_calls_proposal_close(tmp_path, monkeypatch) -> None:
    """lifespan shutdown 调 app.state.proposal_close。"""
    import karvyloop.cli.intent_pump as ip_mod
    monkeypatch.setattr(ip_mod, "_try_build_llm_client", lambda cfg, **kw: None)

    closed = {"called": False}
    workbench = WorkbenchObserver()
    app = build_console_app(workbench=workbench, main_loop=None)
    bundle = build_proposal_pump(
        app, workbench=workbench,
        config_path=tmp_path / "none.yaml",
        trace_db=tmp_path / "t.db", habit_db=tmp_path / "h.db",
    )

    def _close_spy():
        closed["called"] = True
        bundle.close()

    app.state.proposal_pump = bundle.pump
    app.state.proposal_close = _close_spy
    with TestClient(app):
        pass  # 进 = startup, 出 = shutdown
    assert closed["called"] is True


# ---- AC8: K5/K7 铁律 ----


def test_intent_pump_does_not_touch_decision_factory() -> None:
    """K5:intent_pump 是接线层,代码不碰 decision_to_envelope / Courier(docstring 提及允许)。"""
    import karvyloop.cli.intent_pump as mod
    import inspect

    src = inspect.getsource(mod)
    # 剔注释 + triple-quote docstring 块后再 grep
    lines = []
    in_doc = False
    for line in src.splitlines():
        s = line.strip()
        if s.startswith('"""') or s.startswith("'''"):
            if s.count('"""') >= 2 or s.count("'''") >= 2:
                continue
            in_doc = not in_doc
            continue
        if in_doc or s.startswith("#"):
            continue
        lines.append(line)
    code = "\n".join(lines)
    assert "decision_to_envelope" not in code, "K5 违反 — 接线层碰决策工厂"
    assert "Courier" not in code, "K7 违反 — 接线层引 Courier"
