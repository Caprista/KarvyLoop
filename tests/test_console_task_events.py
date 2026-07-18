"""test_console_task_events — §0.7 决策 loop 的 fail-loud + push 接缝。

来源:Hardy 实例复盘 → docs/00 §0.7「怎么样了?」反模式。
失败/状态必须是**事件(push)**,不是靠前端 2s 轮询碰巧发现。

AC:
- AC1-3: broadcast_task_status / step / system_error → N client / 0 / 死连接剔除
- AC4-6: TaskRegistry.on_change 在 start(running)/finish(done)/finish(error) 各触发一次
- AC7: _notify 吞回调异常(广播失败不拖垮任务记录)
- AC8-9: schedule_* 在 loop 上排协程真推送 / 无 loop(同步)静默不崩
- AC10: 结构性保证 —— 接线后 start/finish 自动 push(端到端经 TaskRegistry)
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console.task_events import (  # noqa: E402
    WS_TYPE_SYSTEM_ERROR,
    WS_TYPE_TASK_STATUS,
    WS_TYPE_TASK_STEP,
    _broadcast,
    broadcast_system_error,
    broadcast_task_status,
    broadcast_task_step,
    schedule_system_error,
    schedule_task_broadcast,
)
from karvyloop.console.tasks import TaskRegistry  # noqa: E402
from karvyloop.console.ws import _serialize_ws_send  # noqa: E402


class _FakeWs:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list = []
        self.fail = fail

    async def send_json(self, obj) -> None:
        if self.fail:
            raise RuntimeError("dead client")
        self.sent.append(obj)


class _FakeApp:
    def __init__(self) -> None:
        class _State:
            pass

        self.state = _State()
        self.state.ws_clients = set()


# ---- AC1-3: broadcast_* ----


@pytest.mark.asyncio
async def test_broadcast_task_status_to_n_clients() -> None:
    app = _FakeApp()
    c1, c2 = _FakeWs(), _FakeWs()
    app.state.ws_clients = {c1, c2}
    sent = await broadcast_task_status(app, {"id": "t1", "status": "error", "who": "设计师"})
    assert sent == 2
    for c in (c1, c2):
        assert c.sent[0]["type"] == WS_TYPE_TASK_STATUS
        assert c.sent[0]["payload"]["status"] == "error"
        assert c.sent[0]["payload"]["who"] == "设计师"


@pytest.mark.asyncio
async def test_broadcast_task_step_and_system_error() -> None:
    app = _FakeApp()
    c1 = _FakeWs()
    app.state.ws_clients = {c1}
    await broadcast_task_step(app, {"task_id": "t1", "display": "工程师", "status": "failed"})
    await broadcast_system_error(app, "auto_distill", "boom")
    assert c1.sent[0]["type"] == WS_TYPE_TASK_STEP
    assert c1.sent[0]["payload"]["status"] == "failed"
    assert c1.sent[1]["type"] == WS_TYPE_SYSTEM_ERROR
    assert c1.sent[1]["payload"]["source"] == "auto_distill"
    assert c1.sent[1]["payload"]["message"] == "boom"


@pytest.mark.asyncio
async def test_broadcast_zero_clients_and_dead_eviction() -> None:
    app = _FakeApp()
    assert await broadcast_task_status(app, {"id": "x", "status": "done"}) == 0  # 0 client
    good, dead = _FakeWs(), _FakeWs(fail=True)
    app.state.ws_clients = {good, dead}
    sent = await broadcast_task_status(app, {"id": "y", "status": "done"})
    assert sent == 1
    assert dead not in app.state.ws_clients
    assert good in app.state.ws_clients


@pytest.mark.asyncio
async def test_system_error_truncates_long_message() -> None:
    app = _FakeApp()
    c1 = _FakeWs()
    app.state.ws_clients = {c1}
    await broadcast_system_error(app, "src", "x" * 9999)
    assert len(c1.sent[0]["payload"]["message"]) == 500


# ---- AC4-7: TaskRegistry.on_change ----


def test_on_change_fires_on_start_and_finish() -> None:
    seen: list = []
    reg = TaskRegistry(on_change=lambda d: seen.append(d))
    tid = reg.start(who="小卡", intent="做个登录页")
    assert seen[-1]["status"] == "running"
    assert seen[-1]["who"] == "小卡"
    reg.finish(tid, result="完成了")
    assert seen[-1]["status"] == "done"
    reg.finish(tid, error="炸了")   # 二次 finish 也推(状态变 error)
    assert seen[-1]["status"] == "error"
    assert len(seen) == 3   # start + finish(done) + finish(error)


def test_on_change_none_is_safe() -> None:
    reg = TaskRegistry()  # 无回调
    tid = reg.start(who="x")
    reg.finish(tid, result="ok")   # 不该崩


def test_notify_swallows_callback_exception() -> None:
    def _boom(_d):
        raise RuntimeError("listener exploded")

    reg = TaskRegistry(on_change=_boom)
    tid = reg.start(who="x")        # 回调炸了也不该拖垮 start
    reg.finish(tid, result="ok")   # finish 同理
    assert reg.get(tid)["status"] == "done"


# ---- AC8-9: schedule_* (sync 钩子 → loop 排协程) ----


@pytest.mark.asyncio
async def test_schedule_task_broadcast_pushes_on_loop() -> None:
    app = _FakeApp()
    c1 = _FakeWs()
    app.state.ws_clients = {c1}
    schedule_task_broadcast(app, {"id": "t1", "status": "running", "who": "小卡"})
    schedule_system_error(app, "daily_poll", "down")
    await asyncio.sleep(0)   # 让排上的协程跑完
    await asyncio.sleep(0)
    types = {m["type"] for m in c1.sent}
    assert WS_TYPE_TASK_STATUS in types
    assert WS_TYPE_SYSTEM_ERROR in types


def test_schedule_no_running_loop_is_noop() -> None:
    # 同步上下文(无事件循环)→ RuntimeError 被吞,静默跳过,绝不抛
    app = _FakeApp()
    app.state.ws_clients = {_FakeWs()}
    schedule_task_broadcast(app, {"id": "t", "status": "done"})   # 不该抛


# ---- AC10: 结构性保证 —— 接线后 start/finish 自动 push(端到端经 registry) ----


@pytest.mark.asyncio
async def test_wired_registry_auto_pushes_status() -> None:
    """模拟 entry.py 接线:registry.on_change = schedule_task_broadcast(app, ...)。
    调 start/finish 即自动推 task_status,无需调用点显式广播(结构性保证)。"""
    app = _FakeApp()
    c1 = _FakeWs()
    app.state.ws_clients = {c1}
    reg = TaskRegistry(on_change=lambda d, _a=app: schedule_task_broadcast(_a, d))
    tid = reg.start(who="工程师", intent="跑测试")
    reg.finish(tid, error="测试挂了")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    statuses = [m["payload"]["status"] for m in c1.sent if m["type"] == WS_TYPE_TASK_STATUS]
    assert "running" in statuses
    assert "error" in statuses   # 失败 = 事件,自动冒到 UI(不靠轮询)


# ---- P3-b: 任务终态落 Trace(跑评分离 —— Trace = 所有评价的唯一数据源)----


@pytest.mark.asyncio
async def test_task_change_sink_writes_terminal_states_to_trace() -> None:
    """entry.py 真接线(make_task_change_sink):done/error 落 Trace(kind=task_run),
    running 不落(只有终态才是可评的);WS 推送照旧;trace=None → 只推不记(0 回归)。"""
    from karvyloop.cognition.trace import TraceStore
    from karvyloop.console.task_events import make_task_change_sink
    app = _FakeApp()
    c1 = _FakeWs()
    app.state.ws_clients = {c1}
    trace = TraceStore()
    reg = TaskRegistry(on_change=make_task_change_sink(app, trace))
    tid = reg.start(who="设计师", domain_id="dom-x", role="agent", intent="画个图")
    reg.finish(tid, result="图画完了")
    tid2 = reg.start(who="工程师", intent="跑测试")
    reg.finish(tid2, error="挂了")
    await asyncio.sleep(0); await asyncio.sleep(0)
    # done + error 各一条 task_run;running 不落
    e1 = trace.query(tid, kind="task_run")
    assert len(e1) == 1 and e1[0].payload["status"] == "done" and "图画完了" in e1[0].payload["result"]
    assert e1[0].payload["domain"] == "dom-x" and e1[0].agent == "设计师"
    e2 = trace.query(tid2, kind="task_run")
    assert len(e2) == 1 and e2[0].payload["status"] == "error"
    # WS 推送不受影响
    statuses = [m["payload"]["status"] for m in c1.sent if m["type"] == WS_TYPE_TASK_STATUS]
    assert "running" in statuses and "done" in statuses and "error" in statuses
    # trace=None(--no-llm)→ 不炸、只推
    reg2 = TaskRegistry(on_change=make_task_change_sink(app, None))
    t3 = reg2.start(who="x", intent="y")
    reg2.finish(t3, result="z")   # 不抛即过


# ---- 稳定性:WS 并发 send 锁(活连接不被误剔;真死仍剔)----


class _ConcurrencyWs:
    """假 ws:send_json 里让出控制并记录并发深度,验证 per-connection 锁串行化。

    fail=True 模拟真死连接(send 真抛)。inflight/max_inflight:同一时刻有几个 send
    在跑 —— 加锁后恒为 1(串行);没锁并发 gather 会飙到 N。
    """

    def __init__(self, fail: bool = False) -> None:
        self.sent: list = []
        self.fail = fail
        self.inflight = 0
        self.max_inflight = 0

    async def send_json(self, data, mode: str = "text") -> None:
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            await asyncio.sleep(0)   # 让出:给并发交错的机会(没锁则此处飙 inflight)
            if self.fail:
                raise RuntimeError("dead client")
            self.sent.append(data)
        finally:
            self.inflight -= 1


@pytest.mark.asyncio
async def test_ws_send_lock_serializes_concurrent_broadcasts() -> None:
    """并发压测:同一 ws 被 20 路广播并发打(drive_event/task_status/... 同时到)。
    _serialize_ws_send 挂锁后 send_json 串行(max_inflight==1),活连接绝不被误剔、全部送达。"""
    app = _FakeApp()
    ws = _ConcurrencyWs()
    _serialize_ws_send(ws)                        # 与真实 ws_endpoint 同一处理:挂 per-conn 锁
    app.state.ws_clients = {ws}
    await asyncio.gather(*[
        _broadcast(app, {"type": "drive_event", "payload": {"i": i}})
        for i in range(20)
    ])
    assert ws.max_inflight == 1                   # 串行:任一时刻只有一个 send 在跑
    assert len(ws.sent) == 20                     # 全部送达(无交错丢失)
    assert ws in app.state.ws_clients             # 活连接没被并发误伤剔除


@pytest.mark.asyncio
async def test_ws_send_lock_still_evicts_truly_dead() -> None:
    """真死连接仍正确剔除:send 真抛 → 广播 except → discard(不是被并发误伤),活的留。"""
    app = _FakeApp()
    live = _ConcurrencyWs()
    dead = _ConcurrencyWs(fail=True)
    _serialize_ws_send(live)
    _serialize_ws_send(dead)
    app.state.ws_clients = {live, dead}
    await asyncio.gather(*[
        _broadcast(app, {"type": "task_status", "payload": {"i": i}})
        for i in range(10)
    ])
    assert dead not in app.state.ws_clients       # 真死 → 剔
    assert live in app.state.ws_clients           # 活的留
    assert live.max_inflight == 1                 # 活连接仍串行
    assert len(live.sent) == 10


@pytest.mark.asyncio
async def test_ws_send_lock_no_reentrant_deadlock() -> None:
    """防死锁自证:同一连接连续两次 send 各自取放锁不成环(锁体内不再触发 send)。"""
    ws = _ConcurrencyWs()
    _serialize_ws_send(ws)
    await ws.send_json({"a": 1})
    await ws.send_json({"a": 2})                  # 第二次能拿到锁(前一次已释放)= 不死锁
    assert ws.sent == [{"a": 1}, {"a": 2}]


# ---- 稳定性:fire-and-forget task 强引用 + done-callback(docs/87 §五)----


@pytest.mark.asyncio
async def test_schedule_tracks_and_drains_pending_tasks() -> None:
    """CPython 只对 create_task 持弱引用 → 不存进容器可能被 GC 中途回收 + 吞异常。
    schedule_* 必须把 task 存进强引用 set,完成后 done-callback discard(不裸奔)。"""
    from karvyloop.console import task_events as te
    app = _FakeApp()
    app.state.ws_clients = {_FakeWs()}
    te._pending_tasks.clear()
    schedule_task_broadcast(app, {"id": "t1", "status": "running"})
    assert len(te._pending_tasks) == 1            # 排上即被强引用(不等 GC)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(te._pending_tasks) == 0            # 完成后 done-callback discard


@pytest.mark.asyncio
async def test_failing_broadcast_exception_is_logged_not_swallowed(caplog) -> None:
    """广播协程抛异常 → done-callback 取回 .exception() 记 warning(灭静默吞 + GC 未取回告警)。"""
    import logging

    from karvyloop.console import task_events as te
    te._pending_tasks.clear()

    async def _boom() -> None:
        raise RuntimeError("broadcast boom")

    te._schedule(lambda: _boom())
    with caplog.at_level(logging.WARNING, logger="karvyloop.console.task_events"):
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert len(te._pending_tasks) == 0
    assert any("广播后台任务异常" in r.message for r in caplog.records)
