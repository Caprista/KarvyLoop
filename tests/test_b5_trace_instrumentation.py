"""test_b5_trace_instrumentation — B-5 标定埋点验收(docs/68 P1 未标定常数族)。

六条 kind(#4 #5 #8 #9 #10 #11,决策侧六条另测):
- #4  `promote_blocked`     crystallize.maybe_promote 的 NotYet/NotEligible 拒绝分支
- #5  `cluster_decision`    observe 的 token-overlap 聚类判定点(cluster_overlap=0.2)
- #8  `context_truncated`   ConversationManager.context_view 静默切窗(12 轮)
- #9  `governance_truncated` 1500 帽族四处(conversation/proposal_handlers/inbox_pipe/lessons)
- #10 `spread_recall_stats` 召回咽喉(采样 1/8 + via_spread>0 必落)
- #11 `tick_stats`          调度 30s tick(每小时窗口汇总 + 慢拍节流直报)

每条验:① 触发断言(事件真落、payload 带常数当前值);② fail-soft(trace 坏时主流程
行为一字不变);③ 高频两条(#10 #11)的采样/聚合策略锁;④ payload 封顶;⑤ 新 kind
绝不进 DROPPABLE_KINDS(容量环不滚标定事件)。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from karvyloop.cognition import calibration
from karvyloop.cognition.calibration import (
    CALIBRATION_TASK_ID,
    TickStatsAggregator,
    emit,
    set_calibration_trace,
)
from karvyloop.cognition.trace import DROPPABLE_KINDS, TraceStore

ALL_KINDS = ("promote_blocked", "cluster_decision", "context_truncated",
             "governance_truncated", "spread_recall_stats", "tick_stats")


# ---- 公共 fixture / 工具 ----

@pytest.fixture(autouse=True)
def _reset_calibration_state():
    """每测隔离:全局 sink 清空 + #10 采样计数器归零(相位确定性)。"""
    import karvyloop.cognition.memory as memory_mod
    set_calibration_trace(None)
    memory_mod._spread_calib_seq = 0
    yield
    set_calibration_trace(None)
    memory_mod._spread_calib_seq = 0


class BrokenTrace:
    """append 必炸的 sink:验 fail-soft(埋点炸了主流程一字不变)。"""

    def append(self, entry):  # noqa: ARG002
        raise RuntimeError("trace exploded (test)")


def calib(trace: TraceStore, kind: str):
    return trace.query(CALIBRATION_TASK_ID, kind=kind)


# ---- 发射器基础(纪律①②③)----

def test_emit_fail_soft_and_no_sink():
    # 无 sink → no-op 返 False;坏 sink → 吞异常返 False,绝不冒泡
    assert emit("promote_blocked", {"x": 1}) is False
    set_calibration_trace(BrokenTrace())
    assert emit("promote_blocked", {"x": 1}) is False


def test_emit_payload_capped_at_300_chars():
    ts = TraceStore()
    set_calibration_trace(ts)
    assert emit("governance_truncated", {"s": "长" * 5000, "n": 7, "big": list(range(100))})
    (e,) = calib(ts, "governance_truncated")
    assert e.payload["n"] == 7
    assert len(e.payload["s"]) <= 121                        # 单值封顶(可能带截断记号)
    assert len(e.payload["big"]) <= 12                       # 列表封顶
    assert len(json.dumps(e.payload, ensure_ascii=False)) < 400  # 总量 ~300 字(软帽)


def test_sink_is_weakref_no_zombie():
    ts = TraceStore()
    set_calibration_trace(ts)
    assert emit("tick_stats", {"a": 1}) is True
    del ts   # CPython 引用计数即回收 → sink 自动降级 no-op,不给弃店续命
    assert emit("tick_stats", {"a": 1}) is False


def test_calibration_kinds_never_droppable():
    """纪律③:标定事件微小、要留到内测结束 —— 容量环(prune_raw)绝不滚它。"""
    for k in ALL_KINDS:
        assert k not in DROPPABLE_KINDS, f"{k} 进了 DROPPABLE_KINDS,容量环会把标定分布滚丢"


# ---- #4 promote_blocked ----

def _promote_fixture(now: float = 1000.0):
    from karvyloop.crystallize import InMemoryUsageStore, VerifyStore
    from karvyloop.schemas import UsageStats
    store = InMemoryUsageStore()
    verify = VerifyStore()
    # 关1 已过(有门 + 成功过),关2 的 score 不够 → NotYet
    store.put("sigA", UsageStats(usage_count=1, success_count=1, last_used_at=now))
    verify.mark_verified("sigA", "t:0", clock=lambda: now)
    return store, verify


