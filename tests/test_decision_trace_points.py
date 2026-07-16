"""test_decision_trace_points — docs/85 T1/T3/T4 + docs/81 B-5 决策侧埋点。

铁律(全部单测锁死):
- **fail-soft**:trace=None / append 炸 → 决策流行为一字不变(决策流是命脉)。
- **payload 封顶**:每条新 kind 的 payload JSON ≤ TRACE_PAYLOAD_CAP(500)字符。
- **不进容量环**:新 kind 全部不在 DROPPABLE_KINDS(小事件,prune 永不丢)。
- **run_id 串联**:dispatch 包 run_scope,handler 期间写的 Trace 与 T4 同 run_id
  (contextvar 过 asyncio.to_thread 也成立)。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.trace import DROPPABLE_KINDS, TraceEntry, TraceStore  # noqa: E402
from karvyloop.console.decision_log import DecisionLog, RevocationStore  # noqa: E402
from karvyloop.console.decision_wire import (  # noqa: E402
    DECISION_BATCH,
    PREF_TRACE_TASK,
    TRACE_PAYLOAD_CAP,
    clamp_trace_payload,
    crystallize_candidates,
    dispatch_decision,
    emit_decision_trace,
    maybe_crystallize_decisions,
    record_decision_signals,
)
from karvyloop.crystallize.decision_pref import (  # noqa: E402
    DecisionSample,
    REINFORCE_STEP,
    STRENGTH_FLOOR,
    WEAKEN_STEP,
    make_decision_pref_belief,
)
from karvyloop.karvy.atoms import Proposal  # noqa: E402
from karvyloop.karvy.proposal_registry import (  # noqa: E402
    KIND_ROUTE_TO_ROLE,
    PendingProposalRegistry,
)

NEW_KINDS = (
    "decision_point", "decision_made", "decision_dispatched",       # T1/T3/T4(docs/85)
    "decision_pref_reinforced", "decision_pref_weakened",           # B-5 #1/#2
    "pref_auto_revoked", "surface_triggered", "defer_aged_out",     # B-5 #3/#6/#7
    "revoke_suppressed",                                            # B-5 #12
)


class _State:
    pass


class _App:
    def __init__(self) -> None:
        self.state = _State()


def _mk_app(*, trace: bool = True) -> _App:
    app = _App()
    app.state.runtime_kwargs = {}
    app.state.memory = None
    if trace:
        app.state.main_loop = types.SimpleNamespace(trace=TraceStore())
    return app


def _trace(app) -> TraceStore:
    return app.state.main_loop.trace


def _proposal(pid: str = "", kind: str = KIND_ROUTE_TO_ROLE) -> Proposal:
    return Proposal(summary="把调研交给分析师", options=("ACCEPT", "DEFER", "REJECT"),
                    strength=0.8, evidence_refs=(), habit_id=1, model_ref="m", ts=1.0,
                    kind=kind, payload={"requirement": "调研", "role": "analyst"},
                    proposal_id=pid, basis="最近你连续三次让分析师做这类事")


def _assert_capped(entries) -> None:
    for e in entries:
        blob = json.dumps(e.payload, ensure_ascii=False)
        assert len(blob) <= TRACE_PAYLOAD_CAP, f"{e.kind} payload 超帽: {len(blob)}"


# ---- 容量环纪律 + payload 帽 ----


def test_new_kinds_not_droppable():
    """新 kind 全部不进容量环可丢集(prune 永不丢决策侧小事件)。"""
    for k in NEW_KINDS:
        assert k not in DROPPABLE_KINDS


def test_clamp_payload_cap_deterministic():
    huge = {"a": "长" * 800, "b": "x" * 800, "n": 42, "flag": True}
    out = clamp_trace_payload(huge)
    assert len(json.dumps(out, ensure_ascii=False)) <= TRACE_PAYLOAD_CAP
    assert out["n"] == 42 and out["flag"] is True   # 非字符串值原样保留


def test_emit_decision_trace_fail_soft_no_trace():
    app = _mk_app(trace=False)   # 无 main_loop / 无 trace
    assert emit_decision_trace(app, "decision_made", "p-1", {"a": 1}) == ""


def test_emit_decision_trace_fail_soft_append_raises():
    app = _mk_app()

    class _Boom:
        def append(self, e):
            raise RuntimeError("disk on fire")

    app.state.main_loop = types.SimpleNamespace(trace=_Boom())
    assert emit_decision_trace(app, "decision_made", "p-1", {"a": 1}) == ""   # 不炸不冒泡


# ---- T1 decision_point(broadcast 咽喉)----


@pytest.mark.asyncio
async def test_t1_decision_point_on_broadcast():
    from karvyloop.console.proposals import broadcast_proposal
    app = _mk_app()
    app.state.ws_clients = set()
    app.state.proposal_registry = PendingProposalRegistry()
    p = _proposal()
    await broadcast_proposal(app, p)
    got = _trace(app).query(p.proposal_id, kind="decision_point")
    assert len(got) == 1
    pl = got[0].payload
    assert pl["kind"] == KIND_ROUTE_TO_ROLE
    assert pl["summary"] and pl["basis"] and pl["strength"] == 0.8
    _assert_capped(got)
    # 埋点不改行为:卡照常进待决表
    assert app.state.proposal_registry.get(p.proposal_id) is not None


@pytest.mark.asyncio
async def test_t1_fail_soft_broadcast_survives_bad_trace():
    from karvyloop.console.proposals import broadcast_proposal
    app = _mk_app()
    app.state.ws_clients = set()
    app.state.proposal_registry = PendingProposalRegistry()

    class _Boom:
        def append(self, e):
            raise RuntimeError("no")

    app.state.main_loop = types.SimpleNamespace(trace=_Boom())
    p = _proposal()
    await broadcast_proposal(app, p)   # 不炸
    assert app.state.proposal_registry.get(p.proposal_id) is not None


# ---- T3 decision_made(record_decision_signals 第四路)----


def _decide_app(*, trace: bool = True) -> tuple[_App, Proposal]:
    app = _mk_app(trace=trace)
    reg = PendingProposalRegistry()
    p = _proposal()
    reg.register(p)
    app.state.proposal_registry = reg
    app.state.decision_log = DecisionLog()
    return app, p


def test_t3_decision_made_recorded():
    app, p = _decide_app()
    record_decision_signals(app, decision="ACCEPT", proposal_id=p.proposal_id,
                            reason="值得做", role="analyst", edits={"requirement": "改过的"})
    got = _trace(app).query(p.proposal_id, kind="decision_made")
    assert len(got) == 1
    pl = got[0].payload
    assert pl["decision"] == "ACCEPT" and pl["kind"] == KIND_ROUTE_TO_ROLE
    assert pl["edited"] == ["requirement"]
    _assert_capped(got)


def test_t3_fail_soft_trace_absent_behavior_unchanged():
    """trace 缺席:三路信号(log/样本/stats)一个不少 —— decide 行为一字不变。"""
    app, p = _decide_app(trace=False)
    record_decision_signals(app, decision="REJECT", proposal_id=p.proposal_id, reason="不做")
    assert app.state.decision_log.count() == 1
    assert len(app.state.decision_samples) == 1


def test_t3_fail_soft_append_raises_behavior_unchanged():
    app, p = _decide_app()

    class _Boom:
        def append(self, e):
            raise RuntimeError("no")

    app.state.main_loop = types.SimpleNamespace(trace=_Boom())
    record_decision_signals(app, decision="ACCEPT", proposal_id=p.proposal_id)
    assert app.state.decision_log.count() == 1
    assert len(app.state.decision_samples) == 1


# ---- T4 decision_dispatched(dispatch_decision 咽喉)----


def test_t4_dispatch_records_run_id_and_peeks_verdict():
    app, p = _decide_app()
    tr = _trace(app)
    # 模拟执行体:handler 期间写一条 Trace(不带 run_id → append 咽喉盖 contextvar 戳)
    def handler(prop):
        tr.append(TraceEntry(task_id="drive-x", kind="atom_run",
                             payload={"tool_calls": [{"name": "WebFetch", "input": "u"}]}))
        return True, "done"
    # 报告卡 pop 前 peek:预先 stash(真路径由 handler stash),dispatch 后仍取得到
    app.state.report_cards = {p.proposal_id: {"resolvable": "solved", "grounded": True}}
    res = dispatch_decision(app, proposal_id=p.proposal_id, decision="ACCEPT",
                            handlers={KIND_ROUTE_TO_ROLE: handler})
    assert res is not None and res.ok
    got = tr.query(p.proposal_id, kind="decision_dispatched")
    assert len(got) == 1
    pl = got[0].payload
    assert pl["ok"] is True and pl["decision"] == "ACCEPT"
    assert pl["run_id"], "dispatch 必须包 run_scope 并记 run_id"
    assert pl["verdict"] == "solved" and pl["verdict_grounded"] is True
    # peek 不消费:decide 路径随后 pop 仍拿得到
    from karvyloop.console.proposal_handlers import pop_report_card
    assert pop_report_card(app, p.proposal_id) is not None
    # handler 期间写的执行 Trace 与 T4 同 run_id(lifeline 靠它 query_run)
    steps = tr.query_run(pl["run_id"])
    assert any(e.kind == "atom_run" for e in steps)
    _assert_capped(got)


def test_t4_run_id_survives_to_thread():
    """WS 路径:decide 在 asyncio.to_thread 里跑 —— contextvar 复制,run_id 照样串。"""
    app, p = _decide_app()
    tr = _trace(app)

    def handler(prop):
        tr.append(TraceEntry(task_id="drive-y", kind="tool_call", payload={"name": "Bash"}))
        return True, "ok"

    async def go():
        return await asyncio.to_thread(
            lambda: dispatch_decision(app, proposal_id=p.proposal_id, decision="ACCEPT",
                                      handlers={KIND_ROUTE_TO_ROLE: handler}))

    res = asyncio.run(go())
    assert res is not None and res.ok
    rid = tr.query(p.proposal_id, kind="decision_dispatched")[0].payload["run_id"]
    assert rid and any(e.run_id == rid for e in tr.query("drive-y"))


def test_t4_no_registry_returns_none():
    app = _mk_app()
    assert dispatch_decision(app, proposal_id="p-x", decision="ACCEPT") is None


def test_t4_fail_soft_bad_trace_dispatch_unchanged():
    app, p = _decide_app()

    class _Boom:
        def append(self, e):
            raise RuntimeError("no")

    app.state.main_loop = types.SimpleNamespace(trace=_Boom())
    res = dispatch_decision(app, proposal_id=p.proposal_id, decision="REJECT")
    assert res is not None and res.ok and res.detail == "rejected"   # 行为一字不变


def test_t4_records_task_backref_tid():
    """run_task 兑现登记的任务(Task.proposal_id 回链)→ T4 带 tid。"""
    from karvyloop.console.tasks import TaskRegistry
    app, p = _decide_app()
    app.state.task_registry = TaskRegistry(cap=5)
    tid_holder = {}

    def handler(prop):
        tid_holder["tid"] = app.state.task_registry.start(
            who="小卡", intent="重跑", proposal_id=prop.proposal_id)
        return True, "reran"

    dispatch_decision(app, proposal_id=p.proposal_id, decision="ACCEPT",
                      handlers={KIND_ROUTE_TO_ROLE: handler})
    pl = _trace(app).query(p.proposal_id, kind="decision_dispatched")[0].payload
    assert pl["tid"] == tid_holder["tid"]


# ---- B-5 #1/#2/#3/#12(decision_pref 校准事件)----


class _StubGateway:
    def __init__(self, text: str) -> None:
        self._text = text

    def resolve_model(self, scope):
        return "stub/model"

    async def complete(self, messages, tools, ref, *, system=None):
        class TextDelta:
            def __init__(self, text):
                self.text = text
        yield TextDelta(self._text)


def _samples(n):
    return [DecisionSample(decision="REJECT", context=f"提案{i}", reason="没测试", ts=float(i))
            for i in range(n)]


def _pref_app(gw, mem) -> _App:
    app = _mk_app()
    app.state.runtime_kwargs = {"gateway": gw, "model_ref": ""}
    app.state.memory = mem
    return app


@pytest.mark.asyncio
async def test_b5_reinforced_event():
    from karvyloop.cognition.memory import MemoryManager
    mem = MemoryManager()
    gw = _StubGateway('[{"content":"碰生产先写测试","kind":"constraint","explicit":true}]')
    app = _pref_app(gw, mem)
    for s in _samples(DECISION_BATCH):
        from karvyloop.console.decision_wire import observe_decision
        observe_decision(app, s)
    await maybe_crystallize_decisions(app)     # 第一批:新写
    for s in _samples(DECISION_BATCH):
        from karvyloop.console.decision_wire import observe_decision
        observe_decision(app, s)
    await maybe_crystallize_decisions(app)     # 第二批:同候选 → 加固
    got = _trace(app).query(PREF_TRACE_TASK, kind="decision_pref_reinforced")
    assert len(got) == 1
    pl = got[0].payload
    assert pl["step"] == REINFORCE_STEP
    assert pl["strength_after"] > pl["strength_before"]
    _assert_capped(got)


@pytest.mark.asyncio
async def test_b5_weakened_and_revoked_events():
    from karvyloop.cognition.memory import MemoryManager
    mem = MemoryManager()
    # 已有两条偏好:0.7(削弱后 0.4 存活)、0.5(削弱后 0.2 < 0.25 → 自动撤销)
    mem.write(make_decision_pref_belief("输出用表格", "taste", strength=0.7, now=1.0))
    mem.write(make_decision_pref_belief("先问预算", "taste", strength=0.5, now=1.0))
    gw = _StubGateway('{"new":[],"contradicts":[1,2]}')
    app = _pref_app(gw, mem)
    for s in _samples(DECISION_BATCH):
        from karvyloop.console.decision_wire import observe_decision
        observe_decision(app, s)
    await maybe_crystallize_decisions(app)
    weakened = _trace(app).query(PREF_TRACE_TASK, kind="decision_pref_weakened")
    revoked = _trace(app).query(PREF_TRACE_TASK, kind="pref_auto_revoked")
    assert len(weakened) == 1 and len(revoked) == 1
    assert weakened[0].payload["step"] == WEAKEN_STEP
    assert revoked[0].payload["floor"] == STRENGTH_FLOOR
    assert revoked[0].payload["strength_after"] < STRENGTH_FLOOR
    _assert_capped(weakened + revoked)


@pytest.mark.asyncio
async def test_b5_revoke_suppressed_event():
    from karvyloop.cognition.memory import MemoryManager
    mem = MemoryManager()
    app = _pref_app(None, mem)
    rev = RevocationStore(cooldown_days=14.0)
    rev.mark("输出用表格", now=100.0)
    app.state.decision_revocations = rev
    wrote, _ = await crystallize_candidates(
        app, [{"content": "输出用表格", "kind": "taste", "explicit": True}], now=200.0)
    assert wrote == 0   # 抑制窗内不学回来
    got = _trace(app).query(PREF_TRACE_TASK, kind="revoke_suppressed")
    assert len(got) == 1
    assert got[0].payload["cooldown_days"] == 14.0
    _assert_capped(got)


# ---- B-5 #6 surface_triggered(反投降恰好跨阈)----


def test_b5_surface_triggered_once_at_threshold():
    from karvyloop.console.decision_card_wire import judge_card
    app = _mk_app()
    th = 5   # SurfaceTracker.threshold 默认
    for i in range(th + 2):   # 连按 7 次零修改 ACCEPT:只在恰好第 5 次落一条
        judge_card(app, proposal_id=f"p-{i}", decision="ACCEPT", engaged=False)
    entries = [e for tid in _trace(app).all_tasks()
               for e in _trace(app).query(tid, kind="surface_triggered")]
    assert len(entries) == 1
    assert entries[0].payload["threshold"] == th
    assert entries[0].task_id == f"p-{th - 1}"   # 跨阈那一张卡
    _assert_capped(entries)


def test_b5_surface_fail_soft_no_trace():
    from karvyloop.console.decision_card_wire import judge_card
    app = _mk_app(trace=False)
    for i in range(6):
        out = judge_card(app, proposal_id="p", decision="ACCEPT", engaged=False)
    assert out["ok"] and out["needs_recheck"]   # 行为一字不变


# ---- B-5 #7 defer_aged_out(DEFER 熬过 48h 首次重浮)----


def test_b5_defer_aged_out_once_and_reset_on_redefer():
    from karvyloop.console.proposals import trace_aged_defers
    from karvyloop.karvy.proposal_registry import AGING_THRESHOLD_S
    app = _mk_app()
    reg = PendingProposalRegistry()
    app.state.proposal_registry = reg
    p = _proposal()
    reg.register(p, now=0.0)
    reg.decide(p.proposal_id, "DEFER", now=10.0)
    # 未满 48h:不报
    assert reg.pop_aged_defers(now=10.0 + AGING_THRESHOLD_S - 1) == []
    # 满 48h:报一次,落埋点
    aged = reg.pop_aged_defers(now=10.0 + AGING_THRESHOLD_S + 1)
    assert len(aged) == 1 and aged[0]["proposal_id"] == p.proposal_id
    # 幂等:同一轮 DEFER 不再报
    assert reg.pop_aged_defers(now=10.0 + AGING_THRESHOLD_S + 999) == []
    # 再次 DEFER → 重新计
    reg.decide(p.proposal_id, "DEFER", now=1e6)
    assert len(reg.pop_aged_defers(now=1e6 + AGING_THRESHOLD_S + 1)) == 1
    # console 侧翻译成 Trace(带阈值当前值)
    reg2 = PendingProposalRegistry()
    app.state.proposal_registry = reg2
    p2 = _proposal(pid="p-defer2")
    reg2.register(p2, now=0.0)
    reg2.decide(p2.proposal_id, "DEFER", now=0.0)
    # trace_aged_defers 用真实 now → 让 deferred_at 落在很久以前使其达阈
    reg2._meta[p2.proposal_id]["deferred_at"] = 1.0
    n = trace_aged_defers(app)
    assert n == 1
    got = _trace(app).query(p2.proposal_id, kind="defer_aged_out")
    assert len(got) == 1 and got[0].payload["threshold_s"] == AGING_THRESHOLD_S
    _assert_capped(got)


def test_b5_defer_aged_fail_soft():
    from karvyloop.console.proposals import trace_aged_defers
    app = _mk_app(trace=False)     # 无 registry 也无 trace
    assert trace_aged_defers(app) == 0


# ---- Task.proposal_id 加性兼容 ----


def test_task_proposal_id_additive_compat(tmp_path):
    from karvyloop.console.tasks import TaskRecord, TaskRegistry, TaskStore
    # 老 tasks.json(无 proposal_id 键)读回不炸,补 ""
    old = TaskRecord.from_dict({"id": "t1", "who": "小卡", "intent": "x", "status": "done"})
    assert old.proposal_id == ""
    # 新记录:start 透传 → to_dict/detail/落盘/读回全带
    store = TaskStore(tmp_path / "tasks.json")
    reg = TaskRegistry(cap=5, store=store)
    tid = reg.start(who="小卡", intent="重跑", proposal_id="p-abc")
    assert reg.get(tid)["proposal_id"] == "p-abc"
    reg2 = TaskRegistry(cap=5, store=TaskStore(tmp_path / "tasks.json"))
    assert reg2.get(tid)["proposal_id"] == "p-abc"
