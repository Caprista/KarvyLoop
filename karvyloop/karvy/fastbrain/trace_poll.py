"""Trace 三层漏斗的触发器(M3+ 拍 9.0a 骨架)。

设计:docs/25 §5 + 用户原话 2026-06-17。

**三种触发**(用户拍板):
1. **event-driven**:新事件来时立刻写摘要(caller 调 `TraceIndex.append_summary` 即触发)
2. **daily-poll**:每天定时跑一次(从原文抽摘要 + 从摘要凝习惯);**9.0b 拍实做**
3. **boot-poll**:启动时跑一次(健康检查 / 容量审计);**9.0a 拍实做 health / 9.0b 拍实做凝练**

**本拍 9.0a 范围**:
- `boot_poll` = 健康检查 + 容量审计(实做)
- `daily_poll` = 骨架(只 log 不动数据;9.0b 拍实做抽摘要)
- `install_pollers` = boot 调一次 boot_poll,留 daily 调度接口(9.0b 拍用 threading.Timer / external cron)

**灵魂铁律**(FB-5 / FB-7):
- 本模块**不**写"意图分析"功能
- 本模块**不**依赖小卡私有组件
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from .trace_index import DEFAULT_RAW_CAPACITY_BYTES, TraceIndex

logger = logging.getLogger(__name__)


# Daily poll 间隔(秒)= 24h。9.0a 仅占位,9.0b 可换外部 cron。
DAILY_POLL_INTERVAL_S = 24 * 60 * 60


def boot_poll(index: TraceIndex) -> dict:
    """启动时跑一次:健康检查 + 容量审计。

    Returns:
        status dict 含 raw_bytes / summary_bytes / is_raw_full / is_summary_full

    9.0b 扩展:从原文抽摘要(LLM 慢脑) / 从摘要凝习惯(规则)。
    """
    raw_bytes = index.raw_bytes()
    summary_bytes = index.summary_bytes()
    raw_pct = (raw_bytes / index.raw_capacity) * 100 if index.raw_capacity else 0
    summary_pct = (
        (summary_bytes / index.summary_capacity) * 100
        if index.summary_capacity
        else 0
    )
    is_raw_full = index.is_raw_full()
    is_summary_full = index.is_summary_full()
    status = {
        "raw_bytes": raw_bytes,
        "raw_pct": round(raw_pct, 2),
        "summary_bytes": summary_bytes,
        "summary_pct": round(summary_pct, 2),
        "is_raw_full": is_raw_full,
        "is_summary_full": is_summary_full,
    }
    logger.debug(
        f"[trace_poll] boot: raw={raw_bytes}B ({raw_pct:.1f}%) "
        f"summary={summary_bytes}B ({summary_pct:.1f}%) "
        f"raw_full={is_raw_full} summary_full={is_summary_full}"
    )
    return status


def distill_raw_to_summary(index: TraceIndex, *, recent_n: int = 50) -> Optional[dict]:
    """原文层 → 摘要层(9.3c 修 D1:接通漏斗;docs/27 提炼器的第一道蒸馏)。

    0.1.0 规则版(无 LLM,省 token):读最近 N 条原文事件 → 聚合成一条事件级摘要
    (按 kind 计数 + 最近 intent 文本)→ append_summary。返摘要 dict;无原文返 None。
    (LLM 版摘要 = P1,接 BehaviorPatternAnalyzer 思路;此处先把"原文→摘要"链打通。)
    """
    raw = index.list_raw(limit=recent_n)
    if not raw:
        return None
    by_kind: dict[str, int] = {}
    intents: list[str] = []
    for rec in raw:
        p = rec.payload if isinstance(rec.payload, dict) else {}
        k = p.get("kind", "event")
        by_kind[k] = by_kind.get(k, 0) + 1
        it = p.get("intent")
        if it and len(intents) < 12:
            intents.append(str(it))
    summary = {
        "kind": "distilled_summary",
        "from_raw_count": len(raw),
        "by_kind": by_kind,
        "recent_intents": intents,
    }
    index.append_summary(summary)
    return summary


def daily_poll(index: TraceIndex) -> Optional[dict]:
    """每天定时跑一次:原文→摘要蒸馏(9.3c 接通)+ (P1)摘要→习惯。

    设计:用户原话 2026-06-17 "没必要这么的频繁" → 24h 一次,静默学习。
    """
    summary = distill_raw_to_summary(index)
    logger.debug(
        f"[trace_poll] daily: distilled={summary is not None} "
        f"raw={index.raw_bytes()}B summary={index.summary_bytes()}B"
    )
    # P1:摘要层 → 凝习惯(走 BehaviorPatternAnalyzer);此拍先打通 原文→摘要。
    return summary


def install_pollers(
    index: TraceIndex,
    *,
    enable_daily: bool = False,
) -> Optional[threading.Timer]:
    """挂载轮询器:boot 一次 boot_poll;可选 daily 定时(9.0b 拍默认开)。

    Returns:
        None if enable_daily=False;else Timer handle(caller 持有用于 cancel)

    9.0a 故意默认 enable_daily=False — 避免 0.1.0 阶段开 background thread
    (CLAUDE.md §少脚手架多信模型,后台 timer 9.0b 拍需要时再开)。
    """
    boot_poll(index)
    if not enable_daily:
        return None
    timer = threading.Timer(DAILY_POLL_INTERVAL_S, daily_poll, args=(index,))
    timer.daemon = True
    timer.start()
    logger.debug(f"[trace_poll] daily timer installed, interval={DAILY_POLL_INTERVAL_S}s")
    return timer


__all__ = [
    "DAILY_POLL_INTERVAL_S",
    "boot_poll",
    "daily_poll",
    "distill_raw_to_summary",
    "install_pollers",
]