def test_promote_blocked_not_yet_emitted():
    from karvyloop.crystallize import DecisionKind, maybe_promote
    ts = TraceStore()
    set_calibration_trace(ts)
    store, verify = _promote_fixture()
    d = maybe_promote("sigA", store, verify, now=1000.0)
    assert d.kind is DecisionKind.NOT_YET
    (e,) = calib(ts, "promote_blocked")
    p = e.payload
    assert p["gate"] == "not_yet" and "score" in p["reason"]
    assert p["score"] == 1.0                       # usage=1 × recency 1.0
    assert p["promote_score"] == 3.0               # 常数当前值随事件走
    assert p["floor"] == 0.45                      # satisfaction_floor 当前值


def test_promote_blocked_not_eligible_emitted():
    from karvyloop.crystallize import DecisionKind, InMemoryUsageStore, VerifyStore, maybe_promote
    from karvyloop.schemas import UsageStats
    ts = TraceStore()
    set_calibration_trace(ts)
    store, verify = InMemoryUsageStore(), VerifyStore()
    store.put("sigB", UsageStats(usage_count=2, success_count=2, last_used_at=1000.0))
    d = maybe_promote("sigB", store, verify, now=1000.0)   # 无验证门 → 关1 拒
    assert d.kind is DecisionKind.NOT_ELIGIBLE
    (e,) = calib(ts, "promote_blocked")
    assert e.payload["gate"] == "not_eligible" and "verify gate" in e.payload["reason"]


def test_promote_ready_emits_nothing():
    from karvyloop.crystallize import DecisionKind, maybe_promote
    from karvyloop.schemas import UsageStats
    ts = TraceStore()
    set_calibration_trace(ts)
    store, verify = _promote_fixture()
    store.put("sigA", UsageStats(usage_count=6, success_count=6, last_used_at=1000.0))
    d = maybe_promote("sigA", store, verify, now=1000.0)
    assert d.kind is DecisionKind.READY
    assert not calib(ts, "promote_blocked")        # 只记拒绝,通过不落(分母=eval_fact 侧已有)


def test_promote_blocked_fail_soft():
    from karvyloop.crystallize import DecisionKind, maybe_promote
    set_calibration_trace(BrokenTrace())
    store, verify = _promote_fixture()
    d = maybe_promote("sigA", store, verify, now=1000.0)
    assert d.kind is DecisionKind.NOT_YET          # trace 炸,判定结果一字不变
    assert "score" in d.reason


# ---- #5 cluster_decision ----

SQUARE_A = "写一个 Python 文件计算 n 的平方"
SQUARE_B = "做个 python 平方计算器"


def _arun(intent: str, ts: float):
    from karvyloop.schemas.atom import AtomRun
    return AtomRun(atom_id="a", input={"intent": intent}, output={"text": "x"},
                   success=True, tool_calls=[], trace_ref="t", ts=ts)


def test_cluster_decision_emitted_open_then_merge():
    from karvyloop.crystallize import InMemoryUsageStore
    from karvyloop.crystallize.observe import observe
    ts = TraceStore()
    set_calibration_trace(ts)
    store = InMemoryUsageStore()
    observe([_arun(SQUARE_A, 1000.0)], store, debounce_sec=0, cluster_threshold=0.2)
    observe([_arun(SQUARE_B, 2000.0)], store, debounce_sec=0, cluster_threshold=0.2)
    evs = calib(ts, "cluster_decision")
    assert len(evs) == 2                            # 每次 slow-brain run 恰一条(不采样)
    first, second = evs[0].payload, evs[1].payload
    assert first["merged"] is False and first["n_clusters"] == 0   # 空库开新
    assert second["merged"] is True and second["overlap"] >= 0.2   # 换说法归并
    assert second["threshold"] == 0.2               # 常数当前值随事件走
    assert len(list(store.all())) == 1              # 行为不变:仍塌成 1 簇


def test_cluster_decision_records_below_threshold_overlap():
    """标定要看"没并上的差多远":原始 overlap 未过门槛也进分布。"""
    from karvyloop.crystallize import InMemoryUsageStore
    from karvyloop.crystallize.observe import observe
    ts = TraceStore()
    set_calibration_trace(ts)
    store = InMemoryUsageStore()
    observe([_arun(SQUARE_A, 1000.0)], store, debounce_sec=0, cluster_threshold=0.2)
    observe([_arun("帮我用 python 发一封邮件", 2000.0)], store, debounce_sec=0,
            cluster_threshold=0.2)                  # 只共享 python → 不并
    p = calib(ts, "cluster_decision")[1].payload
    assert p["merged"] is False and p["n_clusters"] == 1
    assert 0.0 < p["overlap"] < 0.2                 # 原始重叠分照记(min_shared/门槛只判不删数据)


