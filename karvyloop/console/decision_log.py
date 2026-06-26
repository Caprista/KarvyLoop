"""console/decision_log — 最近拍板流水(只读回看)。

决策卡拍完就从待决列消失(对的:处置完了)。但人需要**回看自己拍过什么**——
否则"我刚才到底点了认还是拒?"无从查。本模块记每次 H2A 决策的可读流水
(摘要 + 决定 + 时间 + 依据),只读呈现,不可改(拍过的板是事实,不回改)。

与 [[decision_stats]] 的区别:stats 是**度量**(接受率/趋势,只存 decision);
本模块是**给人看的流水**(存摘要/依据,供回看)。两者都落盘(user-data-persists-by-default)。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

_VALID = ("ACCEPT", "REJECT", "DEFER")
_CAP = 50   # 只留最近 N 条(回看够用,不无限长)


class DecisionLog:
    """H2A 决策的可读流水(进程内 + 落盘);newest-last 存储,recent() 给 newest-first。"""

    def __init__(self, *, path: Optional[Path] = None) -> None:
        self._path = path
        self._entries: list[dict] = []
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._entries = [d for d in data if isinstance(d, dict)][-_CAP:]
            except Exception:
                self._entries = []   # 坏文件不致命,从空开始

    def record(self, *, decision: str, summary: str = "", proposal_id: str = "",
               reason: str = "", kind: str = "", domain: str = "", role: str = "",
               now: Optional[float] = None) -> None:
        """记一条拍板流水。只认 ACCEPT/REJECT/DEFER(别的忽略)。"""
        d = (decision or "").upper()
        if d not in _VALID:
            return
        self._entries.append({
            "ts": now if now is not None else time.time(),
            "decision": d, "summary": summary or "", "proposal_id": proposal_id or "",
            "reason": reason or "", "kind": kind or "", "domain": domain or "", "role": role or "",
        })
        if len(self._entries) > _CAP:
            self._entries = self._entries[-_CAP:]
        self._persist()

    def recent(self, limit: int = 10) -> list[dict]:
        """最近 limit 条,newest-first。"""
        if limit <= 0:
            return []
        return list(reversed(self._entries[-limit:]))

    def _persist(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._entries, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass  # 落盘失败不阻塞决策(流水丢一点不致命)


__all__ = ["DecisionLog"]
