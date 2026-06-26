"""atoms/executor 验收测试 —— 逐条对应 docs/modules/atom-executor.md §5 验收标准。

用 ScriptedMockAdapter 驱动模型,M0 不触网。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import pytest

from karvyloop.atoms import (
    LoopState,
    MAX_CONCURRENT,
    Terminal,
    TerminalEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    Transition,
    run,
    run_tools,
)
from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round, tool_round
from karvyloop.atoms.orchestration import ToolResult, ToolUseBlock
from karvyloop.gateway import GatewayClient, ModelRegistry
from karvyloop.schemas import AtomSpec, Capability, CapabilityToken


# ---- 工具协议实现 ----

class _Tool:
    """测试用工具:count 记录调用次数,is_concurrency_safe 可配置。"""
    def __init__(self, name: str, *, safe: bool = True, fail: bool = False,
                 echo_input: bool = True):
        self.name = name
        self.safe = safe
        self.fail = fail
        self.echo_input = echo_input
        self.call_log: list[dict] = []
        self.description = f"test tool {name}"
        self.parameters = {"type": "object", "properties": {}}

    async def __call__(self, input: dict) -> Any:
        self.call_log.append(input)
        if self.fail:
            raise RuntimeError(f"{self.name} boom")
        return {"echo": input} if self.echo_input else "ok"

    def is_concurrency_safe(self, input: dict) -> bool:
        return self.safe


def _atom(model: str = "p/a") -> AtomSpec:
    return AtomSpec(
        id="a1",
        kind="task",
        prompt="you are a test atom",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        tools=["read_file", "write_file"],
        model=model,
    )


def _tok() -> CapabilityToken:
    return CapabilityToken(
        task_id="t",
        grants=[
            Capability(resource="fs:/tmp", ops=["read"]),
            Capability(resource="fs:/tmp", ops=["write"]),
        ],
        expiry=time.time() + 3600,
    )


def _gw(adapter: ScriptedMockAdapter) -> GatewayClient:
    reg = ModelRegistry.from_config({
        "models": {"providers": {"p": {"base_url": "x", "models": [
            {"id": "p/a", "api": "anthropic-messages", "context_window": 1000, "max_tokens": 100},
            {"id": "p/b", "api": "anthropic-messages", "context_window": 1000, "max_tokens": 100},
        ]}}},
        "agents": {"defaults": {"model": "p/a"}},
        "embedding": {"model": "p/a"},
    })
    return GatewayClient(reg, adapters={"anthropic-messages": adapter})


# ============ AC1：无 tool_use 立即终止 + Terminal.COMPLETED + AtomRun ============
@pytest.mark.asyncio
async def test_ac1_no_tool_use_terminates_completed():
    adapter = ScriptedMockAdapter(rounds=[
        text_round("done"),  # 只输出文本,不发 tool_use
    ])
    gw = _gw(adapter)
    events = [ev async for ev in run(_atom(), {"q": "hi"}, _tok(), gateway=gw, tools={})]
    last = events[-1]
    assert isinstance(last, TerminalEvent)
    assert last.reason == Terminal.COMPLETED
    assert last.run.atom_id == "a1"
    assert last.run.success is True


# ============ AC2：mock 模型可驱动完整多轮循环 ============
@pytest.mark.asyncio
async def test_ac2_mock_drives_multi_turn_loop():
    read = _Tool("read_file", safe=True)
    write = _Tool("write_file", safe=False)
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"path": "/tmp/a"}),   # 第 1 轮:发 tool_use
        tool_round("c2", "write_file", {"path": "/tmp/a", "data": "x"}),  # 第 2 轮:再 tool_use
        text_round("all done"),                                # 第 3 轮:无 tool_use → 终止
    ])
    gw = _gw(adapter)
    events = [ev async for ev in run(_atom(), {"q": "go"}, _tok(),
                                      gateway=gw,
                                      tools={"read_file": read, "write_file": write})]
    assert adapter.call_count == 3
    assert read.call_log == [{"path": "/tmp/a"}]
    assert write.call_log == [{"path": "/tmp/a", "data": "x"}]
    assert isinstance(events[-1], TerminalEvent)
    assert events[-1].reason == Terminal.COMPLETED


# ============ AC3：不信 stop_reason，只看本回合有无 tool_use ============
@pytest.mark.asyncio
async def test_ac3_continue_despite_bad_stop_reason():
    """stop_reason='end_turn' 但本轮发了 tool_use → 必须续跑。"""
    # mock 一轮:发 tool_use 但 Done.stop_reason='end_turn'（荒谬,但模拟坏实现）
    from karvyloop.gateway.events import Done, ToolUseStart, ToolUseStop, Usage
    rounds = [[
        ToolUseStart(id="c1", name="read_file"),
        ToolUseStop(id="c1", input={"path": "/tmp/a"}),
        Usage(),
        Done("end_turn"),  # ← 坏 stop_reason
    ], text_round("done")]
    adapter = ScriptedMockAdapter(rounds=rounds)
    gw = _gw(adapter)
    read = _Tool("read_file", safe=True)
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=gw,
                                      tools={"read_file": read})]
    # 必须续跑了一次(read 调了),然后终止
    assert read.call_log == [{"path": "/tmp/a"}]
    assert adapter.call_count == 2
    assert events[-1].reason == Terminal.COMPLETED


# ============ AC4：读写分区 [读,读,写,读] → 3 批 ============
@pytest.mark.asyncio
async def test_ac4_partition_read_write_read():
    safe = _Tool("read_file", safe=True)
    unsafe = _Tool("write_file", safe=False)
    blocks = [
        ToolUseBlock(id="r1", name="read_file", input={"i": 1}),
        ToolUseBlock(id="r2", name="read_file", input={"i": 2}),
        ToolUseBlock(id="w1", name="write_file", input={"i": 3}),
        ToolUseBlock(id="r3", name="read_file", input={"i": 4}),
    ]
    res = await run_tools(
        blocks, {"read_file": safe, "write_file": unsafe}, _tok(),
    )
    # 4 块 → 4 结果,顺序与输入一致
    assert [r.tool_use_id for r in res] == ["r1", "r2", "w1", "r3"]
    # safe/unsafe 都被调过
    assert len(safe.call_log) == 3
    assert len(unsafe.call_log) == 1


def test_ac4_partition_via_direct_unit():
    from karvyloop.atoms.orchestration import _partition
    safe = _Tool("read_file", safe=True)
    unsafe = _Tool("write_file", safe=False)
    blocks = [
        ToolUseBlock(id="r1", name="read_file", input={}),
        ToolUseBlock(id="r2", name="read_file", input={}),
        ToolUseBlock(id="w1", name="write_file", input={}),
        ToolUseBlock(id="r3", name="read_file", input={}),
    ]
    batches = _partition(blocks, {"read_file": safe, "write_file": unsafe})
    # 3 批: [并发读读] / [串行写] / [串行读]  ← spec:写批 + 后续读都是串行
    flags = [b[0] for b in batches]
    sizes = [len(b[1]) for b in batches]
    assert flags == [True, False, False]
    assert sizes == [2, 1, 1]


def test_ac4_is_concurrency_safe_throws_treated_unsafe():
    """工具 is_concurrency_safe 抛异常 → 保守当非并发安全(fail-closed)"""
    from karvyloop.atoms.orchestration import _partition

    class _BoomSafe:
        name = "boom"
        def is_concurrency_safe(self, input):
            raise RuntimeError("nope")
        async def __call__(self, input): return "x"

    b1 = _BoomSafe()
    b2 = _BoomSafe()
    blocks = [
        ToolUseBlock(id="a", name="boom", input={}),
        ToolUseBlock(id="b", name="boom", input={}),
    ]
    batches = _partition(blocks, {"boom": b1})  # b2 不参与,但 blocks 里都走 _BoomSafe 同名
    # 两块都应被分到串行批(b1 的 is_concurrency_safe 抛 → safe=False)
    flags = [b[0] for b in batches]
    assert all(f is False for f in flags), f"应全部分到串行批,得 {flags}"


# ============ AC5：并发批内对共享 context 修改不竞态（批末串行应用）============
@pytest.mark.asyncio
async def test_ac5_concurrent_batch_no_race_on_context():
    """并发批内每个 tool 改 shared list;批末合并时所有元素都在(无丢失)。"""
    shared: list[int] = []

    class _T:
        name = "t"
        async def __call__(self, input):
            # 每个 tool 短暂等待后追加(模拟 IO)
            await asyncio.sleep(0.001)
            shared.append(input["i"])
            return input
        def is_concurrency_safe(self, input):
            return True

    t = _T()
    blocks = [ToolUseBlock(id=f"b{i}", name="t", input={"i": i}) for i in range(20)]
    res = await run_tools(blocks, {"t": t}, _tok())
    assert len(shared) == 20
    # 不强求顺序(并发),但每个 i 都在
    assert sorted(shared) == list(range(20))


# ============ AC6：中断/异常终止后历史里不存在裸 tool_use ============
@pytest.mark.asyncio
async def test_ac6_circuit_open_terminates_without_bare_tool_use():
    """连续失败 → 断路器开；最后一次发过的 tool_use 也必须有 tool_result 回灌。"""
    failing = _Tool("read_file", safe=True, fail=True)
    adapter2 = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"path": "/tmp/a"}),
        tool_round("c2", "read_file", {"path": "/tmp/b"}),
        tool_round("c3", "read_file", {"path": "/tmp/c"}),
        text_round("never reached"),  # 不应再调
    ])
    gw2 = _gw(adapter2)
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=gw2,
                                      tools={"read_file": failing})]
    assert events[-1].reason == Terminal.CIRCUIT_OPEN
    # 检查最后一次请求(adapter 内部保留)历史里无裸 tool_use
    # Anthropic 协议: assistant 消息的 tool_use 在 content blocks 里(不是 tool_calls)
    msgs = adapter2.last_request["messages"]
    for m in msgs:
        if m.get("role") == "assistant":
            for blk in m.get("content") or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                    continue
                tu_id = blk.get("id")
                # 必须在某条 user 消息的 content 里找到对应 tool_result
                assert any(
                    mm.get("role") == "user"
                    and any(
                        b.get("type") == "tool_result" and b.get("tool_use_id") == tu_id
                        for b in (mm.get("content") or [])
                        if isinstance(b, dict)
                    )
                    for mm in msgs
                ), f"裸 tool_use {tu_id} 残留"


# ============ AC7：max_turns=N → 第 N+1 回合不调模型 ============
@pytest.mark.asyncio
async def test_ac7_max_turns_stops_calling_model():
    """max_turns=2 时,第 3 轮不再调模型,直接终止。"""
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"path": "/tmp/a"}),   # 1
        tool_round("c2", "read_file", {"path": "/tmp/b"}),   # 2
        tool_round("c3", "read_file", {"path": "/tmp/c"}),   # 3 ← 不应调
    ])
    gw = _gw(adapter)
    read = _Tool("read_file", safe=True)
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=gw,
                                      tools={"read_file": read},
                                      max_turns=2)]
    assert adapter.call_count == 2
    assert events[-1].reason == Terminal.MAX_TURNS


# ============ AC8：断路器：连续 3 次失败 → CIRCUIT_OPEN ============
@pytest.mark.asyncio
async def test_ac8_circuit_opens_after_three_failures():
    fail = _Tool("read_file", safe=True, fail=True)
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"path": "/tmp/a"}),
        tool_round("c2", "read_file", {"path": "/tmp/b"}),
        tool_round("c3", "read_file", {"path": "/tmp/c"}),
        tool_round("c4", "read_file", {"path": "/tmp/d"}),  # 不应调
    ])
    gw = _gw(adapter)
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=gw,
                                      tools={"read_file": fail})]
    assert events[-1].reason == Terminal.CIRCUIT_OPEN
    assert adapter.call_count == 3


# ============ AC9：被 capability 拒的工具 → is_error 回灌,循环继续 ============
@pytest.mark.asyncio
async def test_ac9_capability_deny_returns_error_result_continues():
    """在 READ_ONLY 模式下,write_file 应当被 capability 决策链 Deny（required=WORKSPACE_WRITE）。

    拒后转 is_error=true tool_result 回灌,read_file 仍可调,循环不崩。
    """
    read = _Tool("read_file", safe=True)
    write = _Tool("write_file", safe=False)
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "write_file", {"path": "/tmp/a", "data": "x"}),  # 被 Deny
        tool_round("c2", "read_file", {"path": "/tmp/a"}),                # 允许
        text_round("done"),                                               # 终止
    ])
    gw = _gw(adapter)
    # 强制 READ_ONLY 模式 → write_file WORKSPACE_WRITE 不达 → Deny
    from karvyloop.capability import Mode
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=gw,
                                      tools={"read_file": read, "write_file": write},
                                      default_mode=Mode.READ_ONLY)]
    # write_file 被拒在门外 → 没被调
    assert write.call_log == []
    # read_file 仍被调
    assert read.call_log == [{"path": "/tmp/a"}]
    # 循环正常终止
    assert events[-1].reason == Terminal.COMPLETED
    # 找到一个 is_error=True 的 ToolResultEvent
    errs = [ev for ev in events if isinstance(ev, ToolResultEvent) and ev.result.is_error]
    assert errs, "未生成 is_error tool_result"
    assert "capability_denied" in errs[0].result.error_reason


# ============ AC10：transition.reason 可断言 ============
def test_ac10_loop_state_transition_is_assertable():
    s = LoopState()
    s.transition = Transition(reason="no_tool_use")
    assert s.transition.reason == "no_tool_use"
    s2 = s.copy_for_next_turn()
    assert s2.transition.reason == "no_tool_use"
    # 改 s2 不影响 s
    s2.transition = Transition(reason="ran_tools")
    assert s.transition.reason == "no_tool_use"
