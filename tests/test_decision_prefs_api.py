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


def _client_with_evidence(evidence: list):
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mem = MemoryManager()
    mem.write(make_decision_pref_belief("先写测试", "constraint", strength=0.7,
                                        evidence=evidence, now=1.0))
    app.state.memory = mem
    return TestClient(app)


def test_evidence_detail_returned():
    """Q3 证据可见:这条偏好从你哪几次拍板学来 —— 回明细(何时/拍了什么/一句摘要),不只个数。"""
    ev = [{"ts": 100.0, "decision": "ACCEPT", "gist": "按你说的先加了测试"},
          {"ts": 200.0, "decision": "EDIT", "gist": "你改了再批"}]
    p = _client_with_evidence(ev).get("/api/decision_prefs").json()["prefs"][0]
    assert p["evidence_n"] == 2
    assert p["evidence"] == [
        {"ts": 200.0, "decision": "EDIT", "gist": "你改了再批"},        # 新的在前
        {"ts": 100.0, "decision": "ACCEPT", "gist": "按你说的先加了测试"},
    ]


def test_evidence_empty_graceful():
    """无证据(老数据 evidence 空)→ evidence: [](优雅空,前端显诚实文案)。"""
    p = _client_with_evidence([]).get("/api/decision_prefs").json()["prefs"][0]
    assert p["evidence_n"] == 0 and p["evidence"] == []


def test_evidence_legacy_float_ts():
    """兼容旧数据:早期 evidence 只存时间戳(float)→ 保留 ts,decision/gist 诚实留空(不编)。"""
    p = _client_with_evidence([123.0]).get("/api/decision_prefs").json()["prefs"][0]
    assert p["evidence"] == [{"ts": 123.0, "decision": "", "gist": ""}]


def test_evidence_capped_and_truncated():
    """payload 别爆:只回最近 5 条(新的在前)+ gist 截断;垃圾形态(str)跳过不编。"""
    ev: list = ["garbage"] + [
        {"ts": float(i), "decision": "ACCEPT", "gist": f"第{i}次" + "长" * 300} for i in range(1, 8)
    ]
    p = _client_with_evidence(ev).get("/api/decision_prefs").json()["prefs"][0]
    assert len(p["evidence"]) == 5
    assert [e["ts"] for e in p["evidence"]] == [7.0, 6.0, 5.0, 4.0, 3.0]   # 最近 5,新的在前
    assert all(len(e["gist"]) <= 120 for e in p["evidence"])
    assert p["evidence"][0]["gist"].startswith("第7次")


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
