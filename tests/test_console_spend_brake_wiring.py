"""test_console_spend_brake_wiring — docs/56 审计 HIGH②:花费预算刹车真接进 console。

治"造了没接线"复发(round5/9/11 各抓过一次;本轮 spend brake 只在 CLI `_bootstrap_runtime`
挂、emit_card=None → web console 里预算刹车是 no-op、达阈值永不出卡)。本文件锁 **console 真装配**:

1. cmd_console 真装配(mock uvicorn.run 捕 app):配了 `budget:` → `get_spend_budget()` 非 None
   + app.state.spend_budget 已挂 + emit_card 桥已接(不是 None,不再"造了没接线")。
2. gateway 咽喉真生效:`check_spend_budget`(gateway.complete 调用前调的那个)达 100% + pause +
   后台自动 source → 真抛 SpendBudgetExceeded(fail-loud);前台 source 永不拦。
3. 提醒 kind 登记:KIND_SPEND_BUDGET_ALERT 在 ALL_KINDS(前端/registry 认得,不当未知 kind)。
4. 零回归:未配 budget → get_spend_budget() 为 None(无刹车 = 无限,console 照旧起)。
"""
from __future__ import annotations

import pathlib
import sys
from argparse import Namespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


def _run_cmd_console(monkeypatch, tmp_path, *, budget_yaml: str):
    """走 cmd_console 真装配,返回捕获到的 app。monkeypatch resolve_runtime(假 gateway)
    + uvicorn.run(捕 app 不真起服务)+ 隔离真 HOME + 关掉 LLM pump。budget_yaml 写进
    app.state.config_path 指向的 config.yaml(spend_budget 从这里读 `budget:` 块)。"""
    import uvicorn

    import karvyloop.cli._runtime as _rt
    import karvyloop.cli.intent_pump as ip_mod
    import karvyloop.console.entry as entry_mod
    from karvyloop.gateway.registry import ModelRegistry
    from karvyloop.runtime.main_loop import MainLoop

    # 隔离真 HOME(cmd_console 往 ~/.karvyloop 写账本/对话/域/原子/角色)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(entry_mod, "_port_free", lambda *a, **k: True)
    monkeypatch.setattr(ip_mod, "_try_build_llm_client", lambda cfg, **kw: None)
    monkeypatch.setattr(ip_mod, "DEFAULT_TRACE_DB", tmp_path / ".karvyloop" / "trace_buffer.db")
    monkeypatch.setattr(ip_mod, "DEFAULT_HABIT_DB", tmp_path / ".karvyloop" / "habits.db")

    captured: list = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.append(app))

    # config.yaml 写 budget 块(spend_budget 读它);--config 显式指向它(app.state.config_path)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(budget_yaml, encoding="utf-8")

    # 假 gateway:带 .reg(ModelRegistry;spend_budget 取每模型 cost 算钱,这里 token 计不需要真价)
    class _FakeGW:
        def __init__(self):
            self.reg = None   # registry=None → 无价,按 token 计(daily_tokens 上限即可命中)

    ml = MainLoop(skills_dir=tmp_path / "skills")
    resolved = _rt.ResolvedRuntime(
        config_path=cfg_path, main_loop=ml,
        runtime_kwargs={"gateway": _FakeGW(), "model_ref": "m",
                        "workspace_root": str(tmp_path)},
        skills_dir=tmp_path / "skills",
    )
    monkeypatch.setattr(_rt, "resolve_runtime", lambda **kw: resolved)

    args = Namespace(host="127.0.0.1", port=8766, config=str(cfg_path),
                     no_browser=True, no_llm=False, lang=None)
    rc = entry_mod.cmd_console(args)
    assert rc == 0 and len(captured) == 1
    return captured[0]


