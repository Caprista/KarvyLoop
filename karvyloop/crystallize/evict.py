"""evict — 用进废退的"废退"半（crystallize/evict.py）。

规格:docs/modules/crystallize.md §3 evict.py + §4 保守可逆
- 7天半衰期评分(usage_score 同函数,与 promote 共用)
- 30 天未用 + 低分 → 归档(archive),不删除
- 归档可逆:recall 命中归档技能 → restore
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from .crystallize import EVICT_SCORE, STALE_DAYS, usage_score
from .store import UsageStore


def days_since(ts: float, *, now: float) -> float:
    if not ts:
        return 1e9
    return max(0.0, (now - ts) / 86400.0)


def evict_stale(
    store: UsageStore,
    *,
    skills_dir: Optional[Path] = None,
    now: Optional[float] = None,
) -> list[str]:
    """遍历 store,分数低于 EVICT_SCORE 且超过 STALE_DAYS 未用 → 归档。

    返回被归档的 sig 列表(供上层打日志/触发用户告知)。

    `skills_dir`:若提供,归档时会从磁盘删除该 skill 的目录(可逆性靠
    UsageStore.archive 保留 UsageStats 留档,真要恢复需重新结晶;保守
    起见,这里默认不删目录 —— 仅翻 store 的 archived 标记)。
    """
    now = now if now is not None else time.time()
    archived: list[str] = []
    for sig, stats in store.all():
        if store.is_archived(sig):
            continue
        # docs/44 断⑧:evict 判据认**复用**(recall_count 是快脑命中的真"用进"信号,
        # store.py 拍 9 就承诺"evict 应优先看它"但从未实现)。把复用并进活跃度再算分:
        # 天天被召回重跑的技能不因 usage_count 冻结在结晶时刻而被误杀;recall_count=0 的
        # 技能分数与旧公式完全一致(0 回归)。只会更保守(少归档),不会多归档。
        activity = stats.model_copy(update={
            "usage_count": stats.usage_count + stats.recall_count,
        })
        score = usage_score(activity, now=now)
        dsl = days_since(stats.last_used_at, now=now)
        if score < EVICT_SCORE and dsl > STALE_DAYS:
            store.archive(sig)
            archived.append(sig)
    return archived


def restore(sig: str, store: UsageStore) -> bool:
    """recall 命中归档技能时由 caller 调,从归档集合恢复。

    可逆 evict 的关键:恢复后 UsageStats 完整保留,只是退出 archived 集合。
    """
    if not store.is_archived(sig):
        return False
    store.restore(sig)
    return True


__all__ = ["evict_stale", "restore", "days_since"]
