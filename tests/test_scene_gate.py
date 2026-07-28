"""test_scene_gate — 「猜你想干 v2」刀1:场景信号 + 确定性唤醒门(docs/94 刀1)。

覆盖(任务书六组):
① 各信号判据真触发/不误触发(刚失败窗口 / 日程将至·仅上次失败过 / 刚完成大活 basis);
② 日预算封顶 + 预算日重置 + 例外不扣(HIGH_RISK / user_initiated);
③ fingerprint 永不重复(跨重启持久)+ REJECT 负反馈 7 天(含拍板咽喉回钩);
④ 源B 降级:daily_poll 不再出卡但凝练/存储照旧(见 test_karvy_intent_analyst.py);
⑤ 预算回执只出一次(用尽当刻,每天至多一次);
⑥ 既有 schedule_suggest / 源A(proactive_from_state / task_monitor)套件回归(原文件)。

全程零 LLM;门坏了宁可不提(旁路纪律)。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from karvyloop.console.tasks import TaskRegistry
from karvyloop.karvy.atoms import Proposal
from karvyloop.karvy.proposal_registry import KIND_RUN_TASK, PendingProposalRegistry
from karvyloop.karvy.scene_gate import (
    SceneGateStore, emit_gated, scene_gate, scene_tick,
)
from karvyloop.karvy.scene_signals import (
    SCENE_MANUAL_REPEAT, SCENE_SCHEDULE_DUE, SCENE_TASK_FAILED,
    collect_scene_signals, recent_big_job_basis, recent_failed_task_signals,
    task_failed_fingerprint, upcoming_schedule_signals,
)
from karvyloop.karvy.scheduler import SchedulerStore


# ---------------------------------------------------------------- 装置
class FakeWS:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_json(self, msg) -> None:
        self.sent.append(msg)


def make_app(tmp_path, *, with_gate_file=True):
    state = SimpleNamespace()
    state.proposal_registry = PendingProposalRegistry()
    state.ws_clients = set()
    state.runtime_kwargs = {}
    state.task_registry = TaskRegistry()
    state.scheduler_store = SchedulerStore(tmp_path / "schedules.json")
    if with_gate_file:
        state.config_path = str(tmp_path / "config.yaml")   # scene_gate.json 落同目录
    return SimpleNamespace(state=state)


def _failed_task(reg: TaskRegistry, intent: str, *, finished_ago: float = 1.0) -> str:
    tid = reg.start(who="小卡", domain_id="l0", intent=intent)
    reg.finish(tid, error="网络中断,没跑完")
    reg._by_id[tid].finished = time.time() - finished_ago
    return tid


def _msgs(ws: FakeWS, mtype: str) -> list:
    return [m for m in ws.sent if m.get("type") == mtype]


# ================================================================ ① 信号判据
def test_recent_failure_signal_fires_only_in_window(tmp_path):
    reg = TaskRegistry()
    now = time.time()
    fresh = _failed_task(reg, "导出季度报表", finished_ago=60)          # 1 分钟前失败 → 触发
    _failed_task(reg, "老掉牙的失败", finished_ago=2 * 3600)            # 2 小时前 → 窗外不触发
    ok_id = reg.start(who="小卡", intent="顺利完成的活儿")
    reg.finish(ok_id, result="done")                                    # 成功 → 不触发
    sigs = recent_failed_task_signals(reg, now=now)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.scene_kind == SCENE_TASK_FAILED
    assert s.fingerprint == task_failed_fingerprint(fresh)
    # kind/文案沿用既有 run_task 重试卡(不新造建议类型)
    assert s.proposal.kind == KIND_RUN_TASK
    assert s.proposal.payload["intent"] == "导出季度报表"
    assert s.proposal.payload["source"] == "scene.recent_failure"
    assert s.proposal.context_ref == {"kind": "task", "id": fresh}


def test_recent_failure_empty_intent_no_signal():
    reg = TaskRegistry()
    tid = reg.start(who="小卡", intent="")
    reg.finish(tid, error="x")
    assert recent_failed_task_signals(reg, now=time.time()) == []
    assert recent_failed_task_signals(None) == []   # 无源 → 空,不炸


def test_schedule_due_signal_only_when_last_failed(tmp_path):
    st = SchedulerStore(tmp_path / "s.json")
    t = st.add("*/5 * * * *", "拉取汇率数据", title="汇率同步")   # 每 5 分钟 → 必在 15min 窗内
    now = time.time()
    # 上次没失败(没跑过)→ 不提前提醒(第一版判据:只做"上次失败过")
    assert upcoming_schedule_signals(st, now=now) == []
    st.mark_run(t.id, "ok")
    assert upcoming_schedule_signals(st, now=now) == []
    # 上次失败过 → 触发:kind 复用 run_task,payload 带 schedule_id,basis 有场景人话
    st.mark_run(t.id, "error", error="上游接口 500")
    sigs = upcoming_schedule_signals(st, now=now)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.scene_kind == SCENE_SCHEDULE_DUE
    assert s.fingerprint.startswith(f"schedule_due:{t.id}:")
    assert s.proposal.kind == KIND_RUN_TASK
    assert s.proposal.payload["intent"] == "拉取汇率数据"
    assert s.proposal.payload["schedule_id"] == t.id
    assert s.proposal.basis and s.proposal.summary
    assert s.proposal.context_ref == {"kind": "schedule", "id": t.id}
    # 同一场 fingerprint 稳定(窗口内多次 tick 采到同一个键 → 门自然去重)
    assert upcoming_schedule_signals(st, now=now + 1)[0].fingerprint == s.fingerprint
    # Hardy 拍(刀1 收口):指纹粒度 = 同 schedule_id **每日**一条 —— 高频失败定时任务
    # (*/5 一直挂)每场新 next_run_ts 不许日日吃满预算;跨"场"同日指纹必须相同。
    import time as _t
    _day = _t.strftime("%Y-%m-%d", _t.localtime(now))
    assert s.fingerprint == f"schedule_due:{t.id}:{_day}"
    later_same_day = upcoming_schedule_signals(st, now=now + 300)   # 下一场(*/5 又一轮)
    if later_same_day and _t.strftime("%Y-%m-%d", _t.localtime(now + 300)) == _day:
        assert later_same_day[0].fingerprint == s.fingerprint, "同日不同场必须同指纹(每日一条)"


def test_schedule_due_far_or_disabled_no_signal(tmp_path):
    st = SchedulerStore(tmp_path / "s.json")
    far = st.add("0 0 1 1 *", "一年一次的大扫除")     # 下次触发在天边 → 不提醒
    st.mark_run(far.id, "error", error="x")
    assert upcoming_schedule_signals(st, now=time.time()) == []
    near = st.add("*/5 * * * *", "拉取汇率数据")
    st.mark_run(near.id, "error", error="x")
    st.set_enabled(near.id, False)                     # 停用 → 不提醒
    assert upcoming_schedule_signals(st, now=time.time()) == []


def test_big_job_basis_only_for_long_recent_drive(tmp_path):
    app = make_app(tmp_path)
    reg = app.state.task_registry
    now = time.time()
    tid = reg.start(who="小卡", intent="生成本周销售周报", kind="drive")
    reg._by_id[tid].started = now - 300               # 跑了 5 分钟 > 阈值 2 分钟
    reg.finish(tid, result="done")                    # finished = 现在(刚跑完)
    basis = recent_big_job_basis(app, "生成本周销售周报", now=time.time())
    assert basis                                       # 场景人话非空
    # 短活不算"大活"
    tid2 = reg.start(who="小卡", intent="随手问一句", kind="drive")
    reg._by_id[tid2].started = time.time() - 10
    reg.finish(tid2, result="ok")
    assert recent_big_job_basis(app, "随手问一句", now=time.time()) == ""
    # intent 对不上 → 空
    assert recent_big_job_basis(app, "别的事", now=time.time()) == ""


# ================================================================ ② 日预算封顶 / 重置 / 例外
def _local_noon(days_from_now: int = 0) -> float:
    lt = time.localtime(time.time() + days_from_now * 86400)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 12, 0, 0, 0, 0, -1))


def test_gate_budget_cap_and_day_reset(tmp_path):
    g = SceneGateStore(tmp_path / "gate.json")
    base = _local_noon(0)
    for i in range(3):
        assert g.check(f"fp{i}", "task_failed", budget=3, now=base) == "allow"
        g.charge(f"fp{i}", "task_failed", budget=3, now=base)
    # 第 4 张:预算用尽
    assert g.check("fp3", "task_failed", budget=3, now=base) == "budget"
    # 预算日重置:第二天照常出
    nxt = _local_noon(1)
    assert g.check("fp3", "task_failed", budget=3, now=nxt) == "allow"
    assert g.spent_today(now=nxt) == 0


@pytest.mark.asyncio
async def test_exempt_high_risk_and_user_initiated_bypass_budget(tmp_path):
    """例外不扣:HIGH_RISK 卡 / 用户主动触发的卡 —— 预算用尽也直出,且不扣预算。"""
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}
    g = scene_gate(app)
    base = _local_noon(0)
    for i in range(3):
        g.charge(f"fp{i}", "task_failed", budget=3, now=base)   # 打满预算
    # HIGH_RISK kind(schedule_catchup)→ 直出
    hr = Proposal(summary="补跑?", options=("ACCEPT", "REJECT"), strength=0.7,
                  evidence_refs=(), habit_id=0, model_ref="", ts=base,
                  kind="schedule_catchup", payload={"intent": "补跑一次"})
    sent = await emit_gated(app, hr, fingerprint="x1", scene_kind="task_failed", now=base)
    assert sent is not None
    # 用户主动触发(payload.user_initiated)→ 直出
    ui = Proposal(summary="用户要的", options=("ACCEPT",), strength=0.5, evidence_refs=(),
                  habit_id=0, model_ref="", ts=base, kind=KIND_RUN_TASK,
                  payload={"intent": "再来一遍", "user_initiated": True})
    sent2 = await emit_gated(app, ui, fingerprint="x2", scene_kind="task_failed", now=base)
    assert sent2 is not None
    # 预算没被例外卡扣掉
    assert g.spent_today(now=base) == 3
    # 普通场景卡仍被预算拦
    plain = Proposal(summary="普通场景卡", options=("ACCEPT",), strength=0.5, evidence_refs=(),
                     habit_id=0, model_ref="", ts=base, kind=KIND_RUN_TASK,
                     payload={"intent": "还想再提一个"})
    assert await emit_gated(app, plain, fingerprint="x3",
                            scene_kind="task_failed", now=base) is None


# ================================================================ ③ 指纹永不重复 + REJECT 负反馈
def test_fingerprint_never_repeats_even_across_restart(tmp_path):
    p = tmp_path / "gate.json"
    g = SceneGateStore(p)
    base = _local_noon(0)
    g.charge("task_failed:abc", "task_failed", budget=3, now=base)
    assert g.check("task_failed:abc", "task_failed", budget=3, now=base) == "dup"
    # 跨"重启"(重新从盘加载)仍记得;隔天也永不再出(dup 优先于预算重置)
    g2 = SceneGateStore(p)
    assert g2.check("task_failed:abc", "task_failed", budget=3, now=_local_noon(1)) == "dup"


def test_reject_cooldown_seven_days(tmp_path):
    g = SceneGateStore(tmp_path / "gate.json")
    base = _local_noon(0)
    g.note_reject(SCENE_SCHEDULE_DUE, now=base)
    # 同 kind 新指纹 7 天内不出
    assert g.check("schedule_due:new:1", SCENE_SCHEDULE_DUE, budget=3,
                   now=base + 86400) == "rejected"
    # 别的场景 kind 不连坐
    assert g.check("task_failed:t1", SCENE_TASK_FAILED, budget=3, now=base + 86400) == "allow"
    # 7 天后解禁
    assert g.check("schedule_due:new:1", SCENE_SCHEDULE_DUE, budget=3,
                   now=base + 8 * 86400) == "allow"


def test_reject_feedback_wired_at_decision_seam(tmp_path):
    """拍板咽喉(record_decision_signals)回钩:REJECT 带 scene_kind 标的卡 → 门记 7 天冷却。"""
    from karvyloop.console.decision_wire import record_decision_signals
    app = make_app(tmp_path)
    card = Proposal(summary="快到点了先试跑?", options=("ACCEPT", "REJECT"), strength=0.6,
                    evidence_refs=(), habit_id=0, model_ref="", ts=1.0, kind=KIND_RUN_TASK,
                    payload={"intent": "拉取汇率数据", "scene_kind": SCENE_SCHEDULE_DUE})
    app.state.proposal_registry.register(card)
    record_decision_signals(app, decision="REJECT", proposal_id=card.proposal_id)
    g = scene_gate(app)
    assert g.check("schedule_due:other:9", SCENE_SCHEDULE_DUE, budget=3,
                   now=time.time()) == "rejected"
    # ACCEPT 不触发负反馈
    card2 = Proposal(summary="x", options=("ACCEPT",), strength=0.6, evidence_refs=(),
                     habit_id=0, model_ref="", ts=1.0, kind=KIND_RUN_TASK,
                     payload={"intent": "另一类", "scene_kind": SCENE_TASK_FAILED})
    app.state.proposal_registry.register(card2)
    record_decision_signals(app, decision="ACCEPT", proposal_id=card2.proposal_id)
    assert g.check("task_failed:z", SCENE_TASK_FAILED, budget=3, now=time.time()) == "allow"


# ================================================================ ②⑤ 全路径:tick 封顶 + 回执只出一次
@pytest.mark.asyncio
async def test_scene_tick_caps_at_budget_and_receipt_once(tmp_path):
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}
    reg = app.state.task_registry
    for i in range(5):   # 5 条新鲜失败任务 → 只出 3 张(预算 N=3)
        _failed_task(reg, f"第 {i} 件失败的正经事")
    emitted = await scene_tick(app)
    assert emitted == 3
    cards = _msgs(ws, "h2a_proposal")
    assert len(cards) == 3
    # 每张放行的卡带 scene_kind 标(REJECT 负反馈回钩凭它)
    assert all(c["payload"]["payload"].get("scene_kind") == SCENE_TASK_FAILED for c in cards)
    # 用尽当刻:一次性轻回执,恰好一条
    receipts = _msgs(ws, "scene_budget_receipt")
    assert len(receipts) == 1 and receipts[0]["payload"]["text"]
    # 再 tick:预算已尽 → 零新卡、回执不重复(只出一次)
    emitted2 = await scene_tick(app)
    assert emitted2 == 0
    assert len(_msgs(ws, "h2a_proposal")) == 3
    assert len(_msgs(ws, "scene_budget_receipt")) == 1


@pytest.mark.asyncio
async def test_scene_tick_no_signal_is_silent_zero_cost(tmp_path):
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}
    assert await scene_tick(app) == 0
    assert ws.sent == []


# ================================================================ 源A 统一记账(boot 兜底 / task_monitor)
@pytest.mark.asyncio
async def test_proactive_from_state_system_path_gated_user_path_not(tmp_path):
    from karvyloop.console.proposals import proactive_from_state
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}
    tid = _failed_task(app.state.task_registry, "每周汇总销售数据")
    # 系统自发(开机兜底)→ 过门:出卡 + 扣预算 + 记指纹
    p, sent = await proactive_from_state(app)
    assert p is not None and sent == 1
    g = scene_gate(app)
    assert g.spent_today(now=time.time()) == 1
    assert g.seen(task_failed_fingerprint(tid))
    # 再来一次系统自发:同任务指纹 → 永不重复
    p2, sent2 = await proactive_from_state(app)
    assert p2 is None and sent2 == 0
    # 用户主动点"来点建议" → 不过门(不是打扰),照出、不扣
    p3, sent3 = await proactive_from_state(app, user_initiated=True)
    assert p3 is not None and sent3 == 1
    assert g.spent_today(now=time.time()) == 1


@pytest.mark.asyncio
async def test_task_monitor_resume_card_counts_into_budget(tmp_path):
    """task_monitor 停滞重试卡也是源A → 扣同一份预算、同指纹家族(docs/94 刀1 统一记账)。"""
    from karvyloop.console.task_monitor import run_task_monitor
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}
    reg = app.state.task_registry
    tid = reg.start(who="小卡", intent="漫长的迁移任务")
    old = time.time() - 2 * 3600
    reg._by_id[tid].started = old
    for ev in reg._by_id[tid].events:
        ev["ts"] = old
    n = await run_task_monitor(app, threshold=600)
    assert n == 1
    assert scene_gate(app).spent_today(now=time.time()) == 1
    assert scene_gate(app).seen(task_failed_fingerprint(tid))
    cards = _msgs(ws, "h2a_proposal")
    assert len(cards) == 1
    assert cards[0]["payload"]["payload"]["source"] == "task_monitor.stalled"


# ================================================================ 信号3(schedule_suggest)记账 + 信号4 前缀
@pytest.mark.asyncio
async def test_schedule_suggest_charges_budget_and_stamps_scene_kind(tmp_path):
    from karvyloop.console.schedule_suggest import maybe_suggest_schedule
    from karvyloop.karvy.manual_run_counter import ManualRunCounter
    app = make_app(tmp_path)
    app.state.manual_run_counter = ManualRunCounter(tmp_path / "counts.json")
    ws = FakeWS(); app.state.ws_clients = {ws}
    assert await maybe_suggest_schedule(app, "跑竞品扫描") is None   # 第 1 次:未达 N
    card = await maybe_suggest_schedule(app, "跑竞品扫描")            # 第 2 次:出卡
    assert card is not None
    assert card.payload.get("scene_kind") == SCENE_MANUAL_REPEAT      # 负反馈回钩标
    assert scene_gate(app).spent_today(now=time.time()) == 1          # 扣统一日预算


@pytest.mark.asyncio
async def test_schedule_suggest_budget_deny_keeps_already_suggested_unburnt(tmp_path):
    """预算拒 → 不出卡且**不烧** already_suggested(今天没轮到 ≠ 永不再提);次日再达 N 照提。"""
    from karvyloop.console.schedule_suggest import maybe_suggest_schedule
    from karvyloop.karvy.ambient import intent_fingerprint
    from karvyloop.karvy.manual_run_counter import ManualRunCounter
    app = make_app(tmp_path)
    app.state.manual_run_counter = ManualRunCounter(tmp_path / "counts.json")
    base = _local_noon(0)
    g = scene_gate(app)
    for i in range(3):
        g.charge(f"fp{i}", "task_failed", budget=3, now=base)   # 今天预算打满
    assert await maybe_suggest_schedule(app, "跑竞品扫描", now=base) is None   # 第 1 次
    assert await maybe_suggest_schedule(app, "跑竞品扫描", now=base) is None   # 第 2 次:达 N 但预算拒
    fp = intent_fingerprint("跑竞品扫描")
    assert app.state.manual_run_counter.already_suggested(fp) is False        # 没被烧掉
    # 第二天手动又跑一次(count=3 ≥ N)→ 预算已重置 → 照提
    card = await maybe_suggest_schedule(app, "跑竞品扫描", now=_local_noon(1))
    assert card is not None
    assert app.state.manual_run_counter.already_suggested(fp) is True


@pytest.mark.asyncio
async def test_schedule_suggest_big_job_prefix_when_just_finished_long_drive(tmp_path):
    """信号4「刚完成大活」:定时建议本来就会出的时刻,basis 前缀「你刚跑完 X」场景人话。"""
    from karvyloop import i18n
    from karvyloop.console.schedule_suggest import maybe_suggest_schedule
    from karvyloop.karvy.manual_run_counter import ManualRunCounter
    app = make_app(tmp_path)
    app.state.manual_run_counter = ManualRunCounter(tmp_path / "counts.json")
    reg = app.state.task_registry
    tid = reg.start(who="小卡", intent="生成本周销售周报", kind="drive")
    reg._by_id[tid].started = time.time() - 300     # 5 分钟的大活,刚 done
    reg.finish(tid, result="done")
    i18n.set_locale("en")
    try:
        await maybe_suggest_schedule(app, "生成本周销售周报")
        card = await maybe_suggest_schedule(app, "生成本周销售周报")
        assert card is not None
        assert card.basis.startswith("You just finished")   # 场景前缀在最前
    finally:
        i18n.set_locale(None)


# ================================================================ i18n + 常量注册
def test_scene_i18n_keys_present_and_parity():
    from karvyloop.i18n._strings import TABLES
    for key in ("proposal.scene_schedule_due.summary", "proposal.scene_schedule_due.basis",
                "scene.big_job.basis_prefix", "scene.budget.receipt"):
        assert key in TABLES["en"] and key in TABLES["zh"], key
    assert set(TABLES["en"]) == set(TABLES["zh"])   # 全表 parity(与既有门同口径)


# ================================================================ docs/94 刀3 ④ 判据窗口可配
def test_dao3_recent_failure_window_configurable(tmp_path):
    """④:刚失败判据窗可经 app.state.scene_recent_failure_window_s 覆盖(默认 30min 不动)。"""
    app = make_app(tmp_path)
    _failed_task(app.state.task_registry, "导出季度报表", finished_ago=45 * 60)  # 45min 前失败
    now = time.time()
    kinds = [s.scene_kind for s in collect_scene_signals(app, now=now)]
    assert SCENE_TASK_FAILED not in kinds            # 默认 30min 窗:窗外不触发
    app.state.scene_recent_failure_window_s = 3600   # 覆盖成 1h → 触发
    kinds2 = [s.scene_kind for s in collect_scene_signals(app, now=now)]
    assert SCENE_TASK_FAILED in kinds2
    app.state.scene_recent_failure_window_s = "bad"  # 坏值退默认(不炸)
    kinds3 = [s.scene_kind for s in collect_scene_signals(app, now=now)]
    assert SCENE_TASK_FAILED not in kinds3


def test_dao3_schedule_due_window_configurable(tmp_path):
    """④:日程将至判据窗可经 app.state.scene_schedule_due_window_s 覆盖(默认 15min 不动)。"""
    app = make_app(tmp_path)
    st = app.state.scheduler_store
    t = st.add("0 * * * *", "拉取汇率数据", title="整点同步")   # 整点触发
    st.mark_run(t.id, "error", error="上游 500")
    lt = time.localtime()
    now = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 12, 30, 0, 0, 0, -1))  # 今天 12:30
    kinds = [s.scene_kind for s in collect_scene_signals(app, now=now)]
    assert SCENE_SCHEDULE_DUE not in kinds           # 下一场 13:00,离 30min > 默认 15min 窗
    app.state.scene_schedule_due_window_s = 3600     # 覆盖成 1h → 触发
    kinds2 = [s.scene_kind for s in collect_scene_signals(app, now=now)]
    assert SCENE_SCHEDULE_DUE in kinds2
    app.state.scene_schedule_due_window_s = 0        # 非正数退默认
    kinds3 = [s.scene_kind for s in collect_scene_signals(app, now=now)]
    assert SCENE_SCHEDULE_DUE not in kinds3


def test_gate_store_bad_file_is_empty_not_crash(tmp_path):
    p = tmp_path / "gate.json"
    p.write_text("{ not json", encoding="utf-8")
    g = SceneGateStore(p)   # 宁空勿毒:坏文件当空
    assert g.check("fp", "task_failed", budget=3, now=time.time()) == "allow"


def test_collect_signals_is_fail_soft(tmp_path):
    """采集各类各自兜异常:坏源不炸、不连坐。"""
    app = SimpleNamespace(state=SimpleNamespace())   # 什么都没接
    assert collect_scene_signals(app) == []
