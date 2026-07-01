"""test_workflow_runs — #39 ① 持久化执行:运行态落盘 + memoize/replay(重启续,完成步秒命中不重跑)。"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.karvy.workflow import run_workflow  # noqa: E402
from karvyloop.karvy.workflow_runs import WorkflowRunStore  # noqa: E402


def test_store_crud_and_persist(tmp_path):
    p = tmp_path / "workflow_runs.json"
    st = WorkflowRunStore(p)
    st.create("r1", goal="做登录页", steps=[{"id": "s1", "agent_id": "pm"}], domain_id="d1")
    st.set_step("r1", "s1", "需求文档")
    assert st.step_output("r1", "s1") == "需求文档"
    assert st.step_output("r1", "nope") is None     # 没跑过 → None(=要跑)
    assert [r["run_id"] for r in st.running()] == ["r1"]
    # 重新加载(模拟重启)→ 运行态 + 已完成步缓存都在
    st2 = WorkflowRunStore(p)
    assert st2.step_output("r1", "s1") == "需求文档"
    assert len(st2.running()) == 1
    st2.finish("r1")
    assert WorkflowRunStore(p).running() == []       # done 不再被 replay


def _memoized_run_step(store, run_id, drives):
    """模拟 execute_workflow_durable 的 run_step:缓存命中不跑;否则"drive"+落盘。"""
    async def run_step(step, upstream):
        sid = step["id"]
        cached = store.step_output(run_id, sid)
        if cached is not None:
            return {"output": cached}     # 重启续:秒命中
        drives.append(sid)                # 真跑了一次
        out = f"{sid}-产出"
        store.set_step(run_id, sid, out)
        return {"output": out}
    return run_step


def test_replay_skips_completed_steps(tmp_path):
    p = tmp_path / "wr.json"
    plan = {"goal": "G", "steps": [
        {"id": "s1", "depends_on": []},
        {"id": "s2", "depends_on": ["s1"]},
    ]}
    # 首跑:登记 + 两步都真跑
    st = WorkflowRunStore(p); st.create("R", goal="G", steps=plan["steps"], domain_id="d")
    d1 = []
    r1 = asyncio.run(run_workflow(plan, run_step=_memoized_run_step(st, "R", d1)))
    assert r1["ok"] and d1 == ["s1", "s2"]            # 首跑两步都烧了
    # 模拟"崩在记录前"→ 没 finish。重启:新 store 从盘加载 → replay
    st2 = WorkflowRunStore(p)
    assert len(st2.running()) == 1                    # 还在 running,待续
    d2 = []
    r2 = asyncio.run(run_workflow(plan, run_step=_memoized_run_step(st2, "R", d2)))
    assert r2["ok"] and d2 == []                      # **续跑零重烧**:两步都命中缓存
    assert r2["steps"][1]["output"] == "s2-产出"        # 结果一致


def test_replay_resumes_after_partial(tmp_path):
    # 只完成了 s1 就崩 → 重启续:s1 命中、只跑 s2
    p = tmp_path / "wr2.json"
    plan = {"goal": "G", "steps": [{"id": "s1", "depends_on": []}, {"id": "s2", "depends_on": ["s1"]}]}
    st = WorkflowRunStore(p); st.create("R", goal="G", steps=plan["steps"], domain_id="d")
    st.set_step("R", "s1", "s1-产出")                 # 模拟:s1 跑完落盘,s2 没跑就崩
    d = []
    asyncio.run(run_workflow(plan, run_step=_memoized_run_step(WorkflowRunStore(p), "R", d)))
    assert d == ["s2"]                                # 只续跑了 s2,s1 没重烧
