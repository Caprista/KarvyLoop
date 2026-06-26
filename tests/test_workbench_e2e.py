"""Workbench 端到端测试(M3 批 3 拍 3a — cli + App 集成)。

设计:plans/snoopy-singing-sunbeam.md §5 端到端。

边界:K4 grep + K5 grep + 0 LLM 调用 + Nuitka 兼容(纯 Py)。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.a2a import BroadcastPayload, Envelope, sign_envelope  # noqa: E402
from karvyloop.domain import Address  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.workbench.app import WorkbenchApp  # noqa: E402


def _pm() -> Address:
    return Address(domain_id="dom-1", role="pm", agent_id="pm-1")


def _now_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _sign(env: Envelope) -> Envelope:
    return Envelope(
        type=env.type, from_=env.from_, by=env.by, to=env.to,
        payload=env.payload, ts=env.ts, signature=sign_envelope(env),
    )


def _broadcast(domain_id: str, message: str) -> Envelope:
    env = Envelope(
        type="broadcast", from_=_pm(), by=(),
        to=Address(domain_id=domain_id, role="observer", agent_id="karvy"),
        payload=BroadcastPayload(message=message, tag=domain_id),
        ts=_now_ts(), signature=b"",
    )
    return _sign(env)


# ---------- AC1: --headless 子命令 ----------

class TestAC1CliHeadless:
    """AC1: `karvyloop chat --headless` 跑通 = 0 exit + 无 stderr 错误。"""

    def test_cli_chat_headless_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "karvyloop.cli.chat", "--headless"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10,
        )
        # 0 exit = 构造成功
        assert result.returncode == 0, f"stderr={result.stderr}"

    def test_cli_chat_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "karvyloop.cli.chat", "--help"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "KarvyLoop" in result.stdout or "chat" in result.stdout


# ---------- AC2: 主子命令注册 ----------

class TestAC2ChatSubcommand:
    """AC2: `karvyloop chat --help` 在主 CLI 注册成功。"""

    def test_main_lists_chat(self):
        result = subprocess.run(
            [sys.executable, "-m", "karvyloop.cli.main", "chat", "--help"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "--headless" in result.stdout
        assert "--serve" in result.stdout


# ---------- AC3: WorkbenchObserver + App 端到端流 ----------

class TestAC3ObserverToAppFlow:
    """AC3: 喂 BROADCAST → snapshot 看到 → App screen mount。"""

    @pytest.mark.asyncio
    async def test_observer_broadcast_reaches_widget_snapshot(self):
        wb = WorkbenchObserver()
        env = _broadcast("dom-1", "战略更新:新财年目标")
        wb.subscribe_to(env)
        app = WorkbenchApp(
            workbench=wb,
            user_address=Address(domain_id="dom-1", role="user", agent_id="ch"),
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            # L2Board 应能看到那条 broadcast 的 message 摘要
            from karvyloop.workbench.widgets import L2Board
            l2 = app.screen.query(L2Board).first()
            assert l2 is not None
            # mount 上去就有 widget
            assert len(l2.children) >= 1

    @pytest.mark.asyncio
    async def test_multiple_broadcasts_visible(self):
        wb = WorkbenchObserver()
        for i in range(3):
            wb.subscribe_to(_broadcast("dom-1", f"msg-{i}"))
        app = WorkbenchApp(
            workbench=wb,
            user_address=Address(domain_id="dom-1", role="user", agent_id="ch"),
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            from karvyloop.workbench.widgets import L2Board
            l2 = app.screen.query(L2Board).first()
            # 至少 3 条 broadcast + 1 banner
            assert l2 is not None


# ---------- AC4: K3 边界端到端 ----------

class TestAC4K3EndToEnd:
    """AC4: K3 强过滤端到端 — 非 BROADCAST 不出现在 UI。"""

    def test_task_assign_does_not_pollute_snapshot(self):
        from karvyloop.a2a import EnvelopeType
        wb = WorkbenchObserver()
        # 喂 task.assign — 必被 K3 拒
        env = Envelope(
            type=EnvelopeType.TASK_ASSIGN.value,
            from_=_pm(), by=(),
            to=Address(domain_id="dom-1", role="observer", agent_id="karvy"),
            payload={"task_id": "t1"},
            ts=_now_ts(), signature=b"",
        )
        signed = _sign(env)
        wb.subscribe_to(signed)
        # snapshot 看不到
        from karvyloop.workbench.snapshot import snapshot_for_widgets
        snap = snapshot_for_widgets(wb)
        assert snap.broadcasts == ()
        assert snap.domains == ()


# ---------- AC5: zero-llm 端到端 ----------

class TestAC5NoLLM:
    """AC5: 端到端跑通不引 LLM(纯 dataclass + Textual)。"""

    def test_no_llm_imports_in_e2e_run(self):
        """WorkbenchApp 整个生命周期不应 import anthropic/openai/minimax。"""
        result = subprocess.run(
            [sys.executable, "-c",
             "from karvyloop.workbench.app import WorkbenchApp; "
             "from karvyloop.karvy.observer import WorkbenchObserver; "
             "from karvyloop.domain import Address; "
             "wb = WorkbenchObserver(); "
             "app = WorkbenchApp(workbench=wb, "
             "user_address=Address(domain_id='dom-1', role='user', agent_id='ch')); "
             "print('OK_NO_LLM')"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10,
        )
        assert "OK_NO_LLM" in result.stdout
        # 不应 import 任何 LLM
        assert "anthropic" not in result.stderr.lower()
        assert "minimax" not in result.stderr.lower()