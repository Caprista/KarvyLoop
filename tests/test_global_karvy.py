"""test_global_karvy — 渠道无关的全局小卡接口:ask 走小卡人格 + 记一轮;dashboard 委派;ready。"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.karvy.global_karvy import GlobalKarvy  # noqa: E402


def test_ask_uses_karvy_persona_and_records(monkeypatch):
    import karvyloop.karvy.global_karvy as gk
    from karvyloop.coding.persona import build_karvy_persona_prompt
    karvy_static = build_karvy_persona_prompt(cwd="/").static   # 小卡人格的标志性 static
    seen = {}

    async def fake_drive(intent, ml, *, ctx=None, persona=None, on_event=None, **rk):
        seen["persona_static"] = getattr(persona, "static", None)
        return types.SimpleNamespace(text="嗨,我是小卡", error="",
                                     brain=types.SimpleNamespace(value="slow"), task_id="t")
    monkeypatch.setattr(gk, "drive_in_tui", fake_drive)

    recorded = {}
    mgr = types.SimpleNamespace(
        context_view=lambda: (),
        record_turn=lambda intent, resp, **kw: recorded.update({"intent": intent, "resp": resp}),
    )
    k = GlobalKarvy(main_loop=object(), conversation_manager=mgr,
                    runtime_kwargs={"gateway": object(), "workspace_root": "/"})
    out = asyncio.run(k.ask("你是谁"))
    assert out.text == "嗨,我是小卡"
    assert seen["persona_static"] == karvy_static          # ← 用的是**小卡人格**,不是裸 forge
    assert recorded == {"intent": "你是谁", "resp": "嗨,我是小卡"}   # 记了一轮


def test_ready_and_dashboard():
    assert GlobalKarvy(main_loop=None, runtime_kwargs={}).ready is False
    assert GlobalKarvy(main_loop=object(), runtime_kwargs={"gateway": object()}).ready is True
    k = GlobalKarvy(main_loop=object(), runtime_kwargs={}, dashboard_fn=lambda: {"tasks": [1, 2]})
    assert k.dashboard() == {"tasks": [1, 2]}
    assert GlobalKarvy(main_loop=object(), runtime_kwargs={}).dashboard() == {}   # 没接 → 空


def test_ask_threads_governance_fn(monkeypatch):
    # Step 0(a):接了 governance_fn → ask() 把你的标准喂进 drive(语音/TUI 不再认知失明)
    import karvyloop.karvy.global_karvy as gk
    seen = {}

    async def fake_drive(intent, ml, *, ctx=None, persona=None, governance="", on_event=None, **rk):
        seen["governance"] = governance
        return types.SimpleNamespace(text="ok", error="",
                                     brain=types.SimpleNamespace(value="slow"), task_id="t")
    monkeypatch.setattr(gk, "drive_in_tui", fake_drive)
    mgr = types.SimpleNamespace(context_view=lambda: (), record_turn=lambda *a, **k: None)

    k = GlobalKarvy(main_loop=object(), conversation_manager=mgr,
                    runtime_kwargs={"gateway": object(), "workspace_root": "/"},
                    governance_fn=lambda intent: f"【你的标准】关于「{intent}」")
    asyncio.run(k.ask("动生产数据库"))
    assert "你的标准" in seen["governance"] and "动生产数据库" in seen["governance"]

    # 没接 governance_fn → governance 空(0 回归,旧行为)
    k2 = GlobalKarvy(main_loop=object(), conversation_manager=mgr,
                     runtime_kwargs={"gateway": object(), "workspace_root": "/"})
    asyncio.run(k2.ask("x"))
    assert seen["governance"] == ""
