"""test_conversation_pending_turn — 挂起轮:begin_turn / finalize_turn / 僵尸扫描。

bug(执行中对话归属竞态):user 消息 + 任务↔对话绑定此前只在 drive 结束才落账 → drive
执行中那段窗口刷新时,活动对话回落默认场(小卡),消息读不到、正在跑的任务飘到小卡。

修法(落账提前 + 挂起轮 + 完成回填):begin_turn 立即在**捕获到的**对话追加挂起轮并落盘;
finalize_turn 就地回填(不追加第二条);崩溃留下的僵尸挂起轮超时标"已中断"。

覆盖任务 test 清单:
①begin 立即落盘可读(挂起态)+ finalize 就地回填不追加
②drive 中途读对话=挂起轮在正确 conv(current 漂移也不落错场)
③error/abort → finalize 标失败不留僵尸
④僵尸 pending 扫描/读时标中断
⑤向后兼容:老对话文件无 pending 字段照读
⑥begin(空轮)不触发下游摘要;finalize(完整轮)才是完整料
"""
from __future__ import annotations

from pathlib import Path

import pytest

from karvyloop.cognition.conversation import (
    PENDING_STALE_THRESHOLD_S,
    TURN_DONE,
    TURN_ERROR,
    TURN_INTERRUPTED,
    TURN_RUNNING,
    ConversationManager,
    ConversationStore,
    Turn,
    karvy_world_peer,
)
from karvyloop.domain.registry import Address


PRIVATE = karvy_world_peer()
BIZ = Address(domain_id="dom-装修", role="agent", agent_id="设计师")


@pytest.fixture
def clock():
    base = [1_700_000_000.0]

    def now() -> float:
        return base[0]

    def advance(s: float) -> None:
        base[0] += s

    return now, advance


@pytest.fixture
def store(tmp_path: Path, clock) -> ConversationStore:
    now, _ = clock
    return ConversationStore(tmp_path / "conv", clock=now)


class _FakeTrace:
    def __init__(self) -> None:
        self.summaries: list = []

    def append_summary(self, payload: dict) -> None:
        self.summaries.append(payload)


# ---- ① begin 立即落盘可读(挂起态)----

def test_begin_turn_persists_pending_immediately(store, clock) -> None:
    mgr = ConversationManager(store)
    conv = mgr.start()
    h = mgr.begin_turn("分析这张图", task_id="reg1", conv=conv)
    assert h.turn_id and h.conv is conv
    # 从盘重读(模拟另一次进程/刷新)→ 挂起轮在,user 消息在,回复位空
    reloaded = store.load(PRIVATE, conv.id)
    assert len(reloaded.turns) == 1
    t = reloaded.turns[0]
    assert t.pending is True and t.status == TURN_RUNNING
    assert t.user_intent == "分析这张图" and t.agent_response == ""
    assert t.turn_id == h.turn_id


# ---- ① finalize 就地回填不追加第二条 ----

def test_finalize_backfills_in_place_no_second_turn(store) -> None:
    mgr = ConversationManager(store)
    conv = mgr.start()
    h = mgr.begin_turn("干活", conv=conv)
    mgr.finalize_turn(h, "干完了", task_id="trace9", brain="slow")
    reloaded = store.load(PRIVATE, conv.id)
    assert len(reloaded.turns) == 1, "finalize 必须就地回填,不叠第二条"
    t = reloaded.turns[0]
    assert t.pending is False and t.status == TURN_DONE
    assert t.agent_response == "干完了" and t.task_id == "trace9"
    # meta turn_count 也 coalesce(begin+finalize 算一轮)
    metas = store.list_conversations(PRIVATE)
    assert metas[0].turn_count == 1


# ---- ② drive 中途读对话:挂起轮在正确 conv(current 漂移也不落错场)----

def test_pending_lands_in_captured_conv_even_if_current_drifts(store) -> None:
    """镜像 ws/routes 接线:begin 捕获业务域对话 A → drive 中用户切到私聊小卡(current 漂移)
    → finalize 仍回填 A,私聊线一条不落。"""
    mgr = ConversationManager(store)
    conv_a = mgr.set_peer(BIZ)          # 业务域对话 A
    h = mgr.begin_turn("在装修域里干活", task_id="", conv=conv_a)
    # drive 中途:用户切场到私聊小卡(current 漂移到别的 peer)
    mgr.set_peer(PRIVATE)
    assert mgr.current().id != conv_a.id
    # 中途从盘读 A → 挂起轮在 A、user 消息在
    mid = store.load(BIZ, conv_a.id)
    assert len(mid.turns) == 1 and mid.turns[0].pending
    assert mid.turns[0].user_intent == "在装修域里干活"
    # finalize 回填 A(不是当前的私聊线)
    mgr.finalize_turn(h, "装修域结果", brain="slow")
    a_final = store.load(BIZ, conv_a.id)
    assert len(a_final.turns) == 1 and a_final.turns[0].agent_response == "装修域结果"
    # 私聊线一条都没落
    assert store.most_recent(PRIVATE) is None or store.most_recent(PRIVATE).id != conv_a.id
    priv = store.list_conversations(PRIVATE)
    assert all(m.turn_count == 0 for m in priv)


# ---- ③ error → finalize 标失败,不留僵尸 pending ----

def test_finalize_error_marks_failed_not_pending(store) -> None:
    mgr = ConversationManager(store)
    conv = mgr.start()
    h = mgr.begin_turn("会崩的活", conv=conv)
    mgr.finalize_turn(h, "", error="网络挂了")
    t = store.load(PRIVATE, conv.id).turns[0]
    assert t.pending is False and t.status == TURN_ERROR
    assert t.agent_response == "网络挂了"


