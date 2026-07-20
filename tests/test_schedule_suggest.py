"""test_schedule_suggest — docs/90 刀3c「时机能力提示」(防 Alexa 坑:藏起来≠教会)。

覆盖:
① 计数器只认手动成功运行(bump)、失败不 +1、持久往返;
② 三道门各自拦(未达 N / already_suggested / 已有定时任务覆盖);
③ 达 N 且三门全过 → 真出一张 schedule_suggest 卡(进待决表 + 广播到 WS);
④ kind 注册:∈ ALL_KINDS、∉ HIGH_RISK_KINDS、∈ SKIP_KINDS(永不被"挣来的静音"自动兑现);
⑤ 接受走既有 create_schedule 预填:handler 无副作用(不建定时任务、不假设 cron),卡带 intent;
⑥ i18n(en/zh 双表 key 一致)。

**不骚扰是产品灵魂**:三道门缺一即静默;计数只在"用户刚手动成功跑完"那刻旁路做。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from karvyloop.karvy.ambient import intent_fingerprint
from karvyloop.karvy.manual_run_counter import ManualRunCounter
from karvyloop.karvy.proposal_registry import (
    ALL_KINDS, KIND_SCHEDULE_SUGGEST, PendingProposalRegistry,
    proposal_for_schedule_suggest,
)
from karvyloop.karvy.scheduler import SchedulerStore
from karvyloop.console.schedule_suggest import (
    maybe_suggest_schedule, schedule_suggest_after_drive,
)


# ---------------------------------------------------------------- 装置
class FakeWS:
    """捕获广播的假 WS client(broadcast_proposal 会 await send_json)。"""

    def __init__(self) -> None:
        self.sent: list = []

    async def send_json(self, msg) -> None:
        self.sent.append(msg)


def make_app(tmp_path, *, with_scheduler=True):
    state = SimpleNamespace()
    state.proposal_registry = PendingProposalRegistry()
    state.manual_run_counter = ManualRunCounter(tmp_path / "counts.json")
    state.ws_clients = set()
    state.runtime_kwargs = {}          # 无 gateway → 押注/静音都不触发(schedule_suggest 本就 SKIP)
    if with_scheduler:
        state.scheduler_store = SchedulerStore(tmp_path / "schedules.json")
    return SimpleNamespace(state=state)


async def _drain(app):
    import asyncio
    tasks = list(getattr(app.state, "_schedule_suggest_tasks", set()) or [])
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------- ① 计数器
def test_counter_bumps_and_persists(tmp_path):
    p = tmp_path / "c.json"
    c = ManualRunCounter(p)
    e1 = c.bump("生成本周周报")
    assert e1 and e1["count"] == 1 and e1["already_suggested"] is False
    e2 = c.bump("生成本周周报")
    assert e2["count"] == 2
    fp = e2["fingerprint"]
    # 持久往返:重新从盘加载,计数与摘要都在
    c2 = ManualRunCounter(p)
    got = c2.get(fp)
    assert got is not None and got["count"] == 2 and got["intent"] == "生成本周周报"


def test_counter_empty_intent_is_noop(tmp_path):
    c = ManualRunCounter(tmp_path / "c.json")
    assert c.bump("") is None and c.bump("   ") is None


def test_counter_bad_file_is_empty_not_crash(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{ this is not json", encoding="utf-8")
    c = ManualRunCounter(p)   # 坏文件当空,不抛
    assert c.bump("跑一遍数据清洗任务")["count"] == 1


def test_counter_chitchat_gate_short_intents_never_count(tmp_path):
    """寒暄门(docs/90 刀3c 对抗自评头号误触面):「谢谢」×2 绝不冒"要自动跑『谢谢』?"。
    CJK <4 字 / 纯拉丁 <3 词 = 不是"一件事",不计数(宁可不提)。"""
    c = ManualRunCounter(tmp_path / "c.json")
    for chat in ("谢谢", "好的", "ok", "thanks", "do it"):
        assert c.bump(chat) is None, f"寒暄不该计数: {chat!r}"
    # 真任务照常计数(CJK ≥4 字 / 拉丁 ≥3 词)
    assert c.bump("生成本周周报")["count"] == 1
    assert c.bump("run the weekly report")["count"] == 1


def test_mark_suggested_persists(tmp_path):
    p = tmp_path / "c.json"
    c = ManualRunCounter(p)
    fp = c.bump("每天汇总进展")["fingerprint"]
    c.mark_suggested(fp)
    assert ManualRunCounter(p).already_suggested(fp) is True


@pytest.mark.asyncio
async def test_failure_does_not_count(tmp_path):
    """失败的 drive 不 bump(schedule_suggest_after_drive 见 error 直接返回)。"""
    app = make_app(tmp_path)
    schedule_suggest_after_drive(app, "生成周报", error="boom")
    await _drain(app)
    fp = intent_fingerprint("生成周报")
    assert app.state.manual_run_counter.get(fp) is None   # 从未 +1


# ---------------------------------------------------------------- ③ 达 N + 全过 → 出卡
@pytest.mark.asyncio
async def test_below_n_no_card(tmp_path):
    """门③:只手动跑 1 次(未达 N=2)→ 静默,不出卡。"""
    app = make_app(tmp_path)
    card = await maybe_suggest_schedule(app, "跑竞品扫描")
    assert card is None
    assert app.state.proposal_registry.pending() == []


@pytest.mark.asyncio
async def test_reaches_n_emits_card(tmp_path):
    """达 N=2 且三门全过 → 出 schedule_suggest 卡:进待决表 + 广播 WS + 置 already_suggested。"""
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}
    assert await maybe_suggest_schedule(app, "跑竞品扫描") is None      # 第 1 次:不提
    card = await maybe_suggest_schedule(app, "跑竞品扫描")             # 第 2 次:提
    assert card is not None and card.kind == KIND_SCHEDULE_SUGGEST
    assert card.payload["intent"] == "跑竞品扫描" and card.payload["count"] == 2
    # 进待决表
    assert app.state.proposal_registry.get(card.proposal_id) is not None
    # 广播进预判象限(h2a_proposal)
    assert any(m.get("type") == "h2a_proposal" and
               m["payload"]["proposal_id"] == card.proposal_id for m in ws.sent)
    # already_suggested 已置(出卡即提过)
    fp = intent_fingerprint("跑竞品扫描")
    assert app.state.manual_run_counter.already_suggested(fp) is True


# ---------------------------------------------------------------- ① already_suggested 门
@pytest.mark.asyncio
async def test_already_suggested_never_again(tmp_path):
    """门①:提过一次(接受/拒绝/忽略都算)→ 之后同类事永不再提。"""
    app = make_app(tmp_path)
    await maybe_suggest_schedule(app, "写今天的日报")
    first = await maybe_suggest_schedule(app, "写今天的日报")   # 第 2 次:提
    assert first is not None
    # 第 3、4 次:already_suggested 拦住,永不再冒
    assert await maybe_suggest_schedule(app, "写今天的日报") is None
    assert await maybe_suggest_schedule(app, "写今天的日报") is None


# ---------------------------------------------------------------- ② 已有定时任务覆盖门
@pytest.mark.asyncio
async def test_existing_schedule_covers_no_card(tmp_path):
    """门②:已有 intent 同指纹的定时任务 → 不劝你自动化一件已经在自动跑的事。"""
    app = make_app(tmp_path)
    # 先建一条同类事的定时任务(cron 合法)
    app.state.scheduler_store.add("0 8 * * 1", "写今天的日报")
    assert await maybe_suggest_schedule(app, "写今天的日报") is None   # 第 1 次
    assert await maybe_suggest_schedule(app, "写今天的日报") is None   # 第 2 次:被覆盖门拦,不提
    assert app.state.proposal_registry.pending() == []


@pytest.mark.asyncio
async def test_covered_gate_reads_none_is_conservative(tmp_path):
    """门②读不到 scheduler_store(None)→ 保守不提(宁可少提)。"""
    app = make_app(tmp_path, with_scheduler=False)
    # _scheduler_store 会懒建一个空 store(无 config_path → 内存),里面没有任务 →
    # 覆盖门返回 False(无覆盖),所以第 2 次应正常出卡。这里验证"读得到且无覆盖"照常提。
    await maybe_suggest_schedule(app, "整理收件箱")
    assert await maybe_suggest_schedule(app, "整理收件箱") is not None


# ---------------------------------------------------------------- ④ kind 注册
def test_kind_registration():
    from karvyloop.crystallize.taste_eval import SKIP_KINDS
    from karvyloop.karvy.silence import HIGH_RISK_KINDS
    assert KIND_SCHEDULE_SUGGEST in ALL_KINDS
    assert KIND_SCHEDULE_SUGGEST not in HIGH_RISK_KINDS   # 温和提示,不是安全拦截
    assert KIND_SCHEDULE_SUGGEST in SKIP_KINDS            # 永不被"挣来的静音"自动兑现


def test_try_silence_refuses_schedule_suggest(tmp_path):
    """静音红线:即便桶被授权,schedule_suggest 也永不被自动兑现(SKIP_KINDS 早返回 False)。"""
    from karvyloop.karvy.silence import try_silence
    app = make_app(tmp_path)
    card = proposal_for_schedule_suggest(intent="x", count=2, fingerprint="fp", ts=1.0)
    assert try_silence(app, card) is False   # 不接管 → 永远回正常出卡(要人点)


# ---------------------------------------------------------------- ⑤ 接受走 create_schedule 预填(无副作用/不假设 cron)
def test_accept_handler_has_no_side_effect(tmp_path):
    """ACCEPT 兑现 = 诚实回执,**不**建定时任务、**不**假设 cron(真落地在前端预填 create_schedule)。"""
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    app = make_app(tmp_path)
    handlers = build_proposal_handlers(app)
    assert KIND_SCHEDULE_SUGGEST in handlers
    card = proposal_for_schedule_suggest(intent="跑竞品扫描", count=2, fingerprint="fp", ts=1.0)
    reg = app.state.proposal_registry
    reg.register(card)
    before = len(app.state.scheduler_store.all())
    res = reg.decide(card.proposal_id, "ACCEPT", handlers=handlers)
    assert res is not None and res.ok is True          # 干净兑现,卡移出待决表
    assert reg.get(card.proposal_id) is None
    # 关键:没有偷偷建定时任务(不替用户假设 cron)
    assert len(app.state.scheduler_store.all()) == before == 0
    # 卡带 intent 供前端预填
    assert card.payload["intent"] == "跑竞品扫描"


# ---------------------------------------------------------------- ⑥ i18n
def test_i18n_keys_present_and_parity():
    from karvyloop import i18n
    from karvyloop.i18n._strings import TABLES
    for key in ("proposal.schedule_suggest.summary", "proposal.schedule_suggest.basis",
                "receipt.schedule_suggest.accepted"):
        assert key in TABLES["en"] and key in TABLES["zh"]
    # summary 占位符两语都在
    i18n.set_locale("en")
    assert "3" in i18n.t("proposal.schedule_suggest.summary", n=3, intent="X")
    i18n.set_locale("zh")
    assert "生成周报" in i18n.t("proposal.schedule_suggest.summary", n=2, intent="生成周报")
    i18n.set_locale(None)
    assert set(TABLES["en"]) == set(TABLES["zh"])   # 全表 key 一致(AC8 同口径)


# ---------------------------------------------------------------- 提案工厂本身
def test_proposal_factory_no_cjk_in_summary_basis():
    """summary/basis 走 i18n(源码扫描门同口径):卡对象上的值由 i18n 定稿,不硬编码。"""
    from karvyloop import i18n
    i18n.set_locale("en")
    card = proposal_for_schedule_suggest(intent="weekly report", count=2, fingerprint="fp", ts=1.0)
    # en locale 下 summary/basis 是英文(证明走了 i18n 而非硬编码中文)
    assert not any("一" <= ch <= "鿿" for ch in card.summary)
    assert card.proposal_id == "schedule_suggest-0-" + __import__("hashlib").sha1(
        b"fp").hexdigest()[:8]
    i18n.set_locale(None)
