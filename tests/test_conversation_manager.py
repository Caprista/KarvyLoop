"""test_conversation_manager — ConversationManager 编排 + 慢脑读 ctx(M3+ 拍 9.1c)。

设计:docs/26 §B(CV-2/4/6/8/10)。

AC 矩阵:
- AC1-AC3: start(续最近/无则新)+ current
- AC4-AC5: new_conversation(CV-2 边界)+ CV-4 摘要喂 Trace
- AC6-AC7: resume(找到/找不到)+ list
- AC8-AC9: record_turn(入对话带 brain)+ context_view(CV-8)
- AC10-AC12: 慢脑读 ctx — _slow_brain_accepts_ctx / _render_ctx_prefix / drive 传 ctx 给接 ctx 的慢脑
- AC13: 向后兼容(老 slow_brain 不接 ctx,drive 只传 intent)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from karvyloop.cognition.conversation import (
    BRAIN_FAST,
    BRAIN_SLOW,
    ConversationManager,
    ConversationStore,
    Turn,
)


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "conv")


class _FakeTraceIndex:
    def __init__(self) -> None:
        self.summaries: list = []

    def append_summary(self, payload: dict) -> None:
        self.summaries.append(payload)


# ---- AC1-AC3: start / current ----


def test_start_new_when_no_history(store: ConversationStore) -> None:
    mgr = ConversationManager(store)
    conv = mgr.start()
    assert conv is not None
    assert conv.turn_count == 0
    assert mgr.current() is conv


def test_start_resumes_most_recent(store: ConversationStore) -> None:
    # 先造一段有内容的对话
    mgr1 = ConversationManager(store)
    c1 = mgr1.start()
    mgr1.record_turn("第一句", "回应", brain=BRAIN_SLOW)
    cid = c1.id
    # 新 manager(模拟重启)→ start 续上最近
    mgr2 = ConversationManager(store)
    resumed = mgr2.start()
    assert resumed.id == cid
    assert resumed.turn_count == 1


# ---- AC4-AC5: new_conversation + CV-4 ----


def test_new_conversation_switches_current(store: ConversationStore) -> None:
    mgr = ConversationManager(store)
    c1 = mgr.start()
    mgr.record_turn("u", "a")
    c2 = mgr.new_conversation(title="新话题")
    assert c2.id != c1.id
    assert mgr.current() is c2
    assert c2.title == "新话题"


def test_new_conversation_summarizes_old_to_trace(store: ConversationStore) -> None:
    """CV-4:开新对话时,旧对话摘要 append 进 Trace。"""
    trace = _FakeTraceIndex()
    mgr = ConversationManager(store, trace_index=trace)
    mgr.start()
    mgr.record_turn("查 git", "好的")
    mgr.record_turn("打包 wheel", "完成")
    assert len(trace.summaries) == 0  # 还没开新对话
    mgr.new_conversation()
    assert len(trace.summaries) == 1
    s = trace.summaries[0]
    assert s["kind"] == "conversation_summary"
    assert s["turn_count"] == 2
    assert "查 git" in s["summary"]


def test_new_conversation_no_trace_when_old_empty(store: ConversationStore) -> None:
    """旧对话无轮 → 不喂 Trace(空摘要没意义)。"""
    trace = _FakeTraceIndex()
    mgr = ConversationManager(store, trace_index=trace)
    mgr.start()  # 空对话
    mgr.new_conversation()
    assert len(trace.summaries) == 0


def test_new_conversation_without_trace_index_ok(store: ConversationStore) -> None:
    """无 trace_index → 开新对话照常(不崩)。"""
    mgr = ConversationManager(store)  # trace_index=None
    mgr.start()
    mgr.record_turn("u", "a")
    c2 = mgr.new_conversation()
    assert c2 is not None


# ---- AC6-AC7: resume / list ----


def test_resume_loads_and_switches(store: ConversationStore) -> None:
    mgr = ConversationManager(store)
    c1 = mgr.start()
    mgr.record_turn("记我", "记住了")
    target = c1.id
    mgr.new_conversation()  # 切到新的
    # resume 回 c1(9.2a:resume 需 peer)
    from karvyloop.cognition.conversation import karvy_world_peer
    resumed = mgr.resume(karvy_world_peer(), target)
    assert resumed is not None
    assert resumed.id == target
    assert mgr.current().id == target
    assert resumed.turns[0].user_intent == "记我"


def test_resume_missing_keeps_current(store: ConversationStore) -> None:
    from karvyloop.cognition.conversation import karvy_world_peer
    mgr = ConversationManager(store)
    c = mgr.start()
    assert mgr.resume(karvy_world_peer(), "nonexistent") is None
    assert mgr.current() is c  # 当前不变


def test_list_conversations(store: ConversationStore) -> None:
    mgr = ConversationManager(store)
    mgr.start()
    mgr.record_turn("u", "a")
    mgr.new_conversation()
    mgr.record_turn("u2", "a2")
    metas = mgr.list_conversations()
    assert len(metas) == 2


# ---- AC8-AC9: record_turn + context_view ----


def test_record_turn_appends_with_brain(store: ConversationStore) -> None:
    mgr = ConversationManager(store)
    mgr.start()
    mgr.record_turn("u1", "a1", brain=BRAIN_FAST, task_id="t1")
    mgr.record_turn("u2", "a2", brain=BRAIN_SLOW)
    conv = mgr.current()
    assert conv.turn_count == 2
    assert conv.turns[0].brain == BRAIN_FAST
    assert conv.turns[0].task_id == "t1"
    assert conv.turns[1].brain == BRAIN_SLOW


def test_record_turn_auto_starts(store: ConversationStore) -> None:
    """没 start 直接 record → 自动 start。"""
    mgr = ConversationManager(store)
    mgr.record_turn("u", "a")
    assert mgr.current() is not None
    assert mgr.current().turn_count == 1


def test_context_view_returns_recent(store: ConversationStore) -> None:
    mgr = ConversationManager(store, context_turns=3)
    mgr.start()
    for i in range(5):
        mgr.record_turn(f"u{i}", f"a{i}")
    view = mgr.context_view()
    assert len(view) == 3
    assert [t.user_intent for t in view] == ["u2", "u3", "u4"]


def test_context_view_empty_when_no_current(store: ConversationStore) -> None:
    mgr = ConversationManager(store)
    assert mgr.context_view() == ()


# ---- 9.2a: 多场(set_peer)+ 场隔离(CV-13)----


def test_set_peer_switches_and_isolates(store: ConversationStore) -> None:
    """私聊线 与 业务域线 各自独立,切场互不串(CV-13)。"""
    from karvyloop.cognition.conversation import karvy_world_peer
    from karvyloop.domain.registry import Address

    priv = karvy_world_peer()
    biz = Address(domain_id="dom-装修", role="agent", agent_id="设计师")

    mgr = ConversationManager(store)
    mgr.set_peer(priv)
    mgr.record_turn("私聊问题", "私聊回答")
    assert mgr.current_peer().domain_id == "l0"
    assert mgr.context_view()[-1].user_intent == "私聊问题"

    # 切到业务域线 —— 全新的上下文,看不到私聊内容
    mgr.set_peer(biz)
    assert mgr.current_peer().domain_id == "dom-装修"
    assert mgr.context_view() == ()  # 业务域线还没内容,**不串**私聊
    mgr.record_turn("装修问题", "装修回答")
    assert mgr.context_view()[-1].user_intent == "装修问题"

    # 切回私聊 —— 续上私聊那条线(CV-6 + CV-13)
    mgr.set_peer(priv)
    assert mgr.context_view()[-1].user_intent == "私聊问题"
    # 私聊线看不到装修内容
    assert all("装修" not in t.user_intent for t in mgr.context_view())


def test_list_conversations_per_peer(store: ConversationStore) -> None:
    from karvyloop.cognition.conversation import karvy_world_peer
    from karvyloop.domain.registry import Address

    priv = karvy_world_peer()
    biz = Address(domain_id="dom-x", role="agent", agent_id="a")
    mgr = ConversationManager(store)
    mgr.set_peer(priv); mgr.record_turn("p", "a")
    mgr.set_peer(biz); mgr.record_turn("b", "a")
    # 各看各的
    assert len(mgr.list_conversations(priv)) == 1
    assert len(mgr.list_conversations(biz)) == 1
    # 默认列当前 peer(biz)
    assert len(mgr.list_conversations()) == 1


# ---- AC10-AC13: 慢脑读 ctx ----


def test_slow_brain_accepts_ctx_detection() -> None:
    from karvyloop.cli.main_loop import _slow_brain_accepts_ctx

    def with_ctx(intent, *, ctx=None):
        return intent, None

    def without_ctx(intent):
        return intent, None

    def with_kwargs(intent, **kw):
        return intent, None

    assert _slow_brain_accepts_ctx(with_ctx) is True
    assert _slow_brain_accepts_ctx(without_ctx) is False
    assert _slow_brain_accepts_ctx(with_kwargs) is True


def test_render_ctx_prefix() -> None:
    from karvyloop.cli.main_loop import _render_ctx_prefix

    assert _render_ctx_prefix(None) == ""
    assert _render_ctx_prefix(()) == ""
    turns = (Turn("看衣服", "要试穿吗", ts=1.0), Turn("好", "试这件", ts=2.0))
    prefix = _render_ctx_prefix(turns)
    assert "看衣服" in prefix
    assert "要试穿吗" in prefix
    assert "好" in prefix


def test_drive_passes_ctx_to_ctx_aware_slow_brain(tmp_path: Path) -> None:
    """drive 把 ctx 传给接 ctx 的慢脑(消解多轮)。"""
    from karvyloop.cli.main_loop import MainLoop
    from karvyloop.schemas import AtomRun

    loop = MainLoop(skills_dir=tmp_path / "s", scope="private")
    seen = {}

    def sb(intent, *, ctx=None):
        seen["ctx"] = ctx
        run = AtomRun(atom_id="a", input={}, output={"t": "ok"}, success=True,
                      tool_calls=[], trace_ref="t", ts=1.0)
        return "ok", run

    ctx_turns = (Turn("看衣服", "要试穿吗", ts=1.0),)
    # 用独立句(不触发上下文依赖门,确保走到慢脑且 ctx 透传)
    loop.drive("帮我搜索附近的店", slow_brain=sb, ctx=ctx_turns)
    assert seen["ctx"] == ctx_turns


def test_drive_old_slow_brain_still_works_with_ctx(tmp_path: Path) -> None:
    """老 slow_brain(只接 intent)+ 传 ctx → drive 只传 intent(0 回归)。"""
    from karvyloop.cli.main_loop import MainLoop
    from karvyloop.schemas import AtomRun

    loop = MainLoop(skills_dir=tmp_path / "s", scope="private")
    calls = {"n": 0}

    def old_sb(intent):  # 不接 ctx
        calls["n"] += 1
        run = AtomRun(atom_id="a", input={}, output={"t": "ok"}, success=True,
                      tool_calls=[], trace_ref="t", ts=1.0)
        return "ok", run

    r = loop.drive("帮我搜索附近的店", slow_brain=old_sb, ctx=(Turn("x", "y", ts=1.0),))
    assert calls["n"] == 1  # 正常调用,没因 ctx 崩
    assert r.text == "ok"
