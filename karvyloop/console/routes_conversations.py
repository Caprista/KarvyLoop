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
        "closed_at": getattr(m, "closed_at", None),   # 沉淀关闭;None = 开着(欠账)
    }


@router.get("/conversations")
def api_conversations(request: Request) -> dict[str, Any]:
    """历史对话列表(0.1.0 刚需,按 last_active 倒序;K4 只读)。
    `unsettled` = 开着(未沉淀关闭)的会话数 —— 你还没沉下心沉淀的欠账(docs/66 §E)。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"conversations": [], "current_id": None, "unsettled": 0}
    metas = mgr.list_conversations()
    cur = mgr.current()
    return {
        "conversations": [_conv_meta_to_dict(m) for m in metas],
        "current_id": cur.id if cur is not None else None,
        # 欠账 = 开着**且聊过**的(空会话没有料,不算"没沉下心";刚顺势开的新会话不背锅)
        "unsettled": sum(1 for m in metas
                         if getattr(m, "closed_at", None) is None and m.turn_count > 0),
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
        "closed_at": conv.closed_at,
        "turns": [
            {"user_intent": t.user_intent, "agent_response": t.agent_response,
             "brain": t.brain, "task_id": t.task_id, "data": t.data}
            for t in conv.turns
        ],
        "roundtable_pending": _roundtable_pending(request.app, conv.id),
    }


# ---- docs/66 §F:收敛 → 分层确认 → 只沉确认的 → 会话关闭(=欠账清一笔)。
#      整套生命周期**只活在「聊知识」线**(Hardy:全局一收敛把工作会话关了=逻辑错乱)----

def _require_knowledge_line(mgr) -> Optional[str]:
    """收敛/沉淀只在知识线可用;其他线返回拒绝理由(工作会话永远不会被它关掉)。"""
    from karvyloop.cognition.knowledge_chat import is_knowledge_peer
    peer = mgr.current_peer() if mgr is not None else None
    if not is_knowledge_peer(peer):
        return "收敛/沉淀只在「聊知识」模式可用 —— 从知识库进入(工作对话不会被关闭)"
    return None


@router.post("/conversation/converge")
async def api_conversation_converge(request: Request) -> dict[str, Any]:
    """收敛当前会话:对话 → 分层认知候选(经历/推理/原则/校正/涌现)→ 沉淀确认卡。
    **不写库**——只产候选;你逐条确认后走 /conversation/sediment 才沉(理解关)。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    deny = _require_knowledge_line(mgr)
    if deny:
        return {"ok": False, "reason": deny}
    conv = mgr.current() if mgr is not None else None
    if conv is None:
        return {"ok": False, "reason": "无当前会话"}
    if not conv.turns:
        return {"ok": False, "reason": "这段对话还没聊呢"}
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return {"ok": False, "reason": "无 gateway,无法收敛(--no-llm?)"}
    from karvyloop.cognition.converge import build_sediment_card, converge_and_propose
    cands = await converge_and_propose(conv.turns, gateway=gw, model_ref=rk.get("model_ref", ""),
                                       trace=_conv_trace(request.app))
    card = build_sediment_card(cands, conversation_ref=conv.id)
    return {"ok": True, "card": card}


class SedimentRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=64)
    # 卡上的候选(收敛响应原样带回;edit 过的 content 在 decisions 里)
    items: list[dict] = Field(default_factory=list, max_length=64)
    # {candidate_id: {"action": "accept"|"edit"|"drop", "content": 改后文本}}
    decisions: dict[str, dict] = Field(default_factory=dict)


@router.post("/conversation/sediment")
async def api_conversation_sediment(req: SedimentRequest, request: Request) -> dict[str, Any]:
    """沉淀你确认的候选(user_explicit)→ **关闭会话**(欠账清一笔)。
    不在 decisions 里的候选 = 未确认 = 不沉;盲拍(全收零改零删)进反投降闸,越深计分越重。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    deny = _require_knowledge_line(mgr)
    if deny:
        return {"ok": False, "reason": deny}
    conv = mgr.current() if mgr is not None else None
    if conv is None or conv.id != req.conversation_id:
        raise HTTPException(status_code=409, detail="不是当前会话(先 resume 再沉淀)")
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"ok": False, "reason": "memory 未接(--no-llm?)"}
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    from karvyloop.cognition.converge import (
        LAYERS, CognitionCandidate, SedimentTracker, apply_confirmation, sediment_confirmed,
    )
    # 重建候选(客户端带回;layer 白名单校验,坏项丢弃=宁空勿毒)
    cands = []
    for it in req.items:
        if not isinstance(it, dict):
            continue
        content = (it.get("content") or "").strip() if isinstance(it.get("content"), str) else ""
        layer = it.get("layer")
        if not content or layer not in LAYERS:
            continue
        wh = it.get("when_hint")
        cands.append(CognitionCandidate(
            content=content, layer=layer, why=(it.get("why") or "")[:300],
            when_hint=wh if isinstance(wh, str) and wh.strip() else None))
    accepted, engaged = apply_confirmation(cands, req.decisions)
    res = {"written": 0, "extends": [], "ids": []}
    if accepted:
        res = await sediment_confirmed(
            accepted, mem=mem, gateway=rk.get("gateway"), model_ref=rk.get("model_ref", ""),
            trace=_conv_trace(request.app), learned_via=f"conversation:{conv.id}")
    # 反投降闸(越深盲拍计分越重);服务级单例,重启清零可接受(闸是行为侦测不是账本)
    tracker = getattr(request.app.state, "sediment_tracker", None)
    if tracker is None:
        tracker = SedimentTracker()
        request.app.state.sediment_tracker = tracker
    max_depth = max((c.depth for c in accepted), default=1)
    tracker.record(accepted_any=bool(accepted), engaged=engaged, max_depth=max_depth)
    # 沉淀了才关(docs/66 §E);一条没沉(全删/全没确认)也算"处理过了"→ 同样关,欠账清
    closed_at = mgr.close_conversation(conv.id, reason="sedimented" if res["written"] else "settled_empty")
    metas = mgr.list_conversations()
    return {
        "ok": True, "written": res["written"], "closed_at": closed_at,
        "unsettled": sum(1 for m in metas
                         if getattr(m, "closed_at", None) is None and m.turn_count > 0),
        "needs_recheck": tracker.needs_recheck(),
        "new_conversation_id": (mgr.current().id if mgr.current() is not None else None),
    }


def _conv_trace(app: Any):
    """Trace 底座句柄(沉淀审计落这);--no-llm/无 main_loop → None(照跑)。"""
    return getattr(getattr(app.state, "main_loop", None), "trace", None)


@router.get("/conversation/knowledge_debt")
def api_knowledge_debt(request: Request) -> dict[str, Any]:
    """知识线欠账:开着**且聊过**的知识会话数(docs/66 §E/§F,给知识库面板入口显示)。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"unsettled": 0}
    from karvyloop.cognition.knowledge_chat import knowledge_peer
    try:
        return {"unsettled": mgr.open_count(knowledge_peer())}
    except Exception:
        return {"unsettled": 0}
