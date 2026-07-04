"""routes_conversations — /api/conversation* + /api/conversations 端点(对话:新建/历史/resume)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import 可达。

resume 用到 _roundtable_pending(家在 roundtable_engine)→ 直接 import,保单一真源。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .roundtable_engine import _roundtable_pending

router = APIRouter(prefix="/api")


# ---- /api/conversation/* (9.1d:对话 — ➕新对话 / 🕘历史 / resume) ----

def _conv_meta_to_dict(m) -> dict[str, Any]:
    return {
        "id": m.id, "title": m.title, "created_at": m.created_at,
        "last_active_at": m.last_active_at, "turn_count": m.turn_count,
        "domain_id": m.peer.domain_id, "peer_role": m.peer.role,
        "peer_agent_id": m.peer.agent_id,
    }


@router.get("/conversations")
def api_conversations(request: Request) -> dict[str, Any]:
    """历史对话列表(0.1.0 刚需,按 last_active 倒序;K4 只读)。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"conversations": [], "current_id": None}
    metas = mgr.list_conversations()
    cur = mgr.current()
    return {
        "conversations": [_conv_meta_to_dict(m) for m in metas],
        "current_id": cur.id if cur is not None else None,
    }


@router.post("/conversation/new")
def api_conversation_new(request: Request) -> dict[str, Any]:
    """开新对话(CV-2 唯一边界;旧对话摘要喂 Trace CV-4)。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"id": None, "reason": "未接对话编排器"}
    conv = mgr.new_conversation()
    return {"id": conv.id, "title": conv.title, "turn_count": conv.turn_count}


class ResumeRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=64)


@router.post("/conversation/resume")
def api_conversation_resume(req: ResumeRequest, request: Request) -> dict[str, Any]:
    """从历史 resume 一段对话(0.1.0 刚需)。找不到 → 404。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"id": None, "reason": "未接对话编排器"}
    # 9.2a:resume 需 (peer, id);0.1.0 console 在当前 peer 内 resume(场切换留 9.2b)
    peer = mgr.current_peer()
    conv = mgr.resume(peer, req.conversation_id) if peer is not None else None
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {
        "id": conv.id, "title": conv.title, "turn_count": conv.turn_count,
        "turns": [
            {"user_intent": t.user_intent, "agent_response": t.agent_response,
             "brain": t.brain, "task_id": t.task_id, "data": t.data}
            for t in conv.turns
        ],
        "roundtable_pending": _roundtable_pending(request.app, conv.id),
    }
