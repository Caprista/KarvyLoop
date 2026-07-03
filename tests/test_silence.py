"""test_silence — 「挣来的静音」:命中率从仪表变控制器(docs/49 机制2 / docs/50 决定1)。

不变量(全部向保守倒:宁可少静音绝不静音错):
① 分桶命中率吃**真账本**(TastePredictionStore 对账流水 ⨝ decision_log),关联不上不计入
② 授权门正反:同桶 n≥20 且 ≥90% 才出授权卡;差一次/差一点都不出;同桶挂着不重复
③ 高危 kind 硬排除(授权门 + store.grant 双层)
④ 静音处理 = 自动兑现 + 完整留痕(台账 + Trace kind=silenced_decision + WS 轻通知),不进待决表
⑤ **只静音 ACCEPT 向**:预测 REJECT / 置信不足 / 预测失败 → 回正常路径出卡
⑥ 撤销可用;押错自动吊销该桶授权并出告知卡;翻案同样吊销
⑦ 授权卡自指防护:silence_grant 自己永不被静音
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.trace import TraceStore  # noqa: E402
from karvyloop.console.decision_log import DecisionLog  # noqa: E402
from karvyloop.console.decision_wire import record_decision_signals  # noqa: E402
from karvyloop.console.proposal_handlers import build_proposal_handlers  # noqa: E402
from karvyloop.console.proposals import broadcast_proposal  # noqa: E402
from karvyloop.crystallize.taste_eval import TastePredictionStore  # noqa: E402
from karvyloop.karvy import silence  # noqa: E402
from karvyloop.karvy.atoms import Proposal  # noqa: E402
from karvyloop.karvy.proposal_registry import PendingProposalRegistry  # noqa: E402
from karvyloop.karvy.silence import (  # noqa: E402
    HIGH_RISK_KINDS, KIND_SILENCE_GRANT, KIND_SILENCE_REVOKED,
    SILENCE_MIN_HIT_RATE, SILENCE_MIN_N, WS_TYPE_SILENCE_NOTICE,
    bucket_key, bucket_stats, get_store, maybe_offer_grant, monthly_reconciliation,
    on_outcome, overturn_silenced, proposal_for_silence_grant, read_ledger,
    revoke_grant, try_silence,
)


def make_app(tmp_path):
    """真部件拼的 app(不是 mock 数据形态):taste/decision_log/registry 全真实例。"""
    state = SimpleNamespace()
    state.taste_predictions = TastePredictionStore(tmp_path / "taste.json")
    state.decision_log = DecisionLog(path=tmp_path / "dlog.json")
    state.proposal_registry = PendingProposalRegistry()
    state.silence_grants_path = tmp_path / "grants.json"
    state.silenced_ledger_path = tmp_path / "silenced.json"
    state.runtime_kwargs = {}
    state.ws_clients = set()
    state.main_loop = SimpleNamespace(trace=TraceStore())
    return SimpleNamespace(state=state)


def seed_bucket(app, kind, domain, n, hits, *, prefix=""):
    """走真路径喂一个桶:押注 → decision_log 记 kind/domain → 对账开奖。"""
    ts = app.state.taste_predictions
    log = app.state.decision_log
    for i in range(n):
        pid = f"{prefix}{kind}-{domain}-{i}"
        actual = "ACCEPT" if i < hits else "REJECT"
        ts.record_prediction(pid, "ACCEPT", 0.9)          # 押 ACCEPT
        log.record(decision=actual, proposal_id=pid, kind=kind, domain=domain)
        ts.resolve(pid, actual)                            # 押中 iff actual=ACCEPT


def card(kind="run_task", *, domain="", summary="重跑上次没跑完的任务", pid=""):
    payload = {"intent": "x"}
    if domain:
        payload["domain_id"] = domain
    return Proposal(summary=summary, options=("ACCEPT", "DEFER", "REJECT"), strength=0.8,
                    evidence_refs=(), habit_id=0, model_ref="", ts=time.time(),
                    kind=kind, payload=payload, proposal_id=pid)


class _WS:
    def __init__(self):
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)


# ---------------------------------------------------------------- ① 分桶统计对真数据
def test_bucket_stats_joins_real_stores(tmp_path):
    app = make_app(tmp_path)
    seed_bucket(app, "run_task", "", 10, 9)
    seed_bucket(app, "route_to_role", "biz1", 4, 2)
    # 一条没进 decision_log 的 outcome(关联不上)→ 不计入任何桶
    app.state.taste_predictions.record_prediction("orphan", "ACCEPT", 0.9)
    app.state.taste_predictions.resolve("orphan", "ACCEPT")
    stats = bucket_stats(app)
    assert stats["run_task"]["n"] == 10 and stats["run_task"]["hits"] == 9
    assert abs(stats["run_task"]["hit_rate"] - 0.9) < 1e-9
    b = bucket_key("route_to_role", "biz1")
    assert stats[b] == {"kind": "route_to_role", "domain": "biz1", "n": 4, "hits": 2,
                        "hit_rate": 0.5}
    assert sum(d["n"] for d in stats.values()) == 14   # orphan 没被算进任何桶
    # l0 与空域同桶(归一)
    seed_bucket(app, "run_task", "l0", 1, 1, prefix="z")
    assert bucket_stats(app)["run_task"]["n"] == 11


# ---------------------------------------------------------------- ② 授权门正反
def test_grant_gate_positive(tmp_path):
    app = make_app(tmp_path)
    seed_bucket(app, "run_task", "", SILENCE_MIN_N, 19)   # 20 次 19 中 = 95% ≥ 90%
    got = maybe_offer_grant(app, kind="run_task")
    assert got is not None and got.kind == KIND_SILENCE_GRANT
    assert got.payload["bucket"] == "run_task"
    assert got.payload["n"] == SILENCE_MIN_N and got.payload["hits"] == 19
    # 无事件循环 → 直接登记进待决表
    assert app.state.proposal_registry.get(got.proposal_id) is not None


def test_grant_gate_negative_n_and_rate(tmp_path):
    app = make_app(tmp_path)
    seed_bucket(app, "run_task", "", SILENCE_MIN_N - 1, SILENCE_MIN_N - 1)  # 19/19 全中但 n 不够
    assert maybe_offer_grant(app, kind="run_task") is None
    app2 = make_app(tmp_path / "b")
    seed_bucket(app2, "run_task", "", SILENCE_MIN_N, 17)   # 85% < 90%
    assert maybe_offer_grant(app2, kind="run_task") is None
    # 桶隔离:别的桶达标不给这个桶授权
    app3 = make_app(tmp_path / "c")
    seed_bucket(app3, "crystallize_skill", "", SILENCE_MIN_N, SILENCE_MIN_N)
    assert maybe_offer_grant(app3, kind="run_task") is None


def test_same_bucket_pending_not_repeated(tmp_path):
    app = make_app(tmp_path)
    seed_bucket(app, "run_task", "", SILENCE_MIN_N, SILENCE_MIN_N)
    got = maybe_offer_grant(app, kind="run_task")
    assert got is not None
    assert maybe_offer_grant(app, kind="run_task") is None      # 同桶挂着 → 不重复
    # 卡被 REJECT 后,冷却窗内也不再纠缠(要授权不能变成新打扰)
    app.state.proposal_registry.decide(got.proposal_id, "REJECT")
    assert maybe_offer_grant(app, kind="run_task") is None


# ---------------------------------------------------------------- ③ 高危硬排除
def test_high_risk_hard_excluded(tmp_path):
    app = make_app(tmp_path)
    for k in ("fs_access", KIND_SILENCE_GRANT, "ops_fix"):
        assert k in HIGH_RISK_KINDS
        seed_bucket(app, k, "", SILENCE_MIN_N, SILENCE_MIN_N, prefix=k)
        assert maybe_offer_grant(app, kind=k) is None            # 授权门层拒
        assert get_store(app).grant(k) is None                   # store 硬地板层拒
    # handler 层:伪造一张高危授权卡,ACCEPT 也授不出权
    handlers = build_proposal_handlers(app)
    fake = Proposal(summary="伪造", options=("ACCEPT",), strength=0.9, evidence_refs=(),
                    habit_id=0, model_ref="", ts=time.time(), kind=KIND_SILENCE_GRANT,
                    payload={"kind": "fs_access", "domain": "", "bucket": "fs_access",
                             "n": 99, "hits": 99})
    ok, detail = handlers[KIND_SILENCE_GRANT](fake)
    assert ok is False and "硬地板" in detail
    assert not get_store(app).is_granted("fs_access")


# ---------------------------------------------------------------- ACCEPT 授权落盘 + 撤销
def test_grant_accept_lands_and_is_revocable(tmp_path):
    app = make_app(tmp_path)
    handlers = build_proposal_handlers(app)
    grant_card = proposal_for_silence_grant(kind="run_task", domain="", n=20, hits=19,
                                            ts=time.time())
    app.state.proposal_registry.register(grant_card)
    res = app.state.proposal_registry.decide(grant_card.proposal_id, "ACCEPT",
                                             handlers=handlers)
    assert res is not None and res.ok
    st = get_store(app)
    assert st.is_granted("run_task")
    assert (tmp_path / "grants.json").exists()                   # 真落盘
    # 重启恢复(重新读盘)
    st2 = silence.SilenceGrantStore(tmp_path / "grants.json")
    assert st2.is_granted("run_task")
    # 撤销函数
    assert revoke_grant(app, "run_task", reason="user") is True
    assert not st.is_granted("run_task")
    assert revoke_grant(app, "run_task") is False                # 已撤 → False


# ---------------------------------------------------------------- ④⑤ 静音路径(async 真走 broadcast)
async def _silence_setup(tmp_path, monkeypatch, predict, handler=None):
    app = make_app(tmp_path)
    get_store(app).grant("run_task", "")
    calls = []

    def _handler(p):
        calls.append(getattr(p, "proposal_id", ""))
        return True, "done"

    app.state.proposal_handlers = {"run_task": handler or _handler}
    app.state.runtime_kwargs = {"gateway": object(), "model_ref": "m"}
    ws = _WS()
    app.state.ws_clients = {ws}

    async def _fake_predict(_app, _proposal):
        return predict

    monkeypatch.setattr(silence, "_predict_for_silence", _fake_predict)
    return app, ws, calls


async def _drain(app):
    tasks = list(getattr(app.state, "_silence_tasks", set()) or [])
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_silenced_accept_full_trail(tmp_path, monkeypatch):
    app, ws, calls = await _silence_setup(tmp_path, monkeypatch, ("ACCEPT", 0.95))
    c = card("run_task")
    sent = await broadcast_proposal(app, c)
    await _drain(app)
    assert sent == 0
    assert calls == [c.proposal_id]                              # 真兑现了
    assert app.state.proposal_registry.get(c.proposal_id) is None  # 不进待决表
    led = read_ledger(app)                                       # 台账留痕
    assert len(led) == 1 and led[0]["proposal_id"] == c.proposal_id
    assert led[0]["predicted"] == "ACCEPT" and led[0]["ok"] is True
    assert led[0]["bucket"] == "run_task" and led[0]["overturned"] is False
    tr = app.state.main_loop.trace.query(c.proposal_id, kind="silenced_decision")
    assert len(tr) == 1 and tr[0].payload["confidence"] == 0.95  # Trace 留痕
    notices = [m for m in ws.sent if m["type"] == WS_TYPE_SILENCE_NOTICE]
    assert len(notices) == 1                                     # WS 轻通知
    assert not any(m["type"] == "h2a_proposal" for m in ws.sent)  # 没推卡


@pytest.mark.asyncio
async def test_reject_direction_never_silenced(tmp_path, monkeypatch):
    app, ws, calls = await _silence_setup(tmp_path, monkeypatch, ("REJECT", 0.99))
    c = card("run_task")
    await broadcast_proposal(app, c)
    await _drain(app)
    assert calls == []                                           # 绝不替人拒
    assert app.state.proposal_registry.get(c.proposal_id) is not None  # 回正常路径出卡
    assert any(m["type"] == "h2a_proposal" for m in ws.sent)
    assert read_ledger(app) == []


@pytest.mark.asyncio
async def test_low_confidence_or_failed_predict_falls_back(tmp_path, monkeypatch):
    app, _, calls = await _silence_setup(tmp_path, monkeypatch, ("ACCEPT", 0.5))  # 置信不足
    c = card("run_task")
    await broadcast_proposal(app, c)
    await _drain(app)
    assert calls == [] and app.state.proposal_registry.get(c.proposal_id) is not None
    app2, _, calls2 = await _silence_setup(tmp_path / "b", monkeypatch, None)     # 预测失败
    c2 = card("run_task")
    await broadcast_proposal(app2, c2)
    await _drain(app2)
    assert calls2 == [] and app2.state.proposal_registry.get(c2.proposal_id) is not None


@pytest.mark.asyncio
async def test_unGranted_bucket_and_no_handler_not_silenced(tmp_path, monkeypatch):
    app, _, calls = await _silence_setup(tmp_path, monkeypatch, ("ACCEPT", 0.95))
    # 未授权桶(crystallize_skill 没授权)→ 正常路径
    c = card("crystallize_skill")
    await broadcast_proposal(app, c)
    await _drain(app)
    assert app.state.proposal_registry.get(c.proposal_id) is not None
    # 已授权但域不匹配(授权是全局桶,卡带 biz9 域)→ 不静音
    c2 = card("run_task", domain="biz9")
    await broadcast_proposal(app, c2)
    await _drain(app)
    assert app.state.proposal_registry.get(c2.proposal_id) is not None
    # 授权了但无兑现 handler → 静音等于吞卡,绝不
    app.state.proposal_handlers = {}
    c3 = card("run_task")
    await broadcast_proposal(app, c3)
    await _drain(app)
    assert app.state.proposal_registry.get(c3.proposal_id) is not None
    assert calls == []


@pytest.mark.asyncio
async def test_grant_card_itself_never_silenced(tmp_path, monkeypatch):
    """⑦ 自指防护:哪怕授权台账被篡改成给 silence_grant 桶授权,授权卡照旧走 H2A。"""
    app, ws, _ = await _silence_setup(tmp_path, monkeypatch, ("ACCEPT", 0.99))
    st = get_store(app)
    st._grants[KIND_SILENCE_GRANT] = {"kind": KIND_SILENCE_GRANT, "domain": "",
                                      "granted_at": time.time(), "n": 99, "hits": 99,
                                      "revoked_at": None, "revoke_reason": ""}  # 篡改
    gc = proposal_for_silence_grant(kind="run_task", domain="", n=20, hits=19, ts=time.time())
    app.state.proposal_handlers = build_proposal_handlers(app)
    assert try_silence(app, gc) is False                          # 拦截层直接拒
    await broadcast_proposal(app, gc)
    await _drain(app)
    assert app.state.proposal_registry.get(gc.proposal_id) is not None  # 照旧出卡问人
    assert read_ledger(app) == []


# ---------------------------------------------------------------- ⑥ 押错吊销 + 撤销 + 翻案
def test_miss_auto_revokes_and_notifies(tmp_path):
    app = make_app(tmp_path)
    get_store(app).grant("run_task", "")
    on_outcome(app, proposal_id="p1", kind="run_task", domain="", hit=False)
    assert not get_store(app).is_granted("run_task")              # 押错一次 → 吊销
    revoked_cards = [p for p in app.state.proposal_registry.pending()
                     if getattr(p, "kind", "") == KIND_SILENCE_REVOKED]
    assert len(revoked_cards) == 1                                # 出卡告知
    assert revoked_cards[0].payload["bucket"] == "run_task"
    # 押错但该桶本就没授权 → 不出告知卡
    on_outcome(app, proposal_id="p2", kind="route_to_role", domain="", hit=False)
    assert len([p for p in app.state.proposal_registry.pending()
                if getattr(p, "kind", "") == KIND_SILENCE_REVOKED]) == 1


def test_revoked_bucket_needs_fresh_evidence(tmp_path):
    """吊销后重挣授权:只认吊销之后的新鲜命中,旧成绩单不算。"""
    app = make_app(tmp_path)
    seed_bucket(app, "run_task", "", SILENCE_MIN_N, SILENCE_MIN_N)
    get_store(app).grant("run_task", "")
    revoked_at = time.time() + 1
    get_store(app).revoke("run_task", reason="押错", now=revoked_at)
    # 旧的 20 次全中还在账本里,但都在吊销水位之前 → 门不过
    assert maybe_offer_grant(app, kind="run_task", now=revoked_at + 2) is None


def test_record_decision_signals_wires_controller(tmp_path):
    """接线回归:真走 record_decision_signals(拍板单一接缝)→ 押错吊销真发生。"""
    app = make_app(tmp_path)
    get_store(app).grant("run_task", "")
    c = card("run_task", pid="live-1")
    app.state.proposal_registry.register(c)
    app.state.taste_predictions.record_prediction("live-1", "ACCEPT", 0.9)
    record_decision_signals(app, decision="REJECT", proposal_id="live-1")   # 人拍了 REJECT=押错
    assert not get_store(app).is_granted("run_task")


def test_gate_fires_at_exactly_nth_decision_via_real_seam(tmp_path):
    """对抗验收缺陷② off-by-one 回归:decision_log 先于静音钩子落账 —— 第 20 次拍板
    (不是第 21 次)就该出授权卡,且卡上的 n 含刚开的这一奖。全程走真接缝
    record_decision_signals,并模拟传输层的 pydantic 占位默认 domain="dom-1"。"""
    app = make_app(tmp_path)
    for i in range(SILENCE_MIN_N):
        pid = f"seam-{i}"
        c = card("run_task", pid=pid, summary=f"任务{i}")
        app.state.proposal_registry.register(c)
        app.state.taste_predictions.record_prediction(pid, "ACCEPT", 0.9)
        if i == SILENCE_MIN_N - 2:   # 第 19 次后:门还不该开
            grants = [p for p in app.state.proposal_registry.pending()
                      if getattr(p, "kind", "") == KIND_SILENCE_GRANT]
            assert grants == []
        record_decision_signals(app, decision="ACCEPT", proposal_id=pid, domain="dom-1")
    grants = [p for p in app.state.proposal_registry.pending()
              if getattr(p, "kind", "") == KIND_SILENCE_GRANT]
    assert len(grants) == 1                                       # 第 20 次拍板即出卡
    assert grants[0].payload["n"] == SILENCE_MIN_N                # 含触发的这一奖
    assert grants[0].payload["bucket"] == "run_task"              # 占位假域没污染桶


def test_domain_authority_is_card_payload_not_transport_default(tmp_path):
    """对抗验收缺陷① 回归:桶域以卡 payload 为权威 —— 传输层默认 "dom-1" 不可信。
    带域卡经真接缝攒出的授权桶,必须和 try_silence 的拦截桶(payload 侧)完全一致。"""
    app = make_app(tmp_path)
    for i in range(SILENCE_MIN_N):
        pid = f"dom-{i}"
        c = card("route_to_role", domain="biz1", pid=pid, summary=f"委派{i}")
        app.state.proposal_registry.register(c)
        app.state.taste_predictions.record_prediction(pid, "ACCEPT", 0.9)
        record_decision_signals(app, decision="ACCEPT", proposal_id=pid, domain="dom-1")
    grants = [p for p in app.state.proposal_registry.pending()
              if getattr(p, "kind", "") == KIND_SILENCE_GRANT]
    assert len(grants) == 1
    assert grants[0].payload["bucket"] == bucket_key("route_to_role", "biz1")
    assert grants[0].payload["domain"] == "biz1"                  # 不再显示假域 dom-1
    # decision_log 里落的也是真域(回看/审计不被占位值污染)
    assert app.state.decision_log.recent(1)[0]["domain"] == "biz1"
    # ACCEPT 授权后,同域卡的拦截判定(payload 侧)与授权桶对得上
    handlers = build_proposal_handlers(app)
    ok, _ = handlers[KIND_SILENCE_GRANT](grants[0])
    assert ok and get_store(app).is_granted("route_to_role", "biz1")


def test_overturn_and_monthly_reconciliation(tmp_path):
    app = make_app(tmp_path)
    get_store(app).grant("run_task", "")
    now = time.time()
    silence.record_silenced(app, {"ts": now, "proposal_id": "s1", "kind": "run_task",
                                  "domain": "", "bucket": "run_task", "summary": "a",
                                  "predicted": "ACCEPT", "confidence": 0.9,
                                  "ok": True, "detail": "", "overturned": False})
    silence.record_silenced(app, {"ts": now, "proposal_id": "s2", "kind": "run_task",
                                  "domain": "", "bucket": "run_task", "summary": "b",
                                  "predicted": "ACCEPT", "confidence": 0.9,
                                  "ok": True, "detail": "", "overturned": False})
    got = overturn_silenced(app, "s1")                            # 翻案
    assert got is not None and got["overturned"] is True
    assert not get_store(app).is_granted("run_task")              # 翻案 = 吊销
    assert overturn_silenced(app, "s1") is None                   # 幂等:翻过的不再翻
    rec = monthly_reconciliation(app, days=30, now=now + 10)
    assert rec["silenced_n"] == 2 and rec["overturned_n"] == 1 and rec["failed_n"] == 0
    assert rec["by_bucket"]["run_task"] == {"silenced": 2, "overturned": 1}
    assert "run_task" not in rec["active_grants"]


def test_ledger_failsafe_and_store_failsafe(tmp_path):
    app = make_app(tmp_path)
    (tmp_path / "silenced.json").write_text("{ bad", encoding="utf-8")
    assert read_ledger(app) == []                                 # 坏台账当空
    (tmp_path / "grants.json").write_text("{ bad", encoding="utf-8")
    st = silence.SilenceGrantStore(tmp_path / "grants.json")
    assert st.active_grants() == {}                               # 坏授权文件当空=回到逐张问人
