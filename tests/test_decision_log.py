"""test_decision_log — 最近拍板流水(只读回看)+ 端点。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console.decision_log import DecisionLog  # noqa: E402


def test_record_and_recent_newest_first():
    log = DecisionLog()
    log.record(decision="ACCEPT", summary="A", now=1.0)
    log.record(decision="REJECT", summary="B", now=2.0)
    log.record(decision="DEFER", summary="C", now=3.0)
    r = log.recent(10)
    assert [x["summary"] for x in r] == ["C", "B", "A"]   # newest-first
    assert r[0]["decision"] == "DEFER"


def test_invalid_decision_ignored():
    log = DecisionLog()
    log.record(decision="MAYBE", summary="x")
    log.record(decision="", summary="y")
    assert log.recent(10) == []


def test_recent_is_display_window_retention_keeps_history():
    """recent(N) 是显示窗(你给上限);留存(_RETAIN=5000)保住历史给审计——60 条全留,不再 50 就丢。"""
    log = DecisionLog()
    for i in range(60):
        log.record(decision="ACCEPT", summary=str(i), now=float(i))
    assert len(log.recent(100)) == 60          # 全留(不再 50 截断)
    assert log.recent(100)[0]["summary"] == "59"
    assert len(log.recent(20)) == 20           # recent 只是显示窗
    assert log.count() == 60                   # 审计:留存条数


def test_query_filters_by_time_and_decision():
    log = DecisionLog()
    log.record(decision="ACCEPT", summary="a", now=10.0)
    log.record(decision="REJECT", summary="b", now=20.0)
    log.record(decision="ACCEPT", summary="c", now=30.0)
    log.record(decision="REVOKE", summary="d", now=40.0)
    # 时间窗
    assert [x["summary"] for x in log.query(since=20.0)] == ["d", "c", "b"]   # newest-first
    assert [x["summary"] for x in log.query(until=20.0)] == ["b", "a"]
    assert [x["summary"] for x in log.query(since=15.0, until=35.0)] == ["c", "b"]
    # 决定类型
    assert [x["summary"] for x in log.query(decision="ACCEPT")] == ["c", "a"]
    assert [x["summary"] for x in log.query(decision="revoke")] == ["d"]      # 大小写不敏感
    # limit
    assert len(log.query(limit=2)) == 2


def test_limit_and_zero():
    log = DecisionLog()
    for i in range(5):
        log.record(decision="ACCEPT", summary=str(i), now=float(i))
    assert len(log.recent(2)) == 2
    assert log.recent(0) == []


def test_persistence_round_trip(tmp_path):
    p = tmp_path / "decision_log.json"
    log = DecisionLog(path=p)
    log.record(decision="ACCEPT", summary="持久", proposal_id="pid-1", reason="ok", kind="run_task")
    assert p.exists()
    log2 = DecisionLog(path=p)            # 重启:从盘恢复
    r = log2.recent(10)
    assert len(r) == 1 and r[0]["summary"] == "持久" and r[0]["proposal_id"] == "pid-1"


def test_corrupt_file_starts_empty(tmp_path):
    p = tmp_path / "decision_log.json"
    p.write_text("{not json", encoding="utf-8")
    log = DecisionLog(path=p)
    assert log.recent(10) == []


# ---- 端点 ----
def _client():
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    return app, TestClient(app)


def test_endpoint_recent_returns_records():
    app, c = _client()
    log = DecisionLog()
    log.record(decision="ACCEPT", summary="部署预发", now=1.0)
    log.record(decision="REJECT", summary="删生产库", now=2.0)
    app.state.decision_log = log
    r = c.get("/api/decisions/recent?limit=10").json()
    assert [x["summary"] for x in r["decisions"]] == ["删生产库", "部署预发"]


def test_endpoint_empty_without_log():
    app, c = _client()
    r = c.get("/api/decisions/recent").json()
    assert r["decisions"] == []


def test_audit_endpoint_filters_and_total():
    """#6 审计端点:按时间窗 + 决定类型查,带 total(留存条数,审计完整性)。"""
    app, c = _client()
    log = DecisionLog()
    log.record(decision="ACCEPT", summary="部署预发", now=10.0)
    log.record(decision="REJECT", summary="删生产库", now=20.0)
    log.record(decision="REVOKE", summary="撤回偏好", now=30.0)
    app.state.decision_log = log
    r = c.get("/api/decisions/audit?since=15").json()
    assert r["ok"] is True and r["total"] == 3
    assert [x["summary"] for x in r["decisions"]] == ["撤回偏好", "删生产库"]   # since 过滤 + newest-first
    r2 = c.get("/api/decisions/audit?decision=REVOKE").json()
    assert [x["summary"] for x in r2["decisions"]] == ["撤回偏好"] and r2["returned"] == 1


def test_audit_endpoint_no_log():
    app, c = _client()
    assert c.get("/api/decisions/audit").json()["ok"] is False
