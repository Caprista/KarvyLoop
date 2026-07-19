"""cognition.pursuit_store — 持久"活跃 Pursuit 集合"(docs/88 §4/§9 第一刀件①)。

`PursuitManager`/`Pursuit`(cognition/pursuit.py + schemas/cognition.py)是**无状态判定核**:
给它一个 Pursuit + context 就单步推进状态,但它自己不记"我现在还在追哪些目标"。第一刀的
关键补件 = 把这颗判定核**接上电**:持久一份活跃 Pursuit 集合(重启读回),让"一个跨天目标
create→自己跑→确定性 verify→done"第一次真发生。

分层(照 docs/88 §3):
- `Pursuit`(schemas/cognition.py,BDI 契约,extra=forbid)保持**纯净不动** ——
  level/statement/commitment_condition/revision_triggers/verify_gate/status。
- 运营态(title/时间戳/owner/domain_id/派生 task 指针/进度备注/修订原因/last outcome
  探针/挂起标)统统留在本层 `PursuitRecord`,**绝不塞进 BDI schema**。

落盘 = JSON 数组,原子写(仿 console/tasks.py:TaskStore);坏文件不阻塞启动(fail-safe 当空)。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from karvyloop.schemas import Pursuit

# 终止态:不再参与 tick(与 PursuitManager 状态机一致)。
_TERMINAL = ("done", "dropped")


class PursuitRecord:
    """一个持久 Pursuit 的运营包装(BDI 契约 + 运营元数据)。

    `pursuit` 是纯净的 BDI 契约;其余字段是运营态,只住这一层。
    """

    def __init__(
        self,
        pursuit: Pursuit,
        *,
        title: str = "",
        owner: str = "karvy",
        domain_id: str = "l0",
        created_ts: Optional[float] = None,
        updated_ts: Optional[float] = None,
        last_task_ids: Optional[list] = None,
        progress_note: str = "",
        revision_reason: str = "",
        suspended: bool = False,
        last_advance_ts: float = 0.0,
        advances: int = 0,
        consecutive_failures: int = 0,
        # 上次 pursue 推进的确定性探针(assemble_context 从这里读,零 LLM):
        last_terminal: str = "",
        last_verdict_passed: bool = False,
        last_infeasible: bool = False,
        last_infra_dead: bool = False,
        transferred_to: str = "",
    ) -> None:
        self.pursuit = pursuit
        self.title = title or (pursuit.statement or "")[:80]
        self.owner = owner or "karvy"
        self.domain_id = domain_id or "l0"
        now = time.time()
        self.created_ts = float(created_ts) if created_ts is not None else now
        self.updated_ts = float(updated_ts) if updated_ts is not None else now
        self.last_task_ids = list(last_task_ids or [])
        self.progress_note = progress_note or ""
        self.revision_reason = revision_reason or ""
        # 运营态:infeasible / 待人拍的修订 = 挂起,tick 不再自动推进(等人决定),但仍确定性验完成。
        self.suspended = bool(suspended)
        # last_advance_ts = 上次做过 tick 工作(推进 or 挂起态确定性验)的时刻;推进节拍/验证子进程
        # 节流都读它(docs/88 真伤1②③:异常路径也必须写它,别让抛异常旁路节流)。
        self.last_advance_ts = float(last_advance_ts or 0.0)
        # advances = **真推进**累计次数(outcome=None/异常不计,免虚高)—— 达 PURSUIT_MAX_ADVANCES
        # 硬地板即挂起升卡(真伤1①:和"预算/infra-dead 确定性地板"同构的兜底,不靠用户 revision_trigger)。
        self.advances = int(advances or 0)
        # consecutive_failures = **连续**失败计数(pursue 抛异常/明确报错 +1;真推进成功清零;确定性
        # infra 故障不计)—— 堵"pursue 每拍都炸 → advances 永不 +1 → PURSUIT_MAX_ADVANCES 永不触发
        # → 节流上限无限静默重试"的静默洞(P2 残余)。老 JSON 无此键 → from_dict 默认 0(向后兼容)。
        self.consecutive_failures = int(consecutive_failures or 0)
        self.last_terminal = last_terminal or ""
        self.last_verdict_passed = bool(last_verdict_passed)
        self.last_infeasible = bool(last_infeasible)
        self.last_infra_dead = bool(last_infra_dead)
        # mesh 跨设备接管(docs/88 第三刀 #3):非空 = 这条已被同主人另一台设备接走
        # (mesh lease 归属清晰,单 owner)→ 本机 tick 不推进不验,直到账回到本机/远端完成。
        self.transferred_to = transferred_to or ""

    @property
    def id(self) -> str:
        return self.pursuit.id

    @property
    def status(self) -> str:
        return self.pursuit.status

    def touch(self) -> None:
        self.updated_ts = time.time()

    _TASK_PTR_CAP = 20   # 派生 task 指针上限(有界,保最近 N 条)

    def note_task(self, task_id: str) -> None:
        if not task_id:
            return
        self.last_task_ids.append(task_id)
        if len(self.last_task_ids) > self._TASK_PTR_CAP:
            self.last_task_ids = self.last_task_ids[-self._TASK_PTR_CAP:]

    def to_dict(self) -> dict:
        return {
            "pursuit": self.pursuit.model_dump(),
            "title": self.title,
            "owner": self.owner,
            "domain_id": self.domain_id,
            "created_ts": self.created_ts,
            "updated_ts": self.updated_ts,
            "last_task_ids": list(self.last_task_ids),
            "progress_note": self.progress_note,
            "revision_reason": self.revision_reason,
            "suspended": self.suspended,
            "last_advance_ts": self.last_advance_ts,
            "advances": self.advances,
            "consecutive_failures": self.consecutive_failures,
            "last_terminal": self.last_terminal,
            "last_verdict_passed": self.last_verdict_passed,
            "last_infeasible": self.last_infeasible,
            "last_infra_dead": self.last_infra_dead,
            "transferred_to": self.transferred_to,
        }

    def summary(self) -> dict:
        """列表用(轻量,不带派生 task 详情)。"""
        p = self.pursuit
        return {
            "id": p.id,
            "level": p.level,
            "statement": p.statement,
            "status": p.status,
            "title": self.title,
            "owner": self.owner,
            "domain_id": self.domain_id,
            "progress_note": self.progress_note,
            "revision_reason": self.revision_reason,
            "suspended": self.suspended,
            "advances": self.advances,
            "consecutive_failures": self.consecutive_failures,
            "verify_gate": dict(p.verify_gate or {}),
            "created_ts": self.created_ts,
            "updated_ts": self.updated_ts,
            "last_task_ids": list(self.last_task_ids),
            "transferred_to": self.transferred_to,   # 加性:非空 = 已被另一台设备接管
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PursuitRecord":
        pd = d.get("pursuit") or {}
        pursuit = Pursuit(**pd)   # extra=forbid → 坏 schema 抛,由 load_all 丢掉该坏项
        return cls(
            pursuit,
            title=str(d.get("title", "") or ""),
            owner=str(d.get("owner", "karvy") or "karvy"),
            domain_id=str(d.get("domain_id", "l0") or "l0"),
            created_ts=d.get("created_ts"),
            updated_ts=d.get("updated_ts"),
            last_task_ids=[str(x) for x in (d.get("last_task_ids") or []) if str(x)],
            progress_note=str(d.get("progress_note", "") or ""),
            revision_reason=str(d.get("revision_reason", "") or ""),
            suspended=bool(d.get("suspended", False)),
            last_advance_ts=float(d.get("last_advance_ts", 0.0) or 0.0),
            advances=int(d.get("advances", 0) or 0),
            consecutive_failures=int(d.get("consecutive_failures", 0) or 0),
            last_terminal=str(d.get("last_terminal", "") or ""),
            last_verdict_passed=bool(d.get("last_verdict_passed", False)),
            last_infeasible=bool(d.get("last_infeasible", False)),
            last_infra_dead=bool(d.get("last_infra_dead", False)),
            transferred_to=str(d.get("transferred_to", "") or ""),
        )


def new_pursuit_id(level: str = "atom") -> str:
    """稳定前缀 + 随机短 id(domain 级前缀带 domain,personal 持久按 id 前缀判层)。"""
    return f"{level or 'atom'}:{uuid.uuid4().hex[:12]}"


class PursuitStore:
    """活跃 Pursuit 集合的磁盘存储(JSON 数组,atomic 写;坏文件不阻塞启动)。

    进程内 dict(id → PursuitRecord)+ 每次变更落盘;重启从盘读回(判定核变持久对象的关键)。
    """

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._by_id: dict[str, PursuitRecord] = {}
        for rec in self._load():
            self._by_id[rec.id] = rec

    def _load(self) -> list[PursuitRecord]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []   # 坏文件当空(与 TaskStore 同调,fail-safe)
        if not isinstance(data, list):
            return []
        out: list[PursuitRecord] = []
        for rec in data:
            if isinstance(rec, dict):
                try:
                    out.append(PursuitRecord.from_dict(rec))
                except Exception:
                    continue   # 单条坏(schema 漂移/手改坏)丢掉,不污染全表
        return out

    def _save(self) -> None:
        payload = json.dumps([r.to_dict() for r in self._by_id.values()],
                             ensure_ascii=False, indent=2)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            pass   # 落盘失败不阻断主流程(活跃集,丢一次不致命)

    def put(self, rec: PursuitRecord) -> None:
        rec.touch()
        self._by_id[rec.id] = rec
        self._save()

    def get(self, pursuit_id: str) -> Optional[PursuitRecord]:
        return self._by_id.get(pursuit_id)

    def remove(self, pursuit_id: str) -> Optional[PursuitRecord]:
        rec = self._by_id.pop(pursuit_id, None)
        if rec is not None:
            self._save()
        return rec

    def all(self) -> list[PursuitRecord]:
        return list(self._by_id.values())

    def active(self) -> list[PursuitRecord]:
        """非终止态(active/committed/revised)—— tick 遍历它们。newest-first。"""
        recs = [r for r in self._by_id.values() if r.status not in _TERMINAL]
        recs.sort(key=lambda r: r.updated_ts, reverse=True)
        return recs

    def active_count(self) -> int:
        """活跃 pursuit 数(snapshot 真数据源;取代 broadcast_count 冒名,docs/88 §6)。"""
        return sum(1 for r in self._by_id.values() if r.status not in _TERMINAL)


__all__ = ["PursuitRecord", "PursuitStore", "new_pursuit_id"]
