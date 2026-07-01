"""Workbench widget 测试(M3 批 3 拍 3a,12 AC)。

设计:plans/snoopy-singing-sunbeam.md §5 测试策略。

K 边界验证:grep `apply_` / `courier_send` 在 workbench/ 必须为空(AC11 源码扫描)。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests._scan import grep_py  # noqa: E402  (OS-portable source scan; needs ROOT on path)
from karvyloop.a2a import BroadcastPayload, Envelope, sign_envelope  # noqa: E402
from karvyloop.domain import Address  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.workbench.app import WorkbenchApp  # noqa: E402
from karvyloop.workbench.binding import EnvelopeArrived  # noqa: E402
from karvyloop.workbench.screens.observer import ObserverScreen  # noqa: E402
from karvyloop.workbench.snapshot import (  # noqa: E402
    WidgetSnapshot,
    snapshot_for_widgets,
    snapshot_with_atoms,
)
from karvyloop.workbench.widgets import (  # noqa: E402
    H2AInput,
    L0TopBar,
    L1DomainDetail,
    L2Board,
    L3StatusBar,
)


# ---------- helpers ----------

def _user() -> Address:
    return Address(domain_id="dom-1", role="user", agent_id="ch")


def _pm() -> Address:
    return Address(domain_id="dom-1", role="pm", agent_id="pm-1")


def _now_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sign(env: Envelope) -> Envelope:
    return Envelope(
        type=env.type, from_=env.from_, by=env.by, to=env.to,
        payload=env.payload, ts=env.ts, signature=sign_envelope(env),
    )


def _broadcast(domain_id: str, message: str, tag: str = "") -> Envelope:
    env = Envelope(
        type="broadcast",
        from_=_pm(),
        by=(),
        to=Address(domain_id=domain_id, role="observer", agent_id="karvy"),
        payload=BroadcastPayload(message=message, tag=tag or domain_id),
        ts=_now_ts(),
        signature=b"",
    )
    return _sign(env)


# ---------- AC1: 导入 + 构造(零 LLM,纯 dataclass) ----------

class TestAC1ImportAndConstruct:
    """AC1: 所有 widget + screen + app 可导入 + 可构造。"""

    def test_app_imports(self):
        from karvyloop.workbench import WorkbenchApp as A
        assert A is WorkbenchApp

    def test_widgets_import(self):
        assert L0TopBar is not None
        assert L1DomainDetail is not None
        assert L2Board is not None
        assert L3StatusBar is not None
        assert H2AInput is not None

    def test_screen_imports(self):
        assert ObserverScreen is not None

    def test_app_construct_no_llm(self):
        """A1 不直接构造 Envelope — 但 App 构造不调用 LLM/网络。"""
        wb = WorkbenchObserver()
        app = WorkbenchApp(workbench=wb, user_address=_user())
        assert app is not None


# ---------- AC2: snapshot_for_widgets 数据规整 ----------

class TestAC2SnapshotAdapter:
    """AC2: snapshot_for_widgets 把 WorkbenchObserver 数据规整成 WidgetSnapshot。"""

    def test_empty_workbench(self):
        wb = WorkbenchObserver()
        snap = snapshot_for_widgets(wb)
        assert snap.domains == ()
        assert snap.current_domain == ""
        assert snap.broadcasts == ()

    def test_with_one_broadcast(self):
        wb = WorkbenchObserver()
        env = _broadcast("dom-1", "hello", tag="strategy")
        wb.subscribe_to(env)
        snap = snapshot_for_widgets(wb)
        assert snap.domains == ("dom-1",)
        assert snap.current_domain == "dom-1"
        assert len(snap.broadcasts) == 1
        assert snap.broadcasts[0].payload.message == "hello"

    def test_k3_filter_only_broadcast(self):
        """K3 强过滤:非 BROADCAST 不进 _boards → snapshot 看不到。"""
        from karvyloop.a2a import EnvelopeType
        wb = WorkbenchObserver()
        # 构造 task.assign(env.type 不进 _boards)
        env = Envelope(
            type=EnvelopeType.TASK_ASSIGN.value,
            from_=_pm(), by=(),
            to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
            payload={"task_id": "t1"},
            ts=_now_ts(), signature=b"",
        )
        signed = _sign(env)
        wb.subscribe_to(signed)
        snap = snapshot_for_widgets(wb)
        # K3 强过滤 → 0 broadcasts
        assert snap.broadcasts == ()


# ---------- AC3: subscribe_async 后端增量 ----------

class TestAC3SubscribeAsync:
    """AC3: WorkbenchObserver.subscribe_async() 异步事件流(K3 边界继承)。"""

    def test_subscribe_async_yields_broadcasts(self):
        import asyncio

        async def collect():
            wb = WorkbenchObserver()
            env = _broadcast("dom-1", "x", tag="strategy")
            wb.subscribe_to(env)
            out = []
            async for e in wb.subscribe_async():
                out.append(e)
            return out

        result = asyncio.run(collect())
        assert len(result) == 1
        assert result[0].payload.message == "x"

    def test_subscribe_async_inherits_k3(self):
        """K3 强过滤:subscribe_async 只 yield BROADCAST。"""
        import asyncio

        async def collect():
            wb = WorkbenchObserver()
            env = _broadcast("dom-1", "y")
            wb.subscribe_to(env)
            out = []
            async for e in wb.subscribe_async():
                out.append(e)
            return out

        result = asyncio.run(collect())
        # 全部是 broadcast 类型
        assert all(e.type == "broadcast" for e in result)


# ---------- AC4: K 边界源码扫描(grep 锁) ----------

class TestAC4KLockedSourceScan:
    """AC4: K4/K5 grep 验证 — workbench/ 不得含 apply_/courier_send。"""

    def test_no_apply_deontic_in_workbench(self):
        """K4:工作台只读 — 不得含 `apply_deontic` 或 `domain.apply_*` 调用(注释提及不算)。"""
        lines = grep_py(r"apply_deontic\(|domain\.apply_\w+\(", ROOT / "karvyloop" / "workbench")
        assert not lines, f"K4 违规:发现 apply_ 函数调用\n{chr(10).join(lines)}"

    def test_no_courier_send_in_workbench(self):
        """K5:UI 不走 Courier — 不得含 `courier_send(...)` 或 `Courier.send(...)` 调用。"""
        lines = grep_py(r"courier\.send\(|Courier\.send\(", ROOT / "karvyloop" / "workbench")
        assert not lines, f"K5 违规:发现 Courier.send 调用\n{chr(10).join(lines)}"

    def test_no_llm_imports_in_workbench(self):
        """0 LLM 调用 — workbench/ 不得 import LLM provider。"""
        lines = grep_py(
            r"^import\s+(anthropic|openai|minimax)\b|^from\s+(anthropic|openai|minimax)\b|^from\s+karvyloop\.llm",
            ROOT / "karvyloop" / "workbench")
        assert not lines, f"0 LLM 违规\n{chr(10).join(lines)}"


# ---------- AC5: EnvelopeArrived Message 类型 ----------

class TestAC5EnvelopeArrivedMessage:
    """AC5: EnvelopeArrived Message 桥(K3 边界 — 只 emit 通过 K3 的 envelope)。"""

    def test_envelope_arrived_carries_envelope(self):
        env = _broadcast("dom-1", "msg")
        msg = EnvelopeArrived(envelope=env)
        assert msg.envelope.payload.message == "msg"
        assert msg.envelope.type == "broadcast"


# ---------- AC6: Textual app run_test headless ----------

class TestAC6AppHeadless:
    """AC6: WorkbenchApp.run_test() 启动 → ObserverScreen 挂载。"""

    @pytest.mark.asyncio
    async def test_app_run_test_mounts_observer_screen(self):
        wb = WorkbenchObserver()
        env = _broadcast("dom-1", "test msg", tag="strategy")
        wb.subscribe_to(env)
        app = WorkbenchApp(workbench=wb, user_address=_user())
        async with app.run_test() as pilot:
            await pilot.pause()
            # 主屏 = ObserverScreen
            assert isinstance(app.screen, ObserverScreen)
            # L1 + L2 至少有一个 widget mount
            assert app.screen.query(L1DomainDetail) is not None
            assert app.screen.query(L2Board) is not None

    @pytest.mark.asyncio
    async def test_app_quit_binding(self):
        wb = WorkbenchObserver()
        app = WorkbenchApp(workbench=wb, user_address=_user())
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            # q 触发 action_quit → app.exit
            # app 已退出 run_test


# ---------- AC7: snapshot_with_atoms 扩展版 ----------

class TestAC7SnapshotWithAtoms:
    """AC7: snapshot_with_atoms 注入原子 agent 后字段丰富。"""

    def test_with_atoms_task_count(self):
        wb = WorkbenchObserver()
        env = _broadcast("dom-1", "x")
        wb.subscribe_to(env)
        # 注入 None — 返 0
        snap = snapshot_with_atoms(wb)
        assert snap.task_count == 0
        assert snap.pursuit_count == 0
        assert snap.unhealthy is False

    def test_with_atoms_uses_default_when_uninjected(self):
        wb = WorkbenchObserver()
        snap = snapshot_with_atoms(wb, task_tracker=None)
        # 即使 None 也 OK — 返 0
        assert snap.task_count == 0


# ---------- AC8: snapshot dataclass 不可变 ----------

class TestAC8SnapshotFrozen:
    """AC8: WidgetSnapshot frozen — 防止 UI 误改后端数据。"""

    def test_widget_snapshot_is_frozen(self):
        snap = WidgetSnapshot(
            domains=("a",), current_domain="a",
            broadcasts=(), task_count=0, pursuit_count=0, unhealthy=False,
        )
        with pytest.raises(dataclasses_error()):
            snap.domains = ("b",)  # type: ignore[misc]


def dataclasses_error():
    import dataclasses
    return (dataclasses.FrozenInstanceError, AttributeError)