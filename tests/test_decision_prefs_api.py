"""test_decision_prefs_api — 你可编辑的决策偏好面(docs/02 §11 P1)。

你掌舵的前提 = 决策画像对你可见 + 可控(看/确认/编辑/删)。
"""
from __future__ import annotations

import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.crystallize.decision_pref import make_decision_pref_belief  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402


def _client():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mem = MemoryManager()
    mem.write(make_decision_pref_belief("先写测试", "constraint", strength=0.7,
                                        status="provisional", now=1.0))
    mem.write(make_decision_pref_belief("用表格", "taste", strength=0.5,
                                        status="confirmed", now=2.0))
    mem.write(Belief(content="普通事实", provenance={"source": "conversation", "kind": "fact"},
                     freshness_ts=3.0, scope="personal"))
    app.state.memory = mem
    return TestClient(app)


def test_list_decision_prefs_sorted():
    prefs = _client().get("/api/decision_prefs").json()["prefs"]
    assert len(prefs) == 2                                  # 普通事实不算决策偏好
    assert prefs[0]["strength"] >= prefs[1]["strength"]     # 按 strength 排
    p0 = prefs[0]
    assert {"content", "kind", "strength", "status", "applies", "evidence_n"} <= set(p0)


def test_memory_panel_excludes_decision_prefs():
    contents = [b["content"] for b in _client().get("/api/memory").json()["beliefs"]]
    assert "普通事实" in contents
    assert "先写测试" not in contents and "用表格" not in contents  # 决策偏好不在知识面双显


def test_op_delete():
    c = _client()
    assert c.post("/api/decision_prefs/op", json={"op": "delete", "content": "先写测试"}).json()["ok"]
    contents = [p["content"] for p in c.get("/api/decision_prefs").json()["prefs"]]
    assert "先写测试" not in contents


def test_op_revoke_archives_and_logs():
    """显式撤回(README'易撤回'兑现):移出活库 + 进决策流水(可审计'是我撤的')。"""
    from karvyloop.console.decision_log import DecisionLog
    c = _client()
    c.app.state.decision_log = DecisionLog()
    assert c.post("/api/decision_prefs/op", json={"op": "revoke", "content": "先写测试"}).json()["ok"]
    contents = [p["content"] for p in c.get("/api/decision_prefs").json()["prefs"]]
    assert "先写测试" not in contents                       # 移出活库
    recent = c.get("/api/decisions/recent").json()["decisions"]
    assert any(d["decision"] == "REVOKE" and "先写测试" in d["summary"] for d in recent)  # 留审计回执
    # 撤回有牙:打了抑制墓碑 → 冷却窗内别自动学回来
    from karvyloop.crystallize.decision_pref import norm_content
    rev = c.app.state.decision_revocations
    assert rev.is_suppressed(norm_content("先写测试"))


def test_op_revoke_confirmed_allowed():
    """confirmed 的也能由你撤(不固化你 凌驾 尊重确认)—— delete 早能,revoke 也必须能。"""
    c = _client()
    assert c.post("/api/decision_prefs/op", json={"op": "revoke", "content": "用表格"}).json()["ok"]
    contents = [p["content"] for p in c.get("/api/decision_prefs").json()["prefs"]]
    assert "用表格" not in contents


def test_op_revoke_no_log_still_ok():
    """没接 decision_log 也不该崩(落审计是尽力而为,不阻塞撤回)。"""
    c = _client()   # 没设 decision_log
    assert c.post("/api/decision_prefs/op", json={"op": "revoke", "content": "先写测试"}).json()["ok"]


def test_confirm_clears_stale_revocation_tombstone():
    """你又确认一条 → 解除它的旧撤回抑制(否则墓碑会压住加固)。"""
    from karvyloop.console.decision_log import RevocationStore
    from karvyloop.crystallize.decision_pref import norm_content
    c = _client()
    rev = c.app.state.decision_revocations = RevocationStore()
    rev.mark(norm_content("先写测试"))                       # 假设之前撤过
    assert rev.is_suppressed(norm_content("先写测试"))
    assert c.post("/api/decision_prefs/op", json={"op": "confirm", "content": "先写测试"}).json()["ok"]
    assert not rev.is_suppressed(norm_content("先写测试"))   # 确认后墓碑解除


def test_op_confirm_upgrades_status():
    c = _client()
    assert c.post("/api/decision_prefs/op", json={"op": "confirm", "content": "先写测试"}).json()["ok"]
    by = {p["content"]: p for p in c.get("/api/decision_prefs").json()["prefs"]}
    assert by["先写测试"]["status"] == "confirmed"


def test_op_edit_changes_content():
    c = _client()
    r = c.post("/api/decision_prefs/op",
               json={"op": "edit", "content": "先写测试", "new_content": "上线前必须有测试"})
    assert r.json()["ok"]
    contents = [p["content"] for p in c.get("/api/decision_prefs").json()["prefs"]]
    assert "上线前必须有测试" in contents and "先写测试" not in contents


def test_op_edit_empty_rejected():
    c = _client()
    r = c.post("/api/decision_prefs/op", json={"op": "edit", "content": "先写测试", "new_content": "  "})
    assert not r.json()["ok"]


def test_op_missing_pref_honest_fail():
    r = _client().post("/api/decision_prefs/op", json={"op": "delete", "content": "压根没有"})
    assert not r.json()["ok"]


def test_op_invalid_op_422():
    # op pattern 校验:非 delete/confirm/edit → 422
    r = _client().post("/api/decision_prefs/op", json={"op": "frobnicate", "content": "x"})
    assert r.status_code == 422


def test_no_memory_graceful():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    c = TestClient(app)   # 没设 memory
    assert c.get("/api/decision_prefs").json()["prefs"] == []
    assert c.post("/api/decision_prefs/op",
                  json={"op": "delete", "content": "x"}).json()["ok"] is False


def test_stats_endpoint_prefs_and_outcomes():
    from karvyloop.console.decision_stats import DecisionStats
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mem = MemoryManager()
    mem.write(make_decision_pref_belief("先写测试", "constraint", strength=0.7,
                                        status="provisional", now=1.0))
    mem.write(make_decision_pref_belief("用表格", "taste", strength=0.5,
                                        status="confirmed", now=2.0))
    app.state.memory = mem
    stats = DecisionStats()
    stats.record("ACCEPT"); stats.record("REJECT")
    app.state.decision_stats = stats
    r = TestClient(app).get("/api/decision_prefs/stats").json()
    assert r["prefs_total"] == 2 and r["confirmed"] == 1
    assert r["by_kind"] == {"constraint": 1, "taste": 1}
    assert r["decisions_total"] == 2
    assert r["accept_rate"] == 0.5
