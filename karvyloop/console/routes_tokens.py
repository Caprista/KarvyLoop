"""routes_tokens — /api/tokens* 端点(token 用量看板:按 source/model/day/hour,K4 只读)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import 可达。

⭐ KarvyLoop 专属:by_source 看清 token 花在哪个功能;记账唯一咽喉在 gateway.complete,这里只读。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")


@router.get("/tokens")
def api_tokens(request: Request) -> dict[str, Any]:
    """token 用量看板:总量 + 按来源(功能)/ 模型 / 天 / **小时(时段)** + 最近调用时间线。无账本 → 空。"""
    led = getattr(request.app.state, "token_ledger", None)
    if led is None:
        return {"totals": {}, "by_source": [], "by_model": [], "by_day": [],
                "by_hour": [], "recent": []}
    return {
        "totals": led.totals(),
        "by_source": led.by_source(),   # ⭐ KarvyLoop 专属:看清 token 花在哪个功能
        "by_model": led.by_model(),
        "by_day": led.by_day(),
        "by_hour": led.buckets(interval_sec=3600, limit=48),  # ⭐ 时段:近 48 小时,看何时烧的
        "recent": led.recent(limit=50),                       # ⭐ 时间线:最近 50 次调用
    }


@router.get("/tokens/buckets")
def api_token_buckets(request: Request, interval: int = 3600,
                      limit: int = 200, since: float | None = None) -> dict[str, Any]:
    """任意粒度的 token 时间序列(压测看分钟级:`?interval=60`)。回答"token 什么时候烧的"。"""
    led = getattr(request.app.state, "token_ledger", None)
    if led is None:
        return {"interval": interval, "buckets": []}
    iv = max(1, min(int(interval), 86400))   # 1 秒 ~ 1 天,挡掉荒谬值
    lim = max(1, min(int(limit), 5000))
    return {"interval": iv, "buckets": led.buckets(interval_sec=iv, since=since, limit=lim)}


@router.get("/tokens/query")
def api_tokens_query(request: Request, start_ts: float | None = None,
                     end_ts: float | None = None,
                     granularity: str = "day") -> dict[str, Any]:
    """分时段 token 查询(Hardy ⑥:"笼统的查询等于没有查询")。**纯只读**,记账路径不碰。

    参数:start_ts/end_ts(epoch 秒,闭区间;缺省=最近 7 天)、granularity=hour|day(缺省 day)。
    返回:窗口回显 + totals(窗口总量)+ by_source(功能排行,烧得多在前)+
    series(按粒度时间序列,oldest-first,给前端画柱状;day 按本地日历日,hour 按整点桶)。
    """
    import time as _time
    gran = granularity if granularity in ("hour", "day") else "day"
    end = float(end_ts) if end_ts is not None else _time.time()
    start = float(start_ts) if start_ts is not None else end - 7 * 86400.0
    if start > end:                       # 传反了 → 调和,不 4xx(查询是只读,宽进)
        start, end = end, start
    base: dict[str, Any] = {"start_ts": start, "end_ts": end, "granularity": gran}
    led = getattr(request.app.state, "token_ledger", None)
    if led is None:                       # 无账本 → 优雅空(与 /tokens 同口径)
        return {**base, "totals": {"input": 0, "output": 0, "cache_read": 0,
                                   "cache_write": 0, "total": 0, "calls": 0},
                "by_source": [], "series": []}
    return {
        **base,
        "totals": led.window_totals(start_ts=start, end_ts=end),
        "by_source": led.window_by_source(start_ts=start, end_ts=end),
        "series": led.window_series(start_ts=start, end_ts=end, granularity=gran),
    }
