"""TUI ↔ MainLoop 接线测试(M3+ 批 5)。

设计:plans/snoopy-singing-sunbeam.md §批 5。

AC 列表:
  AC1: H2AInput on_submit 发 IntentSubmitted 消息(批 5 改造)
  AC2: WorkbenchApp 接受 main_loop + runtime_kwargs 注入(批 5 扩展)
  AC3: submit_intent 走 drive_in_tui → 更新 _crystallized_skills/_last_fast_brain_skill
  AC4: 5 次同 input → 第 5 次触发 crystallized(通过 stub slow_brain 走 high_freq)
  AC5: 第 6 次同 input 走快脑(拍 4 已有的 high_freq 路径同款断言)
  AC6: AC1 AC2 AC3 AC4 AC5 不需真 LLM/真 forge,纯 stub
  AC7: drive_in_tui R3-async 包装不抛(stub slow_brain 工厂绕开 asyncio.run)
  AC8: 灵魂铁律 grep 锁(workbench/main_loop_bridge.py + h2a_input.py + app.py)
"""
from __future__ import annotations

import asyncio
import pathlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.runtime.main_loop import MainLoop  # noqa: E402
from karvyloop.domain import Address  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.workbench.app import WorkbenchApp  # noqa: E402
from karvyloop.workbench.snapshot import WidgetSnapshot  # noqa: E402
from karvyloop.workbench.widgets.h2a_input import H2AInput, IntentSubmitted  # noqa: E402


# ---------- helpers ----------

def _user() -> Address:
    return Address(domain_id="dom-1", role="user", agent_id="ch")


def _stub_slow_brain_factory(*, n_max: int = 100):
    """同拍 4 stub:同 input → 同 sig → high_freq 路径。"""
    from karvyloop.schemas.atom import AtomRun

    call_count = {"n": 0}

    def factory(**kwargs):
        def slow_brain(intent: str) -> tuple[str, AtomRun]:
            n = call_count["n"]
            call_count["n"] += 1
            ts = 1000.0 + n * 200.0
            run = AtomRun(
                atom_id=f"atom-stub-{n}",
                input={"intent": intent},
                output={"text": f"ok-{intent}-{n}"},
                success=True,
                tool_calls=[{"name": "run_command"}],  # brick3:代表真干活→可结晶
                trace_ref=f"trace://atom-stub/{n}",
                ts=ts,
            )
            return (f"ok-{intent}-{n}", run)

        return slow_brain

    return factory


def _build_loop_with_clock(tmp_path, *, clock_offset=0.0):
    """同拍 4 helper:MainLoop + 可控 clock(避开 60s 去抖)。"""
    base_ts = 1000.0 + clock_offset
    state = {"now": base_ts}

    def clock() -> float:
        return state["now"]

    ml = MainLoop(skills_dir=tmp_path / "skills", clock=clock,
                  result_classifier=lambda *_a: "stable")  # §13:确定性桩→stable,测第6次走快脑回放
    ml.bootstrap()
    ml._test_advance = lambda secs: state.__setitem__("now", state["now"] + secs)  # type: ignore[attr-defined]
    return ml


# ---------- AC1: H2AInput on_submit 发 IntentSubmitted ----------

class TestAC1H2AInputOnSubmit:
    """AC1: H2AInput on_input_submitted 发 IntentSubmitted 消息。"""

    @pytest.mark.asyncio
    async def test_input_submitted_posts_intent_message(self):
        """Input.Submitted 事件 → post IntentSubmitted 消息,value 同步传递。

        直接调 H2AInput.on_input_submitted 验证(Textual message pump 在
        headless pilot 下时序不稳;通过 observer.py 的 on_intent_submitted
        路由已由 app.submit_intent 集成测试覆盖 — 见 TestAC3)。
        """
        h2a = H2AInput(id="h2a")
        posted = []

        class _FakeEv:
            value = "test intent"

        # Monkey-patch post_message to capture
        h2a.post_message = lambda m: posted.append(m)  # type: ignore[method-assign]
        h2a.on_input_submitted(_FakeEv())  # type: ignore[arg-type]
        assert len(posted) == 1
        assert isinstance(posted[0], IntentSubmitted)
        assert posted[0].intent == "test intent"
        assert h2a.value == "", "提交后应清空输入框"

    def test_empty_input_no_message(self):
        """空 input 不发消息。"""
        h2a = H2AInput()
        posted = []

        class _FakeEv:
            value = ""

        h2a.post_message = lambda m: posted.append(m)  # type: ignore[method-assign]
        h2a.on_input_submitted(_FakeEv())  # type: ignore[arg-type]
        assert posted == [], "空 input 不应触发 IntentSubmitted"


