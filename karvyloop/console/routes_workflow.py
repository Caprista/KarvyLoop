"""routes_workflow — workflow 逃生门端点(续 / 丢 / 待拍板清单)。

从 routes.py **carve 出来给 routes.py 头寸**(顶破 2000 行红线就拆,不放宽——2026-07-11
激活外部 runtime 把 routes.py 顶到 2004,把这组自包含端点搬出来)。自带 APIRouter,由
app.py include_router;**端点路径不变**(前端契约不动),纯搬移零逻辑改动。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .workflow_engine import discard_workflow, resume_one_workflow

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
