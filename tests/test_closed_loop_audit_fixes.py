"""test_closed_loop_audit_fixes — 闭环审计(2026-07-03)BREAK×3 + HIGH 静默面×4 的回归锁。

对应断环(内部闭环审计,编号沿用):
- 断①(CRITICAL):纯新机器 `karvyloop console` 引导保存 key 必失败(config_path="")。
  修:entry.resolve_config_state_path 恒设默认路径 → fresh 环境 /api/model/save 200 + 文件落盘。
- 断②:保存 key 后 fresh 进程无 gateway/main_loop → /api/model/save 返 restart_required=True,
  前端引导页显示大字"重启生效"提示(合同测试锁 js 构建产物)。
- 断③:ACCEPT 无 handler 的卡不再被吞(test_proposal_registry 里锁);resolve_conflict
  配真 handler(决议台账 + Trace);weekly_digest ACCEPT=归档回执。
- 断④⑤⑥⑦:decision_wire 逐段 fail-loud / task_events 落 Trace 失败有声 /
  memory 落盘失败 error+可感知 / 后台协程 supervisor(崩→响→重启→连崩 3 次停手)。
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
import time
import types

from fastapi.testclient import TestClient

from karvyloop.console import build_console_app
from karvyloop.karvy.observer import WorkbenchObserver

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = ROOT / "karvyloop" / "console" / "static"


# ---------- 断①:冷启动 config_path ----------

def test_resolve_config_state_path_fresh_home(tmp_path, monkeypatch):
    """纯新机器(默认 config 不存在)→ 仍恒设默认路径,且 ~/.karvyloop 目录被创建。"""
    from karvyloop.console.entry import resolve_config_state_path
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    got = resolve_config_state_path(None, no_llm=False)
    assert got == str(tmp_path / ".karvyloop" / "config.yaml")   # 不再是 ""
    assert (tmp_path / ".karvyloop").is_dir()


def test_resolve_config_state_path_explicit_and_no_llm(tmp_path, monkeypatch):
    from karvyloop.console.entry import resolve_config_state_path
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    assert resolve_config_state_path(tmp_path / "my.yaml", no_llm=False) == str(tmp_path / "my.yaml")
    assert resolve_config_state_path(None, no_llm=True) == ""   # 显式只读模式语义不变


def test_model_save_on_fresh_env_writes_config(tmp_path):
    """fresh 环境端到端:config 文件不存在 → POST /api/model/save → 200 ok + 文件真落盘
    + restart_required=True(断②诚实面:fresh 进程无 gateway,保存≠能聊)。"""
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    cfg = tmp_path / ".karvyloop" / "config.yaml"
    app.state.config_path = str(cfg)          # 模拟断①修后的恒设默认路径(文件还不存在)
    assert not cfg.exists()
    client = TestClient(app)
    r = client.post("/api/model/save", json={
        "provider": "acme", "model_id": "acme/foo-1", "api": "openai-completions",
        "base_url": "https://api.acme.test/v1", "api_key": "sk-FAKE-DO-NOT-LEAK",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["restart_required"] is True   # fresh 进程 gateway/main_loop=None → 明说要重启
    assert cfg.exists()                        # key 真落盘(不再"永远保存失败")
    # 落盘内容可被管理视图读回
    listed = client.get("/api/model/config").json()
    assert any(m["id"] == "acme/foo-1" for m in listed["models"])


def test_registry_loads_onboarding_shaped_config():
    """断②尾巴(冷启动演练逮到的):网页引导写的 config 只有 chat 模型、无 embedding 段 →
    ModelRegistry 必须能加载(否则重启后 gateway 仍构造失败,永远到不了首次对话 +
    readiness 永远 must_setup 死循环)。embedding 没配 = 留空,真调 embed 才 fail-closed。"""
    from karvyloop.gateway.registry import ModelRegistry
    cfg = {
        "models": {"providers": {"acme": {
            "base_url": "https://api.acme.test/v1", "api_key": "sk-FAKE-DO-NOT-LEAK",
            "models": [{"id": "acme/foo-1", "api": "openai-completions", "role": "chat",
                        "context_window": 200000, "max_tokens": 8192}],
        }}},
        "agents": {"defaults": {"model": "acme/foo-1"}},
        # 注意:没有 embedding 段(引导保存的真实形态)
    }
    reg = ModelRegistry.from_config(cfg)
    assert reg.default_chat == "acme/foo-1"
    assert reg.default_embedding == ""


# ---------- 断②:前端合同(构建产物真带上提示链) ----------

def test_frontend_contract_restart_hint():
    """models_panel.js 消费 restart_required + i18n 两语都有 onb.saved_restart(en+zh)。"""
    js = (STATIC / "models_panel.js").read_text(encoding="utf-8")
    assert "restart_required" in js, "models_panel.js 没消费 restart_required(没重新 build?)"
    assert "onb.saved_restart" in js
    i18n = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert i18n.count('"onb.saved_restart"') >= 2, "onb.saved_restart 须 en+zh 两表都有"
    assert "密钥已保存" in i18n and "restart the console" in i18n


# ---------- 断③:resolve_conflict 真 handler + weekly_digest 回执 ----------

def _conflict_proposal(ts=1.0):
    from karvyloop.karvy.atoms import Proposal
    from karvyloop.karvy.proposal_registry import KIND_RESOLVE_CONFLICT
    return Proposal(
        summary="技能「批量删库」可能违反域「dom-x」的禁止项", options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.7, evidence_refs=(), habit_id=0, model_ref="", ts=ts,
        kind=KIND_RESOLVE_CONFLICT,
        payload={"role": "DBA", "domain_id": "dom-x", "skill_name": "批量删库",
                 "skill_sig": "sig-del", "rule_type": "forbid", "rule": "禁止删除生产数据库",
                 "reason": "命中关键词", "value_version": "v1",
                 "options": ["disable_in_domain", "amend_skill", "ignore"]},
    )


class _FakeTrace:
    def __init__(self):
        self.entries = []

    def append(self, e):
        self.entries.append(e)


def test_resolve_conflict_accept_has_real_effect(tmp_path):
    """resolve_conflict ACCEPT → ok=True + 决议台账落盘 + Trace 落审计 + 卡离开待决表。"""
    import json
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry

    trace = _FakeTrace()
    ledger = tmp_path / "conflict_resolutions.json"
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        main_loop=types.SimpleNamespace(trace=trace),
        conflict_resolutions_path=ledger))
    handlers = build_proposal_handlers(app)
    reg = PendingProposalRegistry()
    p = _conflict_proposal()
    reg.register(p)
    res = reg.decide(p.proposal_id, "ACCEPT", handlers=handlers)
    assert res.ok, res.detail
    assert reg.get(p.proposal_id) is None      # 有真 handler → 兑现后正常离开
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert len(data) == 1 and data[0]["skill_name"] == "批量删库" and data[0]["domain_id"] == "dom-x"
    assert len(trace.entries) == 1 and trace.entries[0].kind == "conflict_resolution"
    assert "已记录处置" in res.detail          # 回执诚实(不吹自动禁用)


def test_weekly_digest_accept_acknowledges():
    """weekly_digest ACCEPT 不再是 no-handler 内部错误串(前端承诺"接受=归档")。"""
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    from karvyloop.karvy.atoms import Proposal
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry

    handlers = build_proposal_handlers(app=None)
    reg = PendingProposalRegistry()
    p = Proposal(summary="📈 本周周报", options=("ACCEPT",), strength=0.5, evidence_refs=(),
                 habit_id=0, model_ref="", ts=1.0, kind="weekly_digest", payload={})
    reg.register(p)
    res = reg.decide(p.proposal_id, "ACCEPT", handlers=handlers)
    assert res.ok and "归档" in res.detail
    assert reg.get(p.proposal_id) is None


# ---------- 断④:decision_wire 逐段 fail-loud(一段炸不连坐,且有声) ----------

class _BoomStats:
    def record(self, d):
        raise RuntimeError("stats boom")


class _OkLog:
    def __init__(self):
        self.rows = []

    def record(self, **kw):
        self.rows.append(kw)


def test_decision_wire_segment_failure_is_loud_and_isolated(caplog):
    from karvyloop.console.decision_wire import record_decision_signals

    app = types.SimpleNamespace(state=types.SimpleNamespace(
        proposal_registry=None, taste_predictions=None,
        decision_stats=_BoomStats(), decision_log=_OkLog()))
    with caplog.at_level(logging.WARNING, logger="karvyloop.console.decision_wire"):
        record_decision_signals(app, decision="ACCEPT", proposal_id="p-audit-1", reason="r")
    # ① stats 段炸了 → 有声(带 proposal_id),不再整函数吞
    assert any("decision_stats" in r.message and "p-audit-1" in r.message
               for r in caplog.records)
    # ② 不连坐:样本仍入缓冲、decision_log 仍记
    assert len(app.state.decision_samples) == 1
    assert len(app.state.decision_log.rows) == 1


# ---------- 断⑤:任务终态落 Trace 失败有声 ----------

class _BoomTrace:
    def append(self, e):
        raise RuntimeError("trace down")


def test_task_terminal_trace_failure_warns(caplog):
    from karvyloop.console.task_events import make_task_change_sink

    app = types.SimpleNamespace(state=types.SimpleNamespace(ws_clients=set()))
    sink = make_task_change_sink(app, _BoomTrace())
    with caplog.at_level(logging.WARNING, logger="karvyloop.console.task_events"):
        sink({"id": "t-9", "status": "error", "who": "小卡", "intent": "x"})  # 不抛
    assert any("落 Trace 失败" in r.message and "t-9" in r.message for r in caplog.records)


# ---------- 断⑥:Belief 落盘失败 error + 上层可感知 ----------

class _BoomStore:
    def load_all(self):
        return []

    def save_all(self, items):
        raise OSError("disk full")


class _OkStore(_BoomStore):
    def __init__(self):
        self.saved = None

    def save_all(self, items):
        self.saved = items


def _belief(content="喜欢极简"):
    from karvyloop.schemas import Belief
    return Belief(content=content, provenance={"source": "test", "id": content},
                  freshness_ts=time.time(), scope="personal")


def test_memory_persist_failure_is_loud_and_sensed(caplog):
    from karvyloop.cognition.memory import MemoryManager

    mem = MemoryManager(store=_BoomStore())
    with caplog.at_level(logging.ERROR, logger="karvyloop.cognition.memory"):
        ok = mem.write(_belief())
    assert ok is False                        # 调用方能感知(返回值)
    assert mem.persist_error                  # 状态可查(路由/doctor 可上冒)
    assert any("Belief 落盘失败" in r.message for r in caplog.records)
    # 内存态仍在(契约不变:不阻塞主流程)
    assert any(b.content == "喜欢极简" for b in mem.index.all("personal"))


def test_memory_persist_success_clears_error():
    from karvyloop.cognition.memory import MemoryManager

    mem = MemoryManager(store=_OkStore())
    assert mem.write(_belief("ok")) is True
    assert mem.persist_error is None


# ---------- 断⑦:后台协程 supervisor(崩→响→重启;连崩 3 次停手并大声说) ----------

def test_bg_supervisor_restarts_then_stops_loudly(caplog):
    from karvyloop.console.app import _supervised_bg

    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise RuntimeError("bg crash")

    app = types.SimpleNamespace(state=types.SimpleNamespace(ws_clients=set()))
    with caplog.at_level(logging.ERROR, logger="karvyloop.console.app"):
        asyncio.run(_supervised_bg(app, "testloop", boom, max_crashes=3, base_backoff_s=0.001))
    assert calls["n"] == 3                    # 重启真发生(1 次原始 + 2 次重启),第 3 次后停
    crash_logs = [r for r in caplog.records if "意外退出" in r.message]
    assert len(crash_logs) == 3 and all("testloop" in r.message for r in crash_logs)
    assert any("停止重启" in r.message for r in caplog.records)


def test_bg_supervisor_normal_exit_no_restart(caplog):
    from karvyloop.console.app import _supervised_bg

    calls = {"n": 0}

    async def fine():
        calls["n"] += 1

    app = types.SimpleNamespace(state=types.SimpleNamespace(ws_clients=set()))
    with caplog.at_level(logging.ERROR, logger="karvyloop.console.app"):
        asyncio.run(_supervised_bg(app, "okloop", fine, base_backoff_s=0.001))
    assert calls["n"] == 1                    # 正常收尾(cancel-break 语义)不重启
    assert not [r for r in caplog.records if "意外退出" in r.message]
