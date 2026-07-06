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


# ============================================================================
# docs/66 §F(Hardy 三次收敛):认知聊天**整个住在知识库模块里** —— 不是聊天模块的一个分类。
# /api/knowledge/*:按会话 id 操作知识线(l0/librarian)的会话,**完全不碰主聊天的当前会话**
# (结构上就不可能误关工作对话)。生命周期:聊 → 收敛 → 逐条确认 → 只沉确认的 → 关会话(欠账清一笔)。
# ============================================================================

def _kstore(mgr):
    """知识会话直取存储(不经 mgr 的"当前会话"状态机 —— 知识模块与主聊天零耦合)。"""
    return getattr(mgr, "_store", None) if mgr is not None else None


def _kload(mgr, session_id: str):
    from karvyloop.cognition.knowledge_chat import knowledge_peer
    store = _kstore(mgr)
    if store is None or not session_id:
        return None
    return store.load(knowledge_peer(), session_id)


def _kdebt(mgr) -> tuple[int, list]:
    """(欠账数, metas):开着**且聊过**的知识会话(空会话没有料不算)。"""
    from karvyloop.cognition.knowledge_chat import knowledge_peer
    metas = [m for m in (mgr.list_conversations(knowledge_peer()) if mgr is not None else [])
             if m.closed_at is None and m.turn_count > 0]
    return len(metas), metas


class KnowledgeChatRequest(BaseModel):
    session_id: str = Field(default="", max_length=64)   # 空 = 新开一段(临时存放区)
    message: str = Field(..., min_length=1, max_length=20000)


@router.post("/knowledge/chat")
async def api_knowledge_chat(req: KnowledgeChatRequest, request: Request) -> dict[str, Any]:
    """知识馆员聊天(在知识库面板里):丢料/讨论 → 馆员消化回话(人设+知识库召回)。
    session_id 空 = 新开一段;每段就是一份待沉淀的料(docs/66 §E 临时存放区)。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    store = _kstore(mgr)
    if store is None:
        return {"ok": False, "reason": "未接对话编排器"}
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return {"ok": False, "reason": "无 gateway(--no-llm?)"}
    from karvyloop.cognition.knowledge_chat import KNOWLEDGE_PERSONA, knowledge_peer
    peer = knowledge_peer()
    conv = store.load(peer, req.session_id) if req.session_id else None
    if conv is None:
        conv = store.new(peer)
    # 丢即读(Hardy:"我给你资料你本身就要读的呀"):消息里有链接 → 服务端先抓正文喂馆员;
    # 抓不到 → 明说没读到,**严禁**凭 URL 字面瞎猜(真机实拍:馆员把 KarvyLoop 猜成印度金融集团)。
    from .routes_memory import _extract_url, _fetch_url
    model_message = req.message
    _url = _extract_url(req.message)
    if _url:
        fetched = await _fetch_url(_url)
        if fetched:
            model_message = (req.message + "\n\n【链接正文(服务端已抓取,这就是你读到的材料)】\n" + fetched)
        else:
            model_message = (req.message + "\n\n【链接抓取失败:没拿到内容。老实告诉用户你没读到,"
                             "问 ta 贴正文进来;绝不凭 URL 字面猜内容】")
    # 系统提示 = 馆员人设 + 知识库召回(馆员手边有你的库,对照新旧的底气)
    sys_parts = [KNOWLEDGE_PERSONA]
    mem = getattr(request.app.state, "memory", None)
    if mem is not None:
        try:
            block = mem.recall_block(req.message, scope="personal", limit=8)
            if block:
                sys_parts.append(block)
        except Exception:
            pass
    msgs: list[dict] = []
    for turn in conv.turns[-12:]:
        if turn.user_intent:
            msgs.append({"role": "user", "content": turn.user_intent})
        if turn.agent_response:
            msgs.append({"role": "assistant", "content": turn.agent_response})
    msgs.append({"role": "user", "content": model_message})   # 抓到的正文只喂模型;落盘存原话
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.llm.token_ledger import token_source
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=rk.get("model_ref") or None))
    except Exception:
        ref = rk.get("model_ref", "")
    out = ""
    try:
        with token_source("knowledge_chat"):
            async for ev in gw.complete(msgs, [], ref,
                                        system=SystemPrompt(static=["\n\n".join(sys_parts)])):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception as e:
        return {"ok": False, "reason": f"馆员没回上话: {e}"}
    reply = out.strip()
    if not reply:
        return {"ok": False, "reason": "馆员没回上话(空回复)"}
    from karvyloop.cognition.conversation import Turn
    store.append_turn(conv, Turn(user_intent=req.message, agent_response=reply, brain="slow"))
    return {"ok": True, "session_id": conv.id, "reply": reply, "turn_count": conv.turn_count}


class KnowledgeConvergeRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)


@router.post("/knowledge/converge")
async def api_knowledge_converge(req: KnowledgeConvergeRequest, request: Request) -> dict[str, Any]:
    """收敛指定知识会话:对话 → 分层认知候选(经历/推理/原则/校正/涌现)→ 沉淀确认卡。
    **不写库**——只产候选;你逐条确认后走 /knowledge/sediment 才沉(理解关)。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    conv = _kload(mgr, req.session_id)
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


