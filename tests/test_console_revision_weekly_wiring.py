"""test_console_revision_weekly_wiring — 后端接线波的接线契约门。

治"后端造了没接线"复发(test_no_unwired_backend_endpoints 同病):
技能修订(29112e9)与周报卡(0d5d79e)核心模块已落地、测试全绿,但生产接线是另一回事 ——
本文件锁**真装配路径**:

1. cmd_console 真装配(mock uvicorn.run 捕 app):
   - main_loop._revision_judge 已注入(async judge_revision → sync 桥)
   - main_loop._revision_proposal_sink == app.state.proposal_registry.register
   - app.state.proposal_handlers 表有 KIND_REVISE_SKILL
2. handler 真兑现:registry.decide(ACCEPT) 走 handlers 表 → SKILL.md 真被改 + Changelog。
3. daily tick 功能路径:短 interval 真跑 _daily_loop → revision_review 被调 +
   周报卡真进待决议表(weekly_digest_tick 挂上了,不是又一个孤儿函数)。
4. 静态契约:revision_review / weekly_digest_tick 在 _daily_loop 的 idle continue 之后
   (test_idle_zero_llm 同款 —— 没事发生的夜里不烧任何慢侧工作)。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time
import types

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.trace import TraceStore  # noqa: E402
from karvyloop.console.app import build_console_app  # noqa: E402
from karvyloop.crystallize import KIND_REVISE_SKILL  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.karvy.proposal_registry import PendingProposalRegistry  # noqa: E402


# ---------------------------------------------------------------- 1+2. cmd_console 真装配

def test_cmd_console_wires_revision_judge_sink_and_handler(monkeypatch, tmp_path):
    """走 cmd_console 真装配:judge/sink 注入 + handlers 表有 KIND_REVISE_SKILL +
    ACCEPT 真兑现(SKILL.md 被改)。"""
    from argparse import Namespace

    import karvyloop.cli._runtime as _rt
    import karvyloop.cli.intent_pump as ip_mod
    import karvyloop.console.entry as entry_mod
    import uvicorn
    from karvyloop.llm.token_ledger import get_ledger, register_ledger
    from karvyloop.runtime.main_loop import MainLoop

    # 隔离真 HOME(cmd_console 往 ~/.karvyloop 写账本/对话/域/原子/角色)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(entry_mod, "_port_free", lambda *a, **k: True)
    monkeypatch.setattr(ip_mod, "_try_build_llm_client", lambda cfg, **kw: None)
    # DEFAULT_*_DB 在 import 时就按真 HOME 定值 → 必须补丁,否则往用户真 ~/.karvyloop 写 sqlite
    monkeypatch.setattr(ip_mod, "DEFAULT_TRACE_DB", tmp_path / ".karvyloop" / "trace_buffer.db")
    monkeypatch.setattr(ip_mod, "DEFAULT_HABIT_DB", tmp_path / ".karvyloop" / "habits.db")

    captured: list = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.append(app))

    # 假 runtime:真 MainLoop + 假 gateway(判 `_gw is not None` 走接线分支即可)
    ml = MainLoop(skills_dir=tmp_path / "skills")
    resolved = _rt.ResolvedRuntime(
        config_path=tmp_path / "config.yaml", main_loop=ml,
        runtime_kwargs={"gateway": object(), "model_ref": "m",
                        "workspace_root": str(tmp_path)},
        skills_dir=tmp_path / "skills",
    )
    monkeypatch.setattr(_rt, "resolve_runtime", lambda **kw: resolved)

    args = Namespace(host="127.0.0.1", port=8766, config=None,
                     no_browser=True, no_llm=False, lang=None)
    _prev = get_ledger()
    try:
        rc = entry_mod.cmd_console(args)
        assert rc == 0 and len(captured) == 1
        app = captured[0]

        # 断线①:judge + sink 已注入 MainLoop(慢侧 revision_review 有米下锅)
        assert getattr(ml, "_revision_judge", None) is not None
        # 假 gateway 调不通 → 桥必须优雅返 ""(宁空勿毒),不许抛
        assert ml._revision_judge("材料") == ""
        preg = app.state.proposal_registry
        assert preg is not None
        assert getattr(ml, "_revision_proposal_sink", None) == preg.register

        # 断线③:handlers 表有 KIND_REVISE_SKILL
        handlers = app.state.proposal_handlers
        assert KIND_REVISE_SKILL in handlers

        # 真兑现:大改卡 ACCEPT → SKILL.md 真被改 + Changelog 记审计痕
        from karvyloop.crystallize import build_revision_proposal
        sdir = tmp_path / "skills" / "monthly_report"
        sdir.mkdir(parents=True)
        skill_md = sdir / "SKILL.md"
        skill_md.write_text("## Goal\n总结报表\n\n## Steps\n\n1. 旧步骤\n", encoding="utf-8")
        card = build_revision_proposal(
            skill_name="monthly_report", sig="sig-x", path=str(skill_md),
            old_steps=["1. 旧步骤"], new_steps=["1. 新打法", "2. 核对边界"],
            note="换打法", trigger="confidence=0.40", trace_refs=["t:1"], ts=time.time())
        preg.register(card)
        res = preg.decide(card.proposal_id, "ACCEPT", handlers=handlers)
        assert res.ok, res.detail
        text = skill_md.read_text(encoding="utf-8")
        assert "1. 新打法" in text and "1. 旧步骤" not in text
        assert "## Changelog" in text and "[revision:h2a]" in text
    finally:
        register_ledger(_prev)


# ---------------------------------------------------------------- 3. daily tick 功能路径

class _StubPump:
    async def daily(self):
        return None, 0


def test_daily_tick_runs_revision_review_and_weekly_digest(monkeypatch, tmp_path):
    """断线②+周报卡:_daily_loop 真跑一轮 → ml.revision_review 被调,
    weekly_digest_tick 真发卡进待决议表(kind=weekly_digest)。"""
    from karvyloop.cognition.weekly_digest import KIND_WEEKLY_DIGEST

    # weekly 水位文件默认落 ~/.karvyloop → 隔离
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))

    calls = {"revision": 0}

    def _revision_review():
        calls["revision"] += 1
        return {"revised": 0, "proposed": 0}

    ml = types.SimpleNamespace(
        pending_quality_count=lambda cap=None: 0,
        quality_review=lambda: 0,
        lessons_review=lambda: 0,
        revision_review=_revision_review,
        trace=TraceStore(),
    )
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=ml)
    app.state.proposal_pump = _StubPump()
    app.state.proposal_daily_interval_s = 0.05   # 立刻到 daily
    app.state.boot_poll_delay_s = -1             # 关 boot poll(不相干)
    app.state.proposal_registry = PendingProposalRegistry()

    with TestClient(app):
        deadline = time.time() + 5.0
        while time.time() < deadline:
            weekly = [p for p in app.state.proposal_registry.pending()
                      if getattr(p, "kind", "") == KIND_WEEKLY_DIGEST]
            if calls["revision"] >= 1 and weekly:
                break
            time.sleep(0.05)
    assert calls["revision"] >= 1, "daily tick 没调 ml.revision_review(断线②没接上)"
    weekly = [p for p in app.state.proposal_registry.pending()
              if getattr(p, "kind", "") == KIND_WEEKLY_DIGEST]
    assert weekly, "daily tick 没发周报卡(weekly_digest_tick 没接上)"
    # 空周诚实:卡 payload 带结构化 digest + markdown,quiet=True
    payload = getattr(weekly[0], "payload", {}) or {}
    assert payload.get("digest", {}).get("quiet") is True
    assert "周报" in payload.get("markdown", "")
    # 水位已落盘(7 天幂等防重的根)
    assert (tmp_path / ".karvyloop" / "weekly_digest_tick.json").exists()


def test_weekly_digest_idempotent_within_week(monkeypatch, tmp_path):
    """同周第二轮 daily 不重发(水位幂等)—— 直接调 tick 两次验(生产同参)。"""
    from karvyloop.cognition.weekly_digest import weekly_digest_tick

    reg = PendingProposalRegistry()
    trace = TraceStore()
    sp = tmp_path / "wd.json"
    r1 = asyncio.run(weekly_digest_tick(trace=trace, registry=reg, state_path=sp))
    r2 = asyncio.run(weekly_digest_tick(trace=trace, registry=reg, state_path=sp))
    assert r1["ran"] is True and r2["ran"] is False
    assert len(reg.pending()) == 1


# ---------------------------------------------------------------- 4. 静态契约(idle 不烧)

def test_daily_loop_revision_and_weekly_after_idle_continue():
    """revision_review / weekly_digest_tick 必须排在 idle continue 之后
    (test_idle_zero_llm 同契约:没事发生的夜里零慢侧工作)。"""
    import karvyloop.console.app as console_app
    src = pathlib.Path(console_app.__file__).read_text(encoding="utf-8")
    loop_start = src.index("_daily_loop")
    idle_pos = src.index('if action == "idle":', loop_start)
    for work in ("revision_review", "weekly_digest_tick"):
        pos = src.index(work, loop_start)
        assert idle_pos < pos, f"契约破坏:{work} 出现在 idle continue 之前"
