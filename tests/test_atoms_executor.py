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


# ============ slice C：tool_calls_log 事实字段 ok/error_reason(docs/82)============
# 记事实不是算评价(跑评分离禁的是热路径算评价);工具跑完按 tool_use_id 回填。
# 失败真因各自如实标(异常/超时/deny 不吞成一样的),截断 ≤200 字。

def _last_run_calls(events) -> list[dict]:
    last = events[-1]
    assert isinstance(last, TerminalEvent)
    return last.run.tool_calls


@pytest.mark.asyncio
async def test_slicec_tool_success_backfills_ok_true():
    read = _Tool("read_file", safe=True)
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"path": "/tmp/a"}),
        text_round("done"),
    ])
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=_gw(adapter),
                                      tools={"read_file": read})]
    calls = _last_run_calls(events)
    assert len(calls) == 1
    assert calls[0]["id"] == "c1" and calls[0]["name"] == "read_file"
    assert calls[0]["ok"] is True
    assert calls[0]["error_reason"] == ""


@pytest.mark.asyncio
async def test_slicec_tool_exception_backfills_ok_false_with_reason():
    fail = _Tool("read_file", safe=True, fail=True)   # 抛 RuntimeError("read_file boom")
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"path": "/tmp/a"}),
        text_round("done"),
    ])
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=_gw(adapter),
                                      tools={"read_file": fail})]
    calls = _last_run_calls(events)
    assert calls[0]["ok"] is False
    assert calls[0]["error_reason"].startswith("RuntimeError:")   # 异常类名+消息,如实
    assert "boom" in calls[0]["error_reason"]


@pytest.mark.asyncio
async def test_slicec_tool_timeout_has_its_own_reason():
    class _TimeoutTool:
        name = "read_file"
        description = "t"
        parameters = {"type": "object", "properties": {}}
        async def __call__(self, input):
            raise asyncio.TimeoutError("tool timed out")
        def is_concurrency_safe(self, input):
            return True

    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"path": "/tmp/a"}),
        text_round("done"),
    ])
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=_gw(adapter),
                                      tools={"read_file": _TimeoutTool()})]
    calls = _last_run_calls(events)
    assert calls[0]["ok"] is False
    assert calls[0]["error_reason"].startswith("TimeoutError")    # 超时≠一般异常≠deny
    assert "capability_denied" not in calls[0]["error_reason"]


@pytest.mark.asyncio
async def test_slicec_capability_deny_has_its_own_reason():
    write = _Tool("write_file", safe=False)
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "write_file", {"path": "/tmp/a", "data": "x"}),
        text_round("done"),
    ])
    from karvyloop.capability import Mode
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=_gw(adapter),
                                      tools={"write_file": write},
                                      default_mode=Mode.READ_ONLY)]
    calls = _last_run_calls(events)
    assert calls[0]["ok"] is False
    assert "capability_denied" in calls[0]["error_reason"]        # deny 是 deny,不吞成异常
    assert not calls[0]["error_reason"].startswith("RuntimeError")


@pytest.mark.asyncio
async def test_slicec_error_reason_truncated_to_200():
    class _LongBoom:
        name = "read_file"
        description = "t"
        parameters = {"type": "object", "properties": {}}
        async def __call__(self, input):
            raise RuntimeError("x" * 500)
        def is_concurrency_safe(self, input):
            return True

    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"path": "/tmp/a"}),
        text_round("done"),
    ])
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=_gw(adapter),
                                      tools={"read_file": _LongBoom()})]
    calls = _last_run_calls(events)
    assert calls[0]["ok"] is False
    assert len(calls[0]["error_reason"]) == 200                   # 截断 ≤200 字
    assert calls[0]["error_reason"].startswith("RuntimeError:")