# ---- ④ 僵尸 pending:读时超时呈现为已中断 ----

def test_stale_pending_presented_as_interrupted_on_load(store, clock) -> None:
    now, advance = clock
    mgr = ConversationManager(store)
    conv = mgr.start()
    mgr.begin_turn("慢活", conv=conv)
    advance(PENDING_STALE_THRESHOLD_S + 5)   # 进程崩了/久无回填
    t = store.load(PRIVATE, conv.id).turns[0]
    assert t.pending is False and t.status == TURN_INTERRUPTED
    assert t.agent_response   # 有人话"已中断"文案,不空白


def test_fresh_pending_stays_running(store, clock) -> None:
    now, advance = clock
    mgr = ConversationManager(store)
    conv = mgr.start()
    mgr.begin_turn("刚发的慢活", conv=conv)
    advance(60)   # 1 分钟,阈值内 = 活着的慢 drive,不误杀
    t = store.load(PRIVATE, conv.id).turns[0]
    assert t.pending is True and t.status == TURN_RUNNING


# ---- ④ 僵尸 pending:读时呈现是权威机制(幂等、无落盘 tick)----

def test_stale_pending_reads_interrupted_every_load(store, clock) -> None:
    """进程崩后的僵尸挂起轮:每次被读到都判"已中断"(幂等),不依赖任何后台落盘。"""
    now, advance = clock
    mgr = ConversationManager(store)
    conv = mgr.start()
    mgr.begin_turn("慢活", conv=conv)
    advance(PENDING_STALE_THRESHOLD_S + 5)
    # 读一次 = 中断(读时呈现)
    t1 = store.load(PRIVATE, conv.id).turns[0]
    assert t1.status == TURN_INTERRUPTED and not t1.pending
    # 再读一次仍中断(幂等,无副作用改盘)
    t2 = store.load(PRIVATE, conv.id).turns[0]
    assert t2.status == TURN_INTERRUPTED and not t2.pending


# ---- ⑤ 向后兼容:老对话文件无 pending 字段照读 ----

def test_backward_compat_old_turn_without_pending_fields(store) -> None:
    mgr = ConversationManager(store)
    conv = mgr.start()
    mgr.record_turn("老轮问", "老轮答", brain="slow")   # 老 API:无 turn_id/pending/status
    t = store.load(PRIVATE, conv.id).turns[0]
    assert t.turn_id == "" and t.pending is False and t.status == ""
    assert t.user_intent == "老轮问" and t.agent_response == "老轮答"


def test_old_record_never_coalesces(store) -> None:
    """record_turn 的老轮无 turn_id → 永远追加,绝不被 coalesce 吞掉(0 回归)。"""
    mgr = ConversationManager(store)
    conv = mgr.start()
    mgr.record_turn("q1", "a1")
    mgr.record_turn("q2", "a2")
    turns = store.load(PRIVATE, conv.id).turns
    assert [t.user_intent for t in turns] == ["q1", "q2"]


# ---- ⑥ begin(空轮)不触发下游摘要;整轮混合序列正确 ----

def test_begin_does_not_summarize_to_trace(store) -> None:
    """begin 只落挂起轮,不喂 Trace 摘要(下游只在完整轮/切场时触发)。"""
    trace = _FakeTrace()
    mgr = ConversationManager(store, trace_index=trace)
    conv = mgr.start()
    mgr.begin_turn("干活", conv=conv)
    assert trace.summaries == []   # begin 不触发任何下游摘要


def test_finalize_then_switch_summarizes_full_turn(store) -> None:
    """finalize 回填后切场 → 摘要喂 Trace 时看到的是完整轮(有回复),不是空挂起轮。"""
    trace = _FakeTrace()
    mgr = ConversationManager(store, trace_index=trace)
    conv = mgr.start()
    h = mgr.begin_turn("问题X", conv=conv)
    mgr.finalize_turn(h, "答案X")
    mgr.set_peer(BIZ)   # 切场 → 触发对上一条私聊线的摘要
    assert trace.summaries, "切场应喂一条摘要"
    assert trace.summaries[0]["turn_count"] == 1


# ---- 混合:挂起轮 + 老轮 + 回填,加载顺序 & 计数正确 ----

def test_mixed_pending_and_normal_ordering(store) -> None:
    mgr = ConversationManager(store)
    conv = mgr.start()
    mgr.record_turn("q0", "a0")
    h = mgr.begin_turn("q1")           # 无 conv → 用 current
    mgr.record_turn("q2", "a2")        # 挂起轮之后又来一轮普通轮
    mgr.finalize_turn(h, "a1")         # 回填中间那条挂起轮(就地)
    turns = store.load(PRIVATE, conv.id).turns
    assert [(t.user_intent, t.agent_response) for t in turns] == [
        ("q0", "a0"), ("q1", "a1"), ("q2", "a2")]
    assert store.list_conversations(PRIVATE)[0].turn_count == 3


# ---- to_ui_dict 形状(前端序列化契约)----

def test_to_ui_dict_shape() -> None:
    t = Turn(user_intent="u", agent_response="a", brain="slow", task_id="x",
             turn_id="tid", pending=True, status=TURN_RUNNING)
    d = t.to_ui_dict()
    assert d == {"user_intent": "u", "agent_response": "a", "brain": "slow",
                 "task_id": "x", "data": None, "pending": True, "status": TURN_RUNNING}


# ---- finalize 句柄为 None → 返回 None(begin 落账失败降级路径)----

def test_finalize_none_handle_returns_none(store) -> None:
    mgr = ConversationManager(store)
    mgr.start()
    assert mgr.finalize_turn(None, "x") is None
