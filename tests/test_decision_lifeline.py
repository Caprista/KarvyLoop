"""test_decision_lifeline — GET /api/decision/{pid}/lifeline 三态契约(docs/85 Part B)。

契约(与 skill_lifecycle 同构,别改形状):
{"ok","proposal_id","stub","events":[{"ts","type","detail","trace_ref",…}],
 "steps":[{"ts","name","gist"}],"tokens":int|null,"task":{…}|null}
- 全量态:T1/T3/T4 都在 + run_id 工具步投影 + 任务态 + token。
- 部分态:只有部分站 → 只返真有的,不编(缺站前端显诚实空位)。
- 存根态:埋点前老决策(Trace 无痕、decision_log 有流水)→ stub=true + 单条 decided。
K4 只读:端点全部从 Trace/decision_log/任务态/token 账本聚合,零写入。
"""
from __future__ import annotations

import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.trace import TraceEntry, TraceStore, run_scope  # noqa: E402
from karvyloop.console.decision_log import DecisionLog  # noqa: E402
from karvyloop.console.routes_system import api_decision_lifeline  # noqa: E402
from karvyloop.console.tasks import TaskRegistry  # noqa: E402


class _State:
    pass


class _App:
    def __init__(self) -> None:
        self.state = _State()


def _req(app) -> types.SimpleNamespace:
    return types.SimpleNamespace(app=app)


def _mk_app(*, trace: bool = True) -> _App:
    app = _App()
    if trace:
        app.state.main_loop = types.SimpleNamespace(trace=TraceStore())
    return app


class _Ledger:
    def __init__(self, table):
        self._t = table

    def task_total(self, task_id):
        return self._t.get(task_id, 0)


PID = "route_to_role-1-abcd1234"


def test_lifeline_full_state():
    app = _mk_app()
    tr = app.state.main_loop.trace
    tr.append(TraceEntry(task_id=PID, kind="decision_point", ts=10.0,
                         payload={"kind": "route_to_role", "summary": "交给分析师",
                                  "basis": "你连着三次这么派", "strength": 0.8}))
    tr.append(TraceEntry(task_id=PID, kind="decision_made", ts=20.0,
                         payload={"decision": "ACCEPT", "reason": "值得",
                                  "kind": "route_to_role", "edited": []}))
    with run_scope() as rid:
        tr.append(TraceEntry(task_id="drive-1", kind="atom_run", ts=25.0,
                             payload={"tool_calls": [{"name": "WebFetch", "input": "https://x"},
                                                     {"name": "Bash", "input": "ls"}]}))
    tr.append(TraceEntry(task_id=PID, kind="decision_dispatched", ts=30.0,
                         payload={"ok": True, "detail": "已由分析师执行", "run_id": rid,
                                  "verdict": "solved", "decision": "ACCEPT"}))
    reg = TaskRegistry(cap=5)
    tid = reg.start(who="分析师", intent="调研", proposal_id=PID)
    reg.finish(tid, result="报告写完了")
    app.state.task_registry = reg
    app.state.token_ledger = _Ledger({PID: 1234})

    out = api_decision_lifeline(PID, _req(app))
    assert out["ok"] and out["stub"] is False
    types_seen = [e["type"] for e in out["events"]]
    assert types_seen == ["born", "decided", "dispatched"]     # 按 ts 排
    assert out["events"][0]["strength"] == 0.8
    assert out["events"][1]["decision"] == "ACCEPT"
    assert out["events"][2]["verdict"] == "solved"
    assert [s["name"] for s in out["steps"]] == ["WebFetch", "Bash"]   # run_id 工具步投影
    assert out["tokens"] == 1234
    assert out["task"]["id"] == tid and out["task"]["status"] == "done"


def test_lifeline_partial_state():
    """只有 T1(比如卡还挂着/埋点部分可用)→ 只返 born,其余诚实缺。"""
    app = _mk_app()
    app.state.main_loop.trace.append(TraceEntry(
        task_id=PID, kind="decision_point", ts=10.0,
        payload={"kind": "route_to_role", "summary": "s", "basis": "b", "strength": 0.5}))
    out = api_decision_lifeline(PID, _req(app))
    assert out["ok"] and out["stub"] is False
    assert [e["type"] for e in out["events"]] == ["born"]
    assert out["steps"] == [] and out["task"] is None


def test_lifeline_stub_state_from_decision_log():
    """埋点前老决策:Trace 无痕、decision_log 有流水 → 拍板存根 + stub=true(一句实话)。"""
    app = _mk_app()
    log = DecisionLog()
    log.record(decision="ACCEPT", summary="老决策", proposal_id=PID, reason="当时的理由", now=99.0)
    app.state.decision_log = log
    out = api_decision_lifeline(PID, _req(app))
    assert out["ok"] and out["stub"] is True
    assert len(out["events"]) == 1
    ev = out["events"][0]
    assert ev["type"] == "decided" and ev["decision"] == "ACCEPT" and ev["ts"] == 99.0
    assert ev["trace_ref"] == ""    # 存根:无 Trace 证据,诚实空


def test_lifeline_not_found():
    app = _mk_app()
    app.state.decision_log = DecisionLog()
    out = api_decision_lifeline("p-nothing", _req(app))
    assert out["ok"] is False and out["events"] == [] and out["stub"] is False


def test_lifeline_silenced_marked_auto():
    """静音自动兑现的决策:dispatched 站带 auto 标,拍板站诚实留空(非你拍板)。"""
    app = _mk_app()
    app.state.main_loop.trace.append(TraceEntry(
        task_id=PID, kind="silenced_decision", ts=5.0,
        payload={"ok": True, "detail": "按你的口味先办了", "predicted": "ACCEPT"}))
    out = api_decision_lifeline(PID, _req(app))
    assert out["ok"]
    assert [e["type"] for e in out["events"]] == ["dispatched"]
    assert out["events"][0]["auto"] is True
