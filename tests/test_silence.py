"""test_silence — 「挣来的静音」v2:统计判决版授权门(docs/52 §2 六条修正)。

不变量(全部向保守倒:宁可少静音绝不静音错):
① 分桶命中率吃**真账本**(TastePredictionStore 对账流水 ⨝ decision_log),关联不上不计入
② 授权门 = Wilson 95% 下界 ≥0.90(裸命中率门已死:47/50=94% 也不够)+ n≥35
③ 评估水位:每满 25 个新对账样本才判一次门(每来一个试一次 = 序贯凑连击,必须拒)
④ 判别力门杀常数策略:桶内须有 ≥2 条"预测 REJECT 且押中",否则全押 ACCEPT 也能过门
⑤ 不可逆语义(外发/删除/支付/生产写)kind 级 + 单卡级双层硬排除;高危 kind 表扩容
⑥ 不告知随机抽查 15%:抽中的卡照常出卡且**无任何标注**;概率统计上站得住
⑦ 授权 30 天到期:回正常出卡 + 续期卡(带对账数据),人 ACCEPT 才续
⑧ 爆炸半径硬顶:执行类桶近期平均成本 >30k token 不静音回人工(token_task 归因)
⑨ 静音处理 = 自动兑现 + 完整留痕(台账+Trace+WS);只静音 ACCEPT 向;押错/翻案立即吊销;
   吊销后重挣只认新鲜证据;授权卡自指防护
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import random
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
    SILENCE_AUDIT_RATE, SILENCE_COST_CAP_TOKENS, SILENCE_EVAL_BATCH_N,
    SILENCE_GRANT_TTL_S, SILENCE_MIN_N, SILENCE_MIN_REJECT_CORRECT,
    SILENCE_MIN_WILSON_LB, WS_TYPE_SILENCE_NOTICE,
    bucket_key, bucket_recent_avg_cost, bucket_stats, get_store,
    irreversible_semantics, maybe_offer_grant, maybe_offer_renewal,
    monthly_reconciliation, on_outcome, overturn_silenced,
    proposal_for_silence_grant, read_ledger, revoke_grant, try_silence,
    wilson_lower_bound,
)
from karvyloop.llm.token_ledger import TokenLedger, register_ledger  # noqa: E402
from karvyloop.llm.token_ledger import record as record_tokens  # noqa: E402


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


def seed_bucket(app, kind, domain, n, hits, *, prefix="", reject_correct=0):
    """走真路径喂一个桶:押注 → decision_log 记 kind/domain → 对账开奖。

    n 条里 hits 条押中;押中里前 reject_correct 条是「押 REJECT 且用户真 REJECT」
    (判别力门的证据),其余押中是 ACCEPT/ACCEPT;押错的是 押ACCEPT/实REJECT。
    """
    ts = app.state.taste_predictions
    log = app.state.decision_log
    assert reject_correct <= hits <= n
    for i in range(n):
        pid = f"{prefix}{kind}-{domain}-{i}"
        if i < reject_correct:
            pred, actual = "REJECT", "REJECT"
        elif i < hits:
            pred, actual = "ACCEPT", "ACCEPT"
        else:
            pred, actual = "ACCEPT", "REJECT"
        ts.record_prediction(pid, pred, 0.9)
        log.record(decision=actual, proposal_id=pid, kind=kind, domain=domain)
        ts.resolve(pid, actual)


def card(kind="run_task", *, domain="", summary="重跑上次没跑完的任务", pid="", payload=None):
    p = dict(payload) if payload else {"intent": "整理一下昨天的会议纪要"}
    if domain:
        p["domain_id"] = domain
    return Proposal(summary=summary, options=("ACCEPT", "DEFER", "REJECT"), strength=0.8,
                    evidence_refs=(), habit_id=0, model_ref="", ts=time.time(),
                    kind=kind, payload=p, proposal_id=pid)


class _WS:
    def __init__(self):
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)


class _FixedRng:
    """可控抽查骰子:random() 恒返固定值(<0.15 = 必抽查,≥0.15 = 必不抽)。"""

    def __init__(self, v: float):
        self.v = v

    def random(self) -> float:
        return self.v


NO_AUDIT = _FixedRng(0.999)     # 关抽查(测静音主路径用)
ALWAYS_AUDIT = _FixedRng(0.0)   # 必抽查


# ---------------------------------------------------------------- ② Wilson 下界(纯数学)
def test_wilson_lower_bound_math():
    # docs/52 §2 钉死的反例:n=20 命中 18(裸 90%)→ 95% 下界只有 ~0.699,必须拒
    lb_18_20 = wilson_lower_bound(18, 20)
    assert abs(lb_18_20 - 0.699) < 0.002
    assert lb_18_20 < SILENCE_MIN_WILSON_LB
    # 诚实勘误:docs/52 写"约 48/50 → ≈0.905";精确 Wilson(z=1.96)是 0.8654 —— 仍拒。
    # (0.905 是笔误/近似;真实达门形态见下。方向 = 比文档更严 = 保守,安全。)
    lb_48_50 = wilson_lower_bound(48, 50)
    assert abs(lb_48_50 - 0.8654) < 0.002
    assert lb_48_50 < SILENCE_MIN_WILSON_LB
    # 真实达门边界:全中也要 n≥35(35/35 → 0.9011);34/34 → 0.8985 差一点也不行
    assert wilson_lower_bound(35, 35) >= SILENCE_MIN_WILSON_LB
    assert wilson_lower_bound(34, 34) < SILENCE_MIN_WILSON_LB
    assert wilson_lower_bound(50, 50) >= SILENCE_MIN_WILSON_LB   # 0.9287
    assert wilson_lower_bound(49, 50) < SILENCE_MIN_WILSON_LB    # 0.8950
    assert wilson_lower_bound(59, 60) >= SILENCE_MIN_WILSON_LB   # 0.9114(容 1 错的最小形态之一)
    # 边界与保守夹断
    assert wilson_lower_bound(0, 0) == 0.0
    assert wilson_lower_bound(10, 0) == 0.0
    assert wilson_lower_bound(99, 50) == wilson_lower_bound(50, 50)   # hits 夹到 n
    assert wilson_lower_bound(-3, 50) == wilson_lower_bound(0, 50)
    # 同命中率下 n 越大下界越高(样本量真的在起作用)
    assert wilson_lower_bound(90, 100) > wilson_lower_bound(18, 20)


# ---------------------------------------------------------------- ① 分桶统计对真数据
def test_bucket_stats_joins_real_stores(tmp_path):
    app = make_app(tmp_path)
    seed_bucket(app, "run_task", "", 10, 9, reject_correct=2)
    seed_bucket(app, "route_to_role", "biz1", 4, 2)
    # 一条没进 decision_log 的 outcome(关联不上)→ 不计入任何桶
    app.state.taste_predictions.record_prediction("orphan", "ACCEPT", 0.9)
    app.state.taste_predictions.resolve("orphan", "ACCEPT")
    stats = bucket_stats(app)
    assert stats["run_task"]["n"] == 10 and stats["run_task"]["hits"] == 9
    assert abs(stats["run_task"]["hit_rate"] - 0.9) < 1e-9
    assert stats["run_task"]["reject_correct"] == 2          # 判别力证据被数出来
    assert abs(stats["run_task"]["wilson_lb"] - wilson_lower_bound(9, 10)) < 1e-9
    b = bucket_key("route_to_role", "biz1")
    assert stats[b]["n"] == 4 and stats[b]["hits"] == 2
    assert stats[b]["reject_correct"] == 0
    assert sum(d["n"] for d in stats.values()) == 14   # orphan 没被算进任何桶
    # l0 与空域同桶(归一)
    seed_bucket(app, "run_task", "l0", 1, 1, prefix="z")
    assert bucket_stats(app)["run_task"]["n"] == 11


# ---------------------------------------------------------------- ② 授权门正反(Wilson)
def test_grant_gate_positive(tmp_path):
    app = make_app(tmp_path)
    seed_bucket(app, "run_task", "", 50, 50, reject_correct=SILENCE_MIN_REJECT_CORRECT)
    got = maybe_offer_grant(app, kind="run_task")
    assert got is not None and got.kind == KIND_SILENCE_GRANT
    assert got.payload["bucket"] == "run_task"
    assert got.payload["n"] == 50 and got.payload["hits"] == 50
    assert got.payload["wilson_lb"] >= SILENCE_MIN_WILSON_LB
    assert got.payload["reject_correct"] == SILENCE_MIN_REJECT_CORRECT
    # 无事件循环 → 直接登记进待决表
    assert app.state.proposal_registry.get(got.proposal_id) is not None


def test_grant_gate_wilson_kills_raw_rate(tmp_path):
    # 旧门回归杀:47/50 = 94% 裸命中率(旧门 ≥90% 稳过)—— Wilson 下界 0.838,必须拒
    app = make_app(tmp_path)
    seed_bucket(app, "run_task", "", 50, 47, reject_correct=2)
    assert maybe_offer_grant(app, kind="run_task") is None
    # docs/52 的"约 48/50":精确计算 0.865 —— 也拒(见 test_wilson_lower_bound_math 勘误)
    app2 = make_app(tmp_path / "b")
    seed_bucket(app2, "run_task", "", 50, 48, reject_correct=2)
    assert maybe_offer_grant(app2, kind="run_task") is None
    # n 不够:30/30 全中(下界 0.887)—— n < SILENCE_MIN_N=35,拒
    app3 = make_app(tmp_path / "c")
    seed_bucket(app3, "run_task", "", 30, 30, reject_correct=2)
    assert maybe_offer_grant(app3, kind="run_task") is None
    # 桶隔离:别的桶达标不给这个桶授权
    app4 = make_app(tmp_path / "d")
    seed_bucket(app4, "crystallize_skill", "", 50, 50, reject_correct=2)
    assert maybe_offer_grant(app4, kind="run_task") is None


# ---------------------------------------------------------------- ④ 判别力门杀常数策略
def test_discriminative_gate_kills_constant_strategy(tmp_path):
    # 50/50 全中但全是"押 ACCEPT"—— 无脑常数策略的完美战绩 → 不出授权卡
    app = make_app(tmp_path)
    seed_bucket(app, "run_task", "", 50, 50, reject_correct=0)
    assert maybe_offer_grant(app, kind="run_task") is None
    # 只有 1 条 REJECT 押中(< SILENCE_MIN_REJECT_CORRECT=2)→ 仍拒
    app2 = make_app(tmp_path / "b")
    seed_bucket(app2, "run_task", "", 50, 50, reject_correct=1)
    assert maybe_offer_grant(app2, kind="run_task") is None
    # 攒到 2 条 → 过(正例在 test_grant_gate_positive)
    app3 = make_app(tmp_path / "c")
    seed_bucket(app3, "run_task", "", 50, 50, reject_correct=2)
    assert maybe_offer_grant(app3, kind="run_task") is not None


# ---------------------------------------------------------------- ③ 评估水位治 peeking
def test_eval_watermark_blocks_peeking(tmp_path):
    app = make_app(tmp_path)
    b = "run_task"
    # 第一批:26 个样本 → 允许评一次(n<35 不过门),水位记在 26
    seed_bucket(app, "run_task", "", 26, 26, reject_correct=2, prefix="a")
    assert maybe_offer_grant(app, kind="run_task") is None
    assert get_store(app).eval_mark(b) == 26
    # 水位跨重启持久(治"重启刷评估机会")
    st2 = silence.SilenceGrantStore(tmp_path / "grants.json")
    assert st2.eval_mark(b) == 26
    # 加到 n=50(统计上已达门:50/50 全中 + 判别力够)—— 但新增只有 24 < 25 → **不许看门**
    seed_bucket(app, "run_task", "", 24, 24, prefix="b")
    assert bucket_stats(app)[b]["wilson_lb"] >= SILENCE_MIN_WILSON_LB   # 统计确实达标
    assert maybe_offer_grant(app, kind="run_task") is None              # 但水位拦住(反 peeking)
    assert get_store(app).eval_mark(b) == 26                            # 没消耗新评估
    # 攒满一整批(n=51,新增 25)→ 才允许评,这次过门出卡
    seed_bucket(app, "run_task", "", 1, 1, prefix="c")
    got = maybe_offer_grant(app, kind="run_task")
    assert got is not None and got.payload["n"] == 51
    assert get_store(app).eval_mark(b) == 51


def test_same_bucket_pending_not_repeated(tmp_path):
    app = make_app(tmp_path)
    seed_bucket(app, "run_task", "", 50, 50, reject_correct=2)
    got = maybe_offer_grant(app, kind="run_task")
    assert got is not None
    assert maybe_offer_grant(app, kind="run_task") is None      # 同桶挂着 → 不重复
    # 卡被 REJECT 后,冷却窗内也不再纠缠(要授权不能变成新打扰)
    app.state.proposal_registry.decide(got.proposal_id, "REJECT")
    assert maybe_offer_grant(app, kind="run_task") is None


# ---------------------------------------------------------------- ⑤ 高危 + 不可逆语义硬排除
def test_high_risk_hard_excluded(tmp_path):
    app = make_app(tmp_path)
    # docs/52 修正① 语义审查补入的四个 kind 必须在表里
    for k in ("merge_knowledge", "inbox_decision", "inbox_reply", "revise_skill"):
        assert k in HIGH_RISK_KINDS
    for k in ("fs_access", KIND_SILENCE_GRANT, "ops_fix", "inbox_decision"):
        assert k in HIGH_RISK_KINDS
        seed_bucket(app, k, "", 50, 50, reject_correct=2, prefix=k)
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


def test_irreversible_semantics_unit(tmp_path):
    # kind 名本身蕴含不可逆语义 → 桶级永不授权(常量表外的第二层,兜未来新 kind)
    assert irreversible_semantics("send_email") == "outbound"
    assert irreversible_semantics("delete_records") == "delete"
    assert irreversible_semantics("make_payment") == "payment"
    assert irreversible_semantics("deploy_service") == "prod_write"
    app = make_app(tmp_path)
    assert get_store(app).grant("send_email") is None
    seed_bucket(app, "send_email", "", 50, 50, reject_correct=2)
    assert maybe_offer_grant(app, kind="send_email") is None
    # 池内 kind(run_task)的单卡 payload 蕴含不可逆语义 → 单卡命中
    assert irreversible_semantics("run_task", {"intent": "把周报邮件发送给客户"}) == "outbound"
    assert irreversible_semantics("run_task", {"intent": "删除旧备份目录"}) == "delete"
    assert irreversible_semantics("run_task", {"intent": "给供应商付款 3000 元"}) == "payment"
    assert irreversible_semantics("run_task", {"intent": "deploy to production"}) == "prod_write"
    assert irreversible_semantics("run_task", {}, "帮我 send 一封 email") == "outbound"
    # 良性卡不误伤;英文走词边界("payload"/"prepay-analysis" 里的 pay 不算)
    assert irreversible_semantics("run_task", {"intent": "整理会议纪要"},
                                  "重跑上次没跑完的任务") == ""
    assert irreversible_semantics("run_task", {"data": "parse the payload schema"}) == ""
    # 对抗验收 break #1 回归:长良性前缀不得把危险文本顶出扫描窗(全量扫,绝不截断)
    long_payload = {"context": "会议纪要 " * 800,
                    "intent": "给供应商转账 5000 元并发送付款确认邮件"}
    assert irreversible_semantics("run_task", long_payload) in ("outbound", "payment")
    # 对抗验收可疑点回归:CJK 紧贴英文无 \b 边界 / camelCase kind 不拆词
    assert irreversible_semantics("run_task", {}, "把email发给客户") == "outbound"
    assert irreversible_semantics("DeployToProd") == "prod_write"
    # 序列化不了的 payload = 扫不完整 = fail-closed 当命中
    class _Unserializable:
        def __str__(self):
            raise RuntimeError("boom")
    assert irreversible_semantics(
        "run_task", {"x": _Unserializable()}) == "scan_error"


# ---------------------------------------------------------------- ACCEPT 授权落盘 + 撤销
def test_grant_accept_lands_and_is_revocable(tmp_path):
    app = make_app(tmp_path)
    handlers = build_proposal_handlers(app)
    grant_card = proposal_for_silence_grant(kind="run_task", domain="", n=50, hits=50,
                                            ts=time.time())
    app.state.proposal_registry.register(grant_card)
    res = app.state.proposal_registry.decide(grant_card.proposal_id, "ACCEPT",
                                             handlers=handlers)
    assert res is not None and res.ok
    st = get_store(app)
    assert st.is_granted("run_task")
    g = st.active_grants()["run_task"]
    assert abs(g["expires_at"] - (g["granted_at"] + SILENCE_GRANT_TTL_S)) < 1e-6  # 30 天有效期
    assert (tmp_path / "grants.json").exists()                   # 真落盘
    # 重启恢复(重新读盘)
    st2 = silence.SilenceGrantStore(tmp_path / "grants.json")
    assert st2.is_granted("run_task")
    # 撤销函数
    assert revoke_grant(app, "run_task", reason="user") is True
    assert not st.is_granted("run_task")
    assert revoke_grant(app, "run_task") is False                # 已撤 → False


# ---------------------------------------------------------------- 静音路径(async 真走 broadcast)
async def _silence_setup(tmp_path, monkeypatch, predict, handler=None, *,
                         grant_kind="run_task", grant_now=None, audit_rng=NO_AUDIT):
    app = make_app(tmp_path)
    get_store(app).grant(grant_kind, "", now=grant_now)
    calls = []

    def _handler(p):
        calls.append(getattr(p, "proposal_id", ""))
        return True, "done"

    app.state.proposal_handlers = {grant_kind: handler or _handler}
    app.state.runtime_kwargs = {"gateway": object(), "model_ref": "m"}
    ws = _WS()
    app.state.ws_clients = {ws}

    async def _fake_predict(_app, _proposal):
        return predict

    monkeypatch.setattr(silence, "_predict_for_silence", _fake_predict)
    monkeypatch.setattr(silence, "_audit_rng", audit_rng)   # 默认关抽查(抽查单独测)
    return app, ws, calls


async def _drain(app):
    tasks = list(getattr(app.state, "_silence_tasks", set()) or [])
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_silenced_accept_full_trail(tmp_path, monkeypatch):
    led = TokenLedger(None)   # in-memory 账本:验证 token_task 成本归因链
    register_ledger(led)
    try:
        def _handler(p):
            record_tokens(model="m", input=100, output=50)   # 兑现里烧了 150 token
            return True, "done"

        app, ws, _ = await _silence_setup(tmp_path, monkeypatch, ("ACCEPT", 0.95),
                                          handler=_handler)
        c = card("run_task")
        sent = await broadcast_proposal(app, c)
        await _drain(app)
        assert sent == 0
        assert app.state.proposal_registry.get(c.proposal_id) is None  # 不进待决表
        led_items = read_ledger(app)                                   # 台账留痕
        assert len(led_items) == 1 and led_items[0]["proposal_id"] == c.proposal_id
        assert led_items[0]["predicted"] == "ACCEPT" and led_items[0]["ok"] is True
        assert led_items[0]["bucket"] == "run_task" and led_items[0]["overturned"] is False
        # 修正⑥ 的地基:成本按 token_task="silenced:<pid>" 归因并写进台账
        assert led_items[0]["token_task_id"] == f"silenced:{c.proposal_id}"
        assert led_items[0]["cost_tokens"] == 150
        assert led.task_total(f"silenced:{c.proposal_id}") == 150
        tr = app.state.main_loop.trace.query(c.proposal_id, kind="silenced_decision")
        assert len(tr) == 1 and tr[0].payload["confidence"] == 0.95  # Trace 留痕
        notices = [m for m in ws.sent if m["type"] == WS_TYPE_SILENCE_NOTICE]
        assert len(notices) == 1                                     # WS 轻通知
        assert not any(m["type"] == "h2a_proposal" for m in ws.sent)  # 没推卡
    finally:
        register_ledger(None)


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
async def test_irreversible_card_never_silenced_even_when_granted(tmp_path, monkeypatch):
    """⑤ 单卡级不可逆语义:桶已授权、预测 ACCEPT 高置信,payload 蕴含外发 → 照旧出卡问人。"""
    app, _, calls = await _silence_setup(tmp_path, monkeypatch, ("ACCEPT", 0.99))
    c = card("run_task", payload={"intent": "把周报邮件发送给客户"})
    await broadcast_proposal(app, c)
    await _drain(app)
    assert calls == []
    assert app.state.proposal_registry.get(c.proposal_id) is not None
    assert read_ledger(app) == []


@pytest.mark.asyncio
async def test_grant_card_itself_never_silenced(tmp_path, monkeypatch):
    """自指防护:哪怕授权台账被篡改成给 silence_grant 桶授权,授权卡照旧走 H2A。"""
    app, ws, _ = await _silence_setup(tmp_path, monkeypatch, ("ACCEPT", 0.99))
    st = get_store(app)
    st._grants[KIND_SILENCE_GRANT] = {"kind": KIND_SILENCE_GRANT, "domain": "",
                                      "granted_at": time.time(), "n": 99, "hits": 99,
                                      "revoked_at": None, "revoke_reason": ""}  # 篡改
    gc = proposal_for_silence_grant(kind="run_task", domain="", n=50, hits=50, ts=time.time())
    app.state.proposal_handlers = build_proposal_handlers(app)
    assert try_silence(app, gc) is False                          # 拦截层直接拒
    await broadcast_proposal(app, gc)
    await _drain(app)
    assert app.state.proposal_registry.get(gc.proposal_id) is not None  # 照旧出卡问人
    assert read_ledger(app) == []


# ---------------------------------------------------------------- ⑥ 不告知随机抽查
def test_audit_probability_statistics(monkeypatch):
    """抽查骰子在 SILENCE_AUDIT_RATE 附近(4000 掷,±3σ≈±0.017,region 放宽到 ±0.03)。"""
    monkeypatch.setattr(silence, "_audit_rng", random.Random(20260703))
    n = 4000
    frac = sum(1 for _ in range(n) if silence._should_audit()) / n
    assert SILENCE_AUDIT_RATE - 0.03 < frac < SILENCE_AUDIT_RATE + 0.03


@pytest.mark.asyncio
async def test_audit_card_goes_normal_path_unlabeled(tmp_path, monkeypatch):
    """抽中的卡:照常出卡进对账流,且卡上**没有任何抽查标注**(标注=抽查失效)。"""
    app, ws, calls = await _silence_setup(tmp_path, monkeypatch, ("ACCEPT", 0.95),
                                          audit_rng=ALWAYS_AUDIT)
    c = card("run_task")
    await broadcast_proposal(app, c)
    await _drain(app)
    assert calls == []                                            # 没静音执行
    assert app.state.proposal_registry.get(c.proposal_id) is not None  # 正常出卡
    assert read_ledger(app) == []
    pushed = [m for m in ws.sent if m["type"] == "h2a_proposal"]
    assert len(pushed) == 1
    assert "audit" not in json.dumps(pushed[0]["payload"], ensure_ascii=False).lower()  # 不标注
    # 抽查样本照常押注 → 拍板后照常进对账流(对账链走真接缝在别的测试里已锁)
    # 骰子不抽时(≥rate)→ 正常静音
    monkeypatch.setattr(silence, "_audit_rng", NO_AUDIT)
    c2 = card("run_task")
    await broadcast_proposal(app, c2)
    await _drain(app)
    assert calls == [c2.proposal_id]


# ---------------------------------------------------------------- ⑦ 月度到期 + 续期卡
def test_grant_expires_after_ttl(tmp_path):
    app = make_app(tmp_path)
    t0 = time.time()
    st = get_store(app)
    st.grant("run_task", "", now=t0)
    assert st.is_granted("run_task", now=t0 + SILENCE_GRANT_TTL_S - 60)      # 29 天多:还活
    assert not st.is_granted("run_task", now=t0 + SILENCE_GRANT_TTL_S + 1)   # 满 30 天:失效
    assert st.expired_unrevoked("run_task", now=t0 + SILENCE_GRANT_TTL_S + 1) is not None
    assert st.active_grants(now=t0 + SILENCE_GRANT_TTL_S + 1) == {}
    # 旧记录(无 expires_at 字段)不豁免:按 granted_at+30 天推
    st._grants["legacy"] = {"kind": "legacy", "domain": "", "granted_at": t0,
                            "n": 1, "hits": 1, "revoked_at": None, "revoke_reason": ""}
    assert st.is_granted("legacy", now=t0 + 60)
    assert not st.is_granted("legacy", now=t0 + SILENCE_GRANT_TTL_S + 1)


@pytest.mark.asyncio
async def test_expired_bucket_falls_back_and_offers_renewal(tmp_path, monkeypatch):
    """到期:该桶新卡回正常出卡 + 出一张续期卡(带上月对账数据);续期卡不重复。"""
    old = time.time() - SILENCE_GRANT_TTL_S - 3600   # 授权在 30 天零 1 小时前
    app, ws, calls = await _silence_setup(tmp_path, monkeypatch, ("ACCEPT", 0.95),
                                          grant_now=old)
    # 上月静音过 2 次(台账)+ 授权期内攒了 4 次对账(抽查/回退开奖)
    now = time.time()
    for i, pid in enumerate(("s1", "s2")):
        silence.record_silenced(app, {"ts": now - 86400 * (i + 1), "proposal_id": pid,
                                      "kind": "run_task", "domain": "", "bucket": "run_task",
                                      "summary": "x", "predicted": "ACCEPT", "confidence": 0.9,
                                      "ok": True, "detail": "", "overturned": False})
    seed_bucket(app, "run_task", "", 4, 4)
    c = card("run_task")
    await broadcast_proposal(app, c)
    await _drain(app)
    assert calls == []                                             # 到期不再静音
    assert app.state.proposal_registry.get(c.proposal_id) is not None  # 回正常出卡
    renewals = [p for p in app.state.proposal_registry.pending()
                if getattr(p, "kind", "") == KIND_SILENCE_GRANT
                and (getattr(p, "payload", {}) or {}).get("renew")]
    assert len(renewals) == 1                                      # 出了续期卡
    rp = renewals[0].payload
    assert rp["bucket"] == "run_task"
    assert rp["silenced_n"] == 2                                   # 上月对账:静音 N 次
    assert rp["audit_n"] == 4 and rp["audit_hits"] == 4            # 抽查对账 M 次中 H 次
    assert rp["oldest_pid"] in ("s1", "s2")                        # 最老留痕指针
    # 同桶续期卡挂着 → 不重复
    c2 = card("run_task")
    await broadcast_proposal(app, c2)
    await _drain(app)
    renewals2 = [p for p in app.state.proposal_registry.pending()
                 if getattr(p, "kind", "") == KIND_SILENCE_GRANT
                 and (getattr(p, "payload", {}) or {}).get("renew")]
    assert len(renewals2) == 1


def test_renewal_accept_regrants_30_days(tmp_path):
    app = make_app(tmp_path)
    old = time.time() - SILENCE_GRANT_TTL_S - 3600
    get_store(app).grant("run_task", "", now=old)
    assert not get_store(app).is_granted("run_task")
    renewal = maybe_offer_renewal(app, kind="run_task")            # 同步路径也能出续期卡
    assert renewal is not None and renewal.payload.get("renew") is True
    handlers = build_proposal_handlers(app)
    res = app.state.proposal_registry.decide(renewal.proposal_id, "ACCEPT", handlers=handlers)
    assert res is not None and res.ok and "已续期" in res.detail
    st = get_store(app)
    assert st.is_granted("run_task")                               # 续上了
    g = st.active_grants()["run_task"]
    assert g["expires_at"] > time.time() + SILENCE_GRANT_TTL_S - 120   # 新一期 30 天
    # 到期未续 + 押错 → 照吊(押错的桶不配走续期)
    app2 = make_app(tmp_path / "b")
    get_store(app2).grant("run_task", "", now=old)
    on_outcome(app2, proposal_id="pX", kind="run_task", domain="", hit=False)
    assert get_store(app2).expired_unrevoked("run_task") is None   # 已吊销,不再是"到期待续"
    assert maybe_offer_renewal(app2, kind="run_task") is None


# ---------------------------------------------------------------- ⑧ 爆炸半径硬顶
@pytest.mark.asyncio
async def test_blast_radius_cap_blocks_expensive_exec_bucket(tmp_path, monkeypatch):
    app, _, calls = await _silence_setup(tmp_path, monkeypatch, ("ACCEPT", 0.95))
    now = time.time()
    for i in range(3):   # 近期 3 次静音执行平均 35k token > 30k 顶
        silence.record_silenced(app, {"ts": now - i, "proposal_id": f"h{i}",
                                      "kind": "run_task", "domain": "", "bucket": "run_task",
                                      "summary": "x", "predicted": "ACCEPT", "confidence": 0.9,
                                      "ok": True, "detail": "", "overturned": False,
                                      "token_task_id": f"silenced:h{i}",
                                      "cost_tokens": 35_000})
    assert bucket_recent_avg_cost(app, "run_task") == 35_000
    assert bucket_recent_avg_cost(app, "run_task") > SILENCE_COST_CAP_TOKENS
    c = card("run_task")
    await broadcast_proposal(app, c)
    await _drain(app)
    assert calls == []                                             # 超顶 → 回人工
    assert app.state.proposal_registry.get(c.proposal_id) is not None
    # 便宜桶照常静音
    app2, _, calls2 = await _silence_setup(tmp_path / "b", monkeypatch, ("ACCEPT", 0.95))
    silence.record_silenced(app2, {"ts": now, "proposal_id": "cheap", "kind": "run_task",
                                   "domain": "", "bucket": "run_task", "summary": "x",
                                   "predicted": "ACCEPT", "confidence": 0.9, "ok": True,
                                   "detail": "", "overturned": False,
                                   "token_task_id": "silenced:cheap", "cost_tokens": 1_000})
    c2 = card("run_task")
    await broadcast_proposal(app2, c2)
    await _drain(app2)
    assert calls2 == [c2.proposal_id]


@pytest.mark.asyncio
async def test_blast_radius_live_requery_and_nonexec_exempt(tmp_path, monkeypatch):
    # 台账快照 cost_tokens=0(执行有异步尾巴,落账晚)—— 现查 token_ledger 拿到真成本 → 也拦
    led = TokenLedger(None)
    register_ledger(led)
    try:
        app, _, calls = await _silence_setup(tmp_path, monkeypatch, ("ACCEPT", 0.95))
        silence.record_silenced(app, {"ts": time.time(), "proposal_id": "hx",
                                      "kind": "run_task", "domain": "", "bucket": "run_task",
                                      "summary": "x", "predicted": "ACCEPT", "confidence": 0.9,
                                      "ok": True, "detail": "", "overturned": False,
                                      "token_task_id": "silenced:hx", "cost_tokens": 0})
        led.record(source="drive", model="m", input=25_000, output=15_000, task_id="silenced:hx")
        assert bucket_recent_avg_cost(app, "run_task") == 40_000
        c = card("run_task")
        await broadcast_proposal(app, c)
        await _drain(app)
        assert calls == []                                         # 现查超顶 → 回人工
        assert app.state.proposal_registry.get(c.proposal_id) is not None
    finally:
        register_ledger(None)
    # 非执行类 kind(crystallize_skill)不吃成本顶:贵也照静音(顶只管执行类爆炸半径)
    app2, _, calls2 = await _silence_setup(tmp_path / "b", monkeypatch, ("ACCEPT", 0.95),
                                           grant_kind="crystallize_skill")
    silence.record_silenced(app2, {"ts": time.time(), "proposal_id": "k1",
                                   "kind": "crystallize_skill", "domain": "",
                                   "bucket": "crystallize_skill", "summary": "x",
                                   "predicted": "ACCEPT", "confidence": 0.9, "ok": True,
                                   "detail": "", "overturned": False,
                                   "token_task_id": "silenced:k1", "cost_tokens": 99_000})
    c2 = card("crystallize_skill")
    await broadcast_proposal(app2, c2)
    await _drain(app2)
    assert calls2 == [c2.proposal_id]


# ---------------------------------------------------------------- 押错吊销 + 撤销 + 翻案
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
    """吊销后重挣授权:只认吊销之后的新鲜命中,旧成绩单不算;评估水位同时清零。"""
    app = make_app(tmp_path)
    seed_bucket(app, "run_task", "", 50, 50, reject_correct=2)
    st = get_store(app)
    st.grant("run_task", "")
    st.note_eval("run_task", 50)                                  # 曾评估过
    revoked_at = time.time() + 1
    st.revoke("run_task", reason="押错", now=revoked_at)
    assert st.eval_mark("run_task") == 0                          # 水位随吊销清零(新账新算)
    # 旧的 50 次全中还在账本里,但都在吊销水位之前 → 门不过
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


def test_gate_fires_at_eval_watermark_via_real_seam(tmp_path):
    """真接缝端到端:50 次拍板(48 次押 ACCEPT 全中 + 2 次押 REJECT 且中)——
    评估只发生在水位点(n=25 不够 n、n=50 达门),第 50 次拍板出授权卡,含刚开的这一奖;
    传输层的 pydantic 占位默认 domain="dom-1" 不污染桶(卡 payload 权威)。"""
    N = 2 * SILENCE_EVAL_BATCH_N   # 50
    app = make_app(tmp_path)
    for i in range(N):
        pid = f"seam-{i}"
        c = card("run_task", pid=pid, summary=f"任务{i}")
        app.state.proposal_registry.register(c)
        pred = "REJECT" if i < 2 else "ACCEPT"                    # 前 2 次是判别力证据
        app.state.taste_predictions.record_prediction(pid, pred, 0.9)
        if i == N - 2:   # 第 49 次后:统计已 49/49 但没到水位点 → 门不该开
            grants = [p for p in app.state.proposal_registry.pending()
                      if getattr(p, "kind", "") == KIND_SILENCE_GRANT]
            assert grants == []
        record_decision_signals(app, decision=pred if pred == "REJECT" else "ACCEPT",
                                proposal_id=pid, domain="dom-1")
    grants = [p for p in app.state.proposal_registry.pending()
              if getattr(p, "kind", "") == KIND_SILENCE_GRANT]
    assert len(grants) == 1                                       # 第 50 次拍板即出卡
    assert grants[0].payload["n"] == N                            # 含触发的这一奖
    assert grants[0].payload["reject_correct"] == 2
    assert grants[0].payload["bucket"] == "run_task"              # 占位假域没污染桶


def test_domain_authority_is_card_payload_not_transport_default(tmp_path):
    """桶域以卡 payload 为权威 —— 传输层默认 "dom-1" 不可信。带域卡经真接缝攒出的授权桶,
    必须和 try_silence 的拦截桶(payload 侧)完全一致。"""
    N = 2 * SILENCE_EVAL_BATCH_N
    app = make_app(tmp_path)
    for i in range(N):
        pid = f"dom-{i}"
        c = card("route_to_role", domain="biz1", pid=pid, summary=f"委派{i}")
        app.state.proposal_registry.register(c)
        pred = "REJECT" if i < 2 else "ACCEPT"
        app.state.taste_predictions.record_prediction(pid, pred, 0.9)
        record_decision_signals(app, decision=pred, proposal_id=pid, domain="dom-1")
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
    assert st.eval_mark("run_task") == 0
