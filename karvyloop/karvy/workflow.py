"""karvy/workflow.py — 群内协作的 **workflow（工作流）模式**(ch4,Hardy 2026-06)。

群内协作两模式:圆桌(roundtable.py,开放讨论收敛)+ **workflow(本模块,结构化 角色→任务 DAG)**。
@多人(≥2)走 workflow:小卡按语义/岗位职责 + 你的目标设计一张 DAG → 你拍板 → 执行(依赖满足
的步骤并发、上游产出喂下游)→ 稳定成功后结晶给快脑匹配复用。

参照:Coze workflow = DAG(控制流+数据流);Claude Code workflow = 确定性编排 + pipeline/parallel。

本模块只做**纯执行引擎**(可测):`run_workflow(plan, run_step=...)` 拓扑并发跑、喂上游产出;
真驱动(按角色人格 drive)由 console 注入。规划(LLM 设计 DAG)+ 结晶在上层。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


def _topo_ok(steps: dict) -> bool:
    """DAG 无环且依赖都存在(规划产出的 plan 先过这关,别把环喂进执行)。"""
    ids = set(steps)
    for s in steps.values():
        for d in s.get("depends_on", []):
            if d not in ids:
                return False
    # Kahn 检测环
    indeg = {sid: 0 for sid in steps}
    for s in steps.values():
        for d in s.get("depends_on", []):
            indeg[s["id"]] += 1
    q = [sid for sid, n in indeg.items() if n == 0]
    seen = 0
    while q:
        cur = q.pop()
        seen += 1
        for s in steps.values():
            if cur in s.get("depends_on", []):
                indeg[s["id"]] -= 1
                if indeg[s["id"]] == 0:
                    q.append(s["id"])
    return seen == len(steps)


async def run_workflow(
    plan: dict,
    *,
    run_step: Callable[[dict, dict], Awaitable[dict]],
    max_parallel: int = 6,
) -> dict:
    """按 DAG 执行 workflow:依赖满足的步骤**并发**跑,**上游产出喂下游**(data flow)。

    plan: {"goal": str, "steps": [{"id","display","task","depends_on":[ids], ...}]}。
    run_step(step, upstream) -> awaitable dict(至少含 "output");抛错/None/空 output → 该步标 failed,
      但**不挡下游**(下游拿到它拿到的上游、照跑;失败步的 output 视为空)。
    upstream: {dep_id: dep_output} —— 只含该步直接依赖的、已完成步骤的产出。

    返回 {"goal", "steps":[{...step, "output", "status":"done"/"failed"}], "ok", "ran": [ids 完成序]}。
    无步骤 / 有环或悬空依赖 → ok=False(执行前就该被规划层拦,这里兜底)。
    """
    steps = {s["id"]: dict(s) for s in plan.get("steps", []) if s.get("id")}
    goal = plan.get("goal", "")
    if not steps or not _topo_ok(steps):
        return {"goal": goal, "steps": [], "ok": False, "ran": [],
                "reason": "空 workflow 或依赖有环/悬空"}

    attempted: dict[str, str] = {}      # id -> output(失败=空串;键存在=已尝试,解锁下游)
    status: dict[str, str] = {}
    ran_order: list = []
    remaining = set(steps)

    while remaining:
        ready = [sid for sid in remaining
                 if all(d in attempted for d in steps[sid].get("depends_on", []))]
        if not ready:
            break   # 兜底:理论上 _topo_ok 已保证不会卡(无环)
        ready = ready[:max_parallel]

        async def _one(sid):
            up = {d: attempted.get(d, "") for d in steps[sid].get("depends_on", [])}
            try:
                r = await run_step(steps[sid], up)
            except Exception:
                r = None
            out = ((r or {}).get("output") or "").strip()
            return sid, out

        results = await asyncio.gather(*[_one(sid) for sid in ready])
        for sid, out in results:
            remaining.discard(sid)
            attempted[sid] = out
            if out:
                status[sid] = "done"
                ran_order.append(sid)
            else:
                status[sid] = "failed"

    out_steps = [
        {**steps[sid], "output": attempted.get(sid, ""), "status": status.get(sid, "skipped")}
        for sid in steps
    ]
    return {"goal": goal, "steps": out_steps, "ok": bool(ran_order), "ran": ran_order}


__all__ = ["run_workflow"]
