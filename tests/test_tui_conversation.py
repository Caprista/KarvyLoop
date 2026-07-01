"""test_tui_conversation — TUI 跟**全局小卡**对话(Hardy 2026-06-25 重定位)。

TUI 现在只做两件事:看板 + 跟全局 Karvy 沟通。聊天走渠道无关的 `GlobalKarvy.ask`:
- 小卡人格(不是裸 forge / 也不是 per-domain 角色)
- 喂当前对话 ctx(多轮)
- 每轮 record
语音以后走同一个 ask。所以这里验的是"TUI = 全局小卡的一个壳"。

AC:
- AC1: submit_intent 喂 ctx(含前一轮)给 drive
- AC2: 成功后 record_turn 进当前对话(带 brain)
- AC3: 无 manager 时照常(不抛)
- AC4: 用**小卡人格**驱动(全局 Karvy,不是 per-domain governance)
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cli.main_loop import Brain  # noqa: E402
from karvyloop.coding.persona import build_karvy_persona_prompt  # noqa: E402
from karvyloop.cognition.conversation import ConversationManager, ConversationStore  # noqa: E402
from karvyloop.domain.registry import Address  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.workbench.app import WorkbenchApp  # noqa: E402
from karvyloop.workbench.main_loop_bridge import DriveOutcome  # noqa: E402


def _user():
    return Address(domain_id="dom-1", role="user", agent_id="ch")


def _make_outcome(intent, brain=Brain.SLOW):
    return DriveOutcome(intent=intent, brain=brain, text=f"ok-{intent}", skill_name="",
                        fast_brain_hit=(brain == Brain.FAST), crystallized=False, task_id="tid-1")


@pytest.mark.asyncio
async def test_submit_intent_feeds_ctx_records_and_uses_karvy_persona(tmp_path, monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    seen = {}

    # GlobalKarvy.ask 在 global_karvy 模块里调 drive_in_tui → patch 那儿
    async def fake_drive(intent, ml, *, ctx=None, persona=None, on_event=None, **kw):
        seen["ctx"] = ctx
        seen["persona_static"] = getattr(persona, "static", None)
        return _make_outcome(intent)
    monkeypatch.setattr("karvyloop.karvy.global_karvy.drive_in_tui", fake_drive)

    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()
    mgr.record_turn("前一句", "前一应")   # 预置一轮 → ctx 非空

    app = WorkbenchApp(workbench=WorkbenchObserver(), user_address=_user(),
                       main_loop=MagicMock(), runtime_kwargs={}, conversation_manager=mgr)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.submit_intent("新一句")
        await pilot.pause()

    # AC1:drive 收到 ctx(含前一轮)
    assert seen["ctx"] is not None and len(seen["ctx"]) >= 1
    assert seen["ctx"][0].user_intent == "前一句"
    # AC4:用**小卡人格**驱动(全局 Karvy)
    assert seen["persona_static"] == build_karvy_persona_prompt(cwd="/").static
    # AC2:新一轮 record 进对话
    assert mgr.current().turn_count == 2
    assert mgr.current().turns[1].user_intent == "新一句"
    assert mgr.current().turns[1].brain == "slow"


@pytest.mark.asyncio
async def test_submit_intent_no_manager_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)

    async def fake_drive(intent, ml, *, ctx=None, persona=None, on_event=None, **kw):
        assert ctx is None             # 无 manager → 无 ctx
        return _make_outcome(intent)
    monkeypatch.setattr("karvyloop.karvy.global_karvy.drive_in_tui", fake_drive)

    app = WorkbenchApp(workbench=WorkbenchObserver(), user_address=_user(),
                       main_loop=MagicMock(), runtime_kwargs={}, conversation_manager=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.submit_intent("hi")   # 不抛
        await pilot.pause()
    assert app._last_drive_text == "ok-hi"
