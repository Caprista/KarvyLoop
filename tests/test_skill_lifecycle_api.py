"""test_skill_lifecycle_api — GET /api/skill_lifecycle(技能事件时间线,前端时间线视图契约)。

契约(前端并行在接,形状别改):
  {"skills": [{"name", "sig", "events": [{"ts", "type", "detail", "trace_ref"}]}]}
  type ∈ crystallized / revised / rerun;数据不够的 type 诚实不出现(improved 目前
  improve.py 不留 Trace 痕 → 永不出现,不编)。

造数纪律:真 trace.append 走生产 payload 形态(main_loop.drive / revision._writeback_revision
同款字段),不 mock 数据形状。
"""
from __future__ import annotations

import pathlib
import sys
import types

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.trace import TraceEntry, TraceStore  # noqa: E402
from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def _client_with_trace(trace) -> TestClient:
    ml = types.SimpleNamespace(trace=trace)
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=ml)
    return TestClient(app)


def _seed(trace) -> None:
    """真生产 payload 形态造一条技能的完整生命线 + 各类噪声。"""
    # 结晶(main_loop.drive 的 kind="crystallize" 形态)
    trace.append(TraceEntry(task_id="t1", kind="crystallize", ts=100.0, source="main_loop",
                            payload={"sig": "s-a", "name": "skill_report",
                                     "when_to_use": "写周报", "trace_ref": "trace://c1"}))
    # 召回命中重跑(main_loop.drive 的 eval_fact + skill_rerun 标,29112e9 修订闭环数据源)
    trace.append(TraceEntry(task_id="t2", kind="eval_fact", ts=110.0, source="main_loop",
                            payload={"sig": "s-a", "success": True, "verified": True,
                                     "steps": 2, "trace_ref": "trace://r1",
                                     "skill_name": "skill_report", "skill_rerun": True}))
    trace.append(TraceEntry(task_id="t3", kind="eval_fact", ts=115.0, source="main_loop",
                            payload={"sig": "s-a", "success": False, "verified": False,
                                     "steps": 5, "trace_ref": "trace://r2",
                                     "skill_name": "skill_report", "skill_rerun": True}))
    # 修订(revision._writeback_revision 的 kind="skill_revision" 形态)
    trace.append(TraceEntry(task_id="revision:s-a", kind="skill_revision", ts=120.0,
                            source="revision",
                            payload={"sig": "s-a", "skill_name": "skill_report",
                                     "mode": "auto", "n_samples": 5,
                                     "trigger": "confidence=0.40(<0.55触发) bad=3/8(≥2触发)",
                                     "trace_refs": ["trace://r2"], "note": "调整步骤顺序"}))
    # 噪声:普通 eval_fact(无 skill_rerun 标)/ atom_run / 无 sig 事件 —— 都不该进时间线
    trace.append(TraceEntry(task_id="t4", kind="eval_fact", ts=130.0, source="main_loop",
                            payload={"sig": "s-a", "success": True, "verified": True,
                                     "steps": 1, "trace_ref": "trace://x"}))
    trace.append(TraceEntry(task_id="t4", kind="atom_run", ts=131.0, source="main_loop",
                            payload={"atom_id": "a1", "success": True, "tool_calls": [],
                                     "trace_ref": "trace://x", "ts": 131.0}))
    trace.append(TraceEntry(task_id="t5", kind="crystallize", ts=132.0, source="main_loop",
                            payload={"name": "no_sig_skill", "when_to_use": "x",
                                     "trace_ref": "trace://y"}))  # 无 sig → 归不了属,跳过


def test_lifecycle_aggregates_per_skill_timeline():
    trace = TraceStore()
    _seed(trace)
    r = _client_with_trace(trace).get("/api/skill_lifecycle")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"skills"}
    assert len(data["skills"]) == 1          # 只有 s-a;无 sig 的结晶事件被诚实跳过
    s = data["skills"][0]
    assert s["name"] == "skill_report" and s["sig"] == "s-a"
    events = s["events"]
    # 按 ts 升序;噪声(普通 eval_fact / atom_run)不进来
    assert [e["type"] for e in events] == ["crystallized", "rerun", "rerun", "revised"]
    assert [e["ts"] for e in events] == [100.0, 110.0, 115.0, 120.0]
    # 每条都有四个契约字段
    for e in events:
        assert set(e.keys()) == {"ts", "type", "detail", "trace_ref"}
    # detail / trace_ref 可回链
    assert events[0]["detail"] == "写周报" and events[0]["trace_ref"] == "trace://c1"
    assert events[1]["detail"] == "success" and events[1]["trace_ref"] == "trace://r1"
    assert events[2]["detail"] == "failure" and events[2]["trace_ref"] == "trace://r2"
    assert events[3]["detail"] == "auto: 调整步骤顺序"
    assert events[3]["trace_ref"].startswith("revision:s-a:")   # 审计事件自身 entry ref
    # improved:数据不可得 → 诚实不出现
    assert all(e["type"] != "improved" for e in events)


def test_lifecycle_orders_recently_active_first():
    trace = TraceStore()
    _seed(trace)
    trace.append(TraceEntry(task_id="t9", kind="crystallize", ts=999.0, source="main_loop",
                            payload={"sig": "s-b", "name": "skill_new",
                                     "when_to_use": "新活", "trace_ref": "trace://n"}))
    data = _client_with_trace(trace).get("/api/skill_lifecycle").json()
    assert [s["sig"] for s in data["skills"]] == ["s-b", "s-a"]   # 最近有动静的在前


def test_lifecycle_empty_without_main_loop_or_trace():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    r = TestClient(app).get("/api/skill_lifecycle")
    assert r.status_code == 200 and r.json() == {"skills": []}
    # 有 main_loop 但无 trace 属性同样诚实空表
    app2 = build_console_app(workbench=WorkbenchObserver(),
                             main_loop=types.SimpleNamespace())
    assert TestClient(app2).get("/api/skill_lifecycle").json() == {"skills": []}