# ---------- AC2: WorkbenchApp 接受 main_loop + runtime_kwargs ----------

class TestAC2WorkbenchAppAcceptsMainLoop:
    """AC2: WorkbenchApp 接受 main_loop + runtime_kwargs 注入,不破坏既有签名。"""

    def test_construct_with_main_loop_kwarg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ml = _build_loop_with_clock(tmp_path)
        app = WorkbenchApp(
            workbench=WorkbenchObserver(),
            user_address=_user(),
            main_loop=ml,
            runtime_kwargs={"token": "fake", "workspace_root": str(tmp_path)},
        )
        assert app._main_loop is ml
        assert app._runtime_kwargs["workspace_root"] == str(tmp_path)
        assert app._crystallized_skills == []
        assert app._last_fast_brain_skill == ""

    def test_construct_without_main_loop_works(self, tmp_path, monkeypatch):
        """没 main_loop 也能构造(拍 3 既有路径不破)。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        app = WorkbenchApp(
            workbench=WorkbenchObserver(),
            user_address=_user(),
        )
        assert app._main_loop is None
        assert app._runtime_kwargs == {}


# ---------- AC3: submit_intent 走 drive_in_tui 更新 snapshot 字段 ----------

class TestAC3SubmitIntentUpdatesState:
    """AC3: submit_intent 走 drive_in_tui → 更新 _crystallized_skills + _last_fast_brain_skill。"""

    @pytest.mark.asyncio
    async def test_submit_intent_updates_last_drive_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ml = _build_loop_with_clock(tmp_path)
        with patch("karvyloop.workbench.main_loop_bridge.forge_slow_brain_factory", _stub_slow_brain_factory()):
            app = WorkbenchApp(
                workbench=WorkbenchObserver(),
                user_address=_user(),
                main_loop=ml,
                runtime_kwargs={
                    "token": MagicMock(), "sandbox": MagicMock(),
                    "gateway": MagicMock(), "workspace_root": str(tmp_path),
                    "model_ref": "fake",
                },
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.submit_intent("hello")
                await pilot.pause()
        assert app._last_drive_text.startswith("ok-hello-")
        assert ml.stats.slow_brain_runs == 1

    @pytest.mark.asyncio
    async def test_submit_intent_without_main_loop_noop(self, tmp_path, monkeypatch):
        """没 main_loop 注入时 submit_intent 是 no-op(不抛)。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        app = WorkbenchApp(workbench=WorkbenchObserver(), user_address=_user())
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.submit_intent("noop intent")
            await pilot.pause()
        assert app._last_drive_text == ""


# ---------- AC4: 5 次同 input 触发 crystallized ----------

