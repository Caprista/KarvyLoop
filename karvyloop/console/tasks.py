"""console/tasks — 任务看板登记 + 落盘(9.5 P2 / loop-step2)。

一次 drive = 一个任务:记 谁(小卡/角色@域)、意图、状态(running/done/error)、结果摘要+全文、
关联 peer(domain_id+role,点回去切聊天)。

**loop-step2:state-on-disk(落盘)。** loop 工程铁律:"the agent forgets, the repo doesn't" ——
任务看板不能重启就丢。最近 N 个任务持久到 `~/.karvyloop/tasks.json`,重启读回(你回得了"上次做了啥")。
重启时仍处 running 的任务 = 进程中断没跑完 → 老实标成 interrupted(error),不假装还在跑。
(真正的"断点续跑 / wake(sessionId)" 是 M3+,本步只做"记得住"。)
"""
from __future__ import annotations

import json
import os
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, Optional


class TaskRecord:
    def __init__(self, task_id: str, who: str, domain_id: str, role: str, intent: str) -> None:
        self.id = task_id
        self.who = who              # 显示名:"小卡" / 角色名
        self.domain_id = domain_id  # 关联 peer(点回去切聊天)
        self.role = role
        self.intent = intent
        self.status = "running"     # running / done / error
        self.result = ""            # 结果摘要(卡片用,截断)
        self.result_full = ""       # 完整结果(结果文档用)
        self.conversation_id = ""   # 关联对话(圆桌等 → 点卡精准跳回这条聊天记录)
        self.trace_id = ""          # 这次执行写进对话的 turn.task_id —— 料→去聊天靠它**定位到那一轮**
        self.started = time.time()
        self.finished: Optional[float] = None

    def to_dict(self) -> dict:
        """列表用:只带摘要(poll 每 2s,不塞完整结果)。"""
        return {
            "id": self.id, "who": self.who,
            "domain_id": self.domain_id, "role": self.role,
            "intent": self.intent, "status": self.status,
            "result": self.result, "conversation_id": self.conversation_id,
            "trace_id": self.trace_id,
            "started": self.started, "finished": self.finished,
        }

    def detail(self) -> dict:
        """详情用 + 落盘用:带完整结果文档。"""
        d = self.to_dict()
        d["result_full"] = self.result_full
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TaskRecord":
        t = cls(
            task_id=str(d.get("id") or uuid.uuid4().hex[:12]),
            who=d.get("who", ""), domain_id=d.get("domain_id", "l0"),
            role=d.get("role", ""), intent=d.get("intent", ""),
        )
        t.status = d.get("status", "done")
        t.result = d.get("result", "")
        t.result_full = d.get("result_full", "") or t.result
        t.conversation_id = d.get("conversation_id", "") or ""
        t.trace_id = d.get("trace_id", "") or ""
        # 类型强制(手改坏文件里的字符串时间戳 → 抛 → load_all 丢掉该坏项,不污染前端排序)
        t.started = float(d.get("started") or time.time())
        _f = d.get("finished")
        t.finished = float(_f) if _f is not None else None
        return t


class TaskStore:
    """任务看板的磁盘存储(JSON 数组,atomic 写)。活动记录,坏文件不阻塞启动。"""

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[TaskRecord]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, list):
            return []
        out: list[TaskRecord] = []
        for rec in data:
            if isinstance(rec, dict):
                try:
                    out.append(TaskRecord.from_dict(rec))
                except Exception:
                    continue
        return out

    def save_all(self, records) -> None:
        payload = json.dumps([r.detail() for r in records], ensure_ascii=False, indent=2)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)


class TaskRegistry:
    """最近 N 个任务的活动看板(进程内 + 可选落盘)。"""

    def __init__(self, cap: int = 50, *, store: Optional[TaskStore] = None,
                 on_change: Optional[Callable[[dict], None]] = None) -> None:
        self._tasks: "deque[TaskRecord]" = deque(maxlen=cap)
        self._by_id: dict[str, TaskRecord] = {}
        self._store = store
        # §0.7 fail-loud:状态变化(start/finish)同步回调一次,由接线层把它接到
        # WS 广播(task_status push)。registry 自身保持纯粹,不 import console/ws。
        self.on_change: Optional[Callable[[dict], None]] = on_change
        if store is not None:
            loaded = store.load_all()[:cap]  # 存的是 newest-first;截到 cap
            for t in loaded:
                if t.id in self._by_id:
                    continue  # 跳过重复 id(防 deque 与 _by_id 失同步)
                # 重启时仍 running 的 = 进程中断,没跑完 → 老实标 interrupted,不假装在跑
                if t.status == "running":
                    t.status = "error"
                    t.result = t.result_full = "(进程重启时中断,未完成)"
                    t.finished = t.finished or time.time()
                self._tasks.append(t)       # 保持 newest-first 顺序
                self._by_id[t.id] = t

    def start(self, *, who: str, domain_id: str = "l0", role: str = "", intent: str = "") -> str:
        t = TaskRecord(uuid.uuid4().hex[:12], who, domain_id, role, intent)
        if len(self._tasks) == self._tasks.maxlen and self._tasks:
            oldest = self._tasks[-1]
            self._by_id.pop(oldest.id, None)
        self._tasks.appendleft(t)
        self._by_id[t.id] = t
        self._persist()
        self._notify(t)
        return t.id

    def finish(self, task_id: str, *, result: str = "", error: str = "") -> None:
        t = self._by_id.get(task_id)
        if t is None:
            return
        t.status = "error" if error else "done"
        full = (error or result or "")
        t.result_full = full[:16000]   # 结果文档(封顶防爆)
        t.result = full[:280]          # 卡片摘要
        t.finished = time.time()
        self._persist()
        self._notify(t)                # §0.7:完成/失败 = 事件,主动 push(不等人轮询)

    def _notify(self, t: TaskRecord) -> None:
        """状态变化同步回调(start/finish)→ 接线层接 WS 广播。失败不阻塞主流程。"""
        if self.on_change is None:
            return
        try:
            self.on_change(t.to_dict())
        except Exception:
            pass  # 广播失败绝不拖垮任务记录(活动看板,丢一次推送不致命)

    def set_conversation(self, task_id: str, conversation_id: str, *, trace_id: str = "") -> None:
        """挂上关联对话 id(+ 可选 trace_id)—— 点卡"去聊天"精准跳回这条聊天记录并**定位到那一轮**。

        trace_id = 这次 drive 写进对话的 `turn.task_id`;前端按它在对话里 querySelector 那一轮滚过去。
        l0 私聊轮的 turn.task_id 是 drive trace id(≠ 本任务 registry id),故必须显式回填,否则定位空。
        """
        t = self._by_id.get(task_id)
        if t is None:
            return
        t.conversation_id = conversation_id or ""
        if trace_id:
            t.trace_id = trace_id
        self._persist()

    def list(self) -> list[dict]:
        return [t.to_dict() for t in self._tasks]

    def get(self, task_id: str) -> Optional[dict]:
        t = self._by_id.get(task_id)
        return t.detail() if t is not None else None

    def _persist(self) -> None:
        if self._store is not None:
            try:
                self._store.save_all(self._tasks)
            except Exception:
                pass  # 落盘失败不阻塞主流程(活动记录,丢一次不致命)


__all__ = ["TaskRegistry", "TaskRecord", "TaskStore"]
