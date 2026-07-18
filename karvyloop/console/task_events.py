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
WS_TYPE_ROLE_PRESENCE = "role_presence"   # P1.5 工位区:任务 start/done/error 顺势推该角色单行 presence


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


async def broadcast_role_presence(app: Any, row: dict) -> int:
    """P1.5 工位区:某角色的单行 presence(契约形状见 tasks.presence_row)→ push。

    折叠进既有 task_status 广播点(make_task_change_sink),不新开轮询:任务
    start/done/error 时该角色的 busy/idle 已经变了,顺势推一行,前端工位区免刷新。
    """
    return await _broadcast(app, {"type": WS_TYPE_ROLE_PRESENCE, "payload": row})


def roles_for_presence(app: Any) -> list:
    """工位区的"角色花名册"快照:[{"role_id","display","domain_id"}],小卡(l0)恒在第一行。

    display = RoleView.display_name()(花名(职务));domain_id = 该角色所属域(active 域
    member_query 解析,首个命中;不在任何域 → "")。全部只读,取不到就少一截,不猜不崩。
    """
    from karvyloop.console.tasks import KARVY_ROLE_ID
    rows: list = [{"role_id": KARVY_ROLE_ID, "display": "小卡", "domain_id": "l0"}]
    rid_domain: dict = {}
    dom_reg = getattr(app.state, "domain_registry", None)
    if dom_reg is not None:
        try:
            for d in dom_reg.list_active():
                for m in dom_reg.resolve_members(d.id):
                    if getattr(m, "role", "") == "user":
                        continue
                    rid = getattr(m, "agent_id", "") or getattr(m, "role", "")
                    if rid and rid not in rid_domain:
                        rid_domain[rid] = d.id
        except Exception as e:
            logger.debug(f"[task_events] presence 域成员解析失败(domain_id 留空): {e}")
    role_reg = getattr(app.state, "role_registry", None)
    if role_reg is not None:
        try:
            for v in role_reg.list_all():
                rows.append({
                    "role_id": v.id,
                    "display": v.display_name() if hasattr(v, "display_name") else v.id,
                    "domain_id": rid_domain.get(v.id, ""),
                })
        except Exception as e:
            logger.debug(f"[task_events] presence 角色库读取失败(只剩小卡行): {e}")
    return rows


def presence_row_for_task(app: Any, task: dict) -> Any:
    """任务 dict → 它归属角色的**单行** presence(WS 推送用);归不了属(group/未知)→ None。

    聚合走 tasks.aggregate_presence 同一套纯函数 —— API 快照与 WS 增量永远一个口径。
    """
    from karvyloop.console.tasks import aggregate_presence, match_task_role
    roles = roles_for_presence(app)
    role_ids = {r["role_id"] for r in roles}
    display_to_rid = {r["display"]: r["role_id"] for r in roles if r.get("display")}
    rid = match_task_role(task, role_ids, display_to_rid)
    if rid is None:
        return None
    task_reg = getattr(app.state, "task_registry", None)
    try:
        tasks = task_reg.list() if task_reg is not None else [task]
    except Exception:
        tasks = [task]
    rows = aggregate_presence([r for r in roles if r["role_id"] == rid], tasks)
    return rows[0] if rows else None


# fire-and-forget 广播任务的强引用集(docs/87 §五):CPython 只对 create_task 持**弱**引用,
# 不存进任何容器 → task 可能被 GC 中途回收 + 吞掉异常。存进模块级 set + done-callback 取回
# .exception() 记日志、完成即 discard(照仓内正确范式 silence._track_task / decision_wire._decision_tasks)。
# 这些都是 WS 广播协程(task_status/system_error/role_presence),失败只记日志、**不再**升 system_error
# —— 否则 system_error 广播自身失败会递归升卡。
_pending_tasks: set = set()


