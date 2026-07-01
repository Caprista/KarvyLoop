"""workflow_runs — 工作流运行的**持久化执行**(durable execution,借 EVE,#39 ①)。

问题:长 workflow(多角色 drive)跑一半 console 重启 → 整轮丢、白烧的 token 没了。
解法:**每步产出 memoize 落盘**;重启时把未完成的 run **replay** —— 已完成步秒命中缓存、只续跑剩余步
(= EVE 的"事件日志确定性重放"套我们的 step DAG)。

只管"存运行态 + 算哪些没跑完";真执行(按角色 drive)+ 重启时 replay 的接线在 console 层。
落盘 workflow_runs.json(原子写)+ 重启恢复;无 path → 纯内存(测试不污染 home)。
"""
from __future__ import annotations

import json
import pathlib
from typing import Optional


class WorkflowRunStore:
    """活跃 workflow 运行态:{run_id → {goal, steps, domain_id, step_outputs, status}}。

    status: running(在跑/被中断待续) / done(完成)。done 的保留一小段供查,可由调用方清。
    step_outputs: {step_id → output};memoize 的家 —— 有就别重跑。
    """

    def __init__(self, path: Optional[pathlib.Path] = None) -> None:
        self._path = pathlib.Path(path) if path else None
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

    def create(self, run_id: str, *, goal: str, steps: list, domain_id: str, started_at: float = 0.0) -> None:
        """登记一个新运行(status=running)。steps 存原始 step 规格(含 agent_id/domain_id/task/depends_on)。"""
        self._runs[run_id] = {
            "run_id": run_id, "goal": goal, "steps": steps, "domain_id": domain_id,
            "step_outputs": {}, "status": "running", "started_at": started_at,
        }
        self._save()

    def get(self, run_id: str) -> Optional[dict]:
        return self._runs.get(run_id)

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
        if r is not None:
            r["status"] = "done"
            self._save()

    def remove(self, run_id: str) -> None:
        if run_id in self._runs:
            del self._runs[run_id]
            self._save()

    def running(self) -> list:
        """所有未完成的运行(重启时 replay 这些)。"""
        return [r for r in self._runs.values() if r.get("status") == "running"]


__all__ = ["WorkflowRunStore"]
