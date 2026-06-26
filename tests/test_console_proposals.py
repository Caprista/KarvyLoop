"""test_console_proposals — IntentAnalyst → console h2a_proposal 推送桥(M3+ 拍 9.0d)。

设计:docs/20 §3.3.5 + docs/25 + plans/snoopy-singing-sunbeam.md。

AC 列表:
- AC1-AC3: broadcast_proposal(0 client / N client / 死连接剔除)
- AC4-AC6: ProposalPump(boot 有 Proposal 推 / 沉默不推 / on_event)
- AC7-AC9: /api/propose REST(无 pump / 沉默 / 有 Proposal)
- AC10-AC12: WS propose → h2a_proposal emit(无 pump / 沉默 / 有 Proposal 广播)
- AC13: 端到端 — 推 proposal → 用户 h2a_decision ACCEPT → decision_to_envelope(K5)
- AC14-AC15: K5 灵魂铁律(proposals.py 不 import decision_to_envelope / 不偷构 Envelope)
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app, broadcast_proposal, ProposalPump  # noqa: E402
from karvyloop.console.proposals import WS_TYPE_H2A_PROPOSAL  # noqa: E402
from karvyloop.karvy.atoms import (  # noqa: E402
    IntentAnalyst,
    Proposal,
    TRIGGER_EVENT,
    TraceChunk,
)
from karvyloop.karvy.fastbrain.trace_habit import Habit, HabitStore  # noqa: E402
from karvyloop.karvy.fastbrain.trace_index import TraceIndex, TraceRecord  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


# ---- helpers ----


def _proposal(summary: str = "用户可能想试穿", strength: float = 0.85, habit_id: int = 1) -> Proposal:
    return Proposal(
        summary=summary,
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(1, 2, 3),
        habit_id=habit_id,
        model_ref="anthropic/claude-sonnet-4-6",
        ts=1700000000.0,
    )


class _FakeWs:
    """假 WS client(record send_json calls)。"""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list = []
        self.fail = fail

    async def send_json(self, obj) -> None:
        if self.fail:
            raise RuntimeError("dead client")
        self.sent.append(obj)


class _FakeApp:
    """假 FastAPI app(只有 state.ws_clients)。"""

    def __init__(self) -> None:
        class _State:
            pass

        self.state = _State()
        self.state.ws_clients = set()


class _FakeAnalyst:
    """可控 analyst(duck type:boot_poll / daily_poll / on_event)。"""

    def __init__(self, proposal=None) -> None:
        self.proposal = proposal
        self.boot_calls = 0
        self.daily_calls = 0
        self.event_calls = 0

    def boot_poll(self, recent_n: int = 20):
        self.boot_calls += 1
        return self.proposal

    def daily_poll(self, recent_n: int = 50):
        self.daily_calls += 1
        return self.proposal

    def on_event(self, chunk):
        self.event_calls += 1
        return self.proposal


# ---- AC1-AC3: broadcast_proposal ----


@pytest.mark.asyncio
async def test_broadcast_proposal_zero_clients_returns_zero() -> None:
    app = _FakeApp()
    sent = await broadcast_proposal(app, _proposal())
    assert sent == 0


@pytest.mark.asyncio
async def test_broadcast_proposal_to_n_clients() -> None:
    app = _FakeApp()
    c1, c2, c3 = _FakeWs(), _FakeWs(), _FakeWs()
    app.state.ws_clients = {c1, c2, c3}
    sent = await broadcast_proposal(app, _proposal(summary="试穿建议"))
    assert sent == 3
    for c in (c1, c2, c3):
        assert len(c.sent) == 1
        assert c.sent[0]["type"] == WS_TYPE_H2A_PROPOSAL
        assert c.sent[0]["payload"]["summary"] == "试穿建议"
        assert c.sent[0]["payload"]["options"] == ["ACCEPT", "DEFER", "REJECT"]


@pytest.mark.asyncio
async def test_broadcast_proposal_evicts_dead_clients() -> None:
    app = _FakeApp()
    good = _FakeWs()
    dead = _FakeWs(fail=True)
    app.state.ws_clients = {good, dead}
    sent = await broadcast_proposal(app, _proposal())
    assert sent == 1  # 只 good 成功
    # dead 被剔除
    assert dead not in app.state.ws_clients
    assert good in app.state.ws_clients


# ---- AC4-AC6: ProposalPump ----


@pytest.mark.asyncio
async def test_pump_boot_pushes_proposal() -> None:
    app = _FakeApp()
    c1 = _FakeWs()
    app.state.ws_clients = {c1}
    analyst = _FakeAnalyst(proposal=_proposal(habit_id=42))
    pump = ProposalPump(app, analyst)
    proposal, sent = await pump.boot()
    assert proposal is not None
    assert proposal.habit_id == 42
    assert sent == 1
    assert analyst.boot_calls == 1
    assert c1.sent[0]["payload"]["habit_id"] == 42


@pytest.mark.asyncio
async def test_pump_boot_silent_does_not_push() -> None:
    app = _FakeApp()
    c1 = _FakeWs()
    app.state.ws_clients = {c1}
    analyst = _FakeAnalyst(proposal=None)  # 沉默
    pump = ProposalPump(app, analyst)
    proposal, sent = await pump.boot()
    assert proposal is None
    assert sent == 0
    assert len(c1.sent) == 0  # 沉默不推


@pytest.mark.asyncio
async def test_pump_on_event_and_daily() -> None:
    app = _FakeApp()
    c1 = _FakeWs()
    app.state.ws_clients = {c1}
    analyst = _FakeAnalyst(proposal=_proposal())
    pump = ProposalPump(app, analyst)

    # on_event
    chunk = TraceChunk(summaries=(), source=TRIGGER_EVENT, ts=1.0)
    p, sent = await pump.on_event(chunk)
    assert p is not None and sent == 1
    assert analyst.event_calls == 1

    # daily
    p2, sent2 = await pump.daily()
    assert p2 is not None and sent2 == 1
    assert analyst.daily_calls == 1


# ---- AC7-AC9: /api/propose REST ----


def test_api_propose_no_pump_returns_graceful() -> None:
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    client = TestClient(app)
    # loop-step2b:无 pump 时会兜底到"观察任务看板"的主动建议;裸 app 没接 task_registry、
    # 也无失败任务可提 → 静默返 null(不再保证带 "未接 IntentAnalyst" reason 字段)。
    r = client.post("/api/propose")
    assert r.status_code == 200
    body = r.json()
    assert body["proposal"] is None
    assert body["sent"] == 0


def test_api_propose_silent_returns_null() -> None:
    app = build_console_app(
        workbench=WorkbenchObserver(),
        main_loop=None,
        proposal_pump=ProposalPump(_FakeApp(), _FakeAnalyst(proposal=None)),
    )
    client = TestClient(app)
    r = client.post("/api/propose")
    assert r.status_code == 200
    assert r.json()["proposal"] is None


def test_api_propose_with_proposal_returns_dict() -> None:
    # pump 的 app 必须是真 app(才能广播给真 ws_clients);这里 boot 没 client → sent=0,但 proposal 非空
    wb = WorkbenchObserver()
    real_app_holder = {}

    analyst = _FakeAnalyst(proposal=_proposal(summary="试穿这件衣服", habit_id=7))
    # pump 先用占位 app,build 后替换为真 app
    pump = ProposalPump(_FakeApp(), analyst)
    app = build_console_app(workbench=wb, main_loop=None, proposal_pump=pump)
    # 把 pump 的 app 指向真 app(模拟 entry 接线)
    pump._app = app
    client = TestClient(app)
    r = client.post("/api/propose")
    assert r.status_code == 200
    body = r.json()
    assert body["proposal"] is not None
    assert body["proposal"]["summary"] == "试穿这件衣服"
    assert body["proposal"]["habit_id"] == 7


# ---- AC10-AC12: WS propose → h2a_proposal ----


def test_ws_propose_no_pump_returns_null_payload() -> None:
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # 首次 snapshot
        ws.send_json({"type": "propose", "payload": {}})
        msg = ws.receive_json()
        assert msg["type"] == "h2a_proposal"
        # loop-step2b:无 pump → 兜底观察任务看板;裸 app 无 task_registry/无失败任务 → 静默 null
        assert msg["payload"] is None


def test_ws_propose_silent_returns_null() -> None:
    pump = ProposalPump(_FakeApp(), _FakeAnalyst(proposal=None))
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None, proposal_pump=pump)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "propose", "payload": {}})
        msg = ws.receive_json()
        assert msg["type"] == "h2a_proposal"
        assert msg["payload"] is None
        assert msg["sent"] == 0


def test_ws_propose_with_proposal_broadcasts() -> None:
    """有 proposal:pump.boot 通过 broadcast_proposal 推给所有 ws_clients(含本 client)。"""
    analyst = _FakeAnalyst(proposal=_proposal(summary="试穿建议", habit_id=99))
    pump = ProposalPump(_FakeApp(), analyst)
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None, proposal_pump=pump)
    pump._app = app  # 接线真 app(broadcast 推真 ws_clients)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # 首次 snapshot
        ws.send_json({"type": "propose", "payload": {"recent_n": 10}})
        # broadcast 推 h2a_proposal 给本 client
        msg = ws.receive_json()
        assert msg["type"] == "h2a_proposal"
        assert msg["payload"]["summary"] == "试穿建议"
        assert msg["payload"]["habit_id"] == 99


# ---- AC13: 端到端 — 推 proposal → 用户 ACCEPT → decision_to_envelope ----


def test_end_to_end_proposal_then_accept(tmp_path) -> None:
    """完整 9.0a→9.0d 流:trace → habit → IntentAnalyst → Proposal 推 console → 用户 ACCEPT → envelope。"""
    # 1. 真实 trace_index + habit_store
    trace_index = TraceIndex(
        tmp_path / "trace.db", raw_capacity=1024 * 1024, summary_capacity=5 * 1024 * 1024
    )
    habit_store = HabitStore(tmp_path / "habits.db")
    # 写一条 signal trace 摘要
    trace_index.append_summary({"kind": "intent", "text": "看衣服"})

    # 2. 假 behavior_analyzer 凝出强 habit
    class _FakeBehavior:
        def analyze(self, summaries, model_ref):
            return [
                Habit(
                    id=5, pattern="用户常驻足看衣服 — 可能想试穿", strength=0.9,
                    evidence_count=3, evidence_refs=(1, 2, 3),
                    first_seen=1.0, last_reinforced=1.0,
                    model_ref="anthropic/claude-sonnet-4-6",
                )
            ]

    # 3. 真实 IntentAnalyst
    analyst = IntentAnalyst(
        workbench=WorkbenchObserver(),
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=_FakeBehavior(),
    )

    # 4. console + pump
    pump = ProposalPump(_FakeApp(), analyst)
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None, proposal_pump=pump)
    pump._app = app
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # snapshot
        # 5. 触发 propose → IntentAnalyst boot → Proposal 推
        ws.send_json({"type": "propose", "payload": {}})
        proposal_msg = ws.receive_json()
        assert proposal_msg["type"] == "h2a_proposal"
        assert "试穿" in proposal_msg["payload"]["summary"]

        # 6. 用户在 console 上 ACCEPT(K5:走 decision_to_envelope 工厂)
        ws.send_json({
            "type": "h2a_decision",
            "payload": {
                "proposal_id": "p-1",
                "decision": "ACCEPT",
                "reason": "好的我想试",
            },
        })
        env_msg = ws.receive_json()
        assert env_msg["type"] == "h2a_envelope"
        assert env_msg["payload"]["envelope"] is not None
        # K5 不变量:envelope.by = []
        assert env_msg["payload"]["envelope"]["by"] == []

    trace_index.close()
    habit_store.close()


# ---- AC14-AC15: K5 灵魂铁律 ----


def _code_lines(mod) -> str:
    """取模块的 import + 可执行代码行(剔除注释/docstring,供铁律 grep)。"""
    import inspect

    src = inspect.getsource(mod)
    lines = []
    in_doc = False
    for line in src.splitlines():
        stripped = line.strip()
        # 粗略剔除 triple-quote docstring 块
        if stripped.startswith('"""') or stripped.startswith("'''"):
            # 单行 docstring(开闭同行)
            if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                continue
            in_doc = not in_doc
            continue
        if in_doc:
            continue
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def test_k5_proposals_does_not_import_decision_to_envelope() -> None:
    """K5:proposals.py 是**推建议**层,代码不碰 decision_to_envelope(决策走 ws/routes)。"""
    import karvyloop.console.proposals as mod

    code = _code_lines(mod)
    assert "decision_to_envelope" not in code, (
        "K5 违反 — proposals.py 不应碰决策工厂(那是用户拍板后的路径)"
    )


def test_k5_proposals_does_not_construct_envelope() -> None:
    """K5:proposals.py 不偷构 Envelope。"""
    import karvyloop.console.proposals as mod

    code = _code_lines(mod)
    assert "Envelope(" not in code, "K5 违反 — proposals.py 偷构 Envelope"


def test_k7_proposals_no_a2a() -> None:
    """K7:proposals.py 不参与 A2A(代码不引 Courier / EnvelopeRouter)。"""
    import karvyloop.console.proposals as mod

    code = _code_lines(mod)
    assert "Courier" not in code
    assert "EnvelopeRouter" not in code
