"""test_h2a_mode_switch — H2A 阻塞/非阻塞开关(Hardy 2026-08-20「拍板功能可以设置」)。

C 方向(3004b2d)建了机制,本批装开关:卡上「⚡ 先跑」翻转端点 + 控制台默认模式。

AC:
- AC1: registry.set_mode 翻转(冻结 Proposal 换 mode,meta 不动);auto_decided 卡拒翻回
- AC2: /api/proposals/mode 翻非阻塞 → 守卫过 → 立即推进 + auto_decided + dispatch 回执
- AC3: 高危 kind / 无 handler / 不可逆语义 → 拒翻(ok=False + reason,卡仍 blocking pending)
- AC4: 翻回 blocking(未推进)成功;已 auto_decided → 拒翻
- AC5: default_mode GET/POST + config.yaml 持久化
- AC6: 默认 non_blocking 时 broadcast 对普通卡自动推进、对高危卡仍阻塞(守卫不松)
- AC7: 幂等:重复翻非阻塞(已 auto_decided)→ ok already,不二次 dispatch
"""
from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from karvyloop.config_h2a_mode import (
    read_h2a_default_mode, write_h2a_default_mode)
from karvyloop.console import broadcast_proposal, build_console_app
from karvyloop.karvy.atoms import Proposal
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.karvy.proposal_registry import (
    KIND_CRYSTALLIZE_SKILL, PendingProposalRegistry)


def _mk(summary="做个小任务", kind=KIND_CRYSTALLIZE_SKILL, payload=None, mode="blocking"):
    return Proposal(
        summary=summary, options=("ACCEPT", "DEFER", "REJECT"), strength=0.8,
        evidence_refs=(), habit_id=1, model_ref="m", ts=time.time(),
        kind=kind, payload=payload or {}, mode=mode)


def _app(with_handler=True):
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.proposal_registry = PendingProposalRegistry()
    calls = []
    if with_handler:
        app.state.proposal_handlers = {
            KIND_CRYSTALLIZE_SKILL: lambda pr: (calls.append(pr.proposal_id), (True, "done"))[1],
        }
    else:
        app.state.proposal_handlers = {}
    return app, calls


# ---- AC1: registry.set_mode ----
def test_set_mode_flips_and_keeps_meta():
    reg = PendingProposalRegistry()
    p = _mk()
    reg.register(p)
    assert reg.set_mode(p.proposal_id, "non_blocking")
    assert reg.get(p.proposal_id).mode == "non_blocking"
    assert reg.set_mode(p.proposal_id, "blocking")
    assert reg.get(p.proposal_id).mode == "blocking"
    # 非法 mode / 未知 id → False
    assert not reg.set_mode(p.proposal_id, "yolo")
    assert not reg.set_mode("nope", "non_blocking")


def test_set_mode_refuses_flipback_after_auto_decided():
    reg = PendingProposalRegistry()
    p = _mk()
    reg.register(p)
    reg.auto_decide(p.proposal_id)
    assert not reg.set_mode(p.proposal_id, "blocking")   # 已推进 → 拒翻回
    assert reg.get(p.proposal_id).mode == "blocking"     # 原 mode 不动


