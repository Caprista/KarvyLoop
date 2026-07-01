"""test_roundtable_scale — 50+ 角色单圆桌/单工作流的执行压力(Hardy 2026-06-27).

之前只回归了"场景"没回归"压力":圆桌硬上限 6 人静默截断、50 路同时打一把 key 会截断。
这里验:① 大桌全员上桌(不再截到 6)② 并发**分批**(峰值受 concurrency 限,不 50 路齐发)
③ max_seats 截断仍诚实 ④ 工作流 50+ 步按 max_parallel 跑完。
"""
from __future__ import annotations

import asyncio

from karvyloop.karvy.roundtable import run_roundtable
from karvyloop.karvy.workflow import run_workflow


class _ConcTracker:
    """记录并发峰值:进入 +1/记峰值,让出控制权强制重叠,退出 -1。"""
    def __init__(self):
        self.cur = 0
        self.peak = 0

    async def run(self, label):
        self.cur += 1
        self.peak = max(self.peak, self.cur)
        for _ in range(3):
            await asyncio.sleep(0)      # 让同批其它协程也进来 → 真重叠
        self.cur -= 1
        return label


# ---- 圆桌:50 人大桌 ----
def test_roundtable_seats_50_in_bounded_waves():
    members = [f"m{i}" for i in range(50)]
    t = _ConcTracker()

    async def drive(m):
        await t.run(m)
        return {"speaker": m, "text": f"{m} 的发言"}

    replies = asyncio.run(run_roundtable("议题", members, drive_member=drive,
                                         max_seats=64, concurrency=6))
    assert len(replies) == 50, f"50 人没全上桌(还在截到 6?): {len(replies)}"
    assert t.peak <= 6, f"并发没受控(50 路齐发会截断 key): peak={t.peak}"
    assert t.peak >= 2, "根本没并发(退化成串行)"


def test_roundtable_max_seats_truncates_honestly():
    members = [f"m{i}" for i in range(50)]

    async def drive(m):
        return {"speaker": m, "text": "x"}

    replies = asyncio.run(run_roundtable("议题", members, drive_member=drive,
                                         max_seats=10, concurrency=6))
    assert len(replies) == 10, "max_seats 截断没生效"


def test_roundtable_failed_seats_skipped_not_crash():
    members = [f"m{i}" for i in range(20)]

    async def drive(m):
        if m in ("m3", "m7"):
            raise RuntimeError("座位挂了")
        if m == "m9":
            return {"speaker": m, "text": "   "}   # 空发言 → 跳过
        return {"speaker": m, "text": "ok"}

    replies = asyncio.run(run_roundtable("议题", members, drive_member=drive,
                                         max_seats=64, concurrency=6))
    assert len(replies) == 17, f"挂的/空的座位没被干净跳过: {len(replies)}"  # 20 - 2 异常 - 1 空


# ---- 工作流:50+ 步 ----
def test_workflow_runs_50_steps_bounded_parallel():
    plan = {"goal": "压测", "steps": [{"id": f"s{i}", "display": f"步{i}",
                                      "task": "t", "depends_on": []} for i in range(50)]}
    t = _ConcTracker()

    async def run_step(step, upstream):
        await t.run(step["id"])
        return {"output": f"done {step['id']}"}

    res = asyncio.run(run_workflow(plan, run_step=run_step, max_parallel=6))
    assert res["ok"] and len(res["ran"]) == 50, f"50 步没跑完: {len(res['ran'])}"
    assert t.peak <= 6, f"工作流并发没受控: peak={t.peak}"


def test_workflow_chain_50_deep_data_flows():
    """50 步链式依赖:上游产出喂下游,串到底。"""
    steps = [{"id": "s0", "display": "0", "task": "t", "depends_on": []}]
    for i in range(1, 50):
        steps.append({"id": f"s{i}", "display": str(i), "task": "t", "depends_on": [f"s{i-1}"]})
    plan = {"goal": "链", "steps": steps}

    async def run_step(step, upstream):
        return {"output": f"{step['id']}<-{','.join(upstream.values())}"}

    res = asyncio.run(run_workflow(plan, run_step=run_step, max_parallel=6))
    assert res["ok"] and len(res["ran"]) == 50
    last = next(s for s in res["steps"] if s["id"] == "s49")
    assert "s48" in last["output"], "数据流没串到底"


# ---- API 上限放开(50+ 不再被 422 拦)----
def test_api_request_models_accept_50():
    from karvyloop.console.routes import RoundtableStartRequest, WorkflowPlanRequest
    RoundtableStartRequest(intent="议题", participants=[f"a{i}" for i in range(50)])   # 不抛
    WorkflowPlanRequest(intent="活", mentions=[{"agent_id": f"a{i}", "domain_id": "d"} for i in range(50)])
    # 超过 64 仍拦(防真·失控)
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RoundtableStartRequest(intent="议题", participants=[f"a{i}" for i in range(100)])
