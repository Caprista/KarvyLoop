"""task_events — 决策 loop 的 fail-loud + push 接缝(docs/00 §0.7)。

把**任务状态变化 / 步级进度 / 后台任务失败**主动推给 WS clients,取代"靠 2s 轮询
碰巧发现"。对照 §0.7「怎么样了?」反模式:失败必须是**事件(push)**,不是等人来问。

镜像 `proposals.broadcast_proposal` 的模式(遍历 `app.state.ws_clients` → `send_json`
→ 剔死连接),但这里是**确定性系统事件**(非建议),不进 proposal_registry。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# WS 消息类型(与 ws.py 协议注释保持一致)
WS_TYPE_TASK_STATUS = "task_status"
WS_TYPE_TASK_STEP = "task_step"
WS_TYPE_SYSTEM_ERROR = "system_error"
WS_TYPE_DRIVE_EVENT = "drive_event"   # P4 逐字流式:drive 进行中的增量 render 事件


async def _broadcast(app: Any, message: dict) -> int:
    """把一条消息推给所有 WS clients(剔死连接)。返回成功数。"""
    clients = getattr(app.state, "ws_clients", None)
    if not clients:
        return 0
    sent = 0
    dead: list = []
    for ws in list(clients):
        try:
            await ws.send_json(message)
            sent += 1
        except Exception as e:
            logger.debug(f"[task_events] ws 推送失败,剔除: {e}")
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)
    return sent


async def broadcast_task_status(app: Any, task: dict) -> int:
    """任务 running/done/error → push(决策 loop 即时知情,不靠轮询)。"""
    return await _broadcast(app, {"type": WS_TYPE_TASK_STATUS, "payload": task})


async def broadcast_task_step(app: Any, payload: dict) -> int:
    """workflow / 圆桌 步级完成/失败 → push(实时看哪步、谁挂了)。"""
    return await _broadcast(app, {"type": WS_TYPE_TASK_STEP, "payload": payload})


async def broadcast_system_error(app: Any, source: str, message: str) -> int:
    """后台 fire-and-forget 任务失败 → push(灭静默死角)。"""
    return await _broadcast(
        app,
        {"type": WS_TYPE_SYSTEM_ERROR,
         "payload": {"source": source, "message": (message or "")[:500]}},
    )


async def broadcast_drive_event(app: Any, ev: dict) -> int:
    """P4 逐字流式:drive 进行中的一个增量 render 事件(text_delta/tool_call/tool_result/terminal)→ push。"""
    return await _broadcast(app, {"type": WS_TYPE_DRIVE_EVENT, "payload": ev})


def _schedule(coro_factory: Callable[[], Coroutine]) -> None:
    """在当前事件循环上排一个广播协程;无 loop(同步上下文)→ 静默跳过。

    finish/start 在 async handler 里调(loop 线程上),有 loop → 正常排;
    启动期 reload(同步、无 client)→ RuntimeError → 跳过,不报错。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(coro_factory())


def schedule_task_broadcast(app: Any, task: dict) -> None:
    """同步钩子:供 `TaskRegistry.on_change` 调(finish/start 自动推状态)。

    这让"状态即事件"成为**结构性保证**——任何调 start/finish 的代码路径
    (含未来新增的)都自动推送,不会重新引入"靠人轮询"的静默 bug(§0.7)。
    """
    _schedule(lambda: broadcast_task_status(app, task))


def schedule_system_error(app: Any, source: str, message: str) -> None:
    """同步钩子:后台任务 except 里调,把失败 push 给 UI(灭静默死角)。"""
    _schedule(lambda: broadcast_system_error(app, source, message))


__all__ = [
    "WS_TYPE_TASK_STATUS", "WS_TYPE_TASK_STEP", "WS_TYPE_SYSTEM_ERROR",
    "broadcast_task_status", "broadcast_task_step", "broadcast_system_error",
    "schedule_task_broadcast", "schedule_system_error",
]
