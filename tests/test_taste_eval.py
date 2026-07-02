"""test_taste_eval — 口味命中率("越用越像你"的可证明刻度)。

不变量(诚实三律):① 前瞻不回放:只有拍板**前**押过的注才计入;同 id 不许改口
② 宁空勿毒:LLM 输出解析失败 → 不押注;DEFER 不开奖(不是终局)③ 样本门:n<MIN_N
不报百分比;趋势要上一期也满样本 ④ 全链路:提案广播押注(异步)→ 拍板对账 → stats 出数
⑤ 落盘往返 + 坏文件 fail-safe ⑥ 过期押注清理(防 pending 无界)。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.crystallize.taste_eval import (  # noqa: E402
    MIN_N, TastePredictionStore, predict_decision)


def test_prospective_only_and_no_retro():
    st = TastePredictionStore()
    # 没押过的决策 → 不计入(诚实三律 #1)
    assert st.resolve("never-bet", "ACCEPT") is None
    assert st.stats()["taste_n"] == 0
    # 押注 → 开奖计入
    st.record_prediction("p1", "ACCEPT", 0.8)
    assert st.resolve("p1", "ACCEPT") is True
    st.record_prediction("p2", "REJECT", 0.6)
    assert st.resolve("p2", "ACCEPT") is False
    assert st.stats()["taste_n"] == 2
    # 同 id 二次押注不覆盖(第一次押的才算,防事后改口)
    st.record_prediction("p3", "ACCEPT", 0.9)
    st.record_prediction("p3", "REJECT", 0.9)
    assert st.resolve("p3", "ACCEPT") is True


def test_defer_keeps_bet_open_and_invalid_ignored():
    st = TastePredictionStore()
    st.record_prediction("p1", "ACCEPT", 0.7)
    assert st.resolve("p1", "DEFER") is None      # DEFER 不是终局,注继续挂
    assert st.resolve("p1", "ACCEPT") is True     # 之后真拍板照常开奖
    st.record_prediction("", "ACCEPT", 0.5)       # 空 id / 非法方向 → 不入
    st.record_prediction("px", "MAYBE", 0.5)
    assert st.resolve("px", "ACCEPT") is None


def test_sample_gate_and_trend():
    st = TastePredictionStore()
    for i in range(MIN_N - 1):
        st.record_prediction(f"p{i}", "ACCEPT", 0.5)
        st.resolve(f"p{i}", "ACCEPT")
    s = st.stats()
    assert s["taste_hit_rate"] is None and s["taste_need_more"] == 1   # 样本门
    st.record_prediction("plast", "ACCEPT", 0.5)
    st.resolve("plast", "REJECT")
    s2 = st.stats()
    assert s2["taste_enough"] and abs(s2["taste_hit_rate"] - (MIN_N - 1) / MIN_N) < 1e-6
    assert s2["taste_prev_rate"] is None   # 上一期不满样本 → 不报趋势


def test_persist_roundtrip_and_failsafe(tmp_path):
    path = tmp_path / "taste.json"
    st = TastePredictionStore(path)
    st.record_prediction("p1", "ACCEPT", 0.8)
    st.record_prediction("p2", "REJECT", 0.6)
    st.resolve("p1", "ACCEPT")
    st2 = TastePredictionStore(path)               # 重启恢复
    assert st2.stats()["taste_n"] == 1
    assert st2.resolve("p2", "REJECT") is True     # 挂着的注也活着
    path.write_text("{ bad json", encoding="utf-8")
    st3 = TastePredictionStore(path)               # 坏文件当空,不炸
    assert st3.stats()["taste_n"] == 0


def test_prune_stale():
    st = TastePredictionStore()
    st.record_prediction("old", "ACCEPT", 0.5, now=1000.0)
    st.record_prediction("new", "ACCEPT", 0.5, now=1000.0 + 13 * 86400)
    assert st.prune_stale(now=1000.0 + 15 * 86400) == 1
    assert st.resolve("old", "ACCEPT") is None     # 过期注已清
    assert st.resolve("new", "ACCEPT") is True


def test_predict_decision_strict_parse():
    """LLM 输出严解析:合法 JSON → (方向,置信);垃圾/越界 → None(宁空勿毒)。"""
    class _GW:
        def __init__(self, text):
            self._t = text

        def resolve_model(self, scope):
            return "m"

        async def complete(self, messages, tools, ref, system=None):
            from karvyloop.gateway.events import TextDelta
            yield TextDelta(text=self._t)

    ok = asyncio.run(predict_decision(_GW('{"decision":"ACCEPT","confidence":0.8}'), "m",
                                      summary="要不要接这单"))
    assert ok == ("ACCEPT", 0.8)
    fenced = asyncio.run(predict_decision(_GW('```json\n{"decision":"REJECT","confidence":0.3}\n```'), "m",
                                          summary="x"))
    assert fenced == ("REJECT", 0.3)
    for bad in ("我觉得会接受", '{"decision":"MAYBE","confidence":0.5}',
                '{"decision":"ACCEPT","confidence":1.5}', ""):
        assert asyncio.run(predict_decision(_GW(bad), "m", summary="x")) is None


def test_full_wire_broadcast_bet_then_decide_resolves():
    """全链路真接线:broadcast_proposal 押注(异步)→ REST 拍板 → stats 有对账。"""
    from fastapi.testclient import TestClient
    from karvyloop.console import broadcast_proposal, build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry
    from karvyloop.karvy.atoms import Proposal

    class _GW:
        def resolve_model(self, scope):
            return "m"

        async def complete(self, messages, tools, ref, system=None):
            from karvyloop.gateway.events import TextDelta
            yield TextDelta(text='{"decision":"ACCEPT","confidence":0.9}')

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.proposal_registry = PendingProposalRegistry()
    app.state.taste_predictions = TastePredictionStore()
    app.state.runtime_kwargs = {"gateway": _GW(), "model_ref": ""}
    p = Proposal(summary="把月报交给分析师", options=("ACCEPT", "DEFER", "REJECT"), strength=0.8,
                 evidence_refs=(), habit_id=1, model_ref="m", ts=1.0, kind="route_to_role",
                 payload={"requirement": "月报"})

    async def go():
        await broadcast_proposal(app, p)
        # 押注任务是 create_task 的 fire-and-forget → 等它跑完
        import asyncio as _a
        for t in list(getattr(app.state, "_taste_tasks", set())):
            await t
    asyncio.run(go())
    client = TestClient(app)
    r = client.post("/api/h2a_decide", json={"proposal_id": p.proposal_id, "decision": "ACCEPT"})
    assert r.status_code == 200
    assert app.state.taste_predictions.stats()["taste_n"] == 1   # 押了、开奖了
    # stats 端点带出口味字段(前端读同一处)
    s = client.get("/api/decision_prefs/stats").json()
    assert s["taste_n"] == 1 and s["taste_enough"] is False


def test_skip_kinds_not_bet():
    """元循环 kind(confirm_decision_pref)不押注。"""
    from karvyloop.console.proposals import _schedule_taste_bet
    from karvyloop.karvy.atoms import Proposal

    class _State:
        taste_predictions = TastePredictionStore()
        runtime_kwargs = {"gateway": object(), "model_ref": ""}

    class _App:
        state = _State()

    p = Proposal(summary="确认偏好", options=(), strength=0.5, evidence_refs=(), habit_id=0,
                 model_ref="m", ts=1.0, kind="confirm_decision_pref")

    async def go():
        _schedule_taste_bet(_App(), p)
    asyncio.run(go())
    assert _State.taste_predictions.stats()["taste_n"] == 0
    assert _State.taste_predictions.resolve(p.proposal_id, "ACCEPT") is None   # 没押过