@pytest.mark.asyncio
async def test_slicec_multi_turn_every_entry_backfilled():
    """多轮混合(成功→失败→成功):每条日志条目都有 ok 字段,逐条如实。"""
    calls_seen: list[str] = []

    class _Flaky:
        name = "read_file"
        description = "t"
        parameters = {"type": "object", "properties": {}}
        async def __call__(self, input):
            calls_seen.append(input.get("path", ""))
            if len(calls_seen) == 2:
                raise RuntimeError("flaky boom")
            return "ok"
        def is_concurrency_safe(self, input):
            return True

    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"path": "/a"}),
        tool_round("c2", "read_file", {"path": "/b"}),
        tool_round("c3", "read_file", {"path": "/c"}),
        text_round("done"),
    ])
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=_gw(adapter),
                                      tools={"read_file": _Flaky()})]
    calls = _last_run_calls(events)
    assert [c["id"] for c in calls] == ["c1", "c2", "c3"]
    assert [c["ok"] for c in calls] == [True, False, True]
    assert calls[1]["error_reason"].startswith("RuntimeError:")
    assert calls[0]["error_reason"] == "" and calls[2]["error_reason"] == ""


# ---- slice C 修订:生产 coding 工具集以 CodingResult(ok=False) **返回值**报失败、
# 不抛异常(read/write/edit/bash/web/mcp,16+ 处失败点,中间无转换层)——
# 对抗验收实锤:只看 is_error 会把真失败记成 ok=True。以下杀采样偏差。

class _CodingTool:
    """CodingResult 风格工具:不抛异常,按 result_fn 的返回值报成败。"""
    name = "read_file"
    description = "coding-style tool"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, result_fn):
        self._result_fn = result_fn

    async def __call__(self, input):
        return self._result_fn(input)

    def is_concurrency_safe(self, input):
        return True


@pytest.mark.asyncio
async def test_slicec_coding_result_failure_backfills_ok_false():
    from karvyloop.coding.tools import CodingResult
    tool = _CodingTool(lambda inp: CodingResult(
        ok=False, payload=None, error_code=6,
        error_message=f"文件不存在: {inp.get('file_path')}"))
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"file_path": "config.yml"}),
        text_round("done"),
    ])
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=_gw(adapter),
                                      tools={"read_file": tool})]
    calls = _last_run_calls(events)
    assert calls[0]["ok"] is False                                # 返回值式失败也是失败
    assert "文件不存在" in calls[0]["error_reason"]                # error_message 真因如实
    assert "config.yml" in calls[0]["error_reason"]


@pytest.mark.asyncio
async def test_slicec_coding_result_success_backfills_ok_true():
    from karvyloop.coding.tools import CodingResult
    tool = _CodingTool(lambda inp: CodingResult(ok=True, payload="content"))
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"file_path": "a.txt"}),
        text_round("done"),
    ])
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=_gw(adapter),
                                      tools={"read_file": tool})]
    calls = _last_run_calls(events)
    assert calls[0]["ok"] is True and calls[0]["error_reason"] == ""


@pytest.mark.asyncio
async def test_slicec_dict_shaped_failure_and_no_message_fallback():
    """同形 dict(ok=False)也认;ok=False 没附原因 → 非空如实短语,不留假空串。"""
    seen: list[dict] = []

    def _result(inp):
        seen.append(inp)
        if len(seen) == 1:
            return {"ok": False, "error": "端口被占用: 8766"}     # dict 形失败
        return {"ok": False}                                      # 失败但没写原因
    tool = _CodingTool(_result)
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"p": 1}),
        tool_round("c2", "read_file", {"p": 2}),
        text_round("done"),
    ])
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=_gw(adapter),
                                      tools={"read_file": tool})]
    calls = _last_run_calls(events)
    assert calls[0]["ok"] is False and "端口被占用" in calls[0]["error_reason"]
    assert calls[1]["ok"] is False and calls[1]["error_reason"] == "tool_reported_failure"


