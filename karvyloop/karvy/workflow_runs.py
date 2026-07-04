"""workflow_runs — 工作流运行的**持久化执行**(durable execution,借 EVE,#39 ①)。

问题:长 workflow(多角色 drive)跑一半 console 重启 → 整轮丢、白烧的 token 没了。
解法:**每步产出 memoize 落盘**;重启时把未完成的 run **replay** —— 已完成步秒命中缓存、只续跑剩余步
(= EVE 的"事件日志确定性重放"套我们的 step DAG)。

只管"存运行态 + 算哪些没跑完";真执行(按角色 drive)+ 重启时 replay 的接线在 console 层。
落盘 workflow_runs.json(原子写)+ 重启恢复;无 path → 纯内存(测试不污染 home)。

**逃生门(#54 编排 A):**
- cancel(run_id/task_id) → 置 status=cancelled;跑中的 step 循环协作检查 is_cancelled →
  不再起新步,剩余步标 skipped(§0.7:人坐驾驶座,跑起来也能踩刹车)。
- 重启不再**无条件复活**:running 态不自动续跑 —— 由 console 出 H2A 卡让你拍板续/丢,或超 age 上限
  的直接标 abandoned(逃生门解锁:重启真能杀掉一条跑歪烧 token 的 workflow)。
"""
from __future__ import annotations

import json
import pathlib
import time
from typing import Optional


class WorkflowRunStore:
    """活跃 workflow 运行态:{run_id → {goal, steps, domain_id, step_outputs, status, task_id}}。

    status:
      - running:在跑 / 被中断待处置(重启时**不**自动续,交 H2A 拍板 —— 逃生门解锁)。
      - done:完成(保留一小段供查,可由调用方清)。
      - cancelled:被人显式中止(踩刹车)。
      - abandoned:重启时超 age 上限,直接丢弃不复活。
    step_outputs: {step_id → output};memoize 的家 —— 有就别重跑。
    task_id:关联看板任务 id(前端"中止"按钮只知 task_id → 据此定位 run)。
    """

    def __init__(self, path: Optional[pathlib.Path] = None, *, clock=time.time) -> None:
        self._path = pathlib.Path(path) if path else None
        self._clock = clock
        self._runs: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._runs = raw
        except Exception:
            self._runs = {}

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._runs, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            pass

    def create(self, run_id: str, *, goal: str, steps: list, domain_id: str,
               started_at: float = 0.0, task_id: str = "") -> None:
        """登记一个新运行(status=running)。steps 存原始 step 规格(含 agent_id/domain_id/task/depends_on)。

        started_at 不传 → 用当前时钟(重启 age 判据要靠它,别留 0)。task_id = 关联看板任务(中止用)。
        """
        self._runs[run_id] = {
            "run_id": run_id, "goal": goal, "steps": steps, "domain_id": domain_id,
            "step_outputs": {}, "status": "running",
            "started_at": started_at or self._clock(), "task_id": task_id or "",
        }
        self._save()

    def get(self, run_id: str) -> Optional[dict]:
        return self._runs.get(run_id)

    def find_by_task(self, task_id: str) -> Optional[dict]:
        """按关联看板任务 id 反查运行(前端"中止"按钮只握 task_id)。"""
        if not task_id:
            return None
        for r in self._runs.values():
            if r.get("task_id") == task_id:
                return r
        return None

    def step_output(self, run_id: str, step_id: str):
        """已完成步的缓存产出;没跑过 → None(memoize 判据:None=要跑,有=别重跑)。"""
        r = self._runs.get(run_id)
        if r is None:
            return None
        return r.get("step_outputs", {}).get(step_id)

    def set_step(self, run_id: str, step_id: str, output: str) -> None:
        """记一步产出(每步跑完即落盘 → 重启能续)。"""
        r = self._runs.get(run_id)
        if r is None:
            return
        r.setdefault("step_outputs", {})[step_id] = output or ""
        self._save()

    def finish(self, run_id: str) -> None:
        r = self._runs.get(run_id)
        # 已被中止(cancelled)的别覆盖回 done —— 保住"人踩了刹车"的真相
        if r is not None and r.get("status") == "running":
            r["status"] = "done"
            self._save()

    def cancel(self, run_id: str) -> bool:
        """人显式中止一条运行(踩刹车):置 status=cancelled。

        跑中的 step 循环协作检查 is_cancelled → 不再起新步(§0.7 逃生门)。已完成/已中止的返 False。
        """
        r = self._runs.get(run_id)
        if r is None or r.get("status") != "running":
            return False
        r["status"] = "cancelled"
        r["cancelled_at"] = self._clock()
        self._save()
        return True

    def status(self, run_id: str) -> str:
        r = self._runs.get(run_id)
        return r.get("status", "") if r is not None else ""

    def is_cancelled(self, run_id: str) -> bool:
        """执行循环每步前查它 —— True 就别再起新步(协作式中止)。"""
        return self.status(run_id) == "cancelled"

    def remove(self, run_id: str) -> None:
        if run_id in self._runs:
            del self._runs[run_id]
            self._save()

    def running(self) -> list:
        """所有未完成的运行。**注意:重启不再无条件复活这些**(逃生门解锁)——
        由 console 出 H2A 卡拍板续/丢,或对超 age 的先 sweep_stale 标 abandoned。"""
        return [r for r in self._runs.values() if r.get("status") == "running"]

    def sweep_stale(self, max_age_s: float) -> list:
        """重启时:把 age 超上限的 running 运行标 abandoned(不复活,逃生门)。返被丢弃的 run 列表。

        max_age_s<=0 → 不 sweep(全交 H2A 拍板)。一条跑歪烧 token 的 workflow 挂着重启,
        超时的直接死,不给它自动续烧的机会。
        """
        if max_age_s <= 0:
            return []
        now = self._clock()
        dropped = []
        changed = False
        for r in self._runs.values():
            if r.get("status") != "running":
                continue
            started = float(r.get("started_at") or 0.0)
            if started and (now - started) > max_age_s:
                r["status"] = "abandoned"
                r["abandoned_at"] = now
                dropped.append(r)
                changed = True
        if changed:
            self._save()
        return dropped


__all__ = ["WorkflowRunStore"]
