"""cognition/belief_store — Belief 长期库落盘(loop step4b 地基)。

`MemoryIndex` 是内存 dict(重启即丢);它自己注释承认"生产=落盘 markdown + index.sqlite"
但那块从没做 —— 所以个人知识库在跑着的产品里**根本存不住**。本模块给它一个 JSON 落盘
(同任务看板 `console/tasks.py` 套路:atomic 写、坏文件不阻塞启动),让长期记忆**重启不丢**
([[user-data-persists-by-default]])。pinned 状态一并存。

MVP 用 JSON(与任务看板一致、简单、重启安全);更重的 markdown+sqlite 留待量大再说。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from karvyloop.schemas.cognition import Belief


class BeliefStore:
    """Belief 长期库的磁盘存储(JSON 数组,atomic 写)。坏文件不阻塞启动。"""

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[tuple[Belief, bool]]:
        """返回 [(belief, pinned)]。坏文件 / 坏项 → 跳过,不抛。"""
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, list):
            return []
        out: list[tuple[Belief, bool]] = []
        for rec in data:
            if not isinstance(rec, dict):
                continue
            try:
                inv = rec.get("invalid_at", None)
                b = Belief(
                    content=rec["content"],
                    provenance=rec["provenance"],
                    freshness_ts=float(rec["freshness_ts"]),
                    scope=rec["scope"],
                    # 时效/使用信号(缺省兼容旧文件:老快照没这些键 → 默认有效/零使用)
                    invalid_at=(float(inv) if inv is not None else None),
                    invalid_reason=str(rec.get("invalid_reason", "") or ""),
                    last_recalled_ts=float(rec.get("last_recalled_ts", 0.0) or 0.0),
                    recall_count=int(rec.get("recall_count", 0) or 0),
                )
                out.append((b, bool(rec.get("pinned", False))))
            except Exception:
                continue  # 坏项不污染加载
        return out

    def save_all(self, items: list[tuple[Belief, bool]]) -> None:
        """items = [(belief, pinned)]。atomic 写(写 .tmp 再 os.replace)。"""
        payload = json.dumps(
            [
                {
                    "content": b.content,
                    "provenance": b.provenance,
                    "freshness_ts": b.freshness_ts,
                    "scope": b.scope,
                    "pinned": bool(pinned),
                    # 时效/使用信号(失效不删可审计 + 召回使用统计)
                    "invalid_at": b.invalid_at,
                    "invalid_reason": b.invalid_reason,
                    "last_recalled_ts": b.last_recalled_ts,
                    "recall_count": b.recall_count,
                }
                for b, pinned in items
            ],
            ensure_ascii=False,
            indent=2,
        )
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)


__all__ = ["BeliefStore"]