# ---- AC2: 端点翻非阻塞 → 立即推进 ----
def test_endpoint_flip_executes_and_marks_auto():
    app, calls = _app()
    p = _mk()
    app.state.proposal_registry.register(p)
    client = TestClient(app)
    r = client.post("/api/proposals/mode", json={"proposal_id": p.proposal_id, "mode": "non_blocking"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["auto_decided"] and body["dispatch"]["ok"]
    assert calls == [p.proposal_id]                       # 真推进了
    assert app.state.proposal_registry.is_auto_decided(p.proposal_id)
    assert app.state.proposal_registry.get(p.proposal_id) is not None   # 留 pending 等追拍


# ---- AC3: 守卫拒翻 ----
def test_endpoint_flip_high_risk_refused():
    app, calls = _app()
    app.state.proposal_handlers = {"outbound_draft": lambda pr: (calls.append(1), (True, "x"))[1]}
    p = _mk(summary="发邮件给客户", kind="outbound_draft")
    app.state.proposal_registry.register(p)
    client = TestClient(app)
    r = client.post("/api/proposals/mode", json={"proposal_id": p.proposal_id, "mode": "non_blocking"})
    body = r.json()
    assert not body["ok"] and "high_risk" in body["reason"]
    assert calls == []                                     # 没推进
    assert not app.state.proposal_registry.is_auto_decided(p.proposal_id)


def test_endpoint_flip_no_handler_refused():
    app, calls = _app(with_handler=False)
    p = _mk()
    app.state.proposal_registry.register(p)
    client = TestClient(app)
    r = client.post("/api/proposals/mode", json={"proposal_id": p.proposal_id, "mode": "non_blocking"})
    body = r.json()
    assert not body["ok"] and body["reason"] == "no_handler"
    assert calls == []


def test_endpoint_flip_not_found():
    app, _ = _app()
    client = TestClient(app)
    r = client.post("/api/proposals/mode", json={"proposal_id": "nope", "mode": "non_blocking"})
    assert r.json()["reason"] == "not_found"


# ---- AC4: 翻回阻塞 ----
def test_endpoint_flip_back_to_blocking():
    app, _ = _app()
    p = _mk(mode="non_blocking")   # 建了非阻塞但还没推进(直接 register 不经 broadcast)
    app.state.proposal_registry.register(p)
    client = TestClient(app)
    r = client.post("/api/proposals/mode", json={"proposal_id": p.proposal_id, "mode": "blocking"})
    assert r.json()["ok"]
    assert app.state.proposal_registry.get(p.proposal_id).mode == "blocking"


def test_endpoint_flip_back_after_auto_refused():
    app, calls = _app()
    p = _mk()
    app.state.proposal_registry.register(p)
    client = TestClient(app)
    client.post("/api/proposals/mode", json={"proposal_id": p.proposal_id, "mode": "non_blocking"})
    r = client.post("/api/proposals/mode", json={"proposal_id": p.proposal_id, "mode": "blocking"})
    assert not r.json()["ok"] and r.json()["reason"] == "already_auto_decided"


# ---- AC5: 默认模式端点 + 持久化 ----
def test_default_mode_endpoint_and_persistence(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    assert write_h2a_default_mode("non_blocking", config_path=cfg)
    assert read_h2a_default_mode(config_path=cfg) == "non_blocking"
    # 非法值拒写;缺省 blocking
    assert not write_h2a_default_mode("yolo", config_path=cfg)
    assert read_h2a_default_mode(config_path=tmp_path / "missing.yaml") == "blocking"
    # 端点(走真 config 路径;monkeypatch 到 tmp 防污染真配置)
    monkeypatch.setattr("karvyloop.config_h2a_mode._default_path", lambda: cfg)
    app, _ = _app()
    client = TestClient(app)
    assert client.get("/api/proposals/default_mode").json()["mode"] == "non_blocking"
    r = client.post("/api/proposals/default_mode", json={"mode": "blocking"})
    assert r.json()["ok"] and read_h2a_default_mode(config_path=cfg) == "blocking"


# ---- AC6: 默认 non_blocking 时广播咽喉自动推进/高危仍阻塞 ----
def test_broadcast_default_non_blocking_auto_executes(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    write_h2a_default_mode("non_blocking", config_path=cfg)
    monkeypatch.setattr("karvyloop.config_h2a_mode._default_path", lambda: cfg)
    app, calls = _app()
    p = _mk()   # 生产者没显式带 mode(缺省 blocking)→ 默认模式接管
    asyncio.run(broadcast_proposal(app, p))
    assert calls == [p.proposal_id]
    assert app.state.proposal_registry.is_auto_decided(p.proposal_id)


def test_broadcast_default_non_blocking_high_risk_still_blocks(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    write_h2a_default_mode("non_blocking", config_path=cfg)
    monkeypatch.setattr("karvyloop.config_h2a_mode._default_path", lambda: cfg)
    app, calls = _app()
    app.state.proposal_handlers = {"outbound_draft": lambda pr: (calls.append(1), (True, "x"))[1]}
    p = _mk(summary="发邮件", kind="outbound_draft")
    asyncio.run(broadcast_proposal(app, p))
    assert calls == []                                     # 高危卡守卫强制阻塞
    assert not app.state.proposal_registry.is_auto_decided(p.proposal_id)


def test_broadcast_default_blocking_unchanged(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    write_h2a_default_mode("blocking", config_path=cfg)   # 默认(现状)= 不自动推进
    monkeypatch.setattr("karvyloop.config_h2a_mode._default_path", lambda: cfg)
    app, calls = _app()
    p = _mk()
    asyncio.run(broadcast_proposal(app, p))
    assert calls == []
    assert not app.state.proposal_registry.is_auto_decided(p.proposal_id)


# ---- AC7: 幂等 ----
def test_endpoint_flip_idempotent_no_double_dispatch():
    app, calls = _app()
    p = _mk()
    app.state.proposal_registry.register(p)
    client = TestClient(app)
    client.post("/api/proposals/mode", json={"proposal_id": p.proposal_id, "mode": "non_blocking"})
    r = client.post("/api/proposals/mode", json={"proposal_id": p.proposal_id, "mode": "non_blocking"})
    assert r.json()["ok"] and r.json().get("already")
    assert calls == [p.proposal_id]   # 只推进一次
