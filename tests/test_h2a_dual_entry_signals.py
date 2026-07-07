"""test_h2a_dual_entry_signals — 内部审计 P0-6 / P0-7 复现与收口锁。

P0-6(决策信号双入口):REST `/api/h2a_decide` 与 WS `h2a_decision` 两条传输路
必须给 record_decision_signals(单一接缝)喂**等价**信号(decision/reason/edits/域/角色),
且两条路拍板都要**真触发**结晶调度 —— REST 是 sync def 走线程池,线程里没有 running loop,
调度不能因此静默蒸发(否则 REST 拍板 = 白拍,两条学习回路不一致)。

P0-7(确认卡双重计数):crystallize_candidates 弹高价值确认卡 → ACCEPT 后,
同一偏好只允许被写一次、强度只 bump 一次;确认拍板本身不进样本缓冲(元循环闸),
不被口味对账开奖;重放同一 proposal_id(双击/回放)不得二次计数或绕过元循环闸。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.console import decision_wire  # noqa: E402
from karvyloop.karvy.atoms import Proposal  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.karvy.proposal_registry import (  # noqa: E402
    KIND_CONFIRM_DECISION_PREF,
    PendingProposalRegistry,
)


def _mk_app():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.proposal_registry = PendingProposalRegistry()
    return app


def _mk_card(*, habit_id: int = 1, kind: str = "crystallize_skill",
             summary: str = "把周报改成要点式", payload: dict | None = None) -> Proposal:
    return Proposal(
        summary=summary, options=("ACCEPT", "DEFER", "REJECT"), strength=0.8,
        evidence_refs=(), habit_id=habit_id, model_ref="", ts=1.0,
        kind=kind, payload=dict(payload or {"title": "旧标题"}),
    )


def _decide_body(pid: str, decision: str = "ACCEPT", *, reason: str = "",
                 edits: dict | None = None) -> dict:
    body = {"proposal_id": pid, "decision": decision, "reason": reason}
    if edits is not None:
        body["edits"] = edits
    return body


# ---------------------------------------------------------------- P0-6:参数等价(两条传输路)

class TestP06SignalParity:
    def test_rest_and_ws_feed_equivalent_signals(self):
        """REST 与 WS 各拍一次带 reason+edits 的板 → 样本缓冲里两条信号逐字段等价,
        且「改了再批」对照(原文→改文)两条路都折进了 reason(最富偏好信号不丢)。"""
        app = _mk_app()
        reg = app.state.proposal_registry
        a = _mk_card(habit_id=1)
        b = _mk_card(habit_id=2)   # 同 summary/payload,不同 pid
        reg.register(a)
        reg.register(b)
        client = TestClient(app)

        r = client.post("/api/h2a_decide", json=_decide_body(
            a.proposal_id, "ACCEPT", reason="标题要更凝练", edits={"title": "新标题"}))
        assert r.status_code == 200

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # snapshot
            ws.send_json({"type": "h2a_decision", "payload": _decide_body(
                b.proposal_id, "ACCEPT", reason="标题要更凝练", edits={"title": "新标题"})})
            msg = ws.receive_json()
            assert msg["type"] == "h2a_envelope" and not msg["payload"].get("error")

        samples = getattr(app.state, "decision_samples", [])
        assert len(samples) == 2, f"两条入口各 1 条样本,实际 {len(samples)}"
        rest_s, ws_s = samples
        for field in ("decision", "context", "reason", "scope", "domain", "role"):
            assert getattr(rest_s, field) == getattr(ws_s, field), (
                f"双入口信号不等价:字段 {field}: "
                f"REST={getattr(rest_s, field)!r} vs WS={getattr(ws_s, field)!r}")
        # 「改了再批」对照必须在(两条路同折)
        assert "旧标题" in rest_s.reason and "新标题" in rest_s.reason
        assert "标题要更凝练" in rest_s.reason

    def test_rest_reject_gets_same_placeholder_reason_as_ws(self):
        """REJECT 留空 reason:两条路都补同一个诚实占位(协议 A8),样本 reason 一致。"""
        from karvyloop.console.routes import DEFAULT_REJECT_REASON
        app = _mk_app()
        reg = app.state.proposal_registry
        a = _mk_card(habit_id=3)
        b = _mk_card(habit_id=4)
        reg.register(a)
        reg.register(b)
        client = TestClient(app)

        r = client.post("/api/h2a_decide", json=_decide_body(a.proposal_id, "REJECT"))
        assert r.status_code == 200
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "h2a_decision",
                          "payload": _decide_body(b.proposal_id, "REJECT")})
            msg = ws.receive_json()
            assert not msg["payload"].get("error")
        samples = getattr(app.state, "decision_samples", [])
        assert len(samples) == 2
        assert samples[0].reason == samples[1].reason == DEFAULT_REJECT_REASON


# ---------------------------------------------------------------- P0-6:结晶调度不因线程蒸发

class TestP06CrystallizeScheduling:
    def _spy(self, monkeypatch):
        calls: list = []

        async def _fake(_app):
            calls.append("crystallize")
            return 0

        monkeypatch.setattr(decision_wire, "maybe_crystallize_decisions", _fake)
        return calls

    def _wait(self, calls, deadline_s: float = 5.0):
        deadline = time.time() + deadline_s
        while not calls and time.time() < deadline:
            time.sleep(0.02)

    def test_rest_decide_triggers_crystallize_scheduling(self, monkeypatch):
        """REST 拍板(sync def 线程池,线程里无 running loop)也必须真触发结晶调度。
        修前:schedule_decision_crystallize 拿不到 loop 静默 return → REST 拍板永不结晶。"""
        app = _mk_app()
        card = _mk_card(habit_id=5)
        app.state.proposal_registry.register(card)
        calls = self._spy(monkeypatch)
        with TestClient(app) as client:   # 走 lifespan:主事件循环在启动时被记下
            r = client.post("/api/h2a_decide",
                            json=_decide_body(card.proposal_id, "ACCEPT", reason="好"))
            assert r.status_code == 200
            self._wait(calls)
        assert calls, "REST 拍板没有触发结晶调度 —— 两条学习回路不一致(REST 白拍)"

    def test_ws_decide_triggers_crystallize_scheduling(self, monkeypatch):
        """对照组:WS 路径(事件循环内)本来就触发 —— 锁住两条路一致。"""
        app = _mk_app()
        card = _mk_card(habit_id=6)
        app.state.proposal_registry.register(card)
        calls = self._spy(monkeypatch)
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "h2a_decision",
                          "payload": _decide_body(card.proposal_id, "ACCEPT", reason="好")})
            msg = ws.receive_json()
            assert not msg["payload"].get("error")
            self._wait(calls)
        assert calls


# ---------------------------------------------------------------- P0-7:确认卡只计一次

def _mk_memory(tmp_path):
    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.memory import MemoryManager
    return MemoryManager(store=BeliefStore(tmp_path / "beliefs.json"))


def _decision_prefs(app, content: str) -> list:
    from karvyloop.crystallize.decision_pref import is_decision_pref, norm_content
    return [b for sc in ("personal", "domain") for b in app.state.memory.index.all(sc)
            if is_decision_pref(b) and norm_content(b.content) == norm_content(content)]


CAND = {"content": "汇报一律先给结论再给细节", "kind": "constraint",
        "explicit": True, "scope": "global"}


def _seed_confirm_card(app) -> str:
    """跑一次结晶:写 1 条高价值 provisional(0.7)→ 弹恰好一张确认卡,返回卡 id。"""
    written, reinforced = asyncio.run(
        decision_wire.crystallize_candidates(app, [dict(CAND)]))
    assert written == 1 and reinforced == 0
    confirms = [p for p in app.state.proposal_registry.pending()
                if getattr(p, "kind", "") == KIND_CONFIRM_DECISION_PREF]
    assert len(confirms) == 1, "高价值偏好应恰好弹一张确认卡"
    return confirms[0].proposal_id


class TestP07ConfirmCardSingleCount:
    def _mk_full_app(self, tmp_path):
        from karvyloop.console.proposal_handlers import build_proposal_handlers
        from karvyloop.crystallize.taste_eval import TastePredictionStore
        app = _mk_app()
        app.state.memory = _mk_memory(tmp_path)
        app.state.proposal_handlers = build_proposal_handlers(app)
        app.state.taste_predictions = TastePredictionStore()
        return app

    def test_accept_confirm_card_writes_once_bumps_once(self, tmp_path):
        """弹卡→ACCEPT 全流程:该偏好只有一条、升 confirmed、强度只 bump 一次(0.7→0.8);
        确认拍板不进样本缓冲(元循环闸),即使押注侧失守也不被开奖计命中。"""
        app = self._mk_full_app(tmp_path)
        pid = _seed_confirm_card(app)
        # 模拟押注侧闸失守:硬塞一注在确认卡上 —— 对账侧也必须不开奖
        app.state.taste_predictions.record_prediction(pid, "ACCEPT", 0.9)

        client = TestClient(app)
        r = client.post("/api/h2a_decide", json=_decide_body(pid, "ACCEPT"))
        assert r.status_code == 200
        disp = r.json()["dispatch"]
        assert disp and disp["ok"], f"确认卡 ACCEPT 兑现失败: {disp}"

        prefs = _decision_prefs(app, CAND["content"])
        assert len(prefs) == 1, f"同一偏好进了 {len(prefs)} 次结晶(应恰 1 条)"
        assert prefs[0].provenance["status"] == "confirmed"
        assert prefs[0].provenance["strength"] == pytest.approx(0.8), (
            "强度必须只 bump 一次(0.7 + 确认 0.1)")
        assert not getattr(app.state, "decision_samples", []), (
            "确认偏好的拍板漏进了样本缓冲(结晶元循环)")
        assert app.state.taste_predictions.outcomes() == [], (
            "确认卡被口味对账开了奖(元循环双计)")

    def test_replayed_accept_does_not_double_count(self, tmp_path):
        """双击/回放同一确认卡 ACCEPT:第二次不得二次 bump,也不得绕过元循环闸
        (修前:卡已移出待决表 → kind 查不到 → 闸失守,确认拍板漏进样本缓冲)。"""
        app = self._mk_full_app(tmp_path)
        pid = _seed_confirm_card(app)
        client = TestClient(app)
        r1 = client.post("/api/h2a_decide", json=_decide_body(pid, "ACCEPT"))
        assert r1.status_code == 200
        r2 = client.post("/api/h2a_decide", json=_decide_body(pid, "ACCEPT"))
        assert r2.status_code == 200          # 决策流不因重放报错(命脉)
        prefs = _decision_prefs(app, CAND["content"])
        assert len(prefs) == 1
        assert prefs[0].provenance["strength"] == pytest.approx(0.8), "重放二次 bump 了强度"
        assert not getattr(app.state, "decision_samples", []), (
            "重放的确认拍板绕过元循环闸漏进了样本缓冲(去重键 proposal_id 未贯通)")

    def test_re_extraction_reinforces_single_entry_no_second_card(self, tmp_path):
        """同一内容再次被抽出:走加固(单条 0.7→0.8),不写第二条、不弹第二张确认卡。"""
        app = self._mk_full_app(tmp_path)
        _seed_confirm_card(app)
        written, reinforced = asyncio.run(
            decision_wire.crystallize_candidates(app, [dict(CAND)]))
        assert written == 0 and reinforced == 1
        prefs = _decision_prefs(app, CAND["content"])
        assert len(prefs) == 1, "复现候选被写成了第二条(应加固既有那条)"
        assert prefs[0].provenance["strength"] == pytest.approx(0.8)
        confirms = [p for p in app.state.proposal_registry.pending()
                    if getattr(p, "kind", "") == KIND_CONFIRM_DECISION_PREF]
        assert len(confirms) == 1, "同一偏好弹了第二张确认卡"


class TestP07ReplayDedupAllKinds:
    def test_replayed_reject_records_signal_once(self):
        """普通卡 REJECT 后重放同一 proposal_id:样本只记一次(去重键=proposal_id)。"""
        app = _mk_app()
        card = _mk_card(habit_id=7)
        app.state.proposal_registry.register(card)
        client = TestClient(app)
        r1 = client.post("/api/h2a_decide",
                         json=_decide_body(card.proposal_id, "REJECT", reason="不要"))
        assert r1.status_code == 200
        r2 = client.post("/api/h2a_decide",
                         json=_decide_body(card.proposal_id, "REJECT", reason="不要"))
        assert r2.status_code == 200          # 重放不打断决策流,只是不再计数
        samples = getattr(app.state, "decision_samples", [])
        assert len(samples) == 1, f"同一张卡的拍板被计了 {len(samples)} 次"

    def test_defer_then_accept_both_recorded(self):
        """DEFER 后卡仍在待决表 → 之后的 ACCEPT 是新决策,两次都该记(去重不误杀)。"""
        app = _mk_app()
        card = _mk_card(habit_id=8)
        app.state.proposal_registry.register(card)
        client = TestClient(app)
        assert client.post("/api/h2a_decide",
                           json=_decide_body(card.proposal_id, "DEFER")).status_code == 200
        assert client.post("/api/h2a_decide",
                           json=_decide_body(card.proposal_id, "ACCEPT", reason="现在做")
                           ).status_code == 200
        samples = getattr(app.state, "decision_samples", [])
        assert [s.decision for s in samples] == ["DEFER", "ACCEPT"]

    def test_unwired_registry_keeps_degraded_recording(self):
        """registry 没接线的宿主(测试台/裸 console):保持既有降级行为 —— 仍记信号
        (context 降级为 id),去重只在 registry 在场时生效。"""
        import types
        app = types.SimpleNamespace(state=types.SimpleNamespace(
            proposal_registry=None, taste_predictions=None,
            decision_stats=None, decision_log=None))
        decision_wire.record_decision_signals(
            app, decision="ACCEPT", proposal_id="bare-1", reason="r")
        assert len(app.state.decision_samples) == 1


class TestP07GuardSingleSource:
    def test_meta_kind_guard_shares_source_with_taste_skip_kinds(self, monkeypatch):
        """元循环闸与押注侧 SKIP_KINDS 必须同源:往 SKIP_KINDS 加一个 kind,
        record_decision_signals 的闸要立即认它(证明不是两份可漂移的独立字面量)。"""
        import karvyloop.crystallize.taste_eval as te
        monkeypatch.setattr(te, "SKIP_KINDS",
                            (KIND_CONFIRM_DECISION_PREF, "meta_kind_for_test"))
        app = _mk_app()
        card = _mk_card(habit_id=9, kind="meta_kind_for_test")
        app.state.proposal_registry.register(card)
        decision_wire.record_decision_signals(
            app, decision="ACCEPT", proposal_id=card.proposal_id)
        assert not getattr(app.state, "decision_samples", []), (
            "决策信号闸没吃 SKIP_KINDS —— 两处独立 check 会漂移(P0-7 病根)")
