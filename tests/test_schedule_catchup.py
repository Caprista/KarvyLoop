"""test_schedule_catchup — 跨天离线追赶(持久 loop 二环收尾)。

console 关机期间错过的定时任务,开机按水位(last_fired)扫一遍:
- 每个 schedule 聚合弹**一张**「要补跑一次吗」H2A 卡(骑 run_task;72 场错过=1 卡带 N,
  绝不逐场自动重放、绝不 auto-execute);
- 水位随扫描推进到 now:REJECT/无人拍=不补,下次开机不再弹同一批;
- 诚实边界:水位缺失(老数据)/时钟回拨 → 当作没错过,不编。
"""
from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.karvy.proactive import catchup_proposal_for  # noqa: E402
from karvyloop.karvy.proposal_registry import KIND_RUN_TASK, PendingProposalRegistry  # noqa: E402
from karvyloop.karvy.scheduler import CATCHUP_CAP, SchedulerStore, missed_between  # noqa: E402

import datetime as _dt


def _ts(y, mo, d, h, mi=0):
    return _dt.datetime(y, mo, d, h, mi).timestamp()


# ---- 错过检测(水位数学,纯 store 层)----


def test_missed_across_days_counted():
    # 每天 8 点;水位停在 3 天前 9:00 → 错过 23/24/25 号三个 8 点,最近一场=25 号 8:00
    now = _ts(2026, 6, 25, 9)
    st = SchedulerStore(clock=lambda: now)
    t = st.add("0 8 * * *", "汇总昨天进展")
    t.last_fired = _ts(2026, 6, 22, 9)
    got = st.catchup_scan(now=now)
    assert len(got) == 1
    assert got[0]["missed_count"] == 3
    assert got[0]["latest_missed"] == _ts(2026, 6, 25, 8)
    assert got[0]["capped"] is False
    assert t.last_fired == now          # 水位推进:同一批只报这一次


def test_hourly_72_missed_counted_once():
    # 关机三天的 hourly:72 场错过 → 一条聚合记录带 N=72(不是 72 条)
    now = _ts(2026, 6, 25, 12, 30)
    st = SchedulerStore(clock=lambda: now)
    t = st.add("0 * * * *", "整点巡检")
    t.last_fired = now - 72 * 3600
    got = st.catchup_scan(now=now)
    assert len(got) == 1 and got[0]["missed_count"] == 72
    assert got[0]["latest_missed"] == _ts(2026, 6, 25, 12)


def test_missing_watermark_treated_as_no_miss():
    # 老数据没水位(last_fired=0)→ 不编错过,只把水位补到 now(加性兼容)
    now = _ts(2026, 6, 25, 9)
    st = SchedulerStore(clock=lambda: now)
    t = st.add("0 8 * * *", "x")
    t.last_fired = 0.0
    assert st.catchup_scan(now=now) == []
    assert t.last_fired == now


def test_clock_rollback_treated_as_no_miss():
    # 时钟回拨(水位在未来)→ 不编、不动水位(墙钟追上来自愈)
    now = _ts(2026, 6, 25, 9)
    future = now + 3600
    st = SchedulerStore(clock=lambda: now)
    t = st.add("0 8 * * *", "x")
    t.last_fired = future
    assert st.catchup_scan(now=now) == []
    assert t.last_fired == future


def test_disabled_schedule_not_reported_but_watermark_advances():
    # 停用是你主动停的:停用期不算"错过",重新启用也不翻旧账(水位照推)
    now = _ts(2026, 6, 25, 9)
    st = SchedulerStore(clock=lambda: now)
    t = st.add("0 8 * * *", "x")
    t.last_fired = _ts(2026, 6, 22, 9)
    st.set_enabled(t.id, False)
    assert st.catchup_scan(now=now) == []
    assert t.last_fired == now


def test_missed_between_cap_and_edges():
    # 每分钟 cron + 离线 10 天 → 顶在 CATCHUP_CAP,不把开机扫描拖死;最近一场仍给准
    now = _ts(2026, 6, 25, 9)
    n, latest = missed_between("* * * * *", now - 10 * 86400, now)
    assert n == CATCHUP_CAP and latest is not None and latest <= now
    # 诚实边界:非法 cron / 无水位 / 回拨 → (0, None)
    assert missed_between("bad cron", now - 60, now) == (0, None)
    assert missed_between("* * * * *", 0.0, now) == (0, None)
    assert missed_between("* * * * *", now + 60, now) == (0, None)


