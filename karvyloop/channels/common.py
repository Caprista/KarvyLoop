"""channels/common — 出站通道共用的选卡与分级语义(email / webhook 同一套)。

单一真理源:哪些 pending 卡"该被推出去"(DEFER 老化语义)、哪些算高危、
一张卡的价值分级。所有出站通道(邮件 digest、webhook 推送、未来新渠道)都从这里取,
避免每个通道各自演化出不同的卡选择口径。
"""
from __future__ import annotations

from typing import List

from karvyloop.karvy.proposal_registry import AGING_THRESHOLD_S

SUMMARY_MAX = 160                # 每卡摘要截断(digest 纪律:payload 全文不出通道)

# 高危分级(docs/43 ⑤a #4):命中任一 → 远程通道只通知、不可远程回批(必须回控制台拍板)。
# - kind 标记:子串匹配 proposal.kind(fs_access = 放行文件系统路径,天然高危)
# - 文本标记:出现在 summary 里(如"大额"付款/开销类建议)
HIGH_RISK_KIND_MARKERS = ("fs_access",)
HIGH_RISK_TEXT_MARKERS = ("大额",)

# 价值分级(strength → 等级;高危一律 high)
LEVEL_HIGH = "high"
LEVEL_MEDIUM = "medium"
LEVEL_LOW = "low"


def is_high_risk(proposal) -> bool:
    """高危卡判定(远程通道只通知不可回批):kind 含 fs_access 类标记,或摘要含"大额"类标记。"""
    kind = str(getattr(proposal, "kind", "") or "")
    if any(mark in kind for mark in HIGH_RISK_KIND_MARKERS):
        return True
    summary = str(getattr(proposal, "summary", "") or "")
    return any(mark in summary for mark in HIGH_RISK_TEXT_MARKERS)


def value_level(proposal) -> str:
    """一张卡的价值等级:高危 → high;否则按 strength 分档(≥0.8 high / ≥0.5 medium / 其余 low)。"""
    if is_high_risk(proposal):
        return LEVEL_HIGH
    try:
        strength = float(getattr(proposal, "strength", 0.0) or 0.0)
    except (TypeError, ValueError):
        strength = 0.0
    if strength >= 0.8:
        return LEVEL_HIGH
    if strength >= 0.5:
        return LEVEL_MEDIUM
    return LEVEL_LOW


def eligible_pending(registry, now: float,
                     aging_threshold_s: float = AGING_THRESHOLD_S) -> List[tuple]:
    """该被推出去的 pending 卡:pending − 未满老化阈值的 DEFER 卡;按挂龄降序(老卡置顶)。

    DEFER 语义:DEFER 过的卡在 aging_threshold_s 内不计入;满阈值重新计入(DEFER≠消失)。
    返回 [(proposal, age_s), ...]。
    """
    cards: List[tuple] = []
    for prop in registry.pending():
        pid = getattr(prop, "proposal_id", "") or ""
        meta = registry.proposal_meta(pid) if hasattr(registry, "proposal_meta") else {}
        deferred_at = meta.get("deferred_at")
        if deferred_at and (now - float(deferred_at)) < aging_threshold_s:
            continue  # DEFER=暂缓:满老化阈值才重新计入(DEFER≠消失)
        created = meta.get("created_ts") or now
        cards.append((prop, now - float(created)))
    cards.sort(key=lambda t: -t[1])
    return cards


__all__ = [
    "SUMMARY_MAX",
    "HIGH_RISK_KIND_MARKERS",
    "HIGH_RISK_TEXT_MARKERS",
    "LEVEL_HIGH",
    "LEVEL_MEDIUM",
    "LEVEL_LOW",
    "is_high_risk",
    "value_level",
    "eligible_pending",
]
