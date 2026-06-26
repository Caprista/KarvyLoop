"""console/decision_stats — 决策接口结晶的复利信号(docs/02 §11 MVP 验证)。

楔子要证明的不是"功能跑通",而是**复利**:小卡的提案**逐周更少被你拒/改**。
本模块记每次 H2A 决策结果(ACCEPT/REJECT/DEFER),算"提案接受率"+ 近期 vs 早前趋势,
配合"你教会了几条偏好"一起,给你一个**看得见的复利读数**(越用越懂你的可测证据)。

诚实边界:这是**度量**,不是能力——曲线要随真实使用才显现。本模块只做正确的累计+持久,
不杜撰趋势(样本不足时如实说"数据还少")。落盘(重启不丢,守 user-data-persists-by-default)。
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

_ACCEPT = "ACCEPT"
_CAP = 200          # 只留最近 N 条结果(滚动窗口,够算趋势又不无限长)
_RECENT_N = 20      # "近期"窗口大小(近 N 条 vs 之前)
_MIN_FOR_TREND = 10  # 少于这么多结果 → 不报趋势(样本不足,不杜撰)


class DecisionStats:
    """H2A 决策结果的滚动记录 + 复利信号摘要(进程内 + 可选落盘)。"""

    def __init__(self, *, path: Optional[Path] = None) -> None:
        self._path = path
        self._outcomes: list[dict] = []   # [{"ts": float, "decision": "ACCEPT|REJECT|DEFER"}]
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._outcomes = [d for d in data if isinstance(d, dict)][-_CAP:]
            except Exception:
                self._outcomes = []   # 坏文件不致命,从空开始

    def record(self, decision: str, *, now: Optional[float] = None) -> None:
        """记一次提案决策结果。只认 ACCEPT/REJECT/DEFER(别的忽略)。"""
        d = (decision or "").upper()
        if d not in (_ACCEPT, "REJECT", "DEFER"):
            return
        self._outcomes.append({"ts": now if now is not None else time.time(), "decision": d})
        if len(self._outcomes) > _CAP:
            self._outcomes = self._outcomes[-_CAP:]
        self._persist()

    def _persist(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._outcomes, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass  # 落盘失败不阻塞(度量丢一点不致命)

    @staticmethod
    def _accept_rate(outcomes: list[dict]) -> Optional[float]:
        if not outcomes:
            return None
        acc = sum(1 for o in outcomes if o.get("decision") == _ACCEPT)
        return acc / len(outcomes)

    def summary(self) -> dict:
        """复利信号摘要:总决策数 / 接受率 / 近期 vs 早前趋势(样本不足则 trend=None)。"""
        n = len(self._outcomes)
        overall = self._accept_rate(self._outcomes)
        trend: Optional[float] = None
        recent_rate: Optional[float] = None
        if n >= _MIN_FOR_TREND:
            recent = self._outcomes[-_RECENT_N:]
            older = self._outcomes[:-_RECENT_N]
            recent_rate = self._accept_rate(recent)
            older_rate = self._accept_rate(older)
            if recent_rate is not None and older_rate is not None:
                trend = recent_rate - older_rate   # >0 = 接受率在升(提案越来越对路)
        return {
            "decisions_total": n,
            "accept_rate": overall,
            "recent_accept_rate": recent_rate,
            "trend": trend,                 # None = 样本还少,不报
            "enough_for_trend": n >= _MIN_FOR_TREND,
        }


__all__ = ["DecisionStats"]
