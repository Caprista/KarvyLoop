"""test_workflow_escape_hatch — #54 编排逃生门:cancel / resume 不复活 / 失败带原因 / infra-dead
fail-loud / ok 真实成败。

病灶(雷达实锤):
1. 跑起来无法 cancel/pause;
2. 重启无条件复活所有 running 态(逃生门反锁);
3. 失败吞异常不带原因、盲 retry 不分 infra-dead;ok=bool(ran_order) 掩盖真实成败。
本测试走**真** run_workflow 引擎 + 真 WorkflowRunStore,LLM 步用 stub。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from karvyloop.karvy.roundtable import run_roundtable_session  # noqa: E402
from karvyloop.karvy.workflow import run_workflow  # noqa: E402
from karvyloop.karvy.workflow_runs import WorkflowRunStore  # noqa: E402


def _st(res):
    return {s["id"]: s["status"] for s in res["steps"]}


# ================= 病灶 3:失败带原因 + ok 真实成败 =================

def test_failed_step_carries_error_reason():
    """✗ 步必须带**原因**进最终结果(别静默空白,MAST 21%)。"""
    async def run_step(step, upstream):
        if step["id"] == "s2":
            return {"output": "", "error": "模型返回了空"}
        return {"output": "ok"}
    plan = {"goal": "g", "steps": [
        {"id": "s1", "depends_on": []},
        {"id": "s2", "depends_on": ["s1"]},
    ]}
    res = asyncio.run(run_workflow(plan, run_step=run_step))
    by_id = {s["id"]: s for s in res["steps"]}
    assert by_id["s2"]["status"] == "failed"
    assert by_id["s2"]["error"] == "模型返回了空"      # 原因带进来了
    assert res["ok"] is False                          # 有 failed → 不再假 ok


def test_failure_without_error_gets_fallback_reason():
    """步返回空 output 且没给 error → 引擎兜个原因,别留空白 ✗。"""
    async def run_step(step, upstream):
        return {"output": ""}
    plan = {"goal": "g", "steps": [{"id": "x", "depends_on": []}]}
    res = asyncio.run(run_workflow(plan, run_step=run_step))
    x = res["steps"][0]
    assert x["status"] == "failed" and x["error"]      # 非空原因


def test_step_exception_becomes_error_not_swallowed():
    """步内抛异常不再 except: out='' 吞掉 —— 收成失败原因。"""
    async def run_step(step, upstream):
        raise RuntimeError("boom in step")
    plan = {"goal": "g", "steps": [{"id": "x", "depends_on": []}]}
    res = asyncio.run(run_workflow(plan, run_step=run_step))
    assert res["steps"][0]["status"] == "failed"
    assert "boom in step" in res["steps"][0]["error"]


def test_ok_reflects_true_success_not_ran_any():
    """10 步成 1 步不算 ok=True(旧 ok=bool(ran_order) 的假阳性)。"""
    async def run_step(step, upstream):
        return {"output": "done"} if step["id"] == "s1" else {"output": ""}
    plan = {"goal": "g", "steps": [
        {"id": "s1", "depends_on": []},
        {"id": "s2", "depends_on": ["s1"]},
        {"id": "s3", "depends_on": ["s1"]},
    ]}
    res = asyncio.run(run_workflow(plan, run_step=run_step))
    assert res["ran"] == ["s1"]        # 真跑成一步
    assert res["ok"] is False          # 但整体没成 → ok=False


def test_ok_true_when_all_done():
    async def run_step(step, upstream):
        return {"output": "ok"}
    plan = {"goal": "g", "steps": [
        {"id": "s1", "depends_on": []},
        {"id": "s2", "depends_on": ["s1"]},
    ]}
    res = asyncio.run(run_workflow(plan, run_step=run_step))
    assert res["ok"] is True and _st(res) == {"s1": "done", "s2": "done"}


# ================= 病灶 3:infra-dead fail-loud,不盲 retry =================

def test_infra_dead_aborts_and_does_not_retry():
    """infra-dead(基础能力失效)→ fail-loud 中止全流程,且**不**耗尽 retry(同路重试没意义)。"""
    calls = {"n": 0}
    async def run_step(step, upstream):
        if step["id"] == "dead":
            calls["n"] += 1
            return {"output": "", "error": "gateway unreachable", "infra_dead": True}
        return {"output": "ok"}
    plan = {"goal": "g", "steps": [
        {"id": "dead", "depends_on": [], "on_fail": "retry", "max_retries": 3},
        {"id": "after", "depends_on": ["dead"]},
    ]}
    res = asyncio.run(run_workflow(plan, run_step=run_step))
    assert res["infra_dead"] is True
    assert res["aborted"] is True
    assert calls["n"] == 1                    # infra-dead 不盲 retry(没跑 4 次)
    assert _st(res)["after"] == "skipped"     # 下游未执行
    assert res["ok"] is False


def test_normal_failure_still_retries():
    """普通失败(非 infra-dead)仍按 on_fail=retry 重试(别误把所有失败都当 infra-dead)。"""
    calls = {"n": 0}
    async def run_step(step, upstream):
        calls["n"] += 1
        return {"output": "", "error": "just a bad answer"}
    plan = {"goal": "g", "steps": [
        {"id": "x", "depends_on": [], "on_fail": "retry", "max_retries": 2},
    ]}
    res = asyncio.run(run_workflow(plan, run_step=run_step))
    assert calls["n"] == 3                     # 1 + 2 retry(普通失败照重)
    assert res["infra_dead"] is False


# ================= 病灶 1:cancel 真中止(剩余步 skipped) =================

def test_cancel_stops_starting_new_steps():
    """should_cancel() 触发 → 不再起新步,剩余步 skipped,run cancelled=True。"""
    ran = []
    flag = {"cancel": False}

    async def run_step(step, upstream):
        ran.append(step["id"])
        flag["cancel"] = True          # 跑完第一步后就"点了中止"
        return {"output": "ok"}

    plan = {"goal": "g", "steps": [
        {"id": "s1", "depends_on": []},
        {"id": "s2", "depends_on": ["s1"]},
        {"id": "s3", "depends_on": ["s2"]},
    ]}
    res = asyncio.run(run_workflow(plan, run_step=run_step,
                                   should_cancel=lambda: flag["cancel"]))
    assert res["cancelled"] is True
    assert ran == ["s1"]               # 中止后不再起 s2/s3
    st = _st(res)
    assert st["s2"] == "skipped" and st["s3"] == "skipped"
    assert res["ok"] is False          # 被取消 → 不算成功


def test_cancel_before_first_step():
    """一开始就中止 → 一步不跑,全 skipped。"""
    ran = []
    async def run_step(step, upstream):
        ran.append(step["id"]); return {"output": "ok"}
    plan = {"goal": "g", "steps": [{"id": "s1", "depends_on": []}]}
    res = asyncio.run(run_workflow(plan, run_step=run_step, should_cancel=lambda: True))
    assert ran == [] and res["cancelled"] is True
    assert _st(res)["s1"] == "skipped"


# ================= WorkflowRunStore:cancel / sweep_stale / find_by_task =================

def test_store_cancel_and_is_cancelled(tmp_path):
    st = WorkflowRunStore(tmp_path / "wr.json")
    st.create("R", goal="g", steps=[{"id": "s1"}], domain_id="d", task_id="T1")
    assert st.is_cancelled("R") is False
    assert st.cancel("R") is True
    assert st.is_cancelled("R") is True
    assert st.status("R") == "cancelled"
    # cancel 落盘:重启后仍是 cancelled(不复活)
    assert WorkflowRunStore(tmp_path / "wr.json").status("R") == "cancelled"
    # 已中止的 finish 不能覆盖回 done(保住"人踩了刹车"的真相)
    st.finish("R")
    assert st.status("R") == "cancelled"


def test_store_find_by_task(tmp_path):
    st = WorkflowRunStore(tmp_path / "wr.json")
    st.create("R", goal="g", steps=[], domain_id="d", task_id="T7")
    assert st.find_by_task("T7")["run_id"] == "R"
    assert st.find_by_task("nope") is None


def test_cancelled_run_not_in_running(tmp_path):
    st = WorkflowRunStore(tmp_path / "wr.json")
    st.create("R", goal="g", steps=[], domain_id="d")
    st.cancel("R")
    assert st.running() == []        # 中止的不再被当作"待续"


# ================= 病灶 2:resume 不无条件复活(sweep_stale) =================

def test_sweep_stale_abandons_old_running(tmp_path):
    """超 age 上限的 running → abandoned(不复活)。这就是"重启杀掉跑歪的" 的底层。"""
    now = [1000.0]
    st = WorkflowRunStore(tmp_path / "wr.json", clock=lambda: now[0])
    st.create("old", goal="跑歪烧token", steps=[{"id": "s1"}], domain_id="d")   # started_at=1000
    now[0] = 1000.0 + 10 * 3600       # 10h 后重启(> 6h 上限)
    dropped = st.sweep_stale(max_age_s=6 * 3600)
    dropped_ids = {r["run_id"] for r in dropped}
    assert "old" in dropped_ids
    assert st.status("old") == "abandoned"
    assert st.running() == []         # 超时 → 不复活


def test_sweep_stale_keeps_fresh_running(tmp_path):
    """没超 age 的 running 不动(交由 H2A 拍板续/丢,不自动烧)。"""
    now = [1000.0]
    st = WorkflowRunStore(tmp_path / "wr.json", clock=lambda: now[0])
    st.create("fresh", goal="g", steps=[{"id": "s1"}], domain_id="d")
    now[0] = 1000.0 + 60             # 才过 1 分钟
    dropped = st.sweep_stale(max_age_s=6 * 3600)
    assert dropped == []
    assert st.status("fresh") == "running"     # 仍挂着待人拍板,但没被自动复活


def test_sweep_stale_disabled_when_zero(tmp_path):
    now = [1000.0]
    st = WorkflowRunStore(tmp_path / "wr.json", clock=lambda: now[0])
    st.create("old", goal="g", steps=[], domain_id="d")
    now[0] = 1000.0 + 10 ** 9
    assert st.sweep_stale(max_age_s=0) == []    # 关闭 sweep → 全交拍板


# ================= 圆桌 cancel:每轮开始前刹车 =================

def test_roundtable_cancel_stops_next_round():
    """圆桌中止 → 不再烧下一轮 token,拿已有 transcript 返回 cancelled=True。"""
    rounds_started = {"n": 0}
    flag = {"cancel": False}

    async def member_reply(m, topic, transcript):
        return {"speaker": m, "text": f"{m} says something in round"}

    async def host_moderate(topic, transcript, *, final):
        if not final:
            rounds_started["n"] += 1
            flag["cancel"] = True          # 第一轮后就点了中止
            return {"action": "continue"}   # 本来还想继续
        return {"text": "结论"}

    res = asyncio.run(run_roundtable_session(
        "话题", ["a", "b"], member_reply=member_reply, host_moderate=host_moderate,
        max_rounds=3, should_cancel=lambda: flag["cancel"]))
    assert res["cancelled"] is True
    assert res["rounds"] == 1              # 只跑了第一轮,没起第二轮
    assert res["conclusion"] == ""         # 中止不烧 host 收敛调用


# ================= 端点层:cancel / resume / discard 走真 console app =================

from karvyloop.cognition.conversation import (  # noqa: E402
    ConversationManager, ConversationStore,
)
from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.console.tasks import TaskRegistry  # noqa: E402
from karvyloop.domain.registry import Address, BusinessDomainRegistry  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


@pytest.fixture
def app(tmp_path):
    reg = BusinessDomainRegistry()
    mgr = ConversationManager(ConversationStore(tmp_path / "c"), domain_registry=reg)
    mgr.start()
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    a.state.conversation_manager = mgr
    a.state.domain_registry = reg
    a.state.task_registry = TaskRegistry()
    a.state.main_loop = object()
    a.state.config_path = str(tmp_path / "config.yaml")
    a.state.runtime_kwargs = {"gateway": object(), "model_ref": "x", "workspace_root": "/"}
    mgr.set_peer(Address(domain_id="l0", role="group", agent_id=""))
    return a


def test_cancel_endpoint_by_task_id_marks_run_cancelled(app):
    """POST /api/workflow/cancel(task_id)→ 定位到 run 并置 cancelled + 记中止旗。"""
    from karvyloop.console.routes import _workflow_run_store
    store = _workflow_run_store(app)
    store.create("R1", goal="g", steps=[{"id": "s1"}], domain_id="l0", task_id="TID")
    c = TestClient(app)
    body = c.post("/api/workflow/cancel", json={"task_id": "TID"}).json()
    assert body["ok"] is True and body["cancelled"] is True
    assert store.status("R1") == "cancelled"          # run 被标死
    # 中止旗也记上了(圆桌/durable 兜底路径靠它)
    from karvyloop.console.workflow_engine import _is_task_cancelled
    assert _is_task_cancelled(app, "TID") is True


def test_cancel_endpoint_by_run_id(app):
    from karvyloop.console.routes import _workflow_run_store
    store = _workflow_run_store(app)
    store.create("R2", goal="g", steps=[], domain_id="l0")
    body = TestClient(app).post("/api/workflow/cancel", json={"run_id": "R2"}).json()
    assert body["cancelled"] is True and store.status("R2") == "cancelled"


def test_roundtable_cancel_endpoint_marks_flag(app):
    from karvyloop.console.workflow_engine import _is_task_cancelled
    body = TestClient(app).post("/api/roundtable/cancel", json={"task_id": "RT1"}).json()
    assert body["ok"] is True
    assert _is_task_cancelled(app, "RT1") is True


def test_pending_resume_and_resume_endpoint(app, monkeypatch):
    """病灶 2 端点验:重启后中断流程挂起(不复活)→ /pending_resume 可见 → /resume 才真续跑。"""
    import karvyloop.console.workflow_engine as we
    from karvyloop.console.routes import _workflow_run_store
    from karvyloop.runtime.main_loop import Brain
    from karvyloop.workbench.main_loop_bridge import DriveOutcome

    store = _workflow_run_store(app)
    store.create("PR", goal="半截流程", steps=[{"id": "s1", "agent_id": "x", "domain_id": "l0",
                                             "task": "t", "depends_on": []}], domain_id="l0")

    # resume_workflows:不自动复活 → PR 仍 running,进 pending 清单
    summary = asyncio.run(we.resume_workflows(app, max_age_s=6 * 3600))
    assert summary["pending"] == 1 and summary["abandoned"] == 0
    assert store.status("PR") == "running"            # 没被自动跑掉

    pend = TestClient(app).get("/api/workflow/pending_resume").json()["pending"]
    assert any(p["run_id"] == "PR" for p in pend)

    # 只有人显式 /resume 才真续跑(drive_in_tui 由 execute_workflow_durable 从 routes 导入,patch 那里)
    import karvyloop.console.routes as routes_mod
    async def fake_drive(intent, ml, *, persona=None, **kw):
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="续跑产出",
                            skill_name="", fast_brain_hit=False, crystallized=False, task_id="t")
    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)
    body = TestClient(app).post("/api/workflow/resume", json={"run_id": "PR"}).json()
    assert body["ok"] is True and body["workflow"]["ok"] is True
    assert store.status("PR") == "done"


def test_discard_endpoint_kills_without_resuming(app):
    """病灶 2 最疼验:重启杀掉跑歪的 —— /discard 标死,绝不复活。"""
    from karvyloop.console.routes import _workflow_run_store
    store = _workflow_run_store(app)
    store.create("BAD", goal="跑歪烧token", steps=[{"id": "s1"}], domain_id="l0")
    body = TestClient(app).post("/api/workflow/discard", json={"run_id": "BAD"}).json()
    assert body["ok"] is True
    assert store.status("BAD") == "cancelled"         # 标死
    assert store.running() == []                      # 不再被当"待续"复活


def test_frontend_abort_button_present():
    """前端"中止"按钮存在:运行中 workflow/圆桌卡有 task-abort → 打 cancel 端点。"""
    static = ROOT / "karvyloop" / "console" / "static"
    appjs = (static / "app.js").read_text(encoding="utf-8")
    assert "task-abort" in appjs                         # 按钮 class
    assert "_abortTask" in appjs                          # 点击处理
    assert "/api/workflow/cancel" in appjs                # workflow 中止端点
    assert "/api/roundtable/cancel" in appjs              # 圆桌中止端点
    # i18n:两张表都有 task.abort(parity 测试另外锁 en/zh 一致)
    i18n = (static / "i18n.js").read_text(encoding="utf-8")
    assert i18n.count('"task.abort"') >= 2


def test_resume_workflows_abandons_stale_at_startup(app):
    """启动 resume:超 age 的中断流程直接 abandoned,不进 pending、不复活。"""
    import karvyloop.console.workflow_engine as we
    from karvyloop.console.routes import _workflow_run_store
    now = [1000.0]
    store = _workflow_run_store(app)
    store._clock = lambda: now[0]                     # 控时钟
    store.create("STALE", goal="老流程", steps=[{"id": "s1"}], domain_id="l0",
                 started_at=1000.0)
    now[0] = 1000.0 + 10 * 3600
    summary = asyncio.run(we.resume_workflows(app, max_age_s=6 * 3600))
    assert summary["abandoned"] == 1 and summary["pending"] == 0
    assert store.status("STALE") == "abandoned"
