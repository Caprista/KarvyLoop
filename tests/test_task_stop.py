"""docs/90 刀3a:停止控件贴每条活任务 —— kind 字段 / running-run 注册表 / 通用 cancel 端点。

四层验:
  ① kind 字段落到 running 记录(显式类型替代 who 嗅探;老记录无 kind 退 "" 不崩)。
  ② atoms.abort:注册表 + abort_scope 的 contextvar 穿 asyncio.to_thread + 线程内
     asyncio.run(生产链:routes → drive_in_tui(to_thread) → slow_brain(asyncio.run)
     → forge → executor)。
  ③ executor step 0 死代码复活:旗拉响 → 下一轮循环边界 ABORTED_*(协作式,不杀进程)。
  ④ POST /api/task/cancel:真置协作旗 + 置 abort_requested;fail-loud(不存在 → 404)。
前端静态测(⏹ 停止不再靠 who 嗅探)也在本文件。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from karvyloop.atoms.abort import (
    AbortFlag,
    RunningRunRegistry,
    abort_scope,
    current_abort_flag,
    running_runs,
)
from karvyloop.console.tasks import TaskRecord, TaskRegistry

ROOT = Path(__file__).resolve().parents[1]


# ================= ① kind 字段(显式任务类型) =================

def test_kind_lands_on_running_record():
    reg = TaskRegistry()
    tid = reg.start(who="小卡", intent="做个事", kind="drive")
    d = reg.get(tid)
    assert d["status"] == "running" and d["kind"] == "drive"
    assert reg.list()[0]["kind"] == "drive"       # 列表 dict(前端吃的形状)也带


def test_kind_default_empty_and_old_record_compat():
    reg = TaskRegistry()
    tid = reg.start(who="小卡", intent="旧调用不带 kind")
    assert reg.get(tid)["kind"] == ""             # 不传 = ""(前端退 who 嗅探)
    # 老 tasks.json 记录无 kind 键 → from_dict 不崩、kind==""
    old = {"id": "t1", "who": "⚙ 工作流", "intent": "x", "status": "done"}
    t = TaskRecord.from_dict(old)
    assert t.kind == "" and t.to_dict()["kind"] == ""


def test_kind_persists_across_reload(tmp_path):
    from karvyloop.console.tasks import TaskStore
    store = TaskStore(tmp_path / "tasks.json")
    reg = TaskRegistry(store=store)
    tid = reg.start(who="🎡 圆桌", intent="讨论", kind="roundtable")
    reg.finish(tid, result="ok")
    reg2 = TaskRegistry(store=TaskStore(tmp_path / "tasks.json"))
    assert reg2.get(tid)["kind"] == "roundtable"


# ================= ② atoms.abort 注册表 + contextvar 传递 =================

def test_registry_register_abort_unregister():
    reg = RunningRunRegistry()
    flag = reg.register("T1")
    assert reg.is_running("T1") and not flag.is_set()
    assert reg.request_abort("T1") is True and flag.is_set()
    reg.unregister("T1")
    assert not reg.is_running("T1")
    # 没登记的 task → False(如实上报,不装成功)
    assert reg.request_abort("nope") is False


def test_abort_scope_registers_and_cleans():
    with abort_scope("T2") as flag:
        assert running_runs.is_running("T2") and flag is not None
        assert current_abort_flag() is flag
    assert not running_runs.is_running("T2")      # run 完就清
    assert current_abort_flag() is None
    # task_id 空 = no-op(--no-llm / 无 registry 路径零回归)
    with abort_scope("") as f2:
        assert f2 is None and current_abort_flag() is None


def test_nested_scope_same_id_keeps_outer_flag_alive():
    """对抗验收 E2 的潜伏雷回归锁:嵌套同 id scope,内层退出**不许**把外层的旗弹丢
    (注册表引用计数 —— 否则外层 run 从此不可停)。"""
    reg = RunningRunRegistry()
    with abort_scope("TN", registry=reg) as outer:
        with abort_scope("TN", registry=reg) as inner:
            assert inner is outer                      # 同 id 复用同一面旗
        # 内层退出后:外层仍在册、仍可停
        assert reg.is_running("TN"), "内层 scope 退出把外层的旗弹丢了(引用计数失效)"
        assert reg.request_abort("TN") is True and outer.is_set()
    assert not reg.is_running("TN")                    # 外层退出才真清


def test_flag_propagates_through_to_thread_and_asyncio_run():
    """生产链等价传递验证:contextvar 穿 asyncio.to_thread(copy_context)+
    线程内 asyncio.run(Task 复制 context)—— executor 在最里层能看到同一面旗。"""
    seen = {}

    async def innermost():
        f = current_abort_flag()
        seen["flag"] = f
        return f.is_set() if f is not None else None

    def worker():                      # 模拟 forge_slow_brain_factory 的同步化
        return asyncio.run(innermost())

    async def main():
        with abort_scope("T3") as flag:
            flag.set()                 # cancel 端点在 run 中途拉旗
            return await asyncio.to_thread(worker)

    assert asyncio.run(main()) is True
    assert isinstance(seen["flag"], AbortFlag)


# ================= ③ executor:旗拉响 → ABORTED_*(死代码复活) =================

def _atom():
    from karvyloop.schemas import AtomSpec
    return AtomSpec(id="a-stop", kind="task", prompt="t", input_schema={"type": "object"},
                    output_schema={"type": "object"}, tools=["read_file"], model="p/a")


def _tok():
    from karvyloop.schemas import Capability, CapabilityToken
    return CapabilityToken(task_id="t", grants=[Capability(resource="fs:/tmp", ops=["read"])],
                          expiry=time.time() + 3600)


def _gw(adapter):
    from karvyloop.gateway import GatewayClient, ModelRegistry
    reg = ModelRegistry.from_config({
        "models": {"providers": {"p": {"base_url": "x", "models": [
            {"id": "p/a", "api": "anthropic-messages", "context_window": 1000,
             "max_tokens": 100}]}}},
        "agents": {"defaults": {"model": "p/a"}},
        "embedding": {"model": "p/a"},
    })
    return GatewayClient(reg, adapters={"anthropic-messages": adapter})


@pytest.mark.asyncio
async def test_executor_aborts_at_next_loop_boundary_when_flag_set():
    """模型每轮都发 tool_use(会一直跑);工具执行时拉旗(模拟 cancel 打进来)→
    下一轮 step 0 检查点收口:ABORTED_TOOLS + success=False,协作式(本轮工具已跑完)。"""
    from karvyloop.atoms import TerminalEvent, run
    from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, tool_round

    adapter = ScriptedMockAdapter(
        rounds=[tool_round("tu1", "read_file", {"path": "/tmp/x"})],
        default_round=tool_round("tuN", "read_file", {"path": "/tmp/x"}))  # 不中止就永远有下一轮

    class PokeTool:
        name = "read_file"            # 用 policy 表里有下限的工具名(默认 FULL=被拒会触发断路器)
        description = "poke"
        parameters = {"type": "object", "properties": {}}
        calls = 0

        async def __call__(self, input: dict):
            PokeTool.calls += 1
            f = current_abort_flag()
            if f is not None:
                f.set()               # 工具跑到一半,用户点了 ⏹(cancel 端点拉旗)
            return "ok"

        def is_concurrency_safe(self, input: dict) -> bool:
            return True

    events = []
    with abort_scope("T-exec"):
        async for ev in run(_atom(), {"q": 1}, _tok(), gateway=_gw(adapter),
                            tools={"read_file": PokeTool()}, max_turns=10):
            events.append(ev)
    terms = [e for e in events if isinstance(e, TerminalEvent)]
    assert len(terms) == 1
    assert terms[0].run.terminal in ("aborted_tools", "aborted_streaming")
    assert terms[0].run.success is False
    assert PokeTool.calls == 1        # 协作式:本轮工具跑完;之后不再起新轮


@pytest.mark.asyncio
async def test_executor_without_flag_zero_regression():
    """没挂 abort_scope(CLI / 旧路径)→ 行为不变:正常 COMPLETED。"""
    from karvyloop.atoms import Terminal, TerminalEvent, run
    from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round

    adapter = ScriptedMockAdapter(rounds=[text_round("done")])
    events = []
    async for ev in run(_atom(), {"q": 1}, _tok(), gateway=_gw(adapter), tools={}):
        events.append(ev)
    terms = [e for e in events if isinstance(e, TerminalEvent)]
    assert terms[0].reason == Terminal.COMPLETED


# ================= ④ POST /api/task/cancel 端点 =================

@pytest.fixture
def app(tmp_path):
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.console import build_console_app
    from karvyloop.domain.registry import Address, BusinessDomainRegistry
    from karvyloop.karvy.observer import WorkbenchObserver
    reg = BusinessDomainRegistry()
    mgr = ConversationManager(ConversationStore(tmp_path / "c"), domain_registry=reg)
    mgr.start()
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    a.state.conversation_manager = mgr
    a.state.domain_registry = reg
    a.state.task_registry = TaskRegistry()
    a.state.config_path = str(tmp_path / "config.yaml")
    a.state.runtime_kwargs = {"gateway": object(), "model_ref": "x", "workspace_root": "/"}
    mgr.set_peer(Address(domain_id="l0", role="group", agent_id=""))
    return a


def test_task_cancel_sets_flag_and_abort_requested(app):
    """cancel = ① 协作旗 ② 注册表拉响 executor 旗(桩一面已登记的旗当活 LoopState)。"""
    from karvyloop.console.workflow_engine import _is_task_cancelled
    tid = app.state.task_registry.start(who="小卡", intent="跑着的 drive", kind="drive")
    flag = running_runs.register(tid)              # 桩:模拟 drive 正在跑(abort_scope 已登记)
    try:
        body = TestClient(app).post("/api/task/cancel", json={"task_id": tid})
        assert body.status_code == 200
        j = body.json()
        assert j["ok"] is True and j["abort_signalled"] is True
        assert _is_task_cancelled(app, tid) is True     # ① 协作旗真置上
        assert flag.is_set() is True                    # ② executor 侧的旗真拉响
        # 时间线留痕(WS push 同源):最新事件 = cancelling
        assert app.state.task_registry.get(tid)["last_event"]["kind"] == "cancelling"
    finally:
        running_runs.unregister(tid)


def test_task_cancel_unknown_task_404_fail_loud(app):
    r = TestClient(app).post("/api/task/cancel", json={"task_id": "no-such"})
    assert r.status_code == 404                    # 不静默 200


def test_task_cancel_finished_task_reports_not_running(app):
    reg = app.state.task_registry
    tid = reg.start(who="小卡", intent="已跑完", kind="drive")
    reg.finish(tid, result="done")
    j = TestClient(app).post("/api/task/cancel", json={"task_id": tid}).json()
    assert j["ok"] is False and j["reason"] == "not_running"


def test_task_cancel_no_live_run_reports_honestly(app):
    """没登记活 run(路径没接注册表 / 刚好跑完)→ abort_signalled=False,如实上报。"""
    tid = app.state.task_registry.start(who="小卡", intent="没活 run", kind="drive")
    j = TestClient(app).post("/api/task/cancel", json={"task_id": tid}).json()
    assert j["ok"] is True and j["abort_signalled"] is False


def test_workflow_and_roundtable_cancel_also_signal_executor(app):
    """既有两端点内部同走新逻辑:task_id 有活 run → 顺带拉响 executor 旗(不破响应形状)。"""
    for url, tid in (("/api/workflow/cancel", "WF-X"), ("/api/roundtable/cancel", "RT-X")):
        flag = running_runs.register(tid)
        try:
            j = TestClient(app).post(url, json={"task_id": tid}).json()
            assert j["ok"] is True
            assert flag.is_set() is True
        finally:
            running_runs.unregister(tid)


def test_workflow_cancel_by_run_id_signals_resumed_run(app):
    """刀3a 收尾回归锁:resume 续跑无 task_id,durable 执行器按 **run_id** 包 abort_scope
    → /workflow/cancel 只带 run_id 也要拉响同键的旗(此前 resume 是漏网路径,步内中断够不到)。"""
    rid = "WFRUN-RESUME"
    flag = running_runs.register(rid)              # 桩:模拟 resume 的 durable scope 按 run_id 在册
    try:
        j = TestClient(app).post("/api/workflow/cancel", json={"run_id": rid}).json()
        assert flag.is_set() is True, "cancel 带 run_id 应拉响按 run_id 注册的旗(resume 场景)"
        assert j["run_id"] == rid
    finally:
        running_runs.unregister(rid)


# ================= 前端静态:⏹ 停止贴每条活卡(不再靠 who 嗅探) =================

def test_frontend_stop_button_on_every_running_card():
    static = ROOT / "karvyloop" / "console" / "static"
    appjs = (static / "app.js").read_text(encoding="utf-8")
    assert "/api/task/cancel" in appjs                    # 通用停止端点接上了
    assert 'tk.kind === "workflow"' in appjs              # 按显式 kind 路由…
    assert 'tk.kind === "roundtable"' in appjs
    assert "_cancellingTasks" in appjs                    # 点了给即时反馈(cancelling 态)
    assert 'if (tk.status === "running")' in appjs        # 活卡一律画钮(不再 && (isWf || isRt))
    assert 'tk.status === "running" && (isWf || isRt)' not in appjs
    # i18n:en/zh 两表都有停止文案(static + TS 两份镜像;parity 测试另锁 en/zh 键集一致)
    for f in (static / "i18n.js",
              ROOT / "karvyloop" / "console" / "frontend" / "src" / "i18n.ts"):
        txt = f.read_text(encoding="utf-8")
        assert txt.count('"task.stop"') >= 2, f
        assert txt.count('"task.stopping"') >= 2, f


def test_producer_sites_all_tag_kind():
    """7 个 task_reg.start 产生点全部带显式 kind(锁死回归:新产生点漏标会被 grep 出来)。"""
    console = ROOT / "karvyloop" / "console"
    expectations = {
        "routes.py": ['kind="drive"', 'kind="workflow"'],
        "roundtable_engine.py": ['kind="roundtable"'],
        "pursuit_tick.py": ['kind="pursuit"'],
        "routes_schedules.py": ['kind="schedule"'],
        "proposal_handlers.py": ['kind="proposal"'],
    }
    for fname, kinds in expectations.items():
        src = (console / fname).read_text(encoding="utf-8")
        for k in kinds:
            assert k in src, f"{fname} 缺 {k}"
