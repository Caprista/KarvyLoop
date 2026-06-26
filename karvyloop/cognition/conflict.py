"""cognition.conflict — 记忆冲突消解（cognition/conflict.py）。

规格：docs/modules/cognition-memory.md §3 conflict.py + §4 "最新 + 最高 provenance 胜"
- 记忆可靠性三指标:provenance / freshness / conflict(矛盾标记)
- 消解:max(freshness_ts, provenance_rank)
- 矛盾标记:同 content 不同 provenance 留下的冲突由后台 review 处理
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from karvyloop.schemas import Belief


# ---- provenance 排序权重(越高越权威)----
PROVENANCE_RANK = {
    "user_explicit": 100,    # 用户明确告知(打字 / 文件)
    "trace_verified": 80,   # Trace + 通过验证门
    "trace_observed": 60,   # Trace 投影出来(默认)
    "distill_extracted": 40,  # 后台蒸馏小模型抽取
    "imported": 20,         # 导入
    "unknown": 0,
}


def provenance_rank(provenance: dict) -> int:
    """按 provenance.source 查权重;缺/未知 → 0。"""
    src = (provenance or {}).get("source", "unknown")
    return PROVENANCE_RANK.get(src, 0)


@dataclass
class ConflictReport:
    """冲突消解结果:胜出的 Belief + 被压制的 Belief 列表。"""
    winner: Belief
    losers: list[Belief]
    # 同 content 是否还有别的版本(用于后台 review 触发"矛盾标记")
    has_conflict: bool = False


def resolve(beliefs: Iterable[Belief]) -> Optional[Belief]:
    """单组矛盾 Belief 消解:winner = max(freshness_ts, provenance_rank)。

    beliefs 必须表达同一论断(上层按 content/语义聚类后再调)。
    """
    items = list(beliefs)
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    return max(items, key=lambda b: (b.freshness_ts, provenance_rank(b.provenance)))


def detect_conflict(beliefs: Iterable[Belief]) -> ConflictReport:
    """消解 + 矛盾标记。

    has_conflict = True 当有 ≥2 个 Belief 不完全相同(freshness_ts 或 content 不同)。
    """
    items = list(beliefs)
    if not items:
        raise ValueError("detect_conflict 需要至少一个 Belief")
    winner = resolve(items)
    assert winner is not None
    losers = [b for b in items if b is not winner]
    # 矛盾标记:仅当 losers 非空(意味着有不同时间/不同 provenance 的同主题记忆)
    has_conflict = len(losers) > 0
    return ConflictReport(winner=winner, losers=losers, has_conflict=has_conflict)


__all__ = [
    "PROVENANCE_RANK", "provenance_rank",
    "ConflictReport", "resolve", "detect_conflict",
]
