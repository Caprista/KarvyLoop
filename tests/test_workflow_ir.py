"""test_workflow_ir — workflow IR 加深(dev-report #3):条件分支 when / 容错 on_fail / 选择性
输入与分支合并 inputs / 级联跳过。借业界 workflow 语义、不借可视化编辑器。

AC:
- when:上游满足才跑;if/else(同上游相反 when,只走一支)
- 级联跳过:分支没选中 → 下游 skipped(剪枝);但**合并步**只要有一支产出就跑
- on_fail:retry 重试到成功;abort 中止全流程(剩余 skipped)
- inputs:只喂声明的上游;决定合并/跳过判定
"""
from __future__ import annotations

import pytest

from karvyloop.karvy.workflow import run_workflow


def _st(res):
    return {s["id"]: s["status"] for s in res["steps"]}


@pytest.mark.asyncio
async def test_when_status_gate_runs_only_if_satisfied():
    async def run_step(step, upstream):
        if step["id"] == "review":
            return {"output": "looks good"}
        return {"output": "ran"}
    plan = {"goal": "g", "steps": [
        {"id": "review", "depends_on": []},
        {"id": "ship", "depends_on": ["review"], "when": {"step": "review", "status": "done"}},
        {"id": "revise", "depends_on": ["review"], "when": {"step": "review", "status": "failed"}},
    ]}
    st = _st(await run_workflow(plan, run_step=run_step))
    assert st["ship"] == "done"          # review 成功 → 走发布
    assert st["revise"] == "skipped"     # 另一支没选中 → 跳过(不是失败)


@pytest.mark.asyncio
async def test_if_else_takes_failure_branch():
    async def run_step(step, upstream):
        if step["id"] == "review":
            return {"output": ""}        # 评审失败
        return {"output": "ran"}
    plan = {"goal": "g", "steps": [
        {"id": "review", "depends_on": []},
        {"id": "ship", "depends_on": ["review"], "when": {"step": "review", "status": "done"}},
        {"id": "revise", "depends_on": ["review"], "when": {"step": "review", "status": "failed"}},
    ]}
    st = _st(await run_workflow(plan, run_step=run_step))
    assert st["review"] == "failed" and st["ship"] == "skipped" and st["revise"] == "done"


@pytest.mark.asyncio
async def test_when_contains_on_output_text():
    async def run_step(step, upstream):
        return {"output": "VERDICT: 通过" if step["id"] == "judge" else "x"}
    plan = {"goal": "g", "steps": [
        {"id": "judge", "depends_on": []},
        {"id": "pass_path", "depends_on": ["judge"], "when": {"step": "judge", "contains": "通过"}},
        {"id": "fail_path", "depends_on": ["judge"], "when": {"step": "judge", "contains": "驳回"}},
    ]}
    st = _st(await run_workflow(plan, run_step=run_step))
    assert st["pass_path"] == "done" and st["fail_path"] == "skipped"


@pytest.mark.asyncio
async def test_cascade_skip_prunes_downstream_of_skipped():
    async def run_step(step, upstream):
        return {"output": "ok"} if step["id"] == "a" else {"output": "ran"}
    plan = {"goal": "g", "steps": [
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": ["a"], "when": {"step": "a", "status": "failed"}},  # a 成功→b 跳过
        {"id": "c", "depends_on": ["b"]},   # b 跳过 → c 级联跳过(整条没走的分支剪掉)
    ]}
    st = _st(await run_workflow(plan, run_step=run_step))
    assert st["b"] == "skipped" and st["c"] == "skipped"


@pytest.mark.asyncio
async def test_merge_step_runs_if_any_branch_produced():
    """合并步:两个 if/else 分支只走一支,合并步只要有一支产出就跑(不被另一支的跳过拖累)。"""
    async def run_step(step, upstream):
        if step["id"] == "review":
            return {"output": "good"}
        return {"output": f"{step['id']}:{sorted(upstream)}"}
    plan = {"goal": "g", "steps": [
        {"id": "review", "depends_on": []},
        {"id": "ship", "depends_on": ["review"], "when": {"step": "review", "status": "done"}},
        {"id": "revise", "depends_on": ["review"], "when": {"step": "review", "status": "failed"}},
        {"id": "notify", "depends_on": ["ship", "revise"]},   # 合并:发布或返工后都要通知
    ]}
    res = await run_workflow(plan, run_step=run_step)
    st = _st(res)
    assert st["ship"] == "done" and st["revise"] == "skipped"
    assert st["notify"] == "done"        # 合并步照跑(有 ship 这一支)
    out = {s["id"]: s["output"] for s in res["steps"]}
    assert "ship" in out["notify"]       # 只拿到产出的那支(revise 跳过、不在 upstream)


