"""小卡主动建议验收(loop-step2b)——观察任务看板,失败任务 → 提议重试。"""
from __future__ import annotations

from karvyloop.karvy.proactive import propose_from_tasks
from karvyloop.karvy.proposal_registry import KIND_RUN_TASK
from karvyloop.console.tasks import TaskRegistry


def test_proposes_resume_on_failed_task():
    reg = TaskRegistry()
    tid = reg.start(who="小卡", domain_id="l0", intent="把报告导出成 PDF")
    reg.finish(tid, error="权限不足")
    p = propose_from_tasks(reg, now=1.0)
    assert p is not None
    assert p.kind == KIND_RUN_TASK
    assert "把报告导出成 PDF" in p.summary
    assert p.payload["intent"] == "把报告导出成 PDF"
    assert p.proposal_id  # 稳定派生
    # ch4:决策依据(为什么)+ 上下文跳转(去哪看全貌)
    assert "把报告导出成 PDF" in p.basis and "权限不足" in p.basis and "小卡" in p.basis
    assert p.context_ref == {"kind": "task", "id": tid}
    d = p.to_dict()
    assert d["basis"] == p.basis and d["context_ref"]["id"] == tid

def test_silent_when_all_done():
    reg = TaskRegistry()
    tid = reg.start(who="小卡", intent="做完的活")
    reg.finish(tid, result="搞定")
    assert propose_from_tasks(reg) is None

def test_silent_on_empty_or_none():
    assert propose_from_tasks(None) is None
    assert propose_from_tasks(TaskRegistry()) is None

def test_skips_failed_with_empty_intent():
    reg = TaskRegistry()
    tid = reg.start(who="小卡", intent="")
    reg.finish(tid, error="x")
    assert propose_from_tasks(reg) is None


# ---- loop-step2c:ACCEPT「要我重试吗」→ 真重跑(闭环)----

def test_run_task_handler_reruns_and_tracks_new_task(monkeypatch):
    from karvyloop.console import proposal_handlers as ph
    from karvyloop.console.tasks import TaskRegistry
    import karvyloop.cli.main_loop as ml_mod
    from karvyloop.karvy.atoms import Proposal

    # stub forge 工厂(不真调 LLM);stub main_loop.drive 返成功结果
    monkeypatch.setattr(ml_mod, "forge_slow_brain_factory", lambda **k: (lambda intent, **kw: ("ok", None)))

    class _Res:
        error = ""
        text = "重跑成功:报告已导出"

    class _ML:
        def drive(self, intent, slow_brain=None, **k):
            return _Res()

    class _State:
        main_loop = _ML()
        runtime_kwargs = {"token": 1, "sandbox": 1, "gateway": 1, "workspace_root": "/w", "model_ref": ""}
        task_registry = TaskRegistry()
        domain_registry = None

    class _App:
        state = _State()

    app = _App()
    handlers = ph.build_proposal_handlers(app)
    assert KIND_RUN_TASK in handlers

    p = Proposal(summary="重试X", options=("ACCEPT",), strength=0.8, evidence_refs=(),
                 habit_id=0, model_ref="", ts=1.0, kind=KIND_RUN_TASK,
                 payload={"intent": "把报告导出成 PDF", "domain_id": "l0", "role": ""})
    ok, detail = handlers[KIND_RUN_TASK](p)
    assert ok is True and "已重跑" in detail
    # 重跑登记成一条新任务(done)→ 闭环可见
    tasks = app.state.task_registry.list()
    assert len(tasks) == 1 and tasks[0]["status"] == "done" and tasks[0]["intent"] == "把报告导出成 PDF"


def test_run_task_handler_no_main_loop_graceful():
    from karvyloop.console import proposal_handlers as ph
    from karvyloop.karvy.atoms import Proposal

    class _State:
        main_loop = None
        runtime_kwargs = {}
    class _App:
        state = _State()
    handlers = ph.build_proposal_handlers(_App())
    p = Proposal(summary="x", options=("ACCEPT",), strength=0.8, evidence_refs=(), habit_id=0,
                 model_ref="", ts=1.0, kind=KIND_RUN_TASK, payload={"intent": "x"})
    ok, detail = handlers[KIND_RUN_TASK](p)
    assert ok is False and "main_loop" in detail