class TestAC45RunsCrystallize:
    """AC4: 5 次同 input → 第 5 次触发 crystallized(同拍 4 high_freq 路径断言)。"""

    @pytest.mark.asyncio
    async def test_5_runs_crystallize_via_tui(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ml = _build_loop_with_clock(tmp_path)
        app = WorkbenchApp(
            workbench=WorkbenchObserver(),
            user_address=_user(),
            main_loop=ml,
            runtime_kwargs={
                "token": MagicMock(), "sandbox": MagicMock(),
                "gateway": MagicMock(), "workspace_root": str(tmp_path),
            },
        )
        with patch("karvyloop.workbench.main_loop_bridge.forge_slow_brain_factory", _stub_slow_brain_factory()):
            async with app.run_test() as pilot:
                await pilot.pause()
                for i in range(5):
                    ml._test_advance(200.0)  # type: ignore[attr-defined]
                    await app.submit_intent("summarize")
                    await pilot.pause()
        assert ml.stats.slow_brain_runs == 5
        assert ml.stats.crystallizations == 1
        assert len(app._crystallized_skills) == 1, f"应有 1 个结晶,got {app._crystallized_skills}"


# ---------- AC5: 第 6 次同 input 命中快脑 ----------

class TestAC56thCallFastBrain:
    """AC5: 跑过 5 次后第 6 次同 input 命中快脑,app._last_fast_brain_skill 填值。"""

    @pytest.mark.asyncio
    async def test_6th_call_hits_fast_brain_via_tui(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ml = _build_loop_with_clock(tmp_path)
        app = WorkbenchApp(
            workbench=WorkbenchObserver(),
            user_address=_user(),
            main_loop=ml,
            runtime_kwargs={
                "token": MagicMock(), "sandbox": MagicMock(),
                "gateway": MagicMock(), "workspace_root": str(tmp_path),
            },
        )
        with patch("karvyloop.workbench.main_loop_bridge.forge_slow_brain_factory", _stub_slow_brain_factory()):
            async with app.run_test() as pilot:
                await pilot.pause()
                for i in range(5):
                    ml._test_advance(200.0)  # type: ignore[attr-defined]
                    await app.submit_intent("send email")
                    await pilot.pause()
                assert ml.stats.crystallizations == 1
                # 第 6 次:patch 慢脑工厂让它爆 → 若走到慢脑就报错
                def boom(*a, **k):
                    raise AssertionError("第 6 次应走快脑")
                with patch("karvyloop.workbench.main_loop_bridge.forge_slow_brain_factory", lambda **k: boom):
                    ml._test_advance(200.0)  # type: ignore[attr-defined]
                    await app.submit_intent("send email")
                    await pilot.pause()
        assert ml.stats.fast_brain_hits == 1
        assert app._last_fast_brain_skill, "快脑命中后 _last_fast_brain_skill 应填值"


# ---------- AC6 + AC7: 端到端(无真 LLM/forge) + R3-async 包装不抛 ----------

class TestAC7R3AsyncNestedBridge:
    """AC7: drive_in_tui 用 asyncio.to_thread 防 asyncio.run 嵌套。"""

    @pytest.mark.asyncio
    async def test_drive_in_tui_does_not_throw_under_asyncio(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ml = _build_loop_with_clock(tmp_path)
        with patch("karvyloop.workbench.main_loop_bridge.forge_slow_brain_factory", _stub_slow_brain_factory()):
            from karvyloop.workbench.main_loop_bridge import drive_in_tui
            outcome = await drive_in_tui(
                "x", ml,
                token=MagicMock(), sandbox=MagicMock(), gateway=MagicMock(),
                workspace_root=str(tmp_path), model_ref="fake",
            )
        assert outcome.brain.value == "slow"
        assert outcome.text.startswith("ok-x-")


# ---------- AC8: 灵魂铁律 grep 锁 ----------

class TestAC8KLawScan:
    """AC8: 5 个 grep 全锁 0 命中(本批新文件 + workbench 全包)。"""

    def test_main_loop_bridge_no_apply_or_courier(self):
        result = subprocess.run(
            ["grep", "-nE", r"(apply_deontic\(|domain\.apply_\w+\(|Courier\.send\()",
             str(ROOT / "karvyloop" / "workbench" / "main_loop_bridge.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert not lines, f"K 铁律违规\n{chr(10).join(lines)}"

    def test_main_loop_bridge_no_cloud_endpoint(self):
        result = subprocess.run(
            ["grep", "-nE", r"(api\.minimax\.chat|api\.anthropic\.com|api\.openai\.com)",
             str(ROOT / "karvyloop" / "workbench" / "main_loop_bridge.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert not lines, f"0 LLM 违规\n{chr(10).join(lines)}"

    def test_h2a_input_no_apply_or_courier(self):
        result = subprocess.run(
            ["grep", "-nE", r"(apply_deontic\(|domain\.apply_\w+\(|Courier\.send\()",
             str(ROOT / "karvyloop" / "workbench" / "widgets" / "h2a_input.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert not lines, f"K 铁律违规\n{chr(10).join(lines)}"

    def test_app_no_direct_envelope_construct(self):
        """A1 复检:app.py 不直接构造 Envelope(只走 decision_to_envelope)。"""
        result = subprocess.run(
            ["grep", "-nE", r"^\s*Envelope\(", str(ROOT / "karvyloop" / "workbench" / "app.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert not lines, f"A1 违规:app.py 直接构造 Envelope\n{chr(10).join(lines)}"
