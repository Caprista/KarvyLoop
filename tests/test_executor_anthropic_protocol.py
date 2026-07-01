"""Executor 协议合规测试 —— messages 不再含 role=system, tools 用 Anthropic schema。

为什么需要这些测试:
1. Anthropic Messages API 协议规定 `system` 字段在 body 顶层,
   `messages[].role=system` 是 OpenAI 风格 → MiniMax 兼容端点会忽略/拒绝。
2. `tools` 必须是 `{name, description, input_schema}` —— OpenAI 的
   `type:"function"` 在 MiniMax 返 400 "function name or parameters is empty"。
3. assistant 消息的 tool_use 在 content blocks 里(不是 tool_calls 字段)。
4. tool_result 回灌是 user 消息的 content blocks(不是 role:"tool" 字段)。

回归风险:executor 之前两个 bug 都被掩盖(本地 mock 接受),靠这些
测试锁住正确行为,避免之后被"图省事"改回 OpenAI 风格。
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest

from karvyloop.atoms.executor import _tools_to_schemas, run as atom_run
from karvyloop.gateway import GatewayClient, SystemPrompt
from karvyloop.gateway.events import Done, TextDelta, ToolUseStart, ToolUseStop, Usage
from karvyloop.gateway.providers.base import ProviderAdapter
from karvyloop.gateway.registry import ModelRegistry
from karvyloop.schemas import AtomSpec, Capability, CapabilityToken


class CaptureAdapter(ProviderAdapter):
    api = "anthropic-messages"

    def __init__(self, rounds: list[list] | None = None):
        self.rounds = rounds or [[TextDelta("hi"), Usage(input_tokens=1, output_tokens=1), Done("end_turn")]]
        self.call_count = 0
        # 记录每次调用的 messages(最新一次覆盖,用于检查回灌后的形态)
        self.last_messages: list[dict] = []
        self.all_calls: list[list[dict]] = []  # 每次调用的 messages snapshot
        self.captured_tools: list | None = None
        self.captured_system = None

    async def complete(self, messages, tools, model, provider, *, system=None):
        # 每次调用都截获(覆盖 last_messages + 累计 all_calls)
        snapshot = list(messages)
        self.all_calls.append(snapshot)
        self.last_messages = snapshot
        if self.captured_tools is None:
            self.captured_tools = list(tools)
            self.captured_system = system
        idx = min(self.call_count, len(self.rounds) - 1)
        self.call_count += 1
        for ev in self.rounds[idx]:
            yield ev

    async def embed(self, *a, **k):
        return []


def _gw(cap: CaptureAdapter) -> GatewayClient:
    reg_cfg = {
        "models": {"providers": {"p": {"base_url": "x", "models": [
            {"id": "p/m", "name": "m", "api": "anthropic-messages",
             "context_window": 100, "max_tokens": 100},
        ]}}},
        "agents": {"defaults": {"model": "p/m"}},
        "embedding": {"model": "p/m"},
    }
    reg = ModelRegistry.from_config(reg_cfg)
    return GatewayClient(reg, adapters={"anthropic-messages": cap})


def _atom() -> AtomSpec:
    return AtomSpec(id="t", kind="task", prompt="", input_schema={}, output_schema={},
                    tools=[], model="p/m")


def _tok() -> CapabilityToken:
    return CapabilityToken(task_id="t", grants=[Capability(resource="fs:/x", ops=["read"])],
                          expiry=1e10)


@pytest.mark.asyncio
async def test_messages_have_no_system_role():
    """Anthropic 协议: system 走 body 顶层, messages 里不应有 role=system。"""
    cap = CaptureAdapter()
    gw = _gw(cap)
    sys_p = SystemPrompt(static=["you are coder"])
    async for _ in atom_run(_atom(), {"intent": "hi"}, _tok(),
                            gateway=gw, tools={}, system=sys_p):
        pass
    for m in cap.last_messages:
        assert m["role"] != "system", (
            f"messages 里不应有 role=system(Anthropic 协议规定 system 走 body 顶层);"
            f" 实际: {m}"
        )
    assert [m["role"] for m in cap.last_messages] == ["user"]


@pytest.mark.asyncio
async def test_tools_use_anthropic_schema():
    """Anthropic 协议: tool 字段是 {name, description, input_schema}, 不是 type:function。"""
    class FakeTool:
        description = "read a file"
        parameters = {"type": "object", "properties": {"path": {"type": "string"}}}

    schemas = _tools_to_schemas({"read_file": FakeTool()})
    assert len(schemas) == 1
    s = schemas[0]
    assert s.get("name") == "read_file"
    assert "description" in s
    assert "input_schema" in s
    # 禁止 OpenAI 风格
    assert s.get("type") != "function"
    assert "function" not in s


@pytest.mark.asyncio
async def test_assistant_message_uses_content_blocks_not_tool_calls():
    """Anthropic 协议: assistant 消息的 tool_use 在 content blocks 里(不是 tool_calls 字段)。

    验证两轮: 第一轮模型发 tool_use, 第二轮模型只发 text。
    第二轮的 messages 里的 assistant 消息必须是 Anthropic 风格:
      - role=assistant
      - content=[{type:"text",...}, {type:"tool_use",...}]  (或只有 text 块)
      - 不能有 tool_calls 字段
    """
    cap = CaptureAdapter(rounds=[
        [TextDelta(""), ToolUseStart(id="u1", name="echo"),
         ToolUseStop(id="u1", input={"x": 1}),
         Usage(input_tokens=1, output_tokens=1), Done("tool_use")],
        [TextDelta("done"), Usage(input_tokens=1, output_tokens=1), Done("end_turn")],
    ])
    gw = _gw(cap)
    # 注入一个假 echo 工具
    class EchoTool:
        description = "echo"
        parameters = {"type": "object", "properties": {"x": {"type": "integer"}}}
        async def __call__(self, inp): return inp
    async for _ in atom_run(_atom(), {"intent": "hi"}, _tok(),
                            gateway=gw, tools={"echo": EchoTool()}):
        pass
    # 第二轮: 找最新的 assistant 消息
    msgs = cap.last_messages
    # 第二轮 messages: [user(原), assistant(发 tool_use), user(回灌 tool_result), assistant(发 text)]
    asst = [m for m in msgs if m.get("role") == "assistant"]
    assert len(asst) >= 1
    # 第一个 assistant 消息(发 tool_use 的)必须 content blocks
    first = asst[0]
    assert "tool_calls" not in first, (
        f"executor 还在用 OpenAI 风格的 tool_calls 字段;Anthropic 协议用 content blocks"
    )
    assert isinstance(first.get("content"), list), (
        f"content 必须是 list of blocks (Anthropic 协议), 实际: {type(first.get('content')).__name__}"
    )
    block_types = [b.get("type") for b in first["content"]]
    assert "tool_use" in block_types, f"assistant 应有 tool_use block;实际: {block_types}"


@pytest.mark.asyncio
async def test_tool_result_backfilled_as_user_message_with_blocks():
    """Anthropic 协议: tool_result 回灌是 user 消息 + content=[{type:'tool_result',...}], 不是 role='tool'。"""
    cap = CaptureAdapter(rounds=[
        [TextDelta(""), ToolUseStart(id="u1", name="echo"),
         ToolUseStop(id="u1", input={"x": 1}),
         Usage(input_tokens=1, output_tokens=1), Done("tool_use")],
        [TextDelta("done"), Usage(input_tokens=1, output_tokens=1), Done("end_turn")],
    ])
    gw = _gw(cap)
    class EchoTool:
        description = "echo"
        parameters = {"type": "object", "properties": {"x": {"type": "integer"}}}
        async def __call__(self, inp): return {"got": inp}
    async for _ in atom_run(_atom(), {"intent": "hi"}, _tok(),
                            gateway=gw, tools={"echo": EchoTool()}):
        pass
    msgs = cap.last_messages
    # 不应有 role="tool" 的消息
    bad = [m for m in msgs if m.get("role") == "tool"]
    assert not bad, f"executor 还在用 OpenAI 风格的 role='tool';Anthropic 协议是 user + content blocks"
    # 应有 user 消息含 type=tool_result 的 block
    tool_results = []
    for m in msgs:
        if m.get("role") != "user":
            continue
        for b in (m.get("content") or []):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tool_results.append(b)
    assert tool_results, f"应至少有一个 tool_result block(从 echo 工具结果回灌)"
    assert tool_results[0].get("tool_use_id") == "u1"


@pytest.mark.asyncio
async def test_tool_result_content_json_serializable_for_dataclass():
    """tool_result.content 可能是 dataclass(CodingResult 等),必须能 JSON 序列化。

    为什么需要: 第二轮调模型时 adapter 转发 messages 给上游,任何
    non-JSON-serializable 对象都会 TypeError,导致整个 run 提前终止
    (ErrorEvent → run_end reason=completed 但无 text 响应)。

    **Anthropic 协议硬约束**:tool_result.content 必须是 string 或 content blocks
    列表,不能是裸 dict(MiniMax 兼容端点会因 dict 直接 400 Bad Request)。
    """
    from dataclasses import dataclass
    from karvyloop.atoms.executor import _serialize_results_for_model
    from karvyloop.atoms.orchestration import ToolResult

    @dataclass
    class FakeCodingResult:
        ok: bool
        payload: dict
        error_code: int = 0
        error_message: str = ""

    # 模拟 coding 工具返回 CodingResult 对象
    fake = FakeCodingResult(ok=True, payload={"echo": "hello"})
    r = ToolResult(tool_use_id="u1", name="echo", content=fake)

    msgs = _serialize_results_for_model([r])
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    block = msgs[0]["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "u1"
    # content 必须是 string(Anthropic 协议规定 tool_result.content 不能是 dict)
    import json
    content = block["content"]
    assert isinstance(content, str), (
        f"content 必须是 str (Anthropic 协议: tool_result.content 只能 string/blocks),"
        f" 实际: {type(content).__name__}"
    )
    # string 必须是合法 JSON(模型能看到原 dict 结构)
    parsed = json.loads(content)
    assert parsed["ok"] is True
    assert parsed["payload"] == {"echo": "hello"}


@pytest.mark.asyncio
async def test_tool_result_error_also_serialized_as_string():
    """is_error=True 的 tool_result,content 同样是 string(JSON of {error, reason})。"""
    from karvyloop.atoms.executor import _serialize_results_for_model
    from karvyloop.atoms.orchestration import ToolResult

    r = ToolResult(tool_use_id="u2", name="bad", content=None,
                   is_error=True, error_reason="permission_denied")
    msgs = _serialize_results_for_model([r])
    block = msgs[0]["content"][0]
    import json
    content = block["content"]
    assert isinstance(content, str)
    parsed = json.loads(content)
    assert parsed["error"] is True
    assert parsed["reason"] == "permission_denied"