@pytest.mark.asyncio
async def test_slicec_chain_coding_fail_then_success_yields_insight_signal():
    """真 drive 曾暴露的整链回归锁:read 错路径失败(CodingResult 返回值报失败)→
    改对路径成功 → AtomRun.tool_calls 事实 → insight 确定性档必须出 tool_retry 信号。
    (修前:失败被记 ok=True → 全 True 压掉旧推断 → 0 信号,老代码 1 信号 = 回归。)"""
    from types import SimpleNamespace

    from karvyloop.coding.tools import CodingResult
    from karvyloop.cognition.insight import find_insight_signals

    def _result(inp):
        if inp.get("file_path") == "config.yml":                  # 错路径 → 失败
            return CodingResult(ok=False, payload=None, error_code=6,
                                error_message="文件不存在: config.yml")
        return CodingResult(ok=True, payload="yaml content")      # 改对 → 成功
    tool = _CodingTool(_result)
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"file_path": "config.yml"}),
        tool_round("c2", "read_file", {"file_path": "config.yaml"}),
        text_round("读到了"),
    ])
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=_gw(adapter),
                                      tools={"read_file": tool})]
    run_obj = events[-1].run
    assert [c["ok"] for c in run_obj.tool_calls] == [False, True]
    entry = SimpleNamespace(kind="atom_run", task_id="t1", ts=1.0, seq=0,
                            payload={"atom_id": run_obj.atom_id, "success": run_obj.success,
                                     "output": run_obj.output, "terminal": run_obj.terminal,
                                     "tool_calls": run_obj.tool_calls})
    sigs = find_insight_signals([entry])
    assert len(sigs) == 1 and sigs[0].pattern == "tool_retry"
    assert "确定性" in sigs[0].material                            # 走确定性档
    assert "文件不存在" in sigs[0].material                        # 失败真因进材料


# ============ 存量 fail-loud 真伤:turn 内部上抛时终态必须诚实(§0.7)============
# 对抗验收实锤:run_tools 整体上抛(串行批 BaseException / capability_check 自身抛)时,
# finally 的 fail_loud_crash 未置位 → 曾先吐 TerminalEvent(COMPLETED, success=True)
# 假成功事件再传异常。修后:终态照 abort 既有失败形状标 ABORTED_TOOLS(success=False),
# 异常照传不吞;关停路径(CancelledError)不吐终态原样上冒。

class _HardCrash(BaseException):
    """非 Exception 的 BaseException:穿透 _run_one 的 except Exception 兜底。"""


class _RaisingTool:
    """串行(非并发安全)工具,调用即抛指定异常。"""
    def __init__(self, name: str, exc: BaseException):
        self.name = name
        self.exc = exc
        self.description = f"raising tool {name}"
        self.parameters = {"type": "object", "properties": {}}

    async def __call__(self, input: dict):
        raise self.exc

    def is_concurrency_safe(self, input: dict) -> bool:
        return False


def _one_tool_rounds() -> ScriptedMockAdapter:
    return ScriptedMockAdapter(rounds=[
        tool_round("c1", "write_file", {"path": "/tmp/a", "data": "x"}),
        text_round("never reached"),
    ])


@pytest.mark.asyncio
async def test_failloud_serial_batch_baseexception_honest_terminal():
    """串行批工具抛 BaseException → 无 COMPLETED/success=True 假事件;
    终态诚实(ABORTED_TOOLS + success=False,照 abort 既有失败形状)+ 异常照传。"""
    tool = _RaisingTool("write_file", _HardCrash("hard crash in tool"))
    events: list = []
    with pytest.raises(_HardCrash, match="hard crash"):
        async for ev in run(_atom(), {"q": "go"}, _tok(), gateway=_gw(_one_tool_rounds()),
                            tools={"write_file": tool}):
            events.append(ev)
    terms = [e for e in events if isinstance(e, TerminalEvent)]
    assert not any(t.reason == Terminal.COMPLETED or t.run.success for t in terms), \
        f"崩溃路径吐了假成功终态: {[(t.reason, t.run.success) for t in terms]}"
    # 终态诚实:失败终态在、形状照旧(reason + AtomRun.success/terminal,无新形状)
    assert len(terms) == 1
    assert terms[0].reason == Terminal.ABORTED_TOOLS
    assert terms[0].run.success is False
    assert terms[0].run.terminal == "aborted_tools"
    assert isinstance(events[-1], TerminalEvent)   # 终态仍是(异常前)最后一个事件


@pytest.mark.asyncio
async def test_failloud_capability_check_raise_honest_terminal(monkeypatch):
    """capability_check 自身抛(gate 代码缺陷)→ 同款:无假成功终态 + 异常上抛 + 终态诚实。"""
    def _gate_bug(pc):
        raise KeyError("gate bug")
    monkeypatch.setattr("karvyloop.atoms.executor._authorize", _gate_bug)
    read = _Tool("write_file", safe=False)
    events: list = []
    with pytest.raises(KeyError, match="gate bug"):
        async for ev in run(_atom(), {"q": "go"}, _tok(), gateway=_gw(_one_tool_rounds()),
                            tools={"write_file": read}):
            events.append(ev)
    assert read.call_log == []   # 没跑到工具
    terms = [e for e in events if isinstance(e, TerminalEvent)]
    assert not any(t.reason == Terminal.COMPLETED or t.run.success for t in terms)
    assert len(terms) == 1 and terms[0].reason == Terminal.ABORTED_TOOLS
    assert terms[0].run.success is False


