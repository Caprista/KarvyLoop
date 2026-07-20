"""routes_workflow — workflow 逃生门端点(续 / 丢 / 待拍板清单)+ 通用停止(docs/90 刀3a)。

从 routes.py **carve 出来给 routes.py 头寸**(顶破 2000 行红线就拆,不放宽——2026-07-11
激活外部 runtime 把 routes.py 顶到 2004,把这组自包含端点搬出来)。自带 APIRouter,由
app.py include_router;**端点路径不变**(前端契约不动),纯搬移零逻辑改动。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .workflow_engine import (
    _mark_task_cancelled,
    _signal_executor_abort,
    _workflow_run_store,
    discard_workflow,
    resume_one_workflow,
)

router = APIRouter(prefix="/api")


@router.get("/workflow/pending_resume")
def api_workflow_pending_resume(request: Request) -> dict[str, Any]:
    """重启后**挂起待拍板**的中断 workflow 清单(逃生门:不自动复活,让人续/丢)。"""
    pend = getattr(request.app.state, "pending_resume", None) or []
    return {"pending": list(pend)}


class WorkflowResumeRequest(BaseModel):
    run_id: str = Field(..., min_length=1, max_length=64)


@router.post("/workflow/resume")
async def api_workflow_resume(req: WorkflowResumeRequest, request: Request) -> dict[str, Any]:
    """人显式选择"续跑"一条中断的 workflow(已完成步秒命中缓存、只续剩余)。"""
    return await resume_one_workflow(request.app, req.run_id)


@router.post("/workflow/discard")
async def api_workflow_discard(req: WorkflowResumeRequest, request: Request) -> dict[str, Any]:
    """人显式选择"丢弃"一条中断的 workflow(标死,不复活)。"""
    return discard_workflow(request.app, req.run_id)


class TaskCancelRequest(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=64)


@router.post("/task/cancel")
async def api_task_cancel(req: TaskCancelRequest, request: Request) -> dict[str, Any]:
    """通用停止(docs/90 刀3a:停止控件贴每条活任务——火灾键,任何 running 卡的 ⏹ 都打这里)。

    做三件事:① 置协作式中止旗(workflow/roundtable 引擎每步/每轮查它,统一走既有旗);
    ② 该 task 若有 workflow durable run → 同 /workflow/cancel 标 cancelled;③ 经 running-run
    注册表拉响活 executor 的 abort_requested(单任务 drive / pursuit / schedule / proposal /
    workflow 步内,下一轮循环边界协作式停,不杀进程)。终态由产生点既有 finish 落账
    (ABORTED_* / cancelled 语义,不新造终态);task_status 走既有 WS push 刷新前端。

    fail-loud:task_id 不存在 → 404(不静默 200)。停止不设确认弹窗(前端契约:火灾键秒达)。
    """
    app = request.app
    task_reg = getattr(app.state, "task_registry", None)
    rec = task_reg.get(req.task_id) if task_reg is not None else None
    if rec is None:
        raise HTTPException(status_code=404, detail="no_such_task")
    if rec.get("status") != "running":
        # 已终态:不装"已停止"(它本来就停了)。前端拿 ok:false 刷新即可。
        return {"ok": False, "reason": "not_running", "status": rec.get("status", "")}
    _mark_task_cancelled(app, req.task_id)                  # ① 协作旗
    run = _workflow_run_store(app).find_by_task(req.task_id)
    run_cancelled = bool(run and _workflow_run_store(app).cancel(run.get("run_id", "")))  # ②
    aborted = _signal_executor_abort(req.task_id)           # ③ 拉响 executor
    try:
        task_reg.add_event(req.task_id, "cancelling")       # 时间线留痕 + WS push(别的端同步可见)
    except Exception:
        pass
    return {"ok": True, "task_id": req.task_id, "cancelled": True,
            "run_cancelled": run_cancelled, "abort_signalled": aborted}