def test_mark_run_advances_watermark():
    now = _ts(2026, 6, 25, 8, 1)
    st = SchedulerStore(clock=lambda: now)
    t = st.add("0 8 * * *", "x")
    t.last_fired = _ts(2026, 6, 25, 7)
    st.mark_run(t.id, "ok", ts=now)      # 正常到点跑过 = 这场处置过
    assert t.last_fired == now


def test_watermark_persists_across_reload(tmp_path):
    p = tmp_path / "schedules.json"
    st = SchedulerStore(p, clock=lambda: _ts(2026, 6, 25, 9))
    t = st.add("0 8 * * *", "x")
    st2 = SchedulerStore(p)
    assert st2.get(t.id).last_fired == _ts(2026, 6, 25, 9)


# ---- 卡形状(骑 run_task,H2A)----


def test_catchup_card_rides_run_task_with_stable_id():
    now = _ts(2026, 6, 25, 9)
    st = SchedulerStore(clock=lambda: now)
    t = st.add("0 8 * * *", "汇总昨天进展", title="每日进展")
    p = catchup_proposal_for(t, 3, _ts(2026, 6, 25, 8), now=now)
    assert p is not None
    assert p.kind == KIND_RUN_TASK                       # 骑既有 run_task handler,不造新 kind
    assert p.proposal_id == f"schedule_catchup-{t.id}"   # 按 schedule 幂等(防重弹)
    assert p.options == ("ACCEPT", "DEFER", "REJECT")    # H2A:问,不做
    assert p.payload["intent"] == "汇总昨天进展"
    assert p.payload["schedule_id"] == t.id and p.payload["missed_count"] == 3
    assert p.payload["source"] == "schedule_catchup" and p.payload["domain_id"] == "l0"
    assert "每日进展" in p.summary and "3" in p.summary
    assert p.basis                                       # 决策依据非空(为什么弹这张卡)
    # 空 intent / 没错过 → 不出卡
    t.intent = ""
    assert catchup_proposal_for(t, 3, now, now=now) is None
    t.intent = "x"
    assert catchup_proposal_for(t, 0, None, now=now) is None


def test_catchup_card_localizes_en_zh():
    from karvyloop import i18n
    now = _ts(2026, 6, 25, 9)
    st = SchedulerStore(clock=lambda: now)
    t = st.add("0 * * * *", "整点巡检", title="巡检")
    try:
        i18n.set_locale("en")
        p_en = catchup_proposal_for(t, 72, _ts(2026, 6, 25, 8), now=now)
        assert "While you were away" in p_en.summary and "72" in p_en.summary
        i18n.set_locale("zh")
        p_zh = catchup_proposal_for(t, 72, _ts(2026, 6, 25, 8), now=now)
        assert "你不在的时候" in p_zh.summary and "72" in p_zh.summary
    finally:
        i18n.set_locale(None)


# ---- 升卡链路(聚合一张 / 防重弹 / REJECT 不重弹 / ACCEPT 真补跑一次)----


class _FakeState:
    pass


class _FakeApp:
    def __init__(self, tmp_path, store) -> None:
        self.state = _FakeState()
        self.state.scheduler_store = store
        self.state.proposal_registry = PendingProposalRegistry()
        self.state.ws_clients = set()
        self.state.silence_grants_path = tmp_path / "grants.json"   # 不碰真实 home
        self.state.taste_predictions = None
        self.state.decision_log = None
        self.state.main_loop = None
        self.state.runtime_kwargs = {}
        self.state.domain_registry = None


async def test_72_missed_raise_one_card_no_auto_execute(tmp_path):
    # 关机三天的 hourly → **一张**卡带 N=72;没人拍板前绝不执行(卡只在待决表里躺着)
    st = SchedulerStore()
    t = st.add("0 * * * *", "整点巡检", title="巡检")
    t.last_fired = time.time() - 72 * 3600 - 1800   # 72.5h 前(72 或 73 场按墙钟对齐)
    app = _FakeApp(tmp_path, st)

    from karvyloop.console.routes_schedules import raise_schedule_catchup_cards
    raised = await raise_schedule_catchup_cards(app)
    assert raised == 1
    pending = app.state.proposal_registry.pending()
    assert len(pending) == 1
    card = pending[0]
    assert card.kind == KIND_RUN_TASK and card.payload["schedule_id"] == t.id
    assert card.payload["missed_count"] in (72, 73)
    assert str(card.payload["missed_count"]) in card.summary
    # 水位已推进:马上再扫一遍 = 零错过(同一批绝不二次弹)
    assert raised == 1 and await raise_schedule_catchup_cards(app) == 0


