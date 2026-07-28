"""test_scene_preexec — 「猜你想干 v2」刀2:预测定向预执行(docs/94 刀2)。

覆盖(任务书八组):
① relevance 严格 JSON / 坏输出回落(parse_relevance + judge_relevance + 消费面回落);
② 并发=1 / 有活跃任务不起(空闲才干,回落普通卡);
③ 预算上限真拦(每日 token 日账拦 launch;成功才扣刀1 日预算,失败/放弃不扣;
   完成时预算被抢 → 弃产物不出卡);
④ staging 隔离(REJECT/过期零残留 + 知识库/记忆零变化 + ACCEPT 前工作区零写入);
⑤ 错时撤卡(过期 scene_ready 卡 registry 撤卡 + 弃产物,不打扰用户);
⑥ ACCEPT 两类落地(deliver=发进会话+文件移入工作区 / rerun=沿 run_task handler 语义);
⑦ scene_ready 注册(ALL_KINDS / SKIP_KINDS / 不进 HIGH_RISK / 静音永不接管 / handler 已接);
⑧ 刀1 回归(消费面不适用/坏 → scene_tick 照旧出普通卡;既有 test_scene_gate.py 原样绿)。

--no-llm 下整链单测:真 LLM 的 drive/relevance 用桩(_judge / _execute_sync 注入点),
判定链/预算/staging 生命周期全部真跑。
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import karvyloop.console.scene_preexec as sp
from karvyloop.console.tasks import TaskRegistry
from karvyloop.karvy.atoms import Proposal
from karvyloop.karvy.proposal_registry import (
    ALL_KINDS, KIND_RUN_TASK, KIND_SCENE_READY, PendingProposalRegistry,
    proposal_for_scene_ready,
)
from karvyloop.karvy.scene_gate import scene_gate, scene_tick
from karvyloop.karvy.scene_relevance import judge_relevance, parse_relevance
from karvyloop.karvy.scene_signals import (
    SCENE_SCHEDULE_DUE, SCENE_TASK_FAILED, SceneSignal, task_failed_fingerprint,
)


# ---------------------------------------------------------------- 装置
class FakeWS:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_json(self, msg) -> None:
        self.sent.append(msg)


class TextDelta:
    """judge_relevance 按 type(ev).__name__ == "TextDelta" 认增量 —— 同名桩即可。"""

    def __init__(self, text: str) -> None:
        self.text = text


class FakeGateway:
    def __init__(self, replies=None) -> None:
        self.replies = list(replies or [])
        self.calls = 0

    def resolve_model(self, scope) -> str:
        return "fake-model"

    async def complete(self, messages, tools, model_ref, system=None, **kw):
        self.calls += 1
        text = self.replies.pop(0) if self.replies else "{}"
        yield TextDelta(text)


class MemStub:
    """记忆桩:锁"预执行/REJECT/过期全程零记忆写入"(污染教训)。"""

    def __init__(self) -> None:
        self.writes: list = []
        self.index = SimpleNamespace(all=lambda scope: [])

    def write(self, *a, **kw):
        self.writes.append((a, kw))


def make_app(tmp_path, *, with_llm=True):
    from karvyloop.llm.spend_budget import register_spend_budget
    register_spend_budget(None)   # 全局花费刹车归零(别的测试可能注册过)
    state = SimpleNamespace()
    state.proposal_registry = PendingProposalRegistry()
    state.ws_clients = set()
    state.task_registry = TaskRegistry()
    state.config_path = str(tmp_path / "config.yaml")
    state.scene_staging_root = tmp_path / "staging"
    state.memory = MemStub()
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    if with_llm:
        state.main_loop = SimpleNamespace(drive=lambda *a, **kw: None)
        state.runtime_kwargs = {
            "token": object(), "sandbox": object(),
            "gateway": FakeGateway(), "workspace_root": str(ws_root), "model_ref": "m",
        }
    else:
        state.runtime_kwargs = {}
    return SimpleNamespace(state=state)


def _sig(scene_kind=SCENE_TASK_FAILED, *, fp=None, intent="导出季度报表",
         schedule_id="") -> SceneSignal:
    payload = {"intent": intent, "domain_id": "l0", "role": "", "source": "test"}
    if schedule_id:
        payload["schedule_id"] = schedule_id
    p = Proposal(summary=f"重试「{intent}」?", options=("ACCEPT", "REJECT"), strength=0.7,
                 evidence_refs=(), habit_id=0, model_ref="", ts=time.time(),
                 kind=KIND_RUN_TASK, payload=payload,
                 basis=f"「{intent}」刚失败:网络中断", context_ref={"kind": "task", "id": "t1"})
    return SceneSignal(scene_kind=scene_kind,
                       fingerprint=fp or task_failed_fingerprint("t1"), proposal=p)


def _worth(intent="先诊断失败原因"):
    return {"worth": True, "action_intent": intent, "reason": "test"}


async def _judge_worth(*a, **kw):
    return _worth()


async def _drain_inflight(app) -> None:
    tasks = list(getattr(app.state, "_scene_preexec_inflight", {}).values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _cards(ws: FakeWS, kind=None) -> list:
    out = [m for m in ws.sent if m.get("type") == "h2a_proposal"]
    if kind is not None:
        out = [m for m in out if m["payload"].get("kind") == kind]
    return out


# ================================================================ ① relevance 严格 JSON
def test_parse_relevance_strict():
    good = json.dumps({"worth": True, "action_intent": "先试跑一次", "reason": "快到点了"})
    d = parse_relevance(good)
    assert d == {"worth": True, "action_intent": "先试跑一次", "reason": "快到点了"}
    fenced = "```json\n" + good + "\n```"
    assert parse_relevance(fenced)["worth"] is True
    # 坏输出宁空勿毒:非 bool worth / worth 却无定向 / prose / 坏 JSON / 非对象
    assert parse_relevance('{"worth": "true", "action_intent": "x", "reason": ""}') is None
    assert parse_relevance('{"worth": true, "action_intent": "", "reason": "r"}') is None
    assert parse_relevance("我觉得值得干,先试跑一次吧") is None
    assert parse_relevance('{"worth": tru') is None
    assert parse_relevance('[1,2,3]') is None
    assert parse_relevance("") is None
    # worth=false 合法(action 可空)
    assert parse_relevance('{"worth": false, "action_intent": "", "reason": "没必要"}') == \
        {"worth": False, "action_intent": "", "reason": "没必要"}


@pytest.mark.asyncio
async def test_judge_relevance_with_fake_gateway():
    gw = FakeGateway([json.dumps({"worth": True, "action_intent": "先跑一遍", "reason": "r"})])
    d = await judge_relevance(gw, "m", scene_desc="任务快到点且上次失败")
    assert d is not None and d["worth"] is True and d["action_intent"] == "先跑一遍"
    # 垃圾输出 → None(调用方按不值得回落)
    gw2 = FakeGateway(["总之我觉得可以"])
    assert await judge_relevance(gw2, "m", scene_desc="x") is None
    # gateway 炸 → None
    class BoomGw:
        def resolve_model(self, scope):
            raise RuntimeError("boom")
    assert await judge_relevance(BoomGw(), "m", scene_desc="x") is None
    # 空场景/无 gateway → None(零调用)
    assert await judge_relevance(gw, "m", scene_desc="") is None
    assert await judge_relevance(None, "m", scene_desc="x") is None


@pytest.mark.asyncio
async def test_relevance_not_worth_falls_back_to_plain_card(tmp_path, monkeypatch):
    """不值得 → 消费面 False → 回落刀1 普通卡;且同指纹 relevance 只判一次(缓存)。"""
    app = make_app(tmp_path)
    calls = {"n": 0}

    async def fake_judge(app_, sig_):
        calls["n"] += 1
        return {"worth": False, "action_intent": "", "reason": "没必要"}

    monkeypatch.setattr(sp, "_judge", fake_judge)
    sig = _sig()
    assert await sp.maybe_consume_signal(app, sig) is False
    assert await sp.maybe_consume_signal(app, sig) is False
    assert calls["n"] == 1   # 第二次走缓存,不再烧 LLM
    # 消费面 False → scene_tick 原路出普通卡(整链):造条真失败任务
    ws = FakeWS(); app.state.ws_clients = {ws}
    reg = app.state.task_registry
    tid = reg.start(who="小卡", domain_id="l0", intent="导出季度报表")
    reg.finish(tid, error="网络中断")
    assert await scene_tick(app) == 1
    assert len(_cards(ws, KIND_RUN_TASK)) == 1   # 刀1 普通卡照出


# ================================================================ ② 并发=1 / 空闲才干
@pytest.mark.asyncio
async def test_busy_user_task_blocks_preexec(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    called = {"n": 0}

    async def fake_judge(app_, sig_):
        called["n"] += 1
        return _worth()

    monkeypatch.setattr(sp, "_judge", fake_judge)
    app.state.task_registry.start(who="小卡", intent="用户正在跑的大活")   # running
    assert await sp.maybe_consume_signal(app, _sig()) is False
    assert called["n"] == 0   # 忙 → 连 relevance 都不烧(空闲才干)


@pytest.mark.asyncio
async def test_concurrency_is_one(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    monkeypatch.setattr(sp, "_judge", _judge_worth)
    app.state._scene_preexec_inflight = {"task_failed:other": object()}
    # 别的预执行在跑 → 新信号回落普通卡(不排队)
    assert await sp.maybe_consume_signal(app, _sig(fp="task_failed:t9")) is False
    # 同指纹在跑 → True(压住普通卡,等它完成出已备好卡)
    assert await sp.maybe_consume_signal(app, _sig(fp="task_failed:other")) is True


@pytest.mark.asyncio
async def test_no_llm_and_wrong_scene_fall_back(tmp_path):
    app = make_app(tmp_path, with_llm=False)
    app.state.main_loop = None
    assert await sp.maybe_consume_signal(app, _sig()) is False       # 无 LLM → 刀1 原样
    app2 = make_app(tmp_path)
    assert await sp.maybe_consume_signal(app2, _sig("manual_repeat")) is False  # 触发面收窄


# ================================================================ ③ 预算上限真拦
@pytest.mark.asyncio
async def test_daily_token_cap_blocks_launch(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    called = {"n": 0}

    async def fake_judge(app_, sig_):
        called["n"] += 1
        return _worth()

    monkeypatch.setattr(sp, "_judge", fake_judge)
    app.state.scene_preexec_max_tokens = 1000
    sp.preexec_store(app).add_tokens(1000)
    assert await sp.maybe_consume_signal(app, _sig()) is False
    assert called["n"] == 0   # 日预算满 → 不烧 relevance,直接回落普通卡


@pytest.mark.asyncio
async def test_failure_is_silent_no_card_no_charge_attempt_once(tmp_path, monkeypatch):
    """预执行失败:静默弃 —— 不弹卡、不扣刀1 预算、staging 零残留;同指纹只试一次。"""
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}
    monkeypatch.setattr(sp, "_judge", _judge_worth)

    def boom(app_, goal, work_dir, tid):
        raise RuntimeError("sandbox exploded")

    monkeypatch.setattr(sp, "_execute_sync", boom)
    sig = _sig()
    assert await sp.maybe_consume_signal(app, sig) is True
    await _drain_inflight(app)
    assert ws.sent == []                                     # 不弹卡
    g = scene_gate(app)
    assert g.spent_today() == 0                              # 不扣预算
    assert not g.seen(sig.fingerprint)                       # 指纹没烧(普通卡下轮还能提)
    root = sp.staging_root(app)
    assert not root.exists() or list(root.iterdir()) == []   # staging 零残留
    # 任务卡 fail-loud:error 落板
    errs = [t for t in app.state.task_registry.list()
            if t["kind"] == "scene_preexec" and t["status"] == "error"]
    assert len(errs) == 1
    # 同指纹只试一次 → 之后回落普通卡
    assert await sp.maybe_consume_signal(app, sig) is False
    assert app.state.memory.writes == []                     # 零记忆写入


@pytest.mark.asyncio
async def test_success_but_gate_budget_exhausted_discards_product(tmp_path, monkeypatch):
    """完成时刀1 预算已被抢光 → 已备好也不出卡,弃产物(预算铁律:不出卡不落地)。"""
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}
    monkeypatch.setattr(sp, "_execute_sync", lambda *a: {
        "ok": True, "text": "诊断:上游 500,建议加重试", "error": "", "terminal": "completed",
        "infra_dead": False, "unfinished": False,
        "verdict_passed": True, "verdict_inconclusive": False})
    g = scene_gate(app)
    for i in range(3):
        g.charge(f"fp{i}", "task_failed", budget=3)   # 打满今日预算
    await sp._run_preexec(app, _sig(), _worth(), now=time.time())
    assert _cards(ws) == []                                          # 门拒 → 无卡
    assert list(sp.staging_root(app).iterdir()) == []                # 产物已弃
    assert g.spent_today() == 3                                      # 没有多扣


# ================================================================ 成功主链:已备好卡 + 记账
@pytest.mark.asyncio
async def test_preexec_success_emits_scene_ready_and_charges(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}
    seen_goal = {}

    def fake_exec(app_, goal, work_dir, tid):
        seen_goal["goal"] = goal
        seen_goal["work_dir"] = work_dir
        return {"ok": True, "text": "诊断:磁盘满了;修法草稿:清理 tmp 后重跑",
                "error": "", "terminal": "completed", "infra_dead": False,
                "unfinished": False, "verdict_passed": True, "verdict_inconclusive": False}

    monkeypatch.setattr(sp, "_judge", _judge_worth)
    monkeypatch.setattr(sp, "_execute_sync", fake_exec)
    sig = _sig()
    assert await sp.maybe_consume_signal(app, sig) is True
    await _drain_inflight(app)

    cards = _cards(ws, KIND_SCENE_READY)
    assert len(cards) == 1
    payload = cards[0]["payload"]["payload"]
    assert payload["scene_kind"] == SCENE_TASK_FAILED       # 负反馈回钩标
    assert payload["action"] == "rerun"                     # task_failed → 重跑类
    assert payload["intent"] == "导出季度报表"
    assert payload["expiry_ts"] > time.time()               # 错时 expiry 已带
    sid = payload["staging_id"]
    # 产物真在 staging(隔离),工作区零写入
    prod = sp.load_product(app, sid)
    assert prod and "磁盘满了" in prod["text"]
    ws_root = tmp_path / "workspace"
    assert list(ws_root.iterdir()) == []                    # ACCEPT 前工作区零写入
    # 刀1 同一份预算:成功出卡才扣 + 指纹已记(普通卡永不再出)
    g = scene_gate(app)
    assert g.spent_today() == 1
    assert g.seen(sig.fingerprint)
    # 预执行任务卡(可 ⏹ 的那条)落板 done
    pre = [t for t in app.state.task_registry.list() if t["kind"] == "scene_preexec"]
    assert len(pre) == 1 and pre[0]["status"] == "done"
    # 卡在待决表(ACCEPT 凭 id 查回)
    assert app.state.proposal_registry.get(cards[0]["payload"]["proposal_id"]) is not None
    # 诊断目标里带了"绝不重跑"纪律 + 原意图
    assert "导出季度报表" in seen_goal["goal"] and "绝不重跑" in seen_goal["goal"]
    assert str(sp.staging_root(app)) in seen_goal["work_dir"]   # 隔离 workspace
    assert app.state.memory.writes == []                    # 零记忆写入


@pytest.mark.asyncio
async def test_schedule_due_preexec_is_deliver_test_run(tmp_path, monkeypatch):
    """schedule_due:预执行 = 按原意图试跑(deliver);试跑真失败 → 产物 = 错误诊断。"""
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}
    monkeypatch.setattr(sp, "_execute_sync", lambda *a: {
        "ok": True, "text": "汇率已拉取,共 42 条", "error": "", "terminal": "completed",
        "infra_dead": False, "unfinished": False,
        "verdict_passed": False, "verdict_inconclusive": True})
    sig = _sig(SCENE_SCHEDULE_DUE, fp="schedule_due:s1:2026-07-28",
               intent="拉取汇率数据", schedule_id="s1")
    goal, action, what = sp._build_goal(sig, _worth())
    assert goal == "拉取汇率数据" and action == "deliver"   # 试跑 = 原意图,产物投递类
    await sp._run_preexec(app, sig, _worth(), now=time.time())
    cards = _cards(ws, KIND_SCENE_READY)
    assert len(cards) == 1
    assert cards[0]["payload"]["payload"]["action"] == "deliver"
    # 试跑真失败(任务级 error)→ 产物 = 错误诊断(unfinished 的预算耗尽不算)
    app2 = make_app(tmp_path / "b")
    ws2 = FakeWS(); app2.state.ws_clients = {ws2}
    monkeypatch.setattr(sp, "_execute_sync", lambda *a: {
        "ok": False, "text": "试到一半", "error": "上游接口 500", "terminal": "completed",
        "infra_dead": False, "unfinished": False,
        "verdict_passed": False, "verdict_inconclusive": True})
    await sp._run_preexec(app2, sig, _worth(), now=time.time())
    c2 = _cards(ws2, KIND_SCENE_READY)
    assert len(c2) == 1
    prod = sp.load_product(app2, c2[0]["payload"]["payload"]["staging_id"])
    assert "上游接口 500" in prod["text"]
    # 预算耗尽没跑完(unfinished)→ 静默弃,不出卡
    app3 = make_app(tmp_path / "c")
    ws3 = FakeWS(); app3.state.ws_clients = {ws3}
    monkeypatch.setattr(sp, "_execute_sync", lambda *a: {
        "ok": False, "text": "", "error": "max_turns", "terminal": "max_turns",
        "infra_dead": False, "unfinished": True,
        "verdict_passed": False, "verdict_inconclusive": True})
    await sp._run_preexec(app3, sig, _worth(), now=time.time())
    assert _cards(ws3) == []


# ================================================================ ④ staging 隔离(REJECT/过期零残留)
def _make_ready_card_with_staging(app, *, action="deliver", expiry_ts=0.0,
                                  text="备好的调研草稿", files=()):
    staging = sp.create_staging(app)
    assert staging is not None
    for name in files:
        (staging / "work" / name).write_text("content", encoding="utf-8")
    sp.write_product(app, staging, {"text": text, "scene_kind": SCENE_TASK_FAILED,
                                    "action": action, "intent": "导出季度报表"})
    card = proposal_for_scene_ready(
        scene_kind=SCENE_TASK_FAILED, action=action, what="导出季度报表",
        gist=text[:50], staging_id=staging.name, intent="导出季度报表",
        ts=time.time(), expiry_ts=expiry_ts, task_id="t1", scene_basis="刚失败")
    app.state.proposal_registry.register(card)
    return card, staging


def test_reject_cleans_staging_zero_residue_zero_pollution(tmp_path):
    from karvyloop.console.proposal_handlers import (
        _scene_ready_handler, _scene_ready_reject_handler,
    )
    app = make_app(tmp_path)
    card, staging = _make_ready_card_with_staging(app, files=("draft.md",))
    handlers = {KIND_SCENE_READY: _scene_ready_handler(app),
                f"{KIND_SCENE_READY}:reject": _scene_ready_reject_handler(app)}
    res = app.state.proposal_registry.decide(card.proposal_id, "REJECT", handlers=handlers)
    assert res.ok and res.detail == "rejected"               # 通用驳回回执不变
    assert not staging.exists()                              # staging 零残留
    assert app.state.proposal_registry.get(card.proposal_id) is None
    assert app.state.memory.writes == []                     # 知识库/记忆零变化
    assert list((tmp_path / "workspace").iterdir()) == []    # 工作区零写入


# ================================================================ ⑤ 错时撤卡
def test_expiry_maintenance_withdraws_card_and_discards(tmp_path):
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}
    now = time.time()
    card, staging = _make_ready_card_with_staging(app, expiry_ts=now - 10)   # 已过期
    live, live_staging = _make_ready_card_with_staging(app, expiry_ts=now + 3600)
    counts = sp.scene_ready_maintenance(app, now=now)
    assert counts["expired"] == 1
    assert app.state.proposal_registry.get(card.proposal_id) is None   # 撤卡
    assert not staging.exists()                                        # 弃产物
    assert app.state.proposal_registry.get(live.proposal_id) is not None  # 没过期的不动
    assert live_staging.exists()
    assert ws.sent == []                                               # 不打扰用户


def test_staging_bounded_max5_and_ttl_prune(tmp_path):
    app = make_app(tmp_path)
    root = sp.staging_root(app)
    root.mkdir(parents=True, exist_ok=True)
    import os
    old = time.time() - sp.STAGING_TTL_S - 100
    for i in range(sp.STAGING_MAX):
        d = root / f"old{i}"
        (d / "work").mkdir(parents=True)
        os.utime(d, (old, old))
    # 建新目录:满了 → 清最老的未引用目录腾位(仍 ≤ MAX)
    d = sp.create_staging(app)
    assert d is not None
    assert len(list(root.iterdir())) <= sp.STAGING_MAX
    # 超龄未引用 → 巡检清掉;新目录(未超龄)保留
    counts = sp.scene_ready_maintenance(app)
    assert counts["pruned"] >= 1
    assert d.exists()


# ================================================================ ⑥ ACCEPT 两类落地
def test_accept_deliver_lands_chat_and_files(tmp_path):
    from karvyloop.console.proposal_handlers import _scene_ready_handler
    app = make_app(tmp_path)
    turns = []
    cur = SimpleNamespace(is_private=True)
    app.state.conversation_manager = SimpleNamespace(
        current=lambda: cur,
        record_turn=lambda u, a, **kw: turns.append((u, a)))
    card, staging = _make_ready_card_with_staging(
        app, action="deliver", text="这是调研结论:方案 A 更省", files=("report.md", "data.csv"))
    ok, detail = _scene_ready_handler(app)(card)
    assert ok
    # 文本半:发进会话「这是我提前备好的:…」
    assert len(turns) == 1
    assert "这是调研结论:方案 A 更省" in turns[0][1]
    # 文件半:移入工作区 karvy_prepared/<sid>/
    dest = tmp_path / "workspace" / "karvy_prepared" / staging.name
    assert sorted(p.name for p in dest.iterdir()) == ["data.csv", "report.md"]
    assert "karvy_prepared" in detail
    assert not staging.exists()                              # 落地后 staging 清
    # 产物已被清(过期/重复 ACCEPT)→ 诚实回执不假装
    ok2, detail2 = _scene_ready_handler(app)(card)
    assert ok2 and ("expired" in detail2 or "过期" in detail2)


def test_accept_deliver_chat_unavailable_is_honest(tmp_path):
    """会话不可用但文件已移入工作区 → 回执诚实说"文字没发出去",不谎称已发进会话。"""
    from karvyloop.console.proposal_handlers import _scene_ready_handler
    app = make_app(tmp_path)   # 无 conversation_manager
    card, staging = _make_ready_card_with_staging(app, action="deliver",
                                                  text="草稿", files=("a.md",))
    ok, detail = _scene_ready_handler(app)(card)
    assert ok
    assert "karvy_prepared" in detail
    assert "chat" in detail.lower() or "会话" in detail   # files_only 口径
    # 纯文本产物 + 会话不可用 → 失败保留 staging(48h 巡检兜底),不静默丢
    card2, staging2 = _make_ready_card_with_staging(app, action="deliver", text="纯文本")
    ok2, _ = _scene_ready_handler(app)(card2)
    assert not ok2 and staging2.exists()


def test_accept_rerun_delegates_to_run_task_handler(tmp_path):
    from karvyloop.console.proposal_handlers import _scene_ready_handler
    app = make_app(tmp_path)
    got = {}

    def fake_run_task(proposal):
        got["intent"] = (proposal.payload or {}).get("intent", "")
        return True, "已重跑"

    app.state.proposal_handlers = {KIND_RUN_TASK: fake_run_task}
    card, staging = _make_ready_card_with_staging(
        app, action="rerun", text="诊断:磁盘满;修法:清 tmp")
    ok, detail = _scene_ready_handler(app)(card)
    assert ok and detail == "已重跑"
    # 沿 run_task 语义:原意图 + 备好的诊断草稿一起注入
    assert got["intent"].startswith("导出季度报表")
    assert "磁盘满" in got["intent"]
    assert not staging.exists()                              # 兑现成功后清 staging


def test_accept_rerun_failure_keeps_staging_for_prune(tmp_path):
    from karvyloop.console.proposal_handlers import _scene_ready_handler
    app = make_app(tmp_path)
    app.state.proposal_handlers = {KIND_RUN_TASK: lambda p: (False, "重跑失败")}
    card, staging = _make_ready_card_with_staging(app, action="rerun", text="诊断")
    ok, _ = _scene_ready_handler(app)(card)
    assert not ok
    assert staging.exists()   # 失败不清(48h 巡检兜底),不静默丢产物


# ================================================================ ⑦ kind 注册 / 静音 / handler 接线
def test_scene_ready_registration_and_silence_skip(tmp_path):
    from karvyloop.crystallize.taste_eval import SKIP_KINDS
    from karvyloop.karvy.silence import HIGH_RISK_KINDS, try_silence
    from karvyloop.llm.spend_budget import AUTOMATIC_SOURCES
    assert KIND_SCENE_READY in ALL_KINDS
    assert KIND_SCENE_READY in SKIP_KINDS          # 永不被"挣来的静音"自动兑现
    assert KIND_SCENE_READY not in HIGH_RISK_KINDS  # 不是安全拦截(照 schedule_suggest 先例)
    assert "scene_preexec" in AUTOMATIC_SOURCES and "scene_relevance" in AUTOMATIC_SOURCES
    app = make_app(tmp_path)
    card, _ = _make_ready_card_with_staging(app)
    assert try_silence(app, card) is False          # 静音永不接管 scene_ready


def test_build_proposal_handlers_includes_scene_ready(tmp_path):
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    app = make_app(tmp_path)
    handlers = build_proposal_handlers(app)
    assert callable(handlers.get(KIND_SCENE_READY))
    assert callable(handlers.get(f"{KIND_SCENE_READY}:reject"))


def test_scene_ready_i18n_keys_present_and_parity():
    from karvyloop.i18n._strings import TABLES
    for key in ("proposal.scene_ready.summary", "proposal.scene_ready.basis_done",
                "proposal.scene_ready.basis_deliver", "proposal.scene_ready.basis_rerun",
                "receipt.scene_ready.gone", "receipt.scene_ready.delivered",
                "receipt.scene_ready.files_moved", "receipt.scene_ready.deliver_failed",
                "scene_ready.deliver.message", "scene_ready.rerun.diagnosis_block",
                "scene_preexec.task_intent", "scene_preexec.product_error_diag"):
        assert key in TABLES["en"] and key in TABLES["zh"], key
    assert set(TABLES["en"]) == set(TABLES["zh"])


# ================================================================ ⑧ 刀1 回归(消费面坏了不连坐)
@pytest.mark.asyncio
async def test_scene_tick_plain_cards_when_consume_face_broken(tmp_path, monkeypatch):
    """消费面炸 → scene_tick 照旧出刀1 普通卡(旁路纪律,0 回归)。"""
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}

    async def boom(*a, **kw):
        raise RuntimeError("consume face broken")

    monkeypatch.setattr(sp, "maybe_consume_signal", boom)
    reg = app.state.task_registry
    tid = reg.start(who="小卡", domain_id="l0", intent="导出季度报表")
    reg.finish(tid, error="网络中断")
    assert await scene_tick(app) == 1
    assert len(_cards(ws, KIND_RUN_TASK)) == 1


@pytest.mark.asyncio
async def test_scene_tick_no_llm_is_pure_knife1(tmp_path):
    """--no-llm:消费面自动 False,行为与刀1 完全一致(测试 app 无 main_loop)。"""
    app = make_app(tmp_path, with_llm=False)
    ws = FakeWS(); app.state.ws_clients = {ws}
    reg = app.state.task_registry
    tid = reg.start(who="小卡", domain_id="l0", intent="导出季度报表")
    reg.finish(tid, error="网络中断")
    assert await scene_tick(app) == 1
    cards = _cards(ws, KIND_RUN_TASK)
    assert len(cards) == 1
    assert cards[0]["payload"]["payload"]["scene_kind"] == SCENE_TASK_FAILED


# ================================================================ 对抗验收判决修(B1/A4/A1/C3/A3/B2)
def test_b1_staging_token_isolation_judgment_layer(tmp_path):
    """B1:沙箱可写面由 token 决定 —— staging 专属 token 的**真判定层**(不 monkeypatch 盖脸)。

    ① mounts_from_token(staging token):rw 含 staging、不含用户工作区(它降进 ro);
    ② token 门(has_grant):staging 写放行、用户工作区写**拒**;
    ③ bash 沙箱挂载即 ①(mounts_from_token 是三平台 sandbox 的唯一挂载推导,判定层测到即可)。
    """
    from karvyloop.capability import mint
    from karvyloop.capability.token import has_grant
    from karvyloop.sandbox.mounts import mounts_from_token, staging_scoped_token
    from karvyloop.schemas import Capability
    orig_ws = str(tmp_path / "real_workspace")
    staging_work = str(tmp_path / "staging" / "abc" / "work")
    token = mint(task_id="cli", grants=[
        Capability(resource=f"fs:{orig_ws}", ops=["read", "write"]),
        Capability(resource=f"fs:{orig_ws}", ops=["exec"]),
        Capability(resource="net:*", ops=["read"]),
    ])
    st = staging_scoped_token(token, staging_work)
    assert st is not None
    ro, rw = mounts_from_token(st)
    assert staging_work in rw                      # ① staging 真进可写挂载(Linux bash 起得来)
    assert orig_ws not in rw                       # ① 用户工作区不再可写(mac/Win bash 不逃逸)
    assert orig_ws in ro                           # 诊断仍可读
    # ② token 门:staging 写放行、用户工作区写拒、exec 在 staging 有、net 保留
    assert has_grant(st, f"fs:{staging_work}", "write")
    assert has_grant(st, f"fs:{staging_work}", "exec")
    assert not has_grant(st, f"fs:{orig_ws}", "write")
    assert has_grant(st, f"fs:{orig_ws}", "read")
    assert has_grant(st, "net:*", "read")
    # fail-closed:测试桩/坏 token 铸不出 → None(_execute_sync 据此不跑)
    assert staging_scoped_token(object(), staging_work) is None
    assert staging_scoped_token(token, "") is None


def test_b1_execute_sync_fail_closed_without_real_token(tmp_path):
    """B1 fail-closed:token 是桩(铸不出隔离 token)→ _execute_sync 不跑 drive,静默弃。"""
    app = make_app(tmp_path)   # runtime_kwargs.token = object() 桩
    drove = {"n": 0}
    app.state.main_loop = SimpleNamespace(
        drive=lambda *a, **kw: drove.__setitem__("n", drove["n"] + 1))
    res = sp._execute_sync(app, "诊断", str(tmp_path / "w"), "")
    assert res["ok"] is False and res["error"] == "no_staging_token"
    assert drove["n"] == 0                         # 绝不复用原 token 跑


@pytest.mark.asyncio
async def test_a4_preexec_failure_does_not_selffeed(tmp_path, monkeypatch):
    """A4 复现转绿:失败的 scene_preexec 任务绝不被当新场景信号(不繁殖、不弹内部卡)。"""
    from karvyloop.karvy.scene_signals import recent_failed_task_signals
    app = make_app(tmp_path)
    ws = FakeWS(); app.state.ws_clients = {ws}
    judge_calls = {"n": 0}

    async def counting_judge(*a, **kw):
        judge_calls["n"] += 1
        return _worth()

    monkeypatch.setattr(sp, "_judge", counting_judge)
    monkeypatch.setattr(sp, "_execute_sync",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("sandbox 崩")))
    # 第 1 轮:用户任务失败 → 消费 → 预执行崩(任务板留 scene_preexec error)
    reg = app.state.task_registry
    tid = reg.start(who="小卡", domain_id="l0", intent="导出季度报表")
    reg.finish(tid, error="网络中断")
    await scene_tick(app)
    await _drain_inflight(app)
    board = reg.list()
    machine = [t for t in board if t["kind"] == "scene_preexec" and t["status"] == "error"]
    assert len(machine) == 1
    # 机器内部失败任务不产信号(白名单):它的指纹绝不在信号列表里
    sigs = recent_failed_task_signals(reg, now=time.time())
    assert task_failed_fingerprint(machine[0]["id"]) not in {s.fingerprint for s in sigs}
    # 第 2、3 轮 tick:不再烧 relevance、不再繁殖 scene_preexec 任务、不弹机器内部卡
    n0 = judge_calls["n"]
    pre0 = sum(1 for t in reg.list() if t["kind"] == "scene_preexec")
    await scene_tick(app)
    await _drain_inflight(app)
    await scene_tick(app)
    await _drain_inflight(app)
    assert judge_calls["n"] == n0                                  # 不再烧判断
    assert sum(1 for t in reg.list() if t["kind"] == "scene_preexec") == pre0   # 不繁殖
    for m in _cards(ws):
        assert "Pre-run while idle" not in m["payload"].get("summary", "")      # 不弹内部卡
        assert "空闲预执行" not in m["payload"].get("summary", "")


def test_a4_boot_fallback_and_monitor_skip_machine_tasks(tmp_path):
    """A4 同族面:开机兜底(propose_from_tasks)不捡机器任务;pursuit 也不双弹。"""
    from karvyloop.karvy.proactive import USER_TASK_KINDS, is_user_task, propose_from_tasks
    assert "scene_preexec" not in USER_TASK_KINDS and "pursuit" not in USER_TASK_KINDS
    assert "" in USER_TASK_KINDS                    # 老记录(无 kind)不受影响
    reg = TaskRegistry()
    t1 = reg.start(who="小卡", intent="内部预执行", kind="scene_preexec")
    reg.finish(t1, error="崩")
    t2 = reg.start(who="小卡", intent="跨天追求推进", kind="pursuit")
    reg.finish(t2, error="崩")
    assert propose_from_tasks(reg) is None          # 只有机器任务失败 → 沉默
    t3 = reg.start(who="小卡", intent="用户的活")    # kind=""(用户语义)
    reg.finish(t3, error="崩")
    p = propose_from_tasks(reg)
    assert p is not None and p.payload["intent"] == "用户的活"
    assert is_user_task({"kind": "drive"}) and not is_user_task({"kind": "scene_preexec"})


@pytest.mark.asyncio
async def test_a4_task_monitor_marks_but_no_card_for_machine_tasks(tmp_path):
    """A4 同族面:停滞的机器任务照样诚实标中断(fail-loud),但**不弹**重跑卡。"""
    from karvyloop.console.task_monitor import run_task_monitor
    app = make_app(tmp_path, with_llm=False)
    ws = FakeWS(); app.state.ws_clients = {ws}
    reg = app.state.task_registry
    tid = reg.start(who="小卡", intent="漫长的内部预执行", kind="scene_preexec")
    old = time.time() - 2 * 3600
    reg._by_id[tid].started = old
    for ev in reg._by_id[tid].events:
        ev["ts"] = old
    n = await run_task_monitor(app, threshold=600)
    assert n == 0                                   # 无卡
    assert ws.sent == []
    t = reg.get(tid)
    assert t["status"] == "error"                   # 但诚实标了中断


@pytest.mark.asyncio
async def test_a1_concurrent_ticks_single_judge_single_preexec(tmp_path, monkeypatch):
    """A1 复现转绿:同指纹并发进入 → relevance 只判一次、预执行至多一个;
    不同指纹并发 → 并发=1 仍成立(第二条回落普通卡);不值得时坑要摘干净。"""
    app = make_app(tmp_path)
    judge_calls = {"n": 0}
    concurrent = {"now": 0, "max": 0}

    async def slow_judge(*a, **kw):
        judge_calls["n"] += 1
        await asyncio.sleep(0.05)
        return _worth()

    async def counting_run(app_, sig_, rel, *, now):
        concurrent["now"] += 1
        concurrent["max"] = max(concurrent["max"], concurrent["now"])
        await asyncio.sleep(0.1)
        concurrent["now"] -= 1

    monkeypatch.setattr(sp, "_judge", slow_judge)
    monkeypatch.setattr(sp, "_run_preexec", counting_run)
    fp = task_failed_fingerprint("t1")
    r = await asyncio.gather(sp.maybe_consume_signal(app, _sig(fp=fp)),
                             sp.maybe_consume_signal(app, _sig(fp=fp)))
    await _drain_inflight(app)
    assert judge_calls["n"] == 1                    # 同指纹只判一次
    assert concurrent["max"] <= 1                   # 并发=1
    assert list(r).count(True) >= 1                 # 至少一条消费成功(压住普通卡)
    # 不同指纹并发:判断窗互斥(占坑),第二条回落普通卡
    judge_calls["n"] = 0; concurrent["max"] = 0
    app2 = make_app(tmp_path / "b")
    r2 = await asyncio.gather(sp.maybe_consume_signal(app2, _sig(fp="task_failed:a")),
                              sp.maybe_consume_signal(app2, _sig(fp="task_failed:b")))
    await _drain_inflight(app2)
    assert concurrent["max"] <= 1
    assert sorted(r2) == [False, True]              # 一条消费、一条回落
    # 判"不值得"后坑要摘干净(不永久占指纹/占并发位)
    app3 = make_app(tmp_path / "c")

    async def no_worth(*a, **kw):
        return {"worth": False, "action_intent": "", "reason": "no"}

    monkeypatch.setattr(sp, "_judge", no_worth)
    assert await sp.maybe_consume_signal(app3, _sig(fp="task_failed:z")) is False
    assert getattr(app3.state, "_scene_preexec_inflight", {}) == {}


def test_c3_frontend_direct_out_includes_scene_ready():
    """C3 静态锁:「已备好」卡高时效(带 expiry,过期即撤卡弃产物)—— _isDirectOut 必须
    把 scene_ready 直出,绝不收进积压抽屉(收起→过期=白烧的预执行)。"""
    import pathlib
    app_js = (pathlib.Path(__file__).resolve().parent.parent
              / "karvyloop" / "console" / "static" / "app.js")
    src = app_js.read_text(encoding="utf-8")
    i = src.find("function _isDirectOut")
    assert i >= 0, "app.js 里必须有 _isDirectOut(docs/92 抽屉限流)"
    body = src[i:i + 800]
    assert "scene_ready" in body, "_isDirectOut 必须直出 scene_ready(C3)"


@pytest.mark.asyncio
async def test_a3_daily_cap_is_reservation_based(tmp_path, monkeypatch):
    """A3:launch 门 = 已花 + 单次预留 ≤ 每日上限(防"只剩一丝仍放行整个 run"溢出)。"""
    app = make_app(tmp_path)
    called = {"n": 0}

    async def counting_judge(*a, **kw):
        called["n"] += 1
        return _worth()

    async def noop_run(app_, sig_, rel, *, now):
        return None

    monkeypatch.setattr(sp, "_judge", counting_judge)
    monkeypatch.setattr(sp, "_run_preexec", noop_run)  # 不真跑
    app.state.scene_preexec_max_tokens = 30_000
    app.state.scene_preexec_run_reserve = 10_000
    sp.preexec_store(app).add_tokens(25_000)        # 已花 25k:剩 5k < 预留 10k → 拦
    assert await sp.maybe_consume_signal(app, _sig(fp="task_failed:r1")) is False
    assert called["n"] == 0                         # 连 relevance 都不烧
    # 已花 15k:15k+10k ≤ 30k → 放行
    app2 = make_app(tmp_path / "b")
    app2.state.scene_preexec_max_tokens = 30_000
    sp.preexec_store(app2).add_tokens(15_000)
    assert await sp.maybe_consume_signal(app2, _sig(fp="task_failed:r2")) is True
    await _drain_inflight(app2)


def test_b2_distill_filters_machine_source(tmp_path):
    """B2:漏斗事件带 source;习惯蒸馏把 source=scene_preexec 的机器 goal 挡在外面。"""
    from karvyloop.karvy.fastbrain.trace_index import TraceIndex
    from karvyloop.karvy.fastbrain.trace_poll import distill_raw_to_summary
    idx = TraceIndex(tmp_path / "trace.db")
    try:
        idx.append_raw({"kind": "intent", "intent": "用户想生成周报", "brain": "slow",
                        "source": "unknown"})
        idx.append_raw({"kind": "intent", "intent": "任务「导出」上次失败。请诊断失败原因",
                        "brain": "slow", "source": "scene_preexec"})
        s = distill_raw_to_summary(idx)
        assert s is not None
        assert s["from_raw_count"] == 1
        assert s["recent_intents"] == ["用户想生成周报"]        # 机器 goal 不进习惯供料
    finally:
        idx.close()


# ================================================================ 持久状态宁空勿毒
def test_preexec_store_bad_file_and_day_roll(tmp_path):
    p = tmp_path / "scene_preexec.json"
    p.write_text("{ not json", encoding="utf-8")
    st = sp.ScenePreexecStore(p)                     # 坏文件当空
    assert st.attempted("x") is False
    st.mark_attempted("fp1")
    st.put_relevance("fp1", _worth())
    st.add_tokens(500)
    st2 = sp.ScenePreexecStore(p)                    # 跨重启还原
    assert st2.attempted("fp1") and st2.relevance("fp1")["worth"] is True
    assert st2.tokens_spent_today() == 500
    # 日账按本地日历日重置
    assert st2.tokens_spent_today(now=time.time() + 2 * 86400) == 0
