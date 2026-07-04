"""routes_peers — /api/peers + /api/peer/switch 端点(可对话对象:私聊小卡 + 各业务域角色)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import 可达。

peers 列表用 _is_line_hidden(家在 routes_lines)→ 直接 import,保单一真源。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .roundtable_engine import _roundtable_pending
from .routes_lines import _is_line_hidden, _set_line_hidden

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ---- /api/peers (9.2b:可对话对象 — 私聊小卡 + 各业务域角色) ----

@router.get("/peers")
def api_peers(request: Request) -> dict[str, Any]:
    """列可对话对象(场+角色):私聊小卡(l0)+ 各 active 业务域 resolve_members 的角色。

    K4 只读(读 registry,不改)。无 registry → 仅私聊小卡。
    """
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN

    peers: list[dict[str, Any]] = [{
        "domain_id": KARVY_WORLD_DOMAIN, "domain_name": "karvy world(私聊)",
        "role": "observer", "agent_id": "karvy",
        "label": "🏠 私聊小卡", "is_private": True,
    }, {
        # ch4 KarvyChat:Karvy World 大群(小卡协调你所有 Agent)。is_world:前端用它出本地化标题。
        "domain_id": KARVY_WORLD_DOMAIN, "domain_name": "Karvy World",
        "role": "group", "agent_id": "",
        "label": "👥 Karvy World 大群", "is_group": True, "is_private": False, "is_world": True,
    }]
    reg = getattr(request.app.state, "domain_registry", None)
    if reg is not None:
        try:
            for d in reg.list_active():
                # 域群:小卡协调该域全体成员
                peers.append({
                    "domain_id": d.id, "domain_name": d.name,
                    "role": "group", "agent_id": "",
                    "label": f"👥 {d.name} 域群", "is_group": True, "is_private": False,
                })
                for addr in reg.resolve_members(d.id):
                    if addr.role == "user":
                        continue  # 用户自己不是"对话对象"
                    peers.append({
                        "domain_id": d.id, "domain_name": d.name,
                        "role": addr.role, "agent_id": addr.agent_id,
                        "label": f"🏢 {d.name} / {addr.role}"
                                 + (f"·{addr.agent_id}" if addr.agent_id else ""),
                        "is_private": False,
                    })
        except Exception as e:
            logger.warning(f"api_peers 列业务域成员失败(仅返私聊): {e}")
    # 每个对象标注"最近沟通时间"(供左栏:私聊/群聊各自按最近沟通排序;
    # 没私聊过的 agent 前端隐藏,没沟通过的群聊仍显示)。无对话编排器 → 全 None。
    last_active: dict[str, float] = {}
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is not None:
        try:
            # 跨**所有** peer 扫(不能只看当前场,否则别的 agent 永远无 last_active → 左栏不显)
            for m in mgr.all_conversation_metas():
                k = f"{m.peer.domain_id}|{m.peer.role}|{m.peer.agent_id or ''}"
                la = m.last_active_at or 0.0
                if la > last_active.get(k, 0.0):
                    last_active[k] = la
        except Exception as e:
            logger.warning(f"api_peers 标注最近沟通失败(降级无时序): {e}")
    for p in peers:
        k = f"{p['domain_id']}|{p['role']}|{p.get('agent_id') or ''}"
        p["last_active_at"] = last_active.get(k)   # None = 从没沟通过
    # 2f:X 掉的私聊从左栏隐藏(记录还在,重新切到它会自动恢复;小卡置顶不可隐藏)。
    # 群(结构性)不隐藏 —— UI 不给群 X,这里也不滤它们。
    peers = [p for p in peers
             if p.get("is_group") or not _is_line_hidden(request.app, p["domain_id"], p["role"],
                                                          p.get("agent_id") or "")]
    return {"peers": peers}


class PeerSwitchRequest(BaseModel):
    domain_id: str = Field(..., min_length=1, max_length=64)
    role: str = Field(..., min_length=1, max_length=64)
    agent_id: Optional[str] = Field(default=None, max_length=64)


@router.post("/peer/switch")
def api_peer_switch(req: PeerSwitchRequest, request: Request) -> dict[str, Any]:
    """切到某「场+角色」(CV-13:切场 = 独立上下文线)。返该线当前对话 + 历史轮。"""
    from karvyloop.domain.registry import Address

    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"ok": False, "reason": "未接对话编排器"}
    peer = Address(domain_id=req.domain_id, role=req.role, agent_id=req.agent_id)
    conv = mgr.set_peer(peer)
    # 2c:重开一条线(含从料里点追问)→ 自动恢复显示(把它从隐藏集移除),让卡重新回左栏
    _set_line_hidden(request.app, req.domain_id, req.role, req.agent_id or "", False)
    return {
        "ok": True,
        "domain_id": peer.domain_id, "role": peer.role, "agent_id": peer.agent_id,
        "conversation_id": conv.id, "turn_count": conv.turn_count,
        "turns": [
            {"user_intent": t.user_intent, "agent_response": t.agent_response,
             "brain": t.brain, "task_id": t.task_id, "data": t.data}
            for t in conv.turns
        ],
        "roundtable_pending": _roundtable_pending(request.app, conv.id),
    }