def test_cluster_decision_off_and_fail_soft():
    from karvyloop.crystallize import InMemoryUsageStore
    from karvyloop.crystallize.observe import observe
    ts = TraceStore()
    set_calibration_trace(ts)
    store = InMemoryUsageStore()
    observe([_arun(SQUARE_A, 1000.0)], store, debounce_sec=0, cluster_threshold=0.0)
    assert not calib(ts, "cluster_decision")        # 聚类关着 → 无判定无事件
    set_calibration_trace(BrokenTrace())
    store2 = InMemoryUsageStore()
    observe([_arun(SQUARE_A, 1000.0)], store2, debounce_sec=0, cluster_threshold=0.2)
    observe([_arun(SQUARE_B, 2000.0)], store2, debounce_sec=0, cluster_threshold=0.2)
    assert len(list(store2.all())) == 1             # trace 炸,聚类投影一字不变
    # match_cluster 本身:不传 sink = 热路径一字不变(main_loop 3b 对齐重查不双记)
    from karvyloop.crystallize.cluster import match_cluster
    assert match_cluster(SQUARE_B, [("c0", SQUARE_A)], 0.2) == "c0"


# ---- #8 context_truncated ----

def _conv_mgr(tmp_path, *, context_turns: int, domain_registry=None):
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    store = ConversationStore(tmp_path / "conv")
    return ConversationManager(store, context_turns=context_turns,
                               domain_registry=domain_registry)


def test_context_truncated_emitted(tmp_path):
    ts = TraceStore()
    set_calibration_trace(ts)
    mgr = _conv_mgr(tmp_path, context_turns=2)
    mgr.start()
    for i in range(3):
        mgr.record_turn(f"q{i}", f"a{i}")
    view = mgr.context_view()
    assert len(view) == 2                           # 行为不变:只留最近 2 轮
    (e,) = calib(ts, "context_truncated")
    assert e.payload == {"total_turns": 3, "kept": 2, "dropped": 1, "max_turns": 2}


def test_context_view_no_event_when_within_window(tmp_path):
    ts = TraceStore()
    set_calibration_trace(ts)
    mgr = _conv_mgr(tmp_path, context_turns=12)
    mgr.start()
    mgr.record_turn("q", "a")
    assert len(mgr.context_view()) == 1
    assert not calib(ts, "context_truncated")       # 没截断不落账(只记真截)


def test_context_truncated_fail_soft(tmp_path):
    set_calibration_trace(BrokenTrace())
    mgr = _conv_mgr(tmp_path, context_turns=2)
    mgr.start()
    for i in range(5):
        mgr.record_turn(f"q{i}", f"a{i}")
    view = mgr.context_view()                       # trace 炸,视图照常
    assert [t.user_intent for t in view] == ["q3", "q4"]


# ---- #9 governance_truncated(四处 1500 帽)----

class _FakeDomain:
    def __init__(self, text: str):
        self.name = "Biz"
        self.value_md = SimpleNamespace(text=text)
        self.deontic = None


class _FakeRegistry:
    def __init__(self, dom):
        self._dom = dom

    def get(self, domain_id):  # noqa: ARG002
        return self._dom


def test_governance_truncated_conversation_main_injection(tmp_path):
    from karvyloop.domain.registry import Address
    ts = TraceStore()
    set_calibration_trace(ts)
    long_text = "v" * 2000
    mgr = _conv_mgr(tmp_path, context_turns=12,
                    domain_registry=_FakeRegistry(_FakeDomain(long_text)))
    mgr.set_peer(Address(domain_id="biz", role="agent", agent_id="x"))
    out = mgr.governance_text()
    assert "…" in out and "v" * 1501 not in out     # 行为不变:仍截到 1500
    (e,) = calib(ts, "governance_truncated")
    assert e.payload["site"] == "conversation.governance_text"
    assert e.payload["orig_len"] == 2000 and e.payload["cap"] == 1500
    assert e.payload["domain"] == "biz"


