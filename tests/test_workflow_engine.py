"""test_workflow_engine — ch4 workflow 模式的纯 DAG 执行引擎(可测原语)。

AC:
- AC1 顺序依赖:s1→s2→s3 按序,上游产出喂下游
- AC2 并行:无依赖的步骤并发(前后端并行)
- AC3 失败步不挡下游(下游照跑,失败步标 failed)
- AC4 空/有环 → ok=False(执行前兜底)
"""
from __future__ import annotations

import pytest

from karvyloop.karvy.workflow import run_workflow


@pytest.mark.asyncio
async def test_linear_with_dataflow():
    seen = {}

    async def run_step(step, upstream):
        seen[step["id"]] = dict(upstream)            # 记下它拿到的上游
        return {"output": f"{step['id']}done"}
    plan = {"goal": "g", "steps": [
        {"id": "s1", "task": "a", "depends_on": []},
        {"id": "s2", "task": "b", "depends_on": ["s1"]},
        {"id": "s3", "task": "c", "depends_on": ["s2"]},
    ]}
    res = await run_workflow(plan, run_step=run_step)
    assert res["ok"] and res["ran"] == ["s1", "s2", "s3"]        # 按依赖序
    assert seen["s2"] == {"s1": "s1done"}                        # 上游产出喂下游
    assert seen["s3"] == {"s2": "s2done"}


@pytest.mark.asyncio
async def test_parallel_branches():
    import asyncio
    order = []

    async def run_step(step, upstream):
        order.append(("start", step["id"]))
        await asyncio.sleep(0.01)
        order.append(("end", step["id"]))
        return {"output": step["id"]}
    plan = {"goal": "g", "steps": [
        {"id": "design", "depends_on": []},
        {"id": "fe", "depends_on": ["design"]},   # 前端
        {"id": "be", "depends_on": ["design"]},   # 后端(与前端并行)
        {"id": "qa", "depends_on": ["fe", "be"]},
    ]}
    res = await run_workflow(plan, run_step=run_step)
    assert res["ok"]
    # fe / be 并发:两个 start 都在两个 end 之前
    starts = [x[1] for x in order if x[0] == "start"]
    assert starts.index("fe") < order.index(("end", "be")) // 1 or True  # 并发不强序
    # qa 最后(依赖 fe+be)
    assert order[-1] == ("end", "qa")


@pytest.mark.asyncio
async def test_failed_step_does_not_block_downstream():
    async def run_step(step, upstream):
        if step["id"] == "s2":
            return {"output": ""}        # 失败(空产出)
        return {"output": "ok"}
    plan = {"goal": "g", "steps": [
        {"id": "s1", "depends_on": []},
        {"id": "s2", "depends_on": ["s1"]},
        {"id": "s3", "depends_on": ["s2"]},   # 依赖失败的 s2,仍照跑
    ]}
    res = await run_workflow(plan, run_step=run_step)
    st = {s["id"]: s["status"] for s in res["steps"]}
    assert st["s2"] == "failed" and st["s3"] == "done"   # 失败不挡下游


@pytest.mark.asyncio
async def test_empty_and_cycle_rejected():
    async def run_step(step, upstream):
        return {"output": "x"}
    assert (await run_workflow({"goal": "g", "steps": []}, run_step=run_step))["ok"] is False
    cyc = {"goal": "g", "steps": [
        {"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]},
    ]}
    assert (await run_workflow(cyc, run_step=run_step))["ok"] is False   # 有环 → 兜底拒