@pytest.mark.asyncio
async def test_on_fail_retry_until_success():
    calls = {"n": 0}
    async def run_step(step, upstream):
        if step["id"] == "flaky":
            calls["n"] += 1
            return {"output": "ok" if calls["n"] >= 3 else ""}   # 前两次失败,第三次成
        return {"output": "x"}
    plan = {"goal": "g", "steps": [
        {"id": "flaky", "depends_on": [], "on_fail": "retry", "max_retries": 3},
    ]}
    st = _st(await run_workflow(plan, run_step=run_step))
    assert st["flaky"] == "done" and calls["n"] == 3


@pytest.mark.asyncio
async def test_on_fail_retry_exhausts_then_failed():
    calls = {"n": 0}
    async def run_step(step, upstream):
        calls["n"] += 1
        return {"output": ""}        # 永远失败
    plan = {"goal": "g", "steps": [
        {"id": "x", "depends_on": [], "on_fail": "retry", "max_retries": 2},
    ]}
    st = _st(await run_workflow(plan, run_step=run_step))
    assert st["x"] == "failed" and calls["n"] == 3    # 1 次 + 重试 2 次


@pytest.mark.asyncio
async def test_on_fail_abort_stops_whole_flow():
    ran = []
    async def run_step(step, upstream):
        ran.append(step["id"])
        return {"output": ""} if step["id"] == "build" else {"output": "ok"}
    plan = {"goal": "g", "steps": [
        {"id": "build", "depends_on": [], "on_fail": "abort"},
        {"id": "deploy", "depends_on": ["build"]},   # build 中止 → 永不执行
    ]}
    res = await run_workflow(plan, run_step=run_step)
    st = _st(res)
    assert res["aborted"] is True
    assert st["build"] == "failed" and st["deploy"] == "skipped"
    assert "deploy" not in ran                        # 中止后不再跑下游


def test_sanitizer_forces_when_step_into_depends_on():
    """对抗验收修:when 引用的上游不在 depends_on → 会在它跑完前就判(fail-open 误触发)。
    _enrich_plan 必须把 when.step 强制补进 depends_on(让门有意义)。"""
    from karvyloop.console.routes import _enrich_plan
    roles = [{"role_id": "r1", "display": "R1", "agent_id": "a1", "domain_id": "d"},
             {"role_id": "r2", "display": "R2", "agent_id": "a2", "domain_id": "d"}]
    plan = {"goal": "g", "steps": [
        {"id": "s1", "role_id": "r1", "task": "review", "depends_on": []},
        # 注意:when 指 s1,但 depends_on 故意没列 s1(LLM 易犯)
        {"id": "s2", "role_id": "r2", "task": "rollback", "depends_on": [],
         "when": {"step": "s1", "status": "failed"}},
    ]}
    enriched = _enrich_plan(plan, roles)
    s2 = next(s for s in enriched["steps"] if s["id"] == "s2")
    assert "s1" in s2["depends_on"]                  # 被强制补进依赖 → 门会在 s1 跑完后才判


@pytest.mark.asyncio
async def test_when_gate_correct_after_sanitizer_fix():
    """端到端:经 _enrich_plan 净化后,when 门正确——s1 成功 → rollback 正确跳过(不再误触发)。"""
    from karvyloop.console.routes import _enrich_plan
    roles = [{"role_id": "r1", "display": "R1", "agent_id": "a1", "domain_id": "d"},
             {"role_id": "r2", "display": "R2", "agent_id": "a2", "domain_id": "d"}]
    plan = {"goal": "g", "steps": [
        {"id": "s1", "role_id": "r1", "task": "review", "depends_on": []},
        {"id": "s2", "role_id": "r2", "task": "rollback", "depends_on": [],
         "when": {"step": "s1", "status": "failed"}},
    ]}
    enriched = _enrich_plan(plan, roles)

    async def run_step(step, upstream):
        return {"output": "all good"}            # s1 成功
    st = _st(await run_workflow(enriched, run_step=run_step))
    assert st["s1"] == "done" and st["s2"] == "skipped"   # rollback 正确跳过(修前会误跑成 done)


@pytest.mark.asyncio
async def test_inputs_narrows_what_is_fed():
    async def run_step(step, upstream):
        if step["id"] in ("a", "b"):
            return {"output": step["id"]}
        return {"output": ",".join(sorted(upstream))}
    plan = {"goal": "g", "steps": [
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": []},
        # c 依赖 a、b 排序,但只**吃** a 的产出(inputs 收窄)
        {"id": "c", "depends_on": ["a", "b"], "inputs": ["a"]},
    ]}
    res = await run_workflow(plan, run_step=run_step)
    out = {s["id"]: s["output"] for s in res["steps"]}
    assert out["c"] == "a"           # 只喂了 a,没喂 b
