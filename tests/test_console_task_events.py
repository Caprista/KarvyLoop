"""test_console_task_events — §0.7 决策 loop 的 fail-loud + push 接缝。

来源:Hardy OpenClaw 实例复盘 → docs/00 §0.7「怎么样了?」反模式。
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
    broadcast_system_error,
    broadcast_task_status,
    broadcast_task_step,
    schedule_system_error,
    schedule_task_broadcast,
)
from karvyloop.console.tasks import TaskRegistry  # noqa: E402


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