async def test_pending_card_not_reraised(tmp_path):
    # 卡还挂着(没人拍)时,哪怕又攒出新错过,同 schedule 也不再弹第二张(幂等收敛)
    st = SchedulerStore()
    t = st.add("0 8 * * *", "汇总", title="汇总")
    t.last_fired = time.time() - 3 * 86400
    app = _FakeApp(tmp_path, st)
    from karvyloop.console.routes_schedules import raise_schedule_catchup_cards
    assert await raise_schedule_catchup_cards(app) == 1
    t.last_fired = time.time() - 2 * 86400          # 模拟下次开机又有新错过
    assert await raise_schedule_catchup_cards(app) == 0
    assert len(app.state.proposal_registry.pending()) == 1


async def test_reject_no_rerun_and_no_reraise(tmp_path):
    # REJECT = 不补;水位在扫描时已推进 → 卡关掉后不再弹同一批
    st = SchedulerStore()
    t = st.add("0 8 * * *", "汇总", title="汇总")
    t.last_fired = time.time() - 3 * 86400
    app = _FakeApp(tmp_path, st)
    from karvyloop.console.routes_schedules import raise_schedule_catchup_cards
    assert await raise_schedule_catchup_cards(app) == 1
    pid = app.state.proposal_registry.pending()[0].proposal_id
    got = app.state.proposal_registry.decide(pid, "REJECT")
    assert got is not None and got.detail == "rejected"
    assert app.state.proposal_registry.pending() == []
    assert st.catchup_scan() == []                   # 水位已到 now:没有新错过
    assert await raise_schedule_catchup_cards(app) == 0
    assert st.get(t.id).last_run == 0.0              # 真的没补跑


async def test_accept_reruns_exactly_once_and_lands_on_board(tmp_path, monkeypatch):
    # ACCEPT → 真补跑**一次**(不是把 3 场都重放),结果落回 schedule 看板(last_status)
    import karvyloop.runtime.main_loop as ml_mod
    from karvyloop.console import proposal_handlers as ph
    from karvyloop.console.tasks import TaskRegistry

    monkeypatch.setattr(ml_mod, "forge_slow_brain_factory",
                        lambda **k: (lambda intent, **kw: ("ok", None)))

    class _Res:
        error = ""
        text = "补跑成功:进展已汇总"

    calls = []

    class _ML:
        def drive(self, intent, slow_brain=None, **k):
            calls.append(intent)
            return _Res()

    st = SchedulerStore()
    t = st.add("0 8 * * *", "汇总昨天进展", title="每日进展")
    t.last_fired = time.time() - 3 * 86400
    app = _FakeApp(tmp_path, st)
    app.state.main_loop = _ML()
    # 验收能力显式声明"无"(gateway=None)→ 诚实退回单跑分支(与 test_proactive 同桩)
    app.state.runtime_kwargs = {"token": None, "sandbox": None, "gateway": None,
                                "workspace_root": "/w", "model_ref": ""}
    app.state.task_registry = TaskRegistry()

    from karvyloop.console.routes_schedules import raise_schedule_catchup_cards
    assert await raise_schedule_catchup_cards(app) == 1
    assert calls == []                               # 出卡 ≠ 执行(H2A)
    pid = app.state.proposal_registry.pending()[0].proposal_id
    handlers = ph.build_proposal_handlers(app)
    got = app.state.proposal_registry.decide(pid, "ACCEPT", handlers=handlers)
    assert got is not None and got.ok is True
    assert calls == ["汇总昨天进展"]                  # 3 场错过 → 只跑这一次
    # 补跑落回 schedule 看板 + 水位不倒退;下一次开机不再翻这批旧账
    one = st.get(t.id)
    assert one.last_status == "ok" and one.last_run > 0
    assert one.last_fired >= one.last_run
    assert st.catchup_scan() == []
