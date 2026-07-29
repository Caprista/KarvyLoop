"""drive_turn_accounting — drive 前后的对话落账(bug:执行中对话归属竞态)。

抽出 ws._handle_intent_ws 与 routes.api_intent 两个 drive 入口**一模一样**的挂起轮落账逻辑
(此前两处各抄一份):
- `begin`:drive **之前**立即落一条挂起轮 + 把任务绑到**捕获到的**对话(刷新读得到、任务不飘小卡)。
- `finalize`:drive **之后**就地回填(成功填 text / error 填错),回填任务 trace_id;不叠第二条。

热路径纪律:任何异常都吞掉、退回旧行为(begin 失败→handle=None→由 finalize 走旧 record_turn),
**绝不让落账把发消息本身弄失败**。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def begin_drive_turn(mgr, task_reg, task_id, *, intent: str,
                     attachments: Any = None, tag: str = "drive") -> Optional[object]:
    """drive 前:落挂起轮 + 立即 set_conversation。返回 TurnHandle;begin 失败→None(退回旧行为)。"""
    if mgr is None:
        return None
    try:
        conv = mgr.current()   # 捕获 intent 收到那刻的对话(不靠之后可能漂移的 current)
        handle = mgr.begin_turn(
            intent, task_id="", brain="slow",
            data=({"attachments": attachments} if attachments else None), conv=conv)
        if task_reg is not None and task_id and handle is not None:
            try:
                task_reg.set_conversation(task_id, handle.conv.id)
            except Exception:
                pass
        return handle
    except Exception:
        logger.debug(f"[{tag}] begin_turn 挂起落账失败(退回旧行为:完成才记)", exc_info=True)
        return None


def finalize_drive_turn(mgr, handle, task_reg, task_id, *, intent: str,
                        outcome, attachments: Any = None) -> None:
    """drive 后:就地回填挂起轮(成功/失败)+ 回填任务 trace_id。

    handle 有 → finalize(不叠第二条);handle 为 None(begin 曾失败降级)→ 退回旧 record_turn
    (仅成功轮记)。任务卡的 conversation 在 begin 已绑,这里补 trace_id(去聊天定位到那一轮)。
    """
    if mgr is None:
        return
    try:
        if handle is not None:
            mgr.finalize_turn(
                handle, outcome.text or "", error=(outcome.error or ""),
                task_id=(outcome.task_id or ""), brain=outcome.brain.value)
        elif not outcome.error:
            mgr.record_turn(
                intent, outcome.text or "", brain=outcome.brain.value,
                task_id=outcome.task_id,
                data=({"attachments": attachments} if attachments else None))
        if task_reg is not None and task_id:
            cid = (handle.conv.id if handle is not None
                   else (mgr.current().id if mgr.current() is not None else ""))
            try:
                task_reg.set_conversation(task_id, cid, trace_id=outcome.task_id or "")
            except Exception:
                pass
    except Exception:
        pass


def fail_drive_turn(mgr, handle, error: str) -> None:
    """drive 抛异常:挂起轮就地回填成失败态,别留僵尸 pending 永远转圈。fail-soft。"""
    if mgr is None or handle is None:
        return
    try:
        mgr.finalize_turn(handle, "", error=error)
    except Exception:
        pass


def stub_no_main_loop(intent: str, app=None):
    """main_loop=None 时返 DriveOutcome stub(error,不 500)。两个 drive 入口(routes/ws)共用。

    UX 诚实修:error 文案不再一句误导的"请先 karvyloop init"(用户明明已 init 过),
    而是按缺席真因四分(no_llm / 构造失败真原因 / 需 init / 需补 Key)。app 传得进就报真话,
    传不进(老调用)才退回旧兜底。"""
    from karvyloop.runtime.main_loop import Brain
    from karvyloop.workbench.main_loop_bridge import DriveOutcome
    err = "MainLoop 未注入 — 请先 karvyloop init"
    if app is not None:
        try:
            from karvyloop.gateway.readiness import main_loop_absence
            a = main_loop_absence(app)
            if a.get("text"):
                err = a["text"]
        except Exception:
            pass
    return DriveOutcome(
        intent=intent, brain=Brain.SLOW, text="", skill_name="",
        fast_brain_hit=False, crystallized=False, error=err,
    )


__all__ = ["begin_drive_turn", "finalize_drive_turn", "fail_drive_turn", "stub_no_main_loop"]