def _schedule(coro_factory: Callable[[], Coroutine]) -> None:
    """在当前事件循环上排一个广播协程;无 loop(同步上下文)→ 静默跳过。

    finish/start 在 async handler 里调(loop 线程上),有 loop → 正常排;
    启动期 reload(同步、无 client)→ RuntimeError → 跳过,不报错。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(coro_factory())
    _pending_tasks.add(task)

    def _done(t: Any) -> None:
        _pending_tasks.discard(t)
        try:
            exc = t.exception()
        except BaseException:   # CancelledError 等(关停):无结果可报,别穿透吵日志
            return
        if exc is not None:
            logger.warning(f"[task_events] 广播后台任务异常(不拖垮任务流): {exc}")

    task.add_done_callback(_done)


def schedule_task_broadcast(app: Any, task: dict) -> None:
    """同步钩子:供 `TaskRegistry.on_change` 调(finish/start 自动推状态)。

    这让"状态即事件"成为**结构性保证**——任何调 start/finish 的代码路径
    (含未来新增的)都自动推送,不会重新引入"靠人轮询"的静默 bug(§0.7)。
    """
    _schedule(lambda: broadcast_task_status(app, task))


def schedule_system_error(app: Any, source: str, message: str) -> None:
    """同步钩子:后台任务 except 里调,把失败 push 给 UI(灭静默死角)。"""
    _schedule(lambda: broadcast_system_error(app, source, message))


def make_task_change_sink(app: Any, trace: Any) -> Callable[[dict], None]:
    """`TaskRegistry.on_change` 的标准接线:WS 推送 + **任务终态落 Trace**(P3-b)。

    跑评分离:Trace 是所有评价的唯一数据源(镜像 [[trace-is-universal-eval-source]])。
    此前任务结果只进 tasks.json(看板私账),异步评价器(trace_eval/lessons)从 Trace 读,
    永远看不见任务级成败 —— 看板与评价飞轮是两本账。本 sink 在同一个 on_change 接缝上
    把 done/error 终态补进 Trace(kind="task_run");registry 保持纯粹,不 import console。
    trace=None(--no-llm / 无 main_loop)→ 只推送,不记账(0 回归)。
    """
    def _sink(task: dict) -> None:
        schedule_task_broadcast(app, task)
        # P1.5 工位区:同一接缝顺势推该角色单行 presence(不新开轮询;推不出不拖任务流)。
        # 只在 start/done/error 推(契约口径)—— 中途 step/blocked 不改 busy/idle,不白推。
        try:
            _is_start = (task.get("last_event") or {}).get("kind") == "start"
            if task.get("status") in ("done", "error") or _is_start:
                row = presence_row_for_task(app, task)
                if row is not None:
                    _schedule(lambda: broadcast_role_presence(app, row))
        except Exception as e:
            logger.debug(f"[task_events] role_presence 推送失败(工位区少一次增量,不致命): {e}")
        try:
            if trace is not None and task.get("status") in ("done", "error"):
                from karvyloop.cognition.trace import TraceEntry
                trace.append(TraceEntry(
                    task_id=str(task.get("trace_id") or task.get("id") or ""),
                    kind="task_run",
                    payload={"registry_id": task.get("id"), "who": task.get("who", ""),
                             "intent": task.get("intent", ""), "status": task.get("status"),
                             "result": (task.get("result") or "")[:280],
                             "domain": task.get("domain_id", ""), "role": task.get("role", "")},
                    agent=task.get("who", ""), source="task_registry"))
        except Exception as e:
            # fail-loud(闭环审计断⑤):落 Trace 失败绝不拖垮任务流(评是慢侧的事),
            # 但必须可见 —— 否则评价飞轮对任务级成败**静默失明**(两本账病根)。
            logger.warning(f"[task_events] 任务终态落 Trace 失败"
                           f"(task_id={task.get('id', '?')},status={task.get('status', '?')},"
                           f"评价飞轮看不见这条): {e}")
    return _sink


__all__ = [
    "WS_TYPE_TASK_STATUS", "WS_TYPE_TASK_STEP", "WS_TYPE_SYSTEM_ERROR",
    "WS_TYPE_ROLE_PRESENCE",
    "broadcast_task_status", "broadcast_task_step", "broadcast_system_error",
    "broadcast_role_presence", "roles_for_presence", "presence_row_for_task",
    "schedule_task_broadcast", "schedule_system_error", "make_task_change_sink",
]
