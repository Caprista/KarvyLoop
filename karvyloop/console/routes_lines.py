"""routes_lines — /api/line* + /api/lines 端点(左栏会话线:隐藏/列出/打开)(P2-② 纯搬移)。

覆盖:/api/line/hide、/api/lines、/api/line/open、/api/line/open_by_conv。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import(_is_line_hidden 等)可达。

Hardy 语义:X 只是从左栏隐藏这条会话线,**内容不删**;还能从流入的料点"追问"重开。
私聊小卡(l0/observer/karvy)永不可隐藏。从 routes.py 逐字搬移,零逻辑改动。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ---- 2c:左栏"X 掉" = 隐藏不删 ----
# Hardy 语义:X 只是从左栏隐藏这条会话线,**内容不删**;还能从流入的料点"追问"重开(重开即重新显示)。
# 持久化一组被隐藏的 line key("域|角色|agent");私聊小卡(l0/observer/karvy)永不可隐藏。

def _line_key(domain_id: str, role: str, agent_id: str = "") -> str:
    return f"{domain_id or ''}|{role or ''}|{agent_id or ''}"


def _is_karvy_private_line(domain_id: str, role: str, agent_id: str) -> bool:
    """私聊小卡(永远置顶、不可 X)。"""
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
    return domain_id == KARVY_WORLD_DOMAIN and role == "observer" and (agent_id or "") in ("karvy", "")


def _hidden_lines(app) -> set:
    """被隐藏的 line key 集合。持久化(有 config_path 时)→ 重启仍隐藏;测试纯内存。"""
    st = getattr(app.state, "hidden_lines", None)
    if st is None:
        cfgp = getattr(app.state, "config_path", "") or ""
        st = set()
        if cfgp:
            import json
            import pathlib
            path = pathlib.Path(cfgp).parent / "hidden_lines.json"
            app.state._hidden_lines_path = path
            try:
                if path.exists():
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        st = set(str(x) for x in raw)
            except Exception:
                st = set()
        app.state.hidden_lines = st
    return st


def _persist_hidden_lines(app) -> None:
    path = getattr(app.state, "_hidden_lines_path", None)
    if path is None:
        return
    try:
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(_hidden_lines(app)), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.warning(f"[hidden] 隐藏态落盘失败: {e}")


def _is_line_hidden(app, domain_id: str, role: str, agent_id: str = "") -> bool:
    return _line_key(domain_id, role, agent_id) in _hidden_lines(app)


def _set_line_hidden(app, domain_id: str, role: str, agent_id: str, hidden: bool) -> bool:
    """隐藏/恢复一条线。私聊小卡不可隐藏。返回是否生效。"""
    if hidden and _is_karvy_private_line(domain_id, role, agent_id):
        return False
    s = _hidden_lines(app)
    key = _line_key(domain_id, role, agent_id)
    if hidden:
        s.add(key)
    else:
        s.discard(key)
    _persist_hidden_lines(app)
    return True


class LineHideRequest(BaseModel):
    domain_id: str = Field(..., min_length=1, max_length=64)
    role: str = Field(..., min_length=1, max_length=64)
    agent_id: str = Field(default="", max_length=64)
    hidden: bool = Field(default=True)   # True=X 掉隐藏;False=恢复显示


@router.post("/line/hide")
def api_line_hide(req: LineHideRequest, request: Request) -> dict[str, Any]:
    """2c:X 掉(隐藏)/恢复一条左栏会话线 —— 只动显示,**不删内容**。私聊小卡不可隐藏。"""
    ok = _set_line_hidden(request.app, req.domain_id, req.role, req.agent_id, req.hidden)
    if not ok:
        return {"ok": False, "reason": "pinned"}   # 小卡置顶,X 不动它
    return {"ok": True, "hidden": req.hidden}


def _line_origin_name(app, did: str) -> str:
    """发起群名(卡片副标题)。Karvy World 用品牌名;域用域名;兜底 id。"""
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
    if did == KARVY_WORLD_DOMAIN:
        return "Karvy World"
    reg = getattr(app.state, "domain_registry", None)
    d = reg.get(did) if reg is not None else None
    return getattr(d, "name", "") or did


@router.get("/lines")
def api_lines(request: Request) -> dict[str, Any]:
    """2d:左栏「工作流」「圆桌」两区的卡 —— 各自跑出来的独立会话线(主题 + 发起群)。已隐藏的过滤掉。

    工作流线:role=="workflow"(每次运行一条,peer 独立)。
    圆桌线:挂在群 peer 下、标题以 🎡 开头(按 conv_id 区分;隐藏键用 role="roundtable"+conv_id)。
    """
    app = request.app
    out: dict[str, Any] = {"workflows": [], "roundtables": []}
    mgr = getattr(app.state, "conversation_manager", None)
    if mgr is None:
        return out
    try:
        for m in mgr.all_conversation_metas():
            role = getattr(m.peer, "role", "")
            did = getattr(m.peer, "domain_id", "")
            aid = getattr(m.peer, "agent_id", "") or ""
            title = (m.title or "").strip()
            if role == "workflow":
                if _is_line_hidden(app, did, "workflow", aid):
                    continue
                out["workflows"].append({
                    "domain_id": did, "role": "workflow", "agent_id": aid,
                    "conversation_id": m.id, "title": title or "工作流",
                    "origin_group": _line_origin_name(app, did), "last_active_at": m.last_active_at})
            elif role == "group" and title.startswith("🎡"):
                if _is_line_hidden(app, did, "roundtable", m.id):
                    continue
                out["roundtables"].append({
                    "domain_id": did, "role": "roundtable", "agent_id": m.id,
                    "conversation_id": m.id, "title": title.lstrip("🎡 ").strip() or "圆桌",
                    "origin_group": _line_origin_name(app, did), "last_active_at": m.last_active_at})
    except Exception as e:
        logger.warning(f"[lines] 列工作流/圆桌线失败: {e}")
    out["workflows"].sort(key=lambda x: -(x.get("last_active_at") or 0))
    out["roundtables"].sort(key=lambda x: -(x.get("last_active_at") or 0))
    return out


class LineOpenRequest(BaseModel):
    role: str = Field(..., min_length=1, max_length=32)          # workflow / roundtable
    domain_id: str = Field(..., min_length=1, max_length=64)
    agent_id: str = Field(default="", max_length=64)
    conversation_id: str = Field(default="", max_length=64)


@router.post("/line/open")
def api_line_open(req: LineOpenRequest, request: Request) -> dict[str, Any]:
    """2e:打开一条工作流/圆桌线(点卡片 / 料里点追问都走这)。重开 = 切到该线 + 自动恢复显示。"""
    from karvyloop.domain.registry import Address
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"ok": False, "reason": "未接对话编排器"}
    if req.role == "workflow":
        peer = Address(domain_id=req.domain_id, role="workflow", agent_id=req.agent_id)
        conv = mgr.set_peer(peer)
        _set_line_hidden(request.app, req.domain_id, "workflow", req.agent_id, False)
        ret_peer = {"domain_id": req.domain_id, "role": "workflow", "agent_id": req.agent_id}
    elif req.role == "roundtable":
        # 圆桌线在群 peer 下:切到群 + resume 那条 conv(继续这场圆桌,追问免 @ —— nudge 已放行 🎡 线)
        gpeer = Address(domain_id=req.domain_id, role="group", agent_id="")
        mgr.set_peer(gpeer)
        conv = mgr.resume(gpeer, req.conversation_id) or mgr.current()
        _set_line_hidden(request.app, req.domain_id, "roundtable", req.conversation_id, False)
        ret_peer = {"domain_id": req.domain_id, "role": "group", "agent_id": ""}
    else:
        return {"ok": False, "reason": "bad role"}
    if conv is None:
        return {"ok": False, "reason": "not_found"}
    return {
        "ok": True, **ret_peer, "is_group": req.role == "roundtable",
        "conversation_id": conv.id, "turn_count": conv.turn_count,
        "turns": [{"user_intent": t.user_intent, "agent_response": t.agent_response,
                   "brain": t.brain, "task_id": t.task_id, "data": t.data} for t in conv.turns],
    }


class ConvOpenRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=64)


@router.post("/line/open_by_conv")
def api_line_open_by_conv(req: ConvOpenRequest, request: Request) -> dict[str, Any]:
    """2e:按 conversation_id 定位并打开它**真正所在的线**(料里点追问走这)。

    比"切群 + resume"稳:工作流线挂在 role=workflow 的独立 peer 下,在群 peer 上 resume 找不到
    (这就是"追问没上下文"的根)。这里按 id 在所有 peer 的 metas 里找到它的真 peer 再开。
    """
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"ok": False, "reason": "未接对话编排器"}
    target = None
    try:
        for m in mgr.all_conversation_metas():
            if m.id == req.conversation_id:
                target = m.peer
                break
    except Exception as e:
        logger.warning(f"[line] 定位对话失败: {e}")
    if target is None:
        return {"ok": False, "reason": "not_found"}
    mgr.set_peer(target)
    conv = mgr.resume(target, req.conversation_id) or mgr.current()
    if conv is None:
        return {"ok": False, "reason": "not_found"}
    role = getattr(target, "role", "")
    title = (conv.title or "")
    is_round = role == "group" and title.startswith("🎡")
    # 重开 → 自动恢复显示(把它从隐藏集移除)
    if role == "workflow":
        _set_line_hidden(request.app, target.domain_id, "workflow", target.agent_id or "", False)
    elif is_round:
        _set_line_hidden(request.app, target.domain_id, "roundtable", conv.id, False)
    return {
        "ok": True, "domain_id": target.domain_id, "role": role,
        "agent_id": target.agent_id or "", "is_group": role == "group",
        "is_run_line": role == "workflow" or is_round,
        "kind": "workflow" if role == "workflow" else ("roundtable" if is_round else ""),
        "title": title.lstrip("⚙🎡 ").strip(), "origin_group": _line_origin_name(request.app, target.domain_id),
        "conversation_id": conv.id, "turn_count": conv.turn_count,
        "turns": [{"user_intent": t.user_intent, "agent_response": t.agent_response,
                   "brain": t.brain, "task_id": t.task_id, "data": t.data} for t in conv.turns],
    }
