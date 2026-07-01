"""docs/02 §15 — Code ①:终止语义「上冒」+ infra-dead 检测验收。

尽责下属阶梯(§15.2)的硬前置:role 要重规划,先得**看见 atom 为何停**,且能区分
  - **infra-dead**(网关/网络/模型解析调不通)→ 不是 planning 问题,fail-loud,**不进 replan**;
  - **可重规划**(MAX_TURNS / CIRCUIT_OPEN / …)→ planning 不够稳,role 可重规划。

逐条锚 §15.7 不变量:终止语义上冒到 AtomRun/DriveResult/Trace;infra-dead 与 token 预算(BLOCKING_LIMIT)
**不混**(否则 role 会把"网络断"当"任务难"反复白爬阶梯)。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from karvyloop.atoms import Terminal, TerminalEvent, run
from karvyloop.atoms.terminal import is_infra_dead, is_replannable
from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round
from karvyloop.gateway import GatewayClient, ModelRegistry
from karvyloop.schemas import AtomRun, AtomSpec, Capability, CapabilityToken


# ---- 复用 test_atoms_executor 的最小夹具 ----

def _atom(model: str = "p/a") -> AtomSpec:
    return AtomSpec(
        id="a1", kind="task", prompt="you are a test atom",
        input_schema={"type": "object"}, output_schema={"type": "object"},
        tools=["read_file"], model=model,
    )


def _tok() -> CapabilityToken:
    return CapabilityToken(
        task_id="t",
        grants=[Capability(resource="fs:/tmp", ops=["read"])],
        expiry=time.time() + 3600,
    )


def _gw(adapter) -> GatewayClient:
    reg = ModelRegistry.from_config({
        "models": {"providers": {"p": {"base_url": "x", "models": [
            {"id": "p/a", "api": "anthropic-messages", "context_window": 1000, "max_tokens": 100},
        ]}}},
        "agents": {"defaults": {"model": "p/a"}},
        "embedding": {"model": "p/a"},
    })
    return GatewayClient(reg, adapters={"anthropic-messages": adapter})


class _RaisingAdapter:
    """complete 一调就抛 —— 模拟网关/网络调不通(model_call_failed)。"""
    api = "anthropic-messages"

    async def complete(self, messages, tools, model, provider, *, system=None):
        raise ConnectionError("network down")
        yield  # noqa: 让它成为 async generator(永不到达)

    async def embed(self, text, model, provider):  # pragma: no cover
        return [0.0]


async def _run_terminal(gw, atom, tools=None) -> TerminalEvent:
    events = [ev async for ev in run(atom, {"q": "hi"}, _tok(), gateway=gw, tools=tools or {})]
    assert isinstance(events[-1], TerminalEvent)
    return events[-1]


# ============ 分类助手(纯函数,容忍 enum/str/None)============

def test_is_infra_dead_helper():
    assert is_infra_dead(Terminal.INFRA_DEAD) is True
    assert is_infra_dead("infra_dead") is True
    # 关键:token 预算耗尽 ≠ infra-dead(否则 role 把"没预算"当"网络断")
    assert is_infra_dead(Terminal.BLOCKING_LIMIT) is False
    assert is_infra_dead(Terminal.MAX_TURNS) is False
    assert is_infra_dead(None) is False
    assert is_infra_dead("garbage") is False


def test_is_replannable_helper():
    # planning 不够稳的那类 → role 可重规划
    assert is_replannable(Terminal.MAX_TURNS) is True
    assert is_replannable(Terminal.CIRCUIT_OPEN) is True
    assert is_replannable("circuit_open") is True
    # infra-dead 和正常完成都**不**该重规划
    assert is_replannable(Terminal.INFRA_DEAD) is False
    assert is_replannable(Terminal.COMPLETED) is False
    assert is_replannable(None) is False
    assert is_replannable("garbage") is False


def test_infra_and_budget_are_distinct_terminals():
    """守 §15.7:infra-dead 与 token 预算是两个不同终止,别被合并吃掉。"""
    assert Terminal.INFRA_DEAD != Terminal.BLOCKING_LIMIT
    assert Terminal.INFRA_DEAD.value == "infra_dead"


# ============ executor:infra 失败 → INFRA_DEAD(不再误标 BLOCKING_LIMIT)============

@pytest.mark.asyncio
async def test_model_call_failure_is_infra_dead():
    gw = _gw(_RaisingAdapter())
    term = await _run_terminal(gw, _atom())
    assert term.reason == Terminal.INFRA_DEAD
    # 上冒:AtomRun 也带上了终止语义
    assert term.run.terminal == "infra_dead"
    assert term.run.success is False


@pytest.mark.asyncio
async def test_resolve_model_failure_is_infra_dead(monkeypatch):
    gw = _gw(ScriptedMockAdapter(rounds=[text_round("x")]))

    def _boom(scope):
        raise RuntimeError("no usable model configured")

    monkeypatch.setattr(gw, "resolve_model", _boom)
    term = await _run_terminal(gw, _atom())
    assert term.reason == Terminal.INFRA_DEAD
    assert term.run.terminal == "infra_dead"


@pytest.mark.asyncio
async def test_completed_run_carries_terminal_value():
    """正常完成也要把 terminal 写到 AtomRun(上冒契约,非仅失败路径)。"""
    gw = _gw(ScriptedMockAdapter(rounds=[text_round("done")]))
    term = await _run_terminal(gw, _atom())
    assert term.reason == Terminal.COMPLETED
    assert term.run.terminal == "completed"


# ============ drive 把 terminal 上冒到 DriveResult + Trace ============

def _stub_slow_brain(terminal_value: str):
    def slow_brain(intent: str) -> tuple[str, AtomRun]:
        run_obj = AtomRun(
            atom_id="r1",
            input={"intent": intent},
            output={"text": "partial"},
            success=False,
            tool_calls=[{"name": "run_command"}],
            trace_ref="trace://r1/1",
            ts=0.0,
            terminal=terminal_value,
        )
        return "partial", run_obj
    return slow_brain


def test_drive_surfaces_terminal_on_result_and_trace(tmp_path: Path):
    from karvyloop.cli.main_loop import MainLoop

    ml = MainLoop(skills_dir=tmp_path / "skills", clock=lambda: 1000.0)
    ml.bootstrap()
    r = ml.drive("do the impossible", slow_brain=_stub_slow_brain("infra_dead"))

    # 1) 上冒到 DriveResult
    assert r.terminal == "infra_dead"
    assert is_infra_dead(r.terminal) is True

    # 2) 落进 Trace 的 atom_run 事实(不可行报告卡日后据此溯源)
    atom_runs = ml.trace.query(r.task_id, kind="atom_run")
    assert atom_runs, "应有 atom_run Trace 事实"
    assert atom_runs[-1].payload.get("terminal") == "infra_dead"


def test_query_atom_runs_preserves_terminal(tmp_path: Path):
    """对抗验收揪出的真缺陷回归:终止语义必须熬过 query_atom_runs 重建,
    否则结晶侧(lessons/trace_eval/observe 都走这条投影)读回是 None,§15「上冒」白做。
    内存 + sqlite 两实现都锁。
    """
    from karvyloop.cognition.trace import TraceStore, TraceEntry
    from karvyloop.cognition.sqlite_trace import SqliteTraceStore

    def _entry() -> TraceEntry:
        return TraceEntry(
            task_id="T1", kind="atom_run",
            payload={
                "atom_id": "a1", "input": {"intent": "x"}, "output": {"text": "y"},
                "success": False, "tool_calls": [{"name": "run_command"}],
                "trace_ref": "trace://a1/1", "ts": 1.0, "terminal": "infra_dead",
            },
            ts=1.0, source="t",
        )

    mem = TraceStore()
    mem.append(_entry())
    assert mem.query_atom_runs("T1")[0].terminal == "infra_dead"

    sq = SqliteTraceStore(tmp_path / "trace.db")
    sq.append(_entry())
    assert sq.query_atom_runs("T1")[0].terminal == "infra_dead"


def test_annotate_terminal_has_infra_note():
    """infra-dead 的诚实提示:明确"不是任务本身的问题",别让人当任务失败去改。"""
    from karvyloop.cli.main_loop import _annotate_terminal

    out = _annotate_terminal("半截结果", Terminal.INFRA_DEAD)
    assert "半截结果" in out
    assert "基础能力" in out  # 区别于 MAX_TURNS/预算 的提示语