@router.post("/knowledge/sediment")
async def api_knowledge_sediment(req: SedimentRequest, request: Request) -> dict[str, Any]:
    """沉淀你确认的候选(user_explicit)→ **关闭该知识会话**(欠账清一笔)。
    不在 decisions 里的候选 = 未确认 = 不沉;盲拍(全收零改零删)进反投降闸,越深计分越重。
    只按 id 操作知识线会话 —— 主聊天的当前会话动都不动。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    conv = _kload(mgr, req.conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="知识会话不存在")
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
    store = _kstore(mgr)
    closed_at = store.close(conv, reason="sedimented" if res["written"] else "settled_empty")
    n, _metas = _kdebt(mgr)
    return {
        "ok": True, "written": res["written"], "closed_at": closed_at,
        "unsettled": n, "needs_recheck": tracker.needs_recheck(),
    }


def _conv_trace(app: Any):
    """Trace 底座句柄(沉淀审计落这);--no-llm/无 main_loop → None(照跑)。"""
    return getattr(getattr(app.state, "main_loop", None), "trace", None)


class KnowledgeDiscardRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)


@router.post("/knowledge/discard")
def api_knowledge_discard(req: KnowledgeDiscardRequest, request: Request) -> dict[str, Any]:
    """X 掉一段知识会话(Hardy:左栏可关,关=这段没沉淀的就丢了)。
    失效不删式关闭(reason=discarded,转录留档可审计);幂等;不碰主聊天。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    conv = _kload(mgr, req.session_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="知识会话不存在")
    store = _kstore(mgr)
    closed_at = store.close(conv, reason="discarded")
    n, _metas = _kdebt(mgr)
    return {"ok": True, "closed_at": closed_at, "unsettled": n}


@router.get("/knowledge/session")
def api_knowledge_session(id: str, request: Request) -> dict[str, Any]:
    """读一段知识会话的完整轮记录(面板点「待处理知识」行续聊时装历史)。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    conv = _kload(mgr, id)
    if conv is None:
        raise HTTPException(status_code=404, detail="知识会话不存在")
    return {"id": conv.id, "closed_at": conv.closed_at,
            "turns": [{"user_intent": t.user_intent, "agent_response": t.agent_response}
                      for t in conv.turns]}


@router.get("/knowledge/debt")
def api_knowledge_debt(request: Request) -> dict[str, Any]:
    """知识欠账(docs/66 §E/§F):开着**且聊过**的知识会话 —— 「待处理知识」列表
    (Hardy:欠账要一眼看见的**列表**,每段一行;都住在知识库面板里)。
    每段带首句摘要(没起名的会话拿第一句用户话当脸)。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"unsettled": 0, "sessions": []}
    from karvyloop.cognition.knowledge_chat import knowledge_peer
    try:
        n, metas = _kdebt(mgr)
        peer = knowledge_peer()
        store = _kstore(mgr)
        sessions = []
        for m in metas:
            snippet = (m.title or "").strip()
            if not snippet and store is not None:
                try:
                    conv = store.load(peer, m.id)
                    first = next((t.user_intent for t in (conv.turns if conv else []) if t.user_intent), "")
                    snippet = first.strip().replace("\n", " ")[:42]
                except Exception:
                    snippet = ""
            sessions.append({"id": m.id, "snippet": snippet, "turn_count": m.turn_count,
                             "last_active_at": m.last_active_at})
        sessions.sort(key=lambda s: s["last_active_at"], reverse=True)
        return {"unsettled": n, "sessions": sessions}
    except Exception:
        return {"unsettled": 0, "sessions": []}