# ---------------------------------------------------------------- 1. 装配:budget 真接线
def test_cmd_console_wires_spend_budget_with_emit_card(monkeypatch, tmp_path):
    from karvyloop.llm.spend_budget import get_spend_budget, register_spend_budget
    _prev = get_spend_budget()
    try:
        app = _run_cmd_console(
            monkeypatch, tmp_path,
            budget_yaml="budget:\n  daily_tokens: 1000\n  on_limit: pause\n")
        # 全局刹车已注册(gateway.complete 调用前查的就是它)
        b = get_spend_budget()
        assert b is not None, "console 没接 spend budget(造了没接线复发)"
        # app.state 也挂了一份(可查/可测)
        assert getattr(app.state, "spend_budget", None) is b
        # **emit_card 桥真接上**(审计点名:CLI 那条 emit_card=None → 达阈值永不出卡)
        assert getattr(b, "_emit_card", None) is not None, "emit_card 没接 → 达阈值永不出卡"
        # 配置真读进来了(daily_tokens=1000,pause)
        assert b.cfg.enabled and b.cfg.blocks_on_limit and b.cfg.daily_tokens == 1000
    finally:
        register_spend_budget(_prev)


# ---------------------------------------------------------------- 2. gateway 咽喉真生效
def test_wired_budget_blocks_background_at_choke_point(monkeypatch, tmp_path):
    """接线后:模块级 check_spend_budget(gateway.complete 前调的那个)达 100% + 后台 source
    → 真抛 SpendBudgetExceeded;前台 source 永不拦。证明刹车在**真咽喉**上有牙。"""
    from karvyloop.llm.spend_budget import (
        SpendBudgetExceeded, check_spend_budget, get_spend_budget, register_spend_budget)
    from karvyloop.llm.token_ledger import get_ledger

    _prev = get_spend_budget()
    try:
        _run_cmd_console(
            monkeypatch, tmp_path,
            budget_yaml="budget:\n  daily_tokens: 1000\n  on_limit: pause\n")
        assert get_spend_budget() is not None
        # 账本(console 装配时注册的那本)灌一笔超过日上限的用量
        led = get_ledger()
        assert led is not None
        led.record(source="consolidate", model="m", input=800, output=800)  # 1600 > 1000
        # 后台自动 source(在 AUTOMATIC_SOURCES 里)→ 达 100% + pause → fail-loud
        with pytest.raises(SpendBudgetExceeded):
            check_spend_budget("consolidate")
        # 前台 source(用户正在等的 drive,默认 unknown/前台)→ **永不拦**(至多告警)
        check_spend_budget("unknown")   # 不抛即通过
    finally:
        register_spend_budget(_prev)


# ---------------------------------------------------------------- 3. 提醒 kind 已登记
def test_spend_budget_alert_kind_registered():
    from karvyloop.karvy.proposal_registry import ALL_KINDS, KIND_SPEND_BUDGET_ALERT
    assert KIND_SPEND_BUDGET_ALERT == "spend_budget_alert"
    assert KIND_SPEND_BUDGET_ALERT in ALL_KINDS
    # 与 spend_budget.build_card 产出的 kind 对齐(否则前端/registry 认不出)
    from karvyloop.llm.config_budget import spend_budget_config_from_dict
    from karvyloop.llm.spend_budget import SpendBudget
    cfg = spend_budget_config_from_dict({"budget": {"daily_tokens": 10, "on_limit": "warn"}})
    card = SpendBudget(cfg).build_card(
        {"used": 8, "limit": 10, "unit": "tokens", "dimension": "daily_tokens",
         "ratio": 0.8, "action": "warn"})
    assert card["kind"] == KIND_SPEND_BUDGET_ALERT


# ---------------------------------------------------------------- 4. 零回归:未配 budget → 无刹车
def test_no_budget_config_leaves_no_brake(monkeypatch, tmp_path):
    from karvyloop.llm.spend_budget import get_spend_budget, register_spend_budget
    _prev = get_spend_budget()
    try:
        _run_cmd_console(monkeypatch, tmp_path, budget_yaml="# no budget block\nfoo: bar\n")
        # 未配 budget → 不注册刹车(无限,console 照旧起)
        assert get_spend_budget() is None, "未配 budget 却装了刹车(破坏 0 回归)"
    finally:
        register_spend_budget(_prev)
