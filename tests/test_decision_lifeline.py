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


# ---- 二刀:✍️ judged 站(T2 真数据)+ 🧭 aligned 投影 ----


def test_lifeline_judged_full_projects_aligned_row():
    """T2 带卡缓存事实(card_seen)→ judged 行全字段 + 兼喂一条 aligned 行(同 ts 同 trace_ref 源)。"""
    app = _mk_app()
    app.state.main_loop.trace.append(TraceEntry(
        task_id=PID, kind="decision_judged", ts=15.0,
        payload={"decision": "ACCEPT", "engaged": True, "basis": "预算内,先小规模试",
                 "edits_n": 1, "edited": "输出必须带引用来源", "card_seen": True,
                 "aligned": 2, "aligned_omitted": 1, "violations": 1, "high_value": True}))
    out = api_decision_lifeline(PID, _req(app))
    assert out["ok"]
    types_seen = sorted(e["type"] for e in out["events"])
    assert types_seen == ["aligned", "judged"]
    j = next(e for e in out["events"] if e["type"] == "judged")
    assert j["detail"] == "预算内,先小规模试"
    assert j["engaged"] is True and j["card_seen"] is True
    assert j["edits_n"] == 1 and j["edited"] == "输出必须带引用来源"
    assert j["aligned"] == 2 and j["violations"] == 1
    a = next(e for e in out["events"] if e["type"] == "aligned")
    assert a["aligned"] == 2 and a["aligned_omitted"] == 1 and a["violations"] == 1


def test_lifeline_judged_nocard_no_aligned_row():
    """T2 缓存 miss(没看过卡)→ judged 行 card_seen=False,**不**投影 aligned 行(缺省诚实)。"""
    app = _mk_app()
    app.state.main_loop.trace.append(TraceEntry(
        task_id=PID, kind="decision_judged", ts=15.0,
        payload={"decision": "ACCEPT", "engaged": False, "card_seen": False}))
    out = api_decision_lifeline(PID, _req(app))
    assert out["ok"]
    assert [e["type"] for e in out["events"]] == ["judged"]
    j = out["events"][0]
    assert j["engaged"] is False and j["card_seen"] is False
    assert "aligned" not in j and "edits_n" not in j


# ---- 二刀:🔧 执行步下钻(slice C 成败事实透传)----


def test_lifeline_steps_carry_tool_facts():
    """steps 带 input(下钻长摘要)+ ok/err(slice C 事实);老格式条目(无 ok 键)诚实缺省。"""
    app = _mk_app()
    tr = app.state.main_loop.trace
    with run_scope() as rid:
        tr.append(TraceEntry(task_id="drive-1", kind="atom_run", ts=25.0,
                             payload={"tool_calls": [
                                 {"name": "WebFetch", "input": "https://x", "ok": True,
                                  "error_reason": ""},
                                 {"name": "Bash", "input": "cat /etc/shadow", "ok": False,
                                  "error_reason": "capability_denied: 敏感路径"},
                                 {"name": "Read", "input": "notes.md"},   # 老格式:无 ok
                             ]}))
    tr.append(TraceEntry(task_id=PID, kind="decision_dispatched", ts=30.0,
                         payload={"ok": True, "detail": "done", "run_id": rid,
                                  "decision": "ACCEPT"}))
    out = api_decision_lifeline(PID, _req(app))
    assert out["ok"]
    s0, s1, s2 = out["steps"]
    assert s0["ok"] is True and "err" not in s0 and s0["input"] == "https://x"
    assert s1["ok"] is False and s1["err"] == "capability_denied: 敏感路径"
    assert "ok" not in s2 and "err" not in s2       # 老格式不标成败(不编)
    assert all("gist" in s and "input" in s for s in (s0, s1, s2))


# ---- 三刀:♻ learned 站(批次级归因,绝不编逐条对应)----

PREF_TASK = "decision_pref"   # 与 decision_wire.PREF_TRACE_TASK 同值(契约锁死)


def _seed_decided(tr, ts: float) -> None:
    tr.append(TraceEntry(task_id=PID, kind="decision_made", ts=ts,
                         payload={"decision": "ACCEPT", "kind": "route_to_role", "reason": "r"}))