@pytest.mark.asyncio
async def test_failloud_serial_cancellederror_no_terminal_and_propagates():
    """关停路径:CancelledError 原样上抛不吞,且**不吐**任何终态(取消中不再发事件)。"""
    tool = _RaisingTool("write_file", asyncio.CancelledError("shutdown"))
    events: list = []
    with pytest.raises(asyncio.CancelledError):
        async for ev in run(_atom(), {"q": "go"}, _tok(), gateway=_gw(_one_tool_rounds()),
                            tools={"write_file": tool}):
            events.append(ev)
    assert not any(isinstance(e, TerminalEvent) for e in events), \
        "关停路径不该吐 TerminalEvent"


@pytest.mark.asyncio
async def test_concurrent_batch_baseexception_true_cause_not_dropped_unknown():
    """并发批 BaseException:gather 兜底按位归回自己的 block —— error_reason 带真因
    异常类名+消息(旧代码 id='?' 对不上 block,真因被丢成 dropped:unknown)。"""
    class _ConcRaising:
        name = "boom"
        description = "t"
        parameters = {"type": "object", "properties": {}}
        async def __call__(self, input):
            raise _HardCrash("conc crash detail")
        def is_concurrency_safe(self, input):
            return True

    ok = _Tool("read_file", safe=True)
    blocks = [
        ToolUseBlock(id="b1", name="read_file", input={"i": 1}),
        ToolUseBlock(id="b2", name="boom", input={}),
    ]
    res = await run_tools(blocks, {"read_file": ok, "boom": _ConcRaising()}, _tok())
    assert [r.tool_use_id for r in res] == ["b1", "b2"]   # 保序,每块一结果
    assert res[0].is_error is False
    assert res[1].is_error is True
    assert res[1].error_reason.startswith("_HardCrash:")   # 真因类名
    assert "conc crash detail" in res[1].error_reason      # 真因消息
    assert "dropped:unknown" not in res[1].error_reason


@pytest.mark.asyncio
async def test_concurrent_batch_cancellederror_reraises():
    """并发批里子任务抛 CancelledError → run_tools 原样上抛不吞(关停路径)。"""
    class _ConcCancel:
        name = "boom"
        description = "t"
        parameters = {"type": "object", "properties": {}}
        async def __call__(self, input):
            raise asyncio.CancelledError("child cancelled")
        def is_concurrency_safe(self, input):
            return True

    ok = _Tool("read_file", safe=True)
    blocks = [
        ToolUseBlock(id="b1", name="read_file", input={}),
        ToolUseBlock(id="b2", name="boom", input={}),
    ]
    with pytest.raises(asyncio.CancelledError):
        await run_tools(blocks, {"read_file": ok, "boom": _ConcCancel()}, _tok())


@pytest.mark.asyncio
async def test_normal_path_terminal_shape_regression_lock():
    """回归锁:正常路径事件时序/终态形状原样 —— 恰一个 TerminalEvent、居末、
    COMPLETED + success=True + terminal='completed',工具事件序不变。"""
    read = _Tool("read_file", safe=True)
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"path": "/tmp/a"}),
        text_round("done"),
    ])
    events = [ev async for ev in run(_atom(), {}, _tok(), gateway=_gw(adapter),
                                      tools={"read_file": read})]
    terms = [e for e in events if isinstance(e, TerminalEvent)]
    assert len(terms) == 1 and events[-1] is terms[0]
    assert terms[0].reason == Terminal.COMPLETED
    assert terms[0].run.success is True
    assert terms[0].run.terminal == "completed"
    # 时序:ToolCallEvent 在 ToolResultEvent 前,二者都在终态前
    i_call = next(i for i, e in enumerate(events) if isinstance(e, ToolCallEvent))
    i_res = next(i for i, e in enumerate(events) if isinstance(e, ToolResultEvent))
    assert i_call < i_res < len(events) - 1


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