def test_governance_no_event_when_short(tmp_path):
    from karvyloop.domain.registry import Address
    ts = TraceStore()
    set_calibration_trace(ts)
    mgr = _conv_mgr(tmp_path, context_turns=12,
                    domain_registry=_FakeRegistry(_FakeDomain("短 value.md")))
    mgr.set_peer(Address(domain_id="biz", role="agent", agent_id="x"))
    assert mgr.governance_text()
    assert not calib(ts, "governance_truncated")


def test_governance_truncated_proposal_handlers():
    from karvyloop.console.proposal_handlers import _governance_for
    ts = TraceStore()
    set_calibration_trace(ts)
    app = SimpleNamespace(state=SimpleNamespace(
        domain_registry=_FakeRegistry(_FakeDomain("w" * 1800))))
    out = _governance_for(app, {"domain_id": "biz", "domain_name": "Biz", "role": "pm"})
    assert "…" in out
    (e,) = calib(ts, "governance_truncated")
    assert e.payload["site"] == "proposal_handlers._governance_for"
    assert e.payload["orig_len"] == 1800 and e.payload["cap"] == 1500


def test_governance_truncated_inbox_pipe():
    from karvyloop.channels.inbox_pipe import BODY_TRIAGE_CHARS, InboxMail, triage_material
    ts = TraceStore()
    set_calibration_trace(ts)
    mail = InboxMail(msg_id="m1", thread_key="t1", sender="a@b", subject="s",
                     body="b" * (BODY_TRIAGE_CHARS + 300))
    mat = triage_material(mail)
    assert "b" * BODY_TRIAGE_CHARS in mat and "b" * (BODY_TRIAGE_CHARS + 1) not in mat
    (e,) = calib(ts, "governance_truncated")
    assert e.payload["site"] == "inbox_pipe.triage_material"
    assert e.payload["orig_len"] == BODY_TRIAGE_CHARS + 300
    assert e.payload["cap"] == BODY_TRIAGE_CHARS
    # 不超帽不落账
    triage_material(InboxMail(msg_id="m2", thread_key="t2", sender="a@b",
                              subject="s", body="short"))
    assert len(calib(ts, "governance_truncated")) == 1


def test_governance_truncated_lessons_token_cap():
    from karvyloop.crystallize.lessons import judge_lesson

    class _GW:
        def resolve_model(self, scope):  # noqa: ARG002
            return "m"

        async def complete(self, messages, tools, ref, system=None):  # noqa: ARG002
            return
            yield  # pragma: no cover — 空 async generator

    ts = TraceStore()
    set_calibration_trace(ts)
    out = asyncio.run(judge_lesson("x" * 20000, gateway=_GW()))     # ~5000 tok > 1500 帽
    assert out == ""                                # 行为不变(假网关无输出)
    (e,) = calib(ts, "governance_truncated")
    assert e.payload["site"] == "lessons.judge_lesson"
    assert e.payload["orig_len"] == 20000 and e.payload["cap_tokens"] == 1500


def test_governance_truncated_fail_soft(tmp_path):
    from karvyloop.domain.registry import Address
    set_calibration_trace(BrokenTrace())
    long_text = "v" * 2000
    mgr = _conv_mgr(tmp_path, context_turns=12,
                    domain_registry=_FakeRegistry(_FakeDomain(long_text)))
    mgr.set_peer(Address(domain_id="biz", role="agent", agent_id="x"))
    out = mgr.governance_text()                     # trace 炸,治理串照常产出
    assert "…" in out and "价值观" in out


# ---- #10 spread_recall_stats(采样策略锁)----

def _mem_with(contents: list[str], trace=None):
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.schemas import Belief
    mem = MemoryManager(trace=trace)
    for i, c in enumerate(contents):
        mem.write(Belief(content=c, provenance={"source": "trace_observed", "agent": "t",
                                                "ts": float(i + 1)},
                         freshness_ts=float(i + 1), scope="personal"))
    return mem


def test_spread_stats_via_spread_always_recorded():
    """via_spread 命中(hop 分布主料)不吃采样:即使相位没采到也落。"""
    import karvyloop.cognition.memory as memory_mod
    ts = TraceStore()
    # b2 与 query 零词面交集,但与 b1 共享 ≥2 token → 靠图谱扩散抬上来(via_spread)
    mem = _mem_with(["alpha bravo charlie delta", "charlie delta echo foxtrot"], trace=ts)
    memory_mod._spread_calib_seq = 1                # 相位错开:本次**不**采样
    sink: list = []
    out = mem.recall_block("alpha bravo", explain_sink=sink)
    assert out and len(sink) == 2                   # 行为不变:两条都召回、解释照回
    (e,) = calib(ts, "spread_recall_stats")
    p = e.payload
    assert p["sampled"] is False and p["via_spread"] == 1 and p["picked"] == 2
    assert p["hops"] == [1] and p["max_hops"] == 1
    assert p["hops_cfg"] == 3 and p["decay_cfg"] == 0.5   # 被标定常数当前值随事件走