def test_lifeline_learned_first_burst_batch_level():
    """拍板锚之后的**第一簇**偏好事件 → learned 行(attribution=batch + learned_total);
    锚之前的事件、第二簇(隔 > gap)都不算 —— 批次级就只认时间就近那一批。"""
    from karvyloop.console.routes_system import _LEARNED_CLUSTER_GAP_S
    app = _mk_app()
    tr = app.state.main_loop.trace
    _seed_decided(tr, 20.0)
    # 锚之前:这次拍板不可能喂它 → 排除
    tr.append(TraceEntry(task_id=PREF_TASK, kind="decision_pref_reinforced", ts=5.0,
                         payload={"content": "早于拍板的加固", "strength_before": 0.5,
                                  "strength_after": 0.6}))
    # 第一簇(锚后)
    tr.append(TraceEntry(task_id=PREF_TASK, kind="decision_pref_reinforced", ts=25.0,
                         payload={"content": "碰生产先写测试", "strength_before": 0.5,
                                  "strength_after": 0.6}))
    tr.append(TraceEntry(task_id=PREF_TASK, kind="decision_pref_weakened", ts=26.0,
                         payload={"content": "输出用表格", "strength_before": 0.7,
                                  "strength_after": 0.4}))
    tr.append(TraceEntry(task_id=PREF_TASK, kind="pref_auto_revoked", ts=27.0,
                         payload={"content": "先问预算", "strength_after": 0.2}))
    # 第二簇(隔 > gap):另一批拍板喂的 → 不算这条决策的回流
    tr.append(TraceEntry(task_id=PREF_TASK, kind="decision_pref_reinforced",
                         ts=27.0 + _LEARNED_CLUSTER_GAP_S + 1,
                         payload={"content": "第二批的加固", "strength_before": 0.6,
                                  "strength_after": 0.7}))
    out = api_decision_lifeline(PID, _req(app))
    assert out["ok"]
    learned = [e for e in out["events"] if e["type"] == "learned"]
    assert [x["pref_event"] for x in learned] == ["reinforced", "weakened", "revoked"]
    assert [x["detail"] for x in learned] == ["碰生产先写测试", "输出用表格", "先问预算"]
    assert all(x["attribution"] == "batch" for x in learned)   # 诚实:只认批次级
    assert learned[0]["learned_total"] == 3
    assert learned[0]["strength_before"] == 0.5 and learned[0]["strength_after"] == 0.6
    assert "strength_before" not in learned[2]                 # revoked 埋点没有 before → 不编
    # events 整体仍按 ts 排(learned 混排在 decided 之后)
    ts_list = [e["ts"] for e in out["events"]]
    assert ts_list == sorted(ts_list)


def test_lifeline_learned_needs_decided_anchor():
    """无拍板锚(只有 born)→ 不聚合回流(宁缺勿编);pref 桶里有事件也不冒领。"""
    app = _mk_app()
    tr = app.state.main_loop.trace
    tr.append(TraceEntry(task_id=PID, kind="decision_point", ts=10.0,
                         payload={"kind": "route_to_role", "summary": "s", "basis": "b",
                                  "strength": 0.5}))
    tr.append(TraceEntry(task_id=PREF_TASK, kind="decision_pref_reinforced", ts=25.0,
                         payload={"content": "碰生产先写测试", "strength_before": 0.5,
                                  "strength_after": 0.6}))
    out = api_decision_lifeline(PID, _req(app))
    assert out["ok"]
    assert [e["type"] for e in out["events"]] == ["born"]


def test_lifeline_learned_from_stub_anchor():
    """埋点前老决策(decision_log 存根当锚)也能拿到批次级回流 —— 锚是拍板 ts,不挑来源。"""
    app = _mk_app()
    log = DecisionLog()
    log.record(decision="ACCEPT", summary="老决策", proposal_id=PID, reason="理由", now=99.0)
    app.state.decision_log = log
    app.state.main_loop.trace.append(TraceEntry(
        task_id=PREF_TASK, kind="decision_pref_reinforced", ts=105.0,
        payload={"content": "碰生产先写测试", "strength_before": 0.5, "strength_after": 0.6}))
    out = api_decision_lifeline(PID, _req(app))
    assert out["ok"] and out["stub"] is True
    learned = [e for e in out["events"] if e["type"] == "learned"]
    assert len(learned) == 1 and learned[0]["attribution"] == "batch"
