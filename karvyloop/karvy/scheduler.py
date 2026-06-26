"""scheduler — 定时任务(Hardy 2026-06-25)。

设计要点(已与 Hardy 对齐):
- **只有 Karvy 能起定时任务**(创建权收口在 routes;本模块只管存/算/触发)。角色不能自己埋 cron
  → 全系统定时器只有一个审计面、一个看板能全看到。委派给角色到点执行也算 Karvy 起的。
- **完整 cron 表达式**当存储/执行真相(croniter,通用基建必借,不手搓);自然语言入口由
  NL→cron 语义解析器(schedule_parser.py)翻成 cron。
- 落盘持久化(schedules.json)+ 重启恢复;next_run 不存,按 cron 实时算(防时钟漂移/重启错位)。

执行(到点):把 intent 灌进现有 drive 管线;有委派目标就以那个角色人格跑,否则小卡自己跑。
结果走 #2 §13(动态任务每次重跑、不回放 stale —— 定时任务最怕吐旧数据)。
"""
from __future__ import annotations

import json
import pathlib
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class ScheduledTask:
    """一条定时任务。owner 恒为 Karvy(不存,语义上写死)。"""
    id: str
    cron: str                    # 完整 cron 表达式(分 时 日 月 周)
    intent: str                  # 到点要干的事(自然语言意图)
    title: str = ""              # 看板短标题(NL 解析时给,空=截 intent)
    target_domain: str = ""      # 委派目标:域 id(空=小卡自己干)
    target_role: str = ""        # 委派目标:角色
    target_agent_id: str = ""    # 委派目标:agent id(同名角色消歧)
    enabled: bool = True
    created_at: float = 0.0
    last_run: float = 0.0
    last_status: str = ""        # ok | error | ""(没跑过)
    last_error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _valid_cron(expr: str) -> bool:
    from croniter import croniter
    return bool(expr) and croniter.is_valid(expr)


def next_run_after(cron: str, after_ts: float) -> Optional[float]:
    """给定 cron 和起点时间戳,返回**严格大于** after 的下一个触发时间戳;非法 cron → None。

    按**本地墙钟**算(用户说"每天 8 点"= 他本地 8 点):float→本地 naive datetime 喂 croniter,
    回 naive datetime→.timestamp()。不混 UTC,避免"8 点变成别的点"。
    """
    if not _valid_cron(cron):
        return None
    import datetime

    from croniter import croniter
    try:
        start = datetime.datetime.fromtimestamp(after_ts)         # 本地 naive
        nxt = croniter(cron, start).get_next(datetime.datetime)   # 本地 naive
        return nxt.timestamp()
    except Exception:
        return None


class SchedulerStore:
    """定时任务存储:schedules.json(原子写)。无 path → 纯内存(测试,不污染真实 home)。"""

    def __init__(self, path: Optional[pathlib.Path] = None, *, clock=None) -> None:
        self._path = pathlib.Path(path) if path else None
        self._clock = clock  # 测试可注入;None=用 time.time
        self._tasks: dict[str, ScheduledTask] = {}
        self._load()

    def _now(self) -> float:
        if self._clock is not None:
            return self._clock()
        import time
        return time.time()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for d in (raw or []):
                if isinstance(d, dict) and d.get("id") and d.get("cron"):
                    # 只取已知字段(向后兼容多余字段)
                    self._tasks[d["id"]] = ScheduledTask(**{
                        k: d.get(k) for k in ScheduledTask.__dataclass_fields__ if k in d
                    })
        except Exception:
            self._tasks = {}

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps([t.to_dict() for t in self._tasks.values()],
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            pass

    # ---- CRUD ----

    def add(self, cron: str, intent: str, *, title: str = "", target_domain: str = "",
            target_role: str = "", target_agent_id: str = "") -> Optional[ScheduledTask]:
        """新建一条(cron 非法 → None)。创建权(只 Karvy)由 routes 校验,本层不管。"""
        if not _valid_cron(cron) or not (intent or "").strip():
            return None
        tid = uuid.uuid4().hex[:12]
        t = ScheduledTask(
            id=tid, cron=cron.strip(), intent=intent.strip(),
            title=(title or intent).strip()[:60],
            target_domain=target_domain or "", target_role=target_role or "",
            target_agent_id=target_agent_id or "", enabled=True, created_at=self._now(),
        )
        self._tasks[tid] = t
        self._save()
        return t

    def all(self) -> list[ScheduledTask]:
        return sorted(self._tasks.values(), key=lambda t: -t.created_at)

    def get(self, tid: str) -> Optional[ScheduledTask]:
        return self._tasks.get(tid)

    def remove(self, tid: str) -> bool:
        if tid in self._tasks:
            del self._tasks[tid]
            self._save()
            return True
        return False

    def set_enabled(self, tid: str, enabled: bool) -> bool:
        t = self._tasks.get(tid)
        if t is None:
            return False
        t.enabled = bool(enabled)
        self._save()
        return True

    def mark_run(self, tid: str, status: str, *, ts: Optional[float] = None, error: str = "") -> None:
        t = self._tasks.get(tid)
        if t is None:
            return
        t.last_run = ts if ts is not None else self._now()
        t.last_status = status
        t.last_error = (error or "")[:300]
        self._save()

    # ---- 触发判定 ----

    def next_run(self, tid: str, *, after: Optional[float] = None) -> Optional[float]:
        t = self._tasks.get(tid)
        if t is None:
            return None
        base = after if after is not None else max(self._now(), t.last_run)
        return next_run_after(t.cron, base)

    def due(self, *, since: float, now: Optional[float] = None) -> list[ScheduledTask]:
        """返回在 (since, now] 之间应触发的**启用**任务(调度 tick 用)。

        判定:从 max(since, last_run) 起,cron 的下一个触发 ≤ now → 到点。
        用 last_run 兜底防同一窗口重复触发(进程重启/慢 tick)。
        """
        cur = now if now is not None else self._now()
        out = []
        for t in self._tasks.values():
            if not t.enabled:
                continue
            base = max(since, t.last_run)
            nxt = next_run_after(t.cron, base)
            if nxt is not None and nxt <= cur:
                out.append(t)
        return out


__all__ = ["ScheduledTask", "SchedulerStore", "next_run_after"]
