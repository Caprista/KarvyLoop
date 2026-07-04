"""routes_budget — /api/budget 端点(花费预算上限:看用量/改上限)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import/monkeypatch 可达。

docs/56 audit ② MED — "后端有能力没 UI 入口" 补的花费预算入口;复用既有后端
(config_budget / spend_budget),不重写业务逻辑。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _budget_model_cost(app):
    """从 gateway 注册表取"每模型 cost 表"(USD/百万 token);无 gateway → None(按 token 计,不算钱)。

    与 llm/spend_budget.wire_spend_budget 的 _model_cost 同口径,只是这里给只读 status 用。
    """
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    reg = getattr(gw, "reg", None) if gw is not None else None
    if reg is None:
        return None

    def _cost(model_id: str):
        try:
            m = reg.get(model_id)
            return dict(getattr(m, "cost", None) or {})
        except Exception:
            return None
    return _cost


@router.get("/budget")
def api_budget(request: Request) -> dict[str, Any]:
    """花费预算现状:今日/本月已用 vs 上限 + on_limit 开关(K4 只读 — 读 config + token 账本,不写)。

    预算未配(disabled)也返真实用量(上限=null)→ 用户先看花多少再设限。**绝不含 key**。
    """
    from karvyloop.llm.config_budget import budget_status, load_spend_budget_config
    cfg_path = getattr(request.app.state, "config_path", "") or None
    cfg = load_spend_budget_config(cfg_path)
    led = getattr(request.app.state, "token_ledger", None)
    status = budget_status(cfg, ledger=led, model_cost=_budget_model_cost(request.app))
    from karvyloop.llm.config_budget import VALID_ON_LIMIT
    return {**status, "valid_on_limit": list(VALID_ON_LIMIT)}


class BudgetSaveRequest(BaseModel):
    daily_usd: float = Field(default=0.0, ge=0)
    daily_tokens: int = Field(default=0, ge=0)
    monthly_usd: float = Field(default=0.0, ge=0)
    monthly_tokens: int = Field(default=0, ge=0)
    on_limit: str = Field(default="warn", max_length=16)   # warn | pause


@router.post("/budget")
def api_budget_save(req: BudgetSaveRequest, request: Request) -> dict[str, Any]:
    """改花费预算上限(写 config.yaml `budget:` 块;四维全 0 = 关刹车 = 无限)+ 热重载全局刹车。

    写后立刻 wire_spend_budget 让新上限即时在 gateway 咽喉生效(不必重启)。emit_card 复用启动期
    接的 broadcast 桥(达阈值出卡);拿不到 → 只日志。**只碰 budget 块,不动 models/keys**。
    """
    cfg_path = getattr(request.app.state, "config_path", "") or None
    if not cfg_path:
        return {"ok": False, "reason": "未接 config(--no-llm?)"}
    from karvyloop.llm.config_budget import save_spend_budget_config
    ok, reason = save_spend_budget_config(req.model_dump(), cfg_path)
    if not ok:
        return {"ok": False, "reason": reason}
    # 热重载全局刹车(即时生效,不必重启)。拿 gateway 注册表算钱、启动期存的 emit_card 出卡。
    reloaded = False
    try:
        from karvyloop.llm.spend_budget import wire_spend_budget
        rk = getattr(request.app.state, "runtime_kwargs", None) or {}
        gw = rk.get("gateway")
        reg = getattr(gw, "reg", None) if gw is not None else None
        emit = getattr(request.app.state, "budget_emit_card", None)
        wire_spend_budget(registry=reg, config_path=cfg_path, emit_card=emit)
        reloaded = True
    except Exception:
        logger.debug("[budget] 热重载刹车失败(改已落盘,重启生效)", exc_info=True)
    return {"ok": True, "reloaded": reloaded}