def test_spread_stats_sampling_one_in_eight():
    """采样锁:explain_sink 不给、无扩散命中 → 只有 1/8 相位落账(防灌爆 Trace)。"""
    ts = TraceStore()
    mem = _mem_with(["alpha bravo charlie delta"], trace=ts)
    for _ in range(16):
        mem.recall_block("alpha bravo")             # seq 0..15 → 采样恰 2 次(0 与 8)
    evs = calib(ts, "spread_recall_stats")
    assert len(evs) == 2
    assert all(e.payload["sampled"] is True and e.payload["via_spread"] == 0 for e in evs)


def test_spread_stats_explain_sink_alone_does_not_flood():
    """drive 生产路径每轮都传 explain_sink:无扩散命中且没采到 → 不落账。"""
    import karvyloop.cognition.memory as memory_mod
    ts = TraceStore()
    mem = _mem_with(["alpha bravo charlie delta"], trace=ts)
    memory_mod._spread_calib_seq = 1                # 非采样相位
    sink: list = []
    assert mem.recall_block("alpha bravo", explain_sink=sink)
    assert sink                                     # 解释照回(行为不变)
    assert not calib(ts, "spread_recall_stats")     # 但标定不落(量控)


def test_spread_stats_fail_soft():
    mem = _mem_with(["alpha bravo charlie delta", "charlie delta echo foxtrot"],
                    trace=BrokenTrace())
    sink: list = []
    out = mem.recall_block("alpha bravo", explain_sink=sink)   # 采样相位 + trace 炸
    assert out and len(sink) == 2                   # 召回/解释一字不变


# ---- #11 tick_stats(聚合策略锁)----

def _clock(state: dict):
    return lambda: state["now"]


def test_tick_stats_hourly_window_summary():
    ts = TraceStore()
    st = {"now": 0.0}
    agg = TickStatsAggregator(interval_s=30.0, window_s=3600.0, clock=_clock(st), trace=ts)
    for _ in range(10):
        st["now"] += 30.0
        agg.record(0.01, fired=1, elapsed_s=30.0)
    assert not calib(ts, "tick_stats")              # 窗未满:一条不落(绝不每拍落账)
    st["now"] = 3601.0
    agg.record(0.02, fired=0, elapsed_s=70.0)       # 第 11 拍:elapsed>2×30s = 跳拍
    (e,) = calib(ts, "tick_stats")
    p = e.payload
    assert p["event"] == "window"
    assert p["ticks"] == 11 and p["fired"] == 10 and p["late"] == 1
    assert p["max_s"] == 0.02 and p["interval_s"] == 30.0
    # 窗口重置:下一拍不再立刻出汇总
    st["now"] += 30.0
    agg.record(0.01, elapsed_s=30.0)
    assert len(calib(ts, "tick_stats")) == 1


def test_tick_stats_slow_tick_throttled_direct_report():
    ts = TraceStore()
    st = {"now": 1000.0}
    agg = TickStatsAggregator(interval_s=30.0, window_s=10**9, slow_s=5.0,
                              slow_report_min_gap_s=600.0, clock=_clock(st), trace=ts)
    agg.record(6.0)                                 # 慢拍 → 直报
    st["now"] += 30.0
    agg.record(7.0)                                 # 节流窗内 → 不再报
    slow = [e for e in calib(ts, "tick_stats") if e.payload.get("event") == "slow_tick"]
    assert len(slow) == 1 and slow[0].payload["duration_s"] == 6.0
    st["now"] += 601.0
    agg.record(6.5)                                 # 过节流窗 → 再报
    slow = [e for e in calib(ts, "tick_stats") if e.payload.get("event") == "slow_tick"]
    assert len(slow) == 2


def test_tick_stats_fail_soft():
    st = {"now": 0.0}
    agg = TickStatsAggregator(interval_s=30.0, window_s=1.0, clock=_clock(st),
                              trace=BrokenTrace())
    for _ in range(3):
        st["now"] += 30.0
        agg.record(9.9, fired=1, elapsed_s=30.0)    # 慢拍 + 窗满都触发 emit → 全吞,不炸
    assert agg.n_ticks >= 0                         # record 从不抛(调度循环行为不变)
