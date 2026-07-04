"""routes_atoms — /api/atoms* + /api/atom/* 端点(公共原子库:列/建/改/删 + 语义合并 suggest/apply)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import 可达。

护城河资产 → 合并须人拍板(suggest 只升 H2A 决策卡,apply 才真 rewire-before-delete)。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from karvyloop.llm.token_ledger import token_source as _token_src

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


class AtomCreateRequest(BaseModel):
    atom_id: str = Field(..., min_length=1, max_length=64)   # 名字(COMPOSITION-safe)
    kind: str = Field(default="task")                        # task / daemon
    prompt: str = Field(default="", max_length=8000)
    tools: list[str] = Field(default_factory=list)
    model: Optional[str] = Field(default=None, max_length=128)


def _atom_to_dict(a) -> dict[str, Any]:
    return {"id": a.id, "kind": a.kind, "prompt": a.prompt,
            "tools": list(a.tools), "model": a.model,
            "is_read_only": a.is_read_only,
            # §11.1 诚实标注:原子库列表也暴露工具真实性(顾问 vs 真能执行)
            "executable": getattr(a, "executable", True),
            "unresolved_tools": list(getattr(a, "unresolved_tools", []))}


@router.get("/atoms")
def api_atoms(request: Request) -> dict[str, Any]:
    """列公共原子库。"""
    reg = getattr(request.app.state, "atom_registry", None)
    if reg is None:
        return {"atoms": []}
    return {"atoms": [_atom_to_dict(a) for a in reg.list_all()]}


class AtomMergeRequest(BaseModel):
    canonical_id: str = Field(..., min_length=1, max_length=64)
    member_ids: list[str] = Field(default_factory=list, max_length=64)
    merged_purpose: str = Field(default="", max_length=400)
    merged_tools: list[str] = Field(default_factory=list, max_length=16)


@router.post("/atoms/consolidate/suggest")
async def api_atoms_consolidate_suggest(request: Request) -> dict[str, Any]:
    """原子语义合并·**建议**(docs/14 §11.2):LLM 找近重复原子簇,**只给建议不改任何东西**(dry-run)。
    护城河资产 → 合并须人拍板,这里是"提案"那一步;`apply` 才真改。"""
    app = request.app
    reg = getattr(app.state, "atom_registry", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if reg is None:
        return {"ok": False, "reason": "未接 atom_registry", "clusters": []}
    if gw is None:
        return {"ok": False, "reason": "未接 LLM(--no-llm?)无法语义聚类", "clusters": []}
    from karvyloop.atoms.consolidate import suggest_consolidation
    try:
        with _token_src("atom_consolidate"):
            clusters = await suggest_consolidation(reg.list_all(), gateway=gw,
                                                   model_ref=rk.get("model_ref", ""))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[atom consolidate] 建议失败: {e}")
        return {"ok": False, "reason": f"聚类失败: {e}", "clusters": []}
    # docs/14 §11.2 / docs/02 §15.5:合并**不静默** —— 每个近义簇落成 merge_atoms 决策卡进 H2A,
    # ACCEPT 才真 apply(rewire-before-delete)。把"建议"接进统一提案流(像 route/ops_fix),不再让
    # 前端拿裸 clusters 直接调 apply。
    proposal_ids: list[str] = []
    preg = getattr(app.state, "proposal_registry", None)
    if preg is not None:
        import time as _t
        from karvyloop.console.proposals import broadcast_proposal
        from karvyloop.karvy.proposal_registry import proposal_for_merge_atoms
        for c in clusters:
            try:
                card = proposal_for_merge_atoms(
                    canonical_id=c.get("canonical_id", ""), member_ids=c.get("member_ids", []),
                    merged_purpose=c.get("merged_purpose", ""), merged_tools=c.get("merged_tools", []),
                    reason=c.get("reason", ""), ts=_t.time())
                preg.register(card)
                await broadcast_proposal(app, card)
                proposal_ids.append(card.proposal_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[atom consolidate] 升合并卡失败: {e}")
    return {"ok": True, "clusters": clusters, "atom_total": len(reg.list_all()),
            "proposals": proposal_ids}


@router.post("/atoms/consolidate/apply")
def api_atoms_consolidate_apply(req: AtomMergeRequest, request: Request) -> dict[str, Any]:
    """原子语义合并·**兑现**(经 H2A,Hardy 拍过的一簇):rewire-before-delete —— 先改所有角色引用到
    规范原子,再删冗余,绝不留悬空引用。"""
    app = request.app
    areg = getattr(app.state, "atom_registry", None)
    rreg = getattr(app.state, "role_registry", None)
    if areg is None or rreg is None:
        return {"ok": False, "reason": "未接 atom_registry / role_registry"}
    from karvyloop.atoms.consolidate import apply_merge
    return apply_merge(req.canonical_id.strip(), req.member_ids,
                       merged_purpose=req.merged_purpose, merged_tools=req.merged_tools,
                       atom_registry=areg, role_registry=rreg)


@router.post("/atom/create")
def api_atom_create(req: AtomCreateRequest, request: Request) -> dict[str, Any]:
    """建一个原子入公共库(就地买糖也走这个)。"""
    reg = getattr(request.app.state, "atom_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 atom_registry"}
    try:
        a = reg.create(req.atom_id, req.kind, req.prompt,
                       tools=list(req.tools), model=req.model)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"建原子失败:{e}")
    return {"ok": True, "atom": _atom_to_dict(a)}


class AtomUpdateRequest(BaseModel):
    atom_id: str = Field(..., min_length=1, max_length=64)    # id 是引用键,不改;改名=删+建
    prompt: Optional[str] = Field(default=None, max_length=8000)   # None=不改
    kind: Optional[str] = Field(default=None)                # task/daemon;None=不改
    tools: Optional[list[str]] = Field(default=None)         # None=不改


@router.post("/atom/update")
def api_atom_update(req: AtomUpdateRequest, request: Request) -> dict[str, Any]:
    """编辑一个原子的 prompt/kind/tools(此前只能删了重建)。id 不可改;改 tools 重算 executable。"""
    reg = getattr(request.app.state, "atom_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 atom_registry"}
    try:
        a = reg.update(req.atom_id, prompt=req.prompt, kind=req.kind, tools=req.tools)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"改原子失败:{e}")
    if a is None:
        return {"ok": False, "reason": f"原子「{req.atom_id}」不存在"}
    return {"ok": True, "atom": _atom_to_dict(a)}


class AtomRemoveRequest(BaseModel):
    atom_id: str = Field(..., min_length=1, max_length=64)


@router.post("/atom/remove")
def api_atom_remove(req: AtomRemoveRequest, request: Request) -> dict[str, Any]:
    reg = getattr(request.app.state, "atom_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 atom_registry"}
    return {"ok": reg.remove(req.atom_id)}
