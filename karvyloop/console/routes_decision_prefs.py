"""routes_decision_prefs — /api/decision_prefs* 端点(决策偏好:列/撤回/删/确认/编辑 + 复利统计)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import 可达。

docs/02 §11:可见 = 你掌舵的前提;你随时能撤能改 = 不固化你 + H2A。撤回区别于删除:留可审计回执。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


class DecisionPrefOpRequest(BaseModel):
    op: str = Field(..., pattern="^(delete|confirm|edit|revoke)$")   # 删 / 确认 / 编辑 / 撤回(显式收回+留审计)
    content: str = Field(..., min_length=1, max_length=2000)  # 按内容定位(Belief 无 id)
    new_content: str = Field(default="", max_length=2000)     # edit 用


@router.get("/decision_prefs")
def api_decision_prefs(request: Request) -> dict[str, Any]:
    """列你的决策偏好(可见 = 你掌舵的前提)。docs/02 §11 P1 可编辑面。"""
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"prefs": []}
    from karvyloop.crystallize.decision_pref import is_decision_pref
    prefs: list = []
    for sc in ("personal", "domain"):
        for b in mem.index.all(sc):
            if not is_decision_pref(b):
                continue
            p = b.provenance
            prefs.append({
                "content": b.content, "kind": p.get("kind", "taste"),
                "strength": p.get("strength", 0.0), "status": p.get("status", "provisional"),
                "applies": p.get("applies", {}), "evidence_n": len(p.get("evidence", [])),
                "freshness_ts": b.freshness_ts,
            })
    prefs.sort(key=lambda x: x["strength"], reverse=True)
    return {"prefs": prefs}


def _clear_revocation(app: Any, content: str) -> None:
    """你又确认/写定一条偏好 → 解除它的撤回抑制墓碑(否则旧墓碑会压住它的加固)。"""
    rev = getattr(app.state, "decision_revocations", None)
    if rev is None or not content:
        return
    try:
        from karvyloop.crystallize.decision_pref import norm_content
        rev.clear(norm_content(content))
    except Exception:
        pass


@router.post("/decision_prefs/op")
def api_decision_pref_op(req: DecisionPrefOpRequest, request: Request) -> dict[str, Any]:
    """对一条决策偏好:撤回(revoke,主动收回+留审计)/ 删除 / 确认(升 confirmed)/ 编辑内容。
    你随时能撤能改 = 不固化你 + H2A。撤回区别于删除:留可审计回执、confirmed 的也能由你收回。"""
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"ok": False, "reason": "未接认知库"}
    from karvyloop.crystallize.decision_pref import (
        confirm_pref, find_decision_pref, rename_pref, revoke_pref,
    )
    beliefs = [b for sc in ("personal", "domain") for b in mem.index.all(sc)]
    target = find_decision_pref(beliefs, req.content)
    if target is None:
        return {"ok": False, "reason": "偏好不存在(可能已被你删/改)"}
    try:
        if req.op == "delete":
            mem.archive(target)
        elif req.op == "revoke":
            # 你**主动撤回**:从活库移除 + 记进决策流水(可审计回看"是我撤的")+ 打抑制墓碑
            # (冷却窗口内别自动结晶回来,让撤回有牙)。confirmed 的也能撤——'不固化你'凌驾'尊重确认'。
            from karvyloop.crystallize.decision_pref import norm_content
            revoked = revoke_pref(target, reason="用户在偏好面板主动撤回")
            rts = float(revoked.provenance.get("revoked_ts", 0.0)) or None
            mem.archive(target)
            dlog = getattr(request.app.state, "decision_log", None)
            if dlog is not None:
                dlog.record(decision="REVOKE", summary=f"撤回偏好:{revoked.content[:80]}",
                            kind=str(revoked.provenance.get("kind", "")),
                            reason="主动撤回(不固化你)")
            rev = getattr(request.app.state, "decision_revocations", None)
            if rev is None:
                from karvyloop.console.decision_log import RevocationStore
                rev = request.app.state.decision_revocations = RevocationStore()   # 无盘兜底
            rev.mark(norm_content(revoked.content), now=rts)
        elif req.op == "confirm":
            mem.archive(target)
            mem.write(confirm_pref(target))
            _clear_revocation(request.app, target.content)   # 你又确认了它 → 解除旧撤回抑制
        elif req.op == "edit":
            nc = (req.new_content or "").strip()
            if not nc:
                return {"ok": False, "reason": "新内容不能为空"}
            mem.archive(target)
            mem.write(rename_pref(target, nc))
            _clear_revocation(request.app, nc)               # 你重新写定这条 → 解除其撤回抑制
        return {"ok": True}
    except Exception as e:
        logger.warning(f"[decision_prefs] {req.op} 失败: {e}")
        return {"ok": False, "reason": str(e)}


@router.get("/decision_prefs/stats")
def api_decision_pref_stats(request: Request) -> dict[str, Any]:
    """复利信号(docs/02 §11 MVP):教会几条偏好 + 提案接受率趋势(越用越懂你的可测证据)。"""
    app = request.app
    mem = getattr(app.state, "memory", None)
    total = confirmed = 0
    by_kind: dict[str, int] = {}
    if mem is not None:
        from karvyloop.crystallize.decision_pref import is_decision_pref
        for sc in ("personal", "domain"):
            for b in mem.index.all(sc):
                if not is_decision_pref(b):
                    continue
                total += 1
                if b.provenance.get("status") == "confirmed":
                    confirmed += 1
                k = b.provenance.get("kind", "taste")
                by_kind[k] = by_kind.get(k, 0) + 1
    stats = getattr(app.state, "decision_stats", None)
    outcome = stats.summary() if stats is not None else {
        "decisions_total": 0, "accept_rate": None, "recent_accept_rate": None,
        "trend": None, "enough_for_trend": False,
    }
    # 口味命中率(taste_eval):"越用越像你"的可证明刻度 —— 前瞻押注的滚动对账
    tstore = getattr(app.state, "taste_predictions", None)
    taste = tstore.stats() if tstore is not None else {
        "taste_n": 0, "taste_hit_rate": None, "taste_prev_rate": None,
        "taste_trend": None, "taste_enough": False, "taste_need_more": 10,
    }
    return {"prefs_total": total, "confirmed": confirmed, "by_kind": by_kind, **outcome, **taste}
