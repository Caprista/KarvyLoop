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

_VALID = ("ACCEPT", "REJECT", "DEFER", "REVOKE")   # REVOKE=你主动撤回一条学到的偏好(可审计回看)
_CAP = 50        # UI「最近拍板」默认回看窗(recent 的软上限)
_RETAIN = 5000   # **审计留存**:落盘保留这么多条(给外部审计/合规查;不是 50 的滚动窗就丢了)


class DecisionLog:
    """H2A 决策的可读流水(进程内 + 落盘);newest-last 存储,recent() 给 newest-first。"""

    def __init__(self, *, path: Optional[Path] = None) -> None:
        self._path = path
        self._entries: list[dict] = []
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._entries = [d for d in data if isinstance(d, dict)][-_RETAIN:]
            except Exception:
                self._entries = []   # 坏文件不致命,从空开始

    def record(self, *, decision: str, summary: str = "", proposal_id: str = "",
               reason: str = "", kind: str = "", domain: str = "", role: str = "",
               now: Optional[float] = None) -> None:
        """记一条拍板流水。只认 ACCEPT/REJECT/DEFER/REVOKE(别的忽略)。"""
        d = (decision or "").upper()
        if d not in _VALID:
            return
        self._entries.append({
            "ts": now if now is not None else time.time(),
            "decision": d, "summary": summary or "", "proposal_id": proposal_id or "",
            "reason": reason or "", "kind": kind or "", "domain": domain or "", "role": role or "",
        })
        if len(self._entries) > _RETAIN:
            self._entries = self._entries[-_RETAIN:]   # 留存上限(审计够长,仍有界)
        self._persist()

    def recent(self, limit: int = 10) -> list[dict]:
        """最近 limit 条,newest-first(UI 回看)。"""
        if limit <= 0:
            return []
        return list(reversed(self._entries[-limit:]))

    def query(self, *, since: Optional[float] = None, until: Optional[float] = None,
              decision: str = "", limit: int = 1000) -> list[dict]:
        """**审计/合规查询**:按时间窗 + 决定类型筛决策流水(newest-first)。外部用决策历史做审计走这。

        - since/until:Unix 时间戳闭区间(None=不限);decision:ACCEPT/REJECT/DEFER/REVOKE(空=全)。
        - limit:返回上限(防一次性拉爆;默认 1000,留存上限 _RETAIN 条)。
        """
        d = (decision or "").upper()
        out = []
        for e in reversed(self._entries):     # newest-first
            ts = e.get("ts", 0.0)
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue
            if d and e.get("decision") != d:
                continue
            out.append(e)
            if len(out) >= max(1, int(limit)):
                break
        return out

    def count(self) -> int:
        """当前留存的决策流水总条数(审计:看留了多少历史)。"""
        return len(self._entries)

    def _persist(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._entries, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass  # 落盘失败不阻塞决策(流水丢一点不致命)


_REVOKE_COOLDOWN_DAYS = 14.0   # 撤回后这么久内别自动结晶回来(给"撤回"装牙;过窗后能重学)


class RevocationStore:
    """你**撤回过**的偏好 → 抑制窗口内别再自动结晶回来。

    让"撤回/不固化你"有牙:光从活库删掉不够——同样的拍板模式一复现,结晶器会把它**原样学回来**
    (显式陈述 support=1 下一批就回来),等于没撤。本 store 记下"你撤过这条内容 + 何时",
    结晶提升前查一次:窗口内命中 → 跳过(连复现计数也清,别偷偷攒)。

    **两头都守 '不固化你'**:窗口内尊重你的撤回(不复活);**窗口过后**你若仍持续这么做,照常重学
    ——撤回不是永久封杀(那也是固化)。落盘 = 撤回跨重启也算数。"""

    def __init__(self, *, path: Optional[Path] = None,
                 cooldown_days: float = _REVOKE_COOLDOWN_DAYS) -> None:
        self._path = path
        self._cooldown = max(0.0, float(cooldown_days)) * 86400.0
        self._marks: dict[str, float] = {}   # norm_content -> revoked_ts
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._marks = {str(k): float(v) for k, v in data.items()
                                   if isinstance(v, (int, float))}
            except Exception:
                self._marks = {}

    def mark(self, norm_content: str, *, now: Optional[float] = None) -> None:
        """记一条撤回(键 = 归一内容,值 = 撤回时刻)。"""
        k = (norm_content or "").strip()
        if not k:
            return
        self._marks[k] = now if now is not None else time.time()
        self._persist()

    def is_suppressed(self, norm_content: str, *, now: Optional[float] = None) -> bool:
        """这条归一内容是否仍在撤回抑制窗口内(窗口外/没撤过 → False)。"""
        ts = self._marks.get((norm_content or "").strip())
        if ts is None:
            return False
        n = now if now is not None else time.time()
        return (n - ts) < self._cooldown

    def clear(self, norm_content: str) -> None:
        """解除某条抑制(如你又主动确认了它)。"""
        if self._marks.pop((norm_content or "").strip(), None) is not None:
            self._persist()

    def _persist(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._marks, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


__all__ = ["DecisionLog", "RevocationStore"]
