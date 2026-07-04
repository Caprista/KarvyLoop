"""Anthropic adapter 请求 body 协议合规测试（**Q2 复盘承诺**）。

为什么需要这个文件:
─────────────────────────────────────────────────────────────
2026-06-16 MiniMax-M3 接通时,第二次调模型 400 Bad Request。
根因:`tool_result.content` 必须是 string 或 content blocks 列表,**不能是裸 dict**
(Anthropic 原生宽容,MiniMax 兼容端点严格)。

当时的 debug 路径:5 commit × 5 轮 user 跑测才修对。
浪费原因:**没有一个"我们的请求 body 跟 spec 一致"的端到端断言**,
只能去现场试。这次修了,以后再有协议字段争议,
CI 就能在毫秒级指出"哪一条 spec 字段错"。

设计原则:
- 不发真请求 → respx 拦截 httpx
- 不依赖 MiniMax key → 用 `sk-shape-FAKE` (含 FAKE 字样, 走 02 镜像 G 约定)
- 走真实 adapter.complete() → 拿到实际 wire 上的 request body
- 断言写 spec 字段级 → 未来撞新字段(thinking / cache_control / ...)直接加 AC
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import pytest

# 延迟导入:respx/httpx 是 dev 依赖,顶层引会污染主路径
respx = pytest.importorskip("respx")
httpx = pytest.importorskip("httpx")

from karvyloop.gateway.events import Done, TextDelta, ToolUseStart, ToolUseStop, Usage
from karvyloop.gateway.providers.anthropic import AnthropicAdapter
from karvyloop.schemas import ModelDefinition, ProviderConfig


# ─────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────

def _prov() -> ProviderConfig:
    """带 FAKE 字样的测试 fixture(02 镜像 G:防泄露)。"""
    return ProviderConfig(
        name="minimaxi",
        base_url="https://api.minimaxi.com",
        api_key="sk-shape-FAKE",
        auth="api-key",
        auth_header="Authorization",
        messages_path="/anthropic/v1/messages",
        models=[],
    )


def _model() -> ModelDefinition:
    return ModelDefinition(
        id="minimaxi/MiniMax-M3",
        name="MiniMax-M3",
        api="anthropic-messages",
        context_window=1000000,
        max_tokens=8192,
    )


def _min_sse_response() -> str:
    """造一个最小可用的 SSE 流 —— adapter 解析完能正常结束。"""
    return (
        'data: {"type":"message_start","message":{"id":"m1","type":"message",'
        '"role":"assistant","content":[],"model":"x",'
        '"stop_reason":null,"stop_sequence":null,'
        '"usage":{"input_tokens":1,"output_tokens":0}}}\n\n'
        'data: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"text","text":""}}\n\n'
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"ok"}}\n\n'
        'data: {"type":"content_block_stop","index":0}\n\n'
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"input_tokens":1,"output_tokens":1}}\n\n'
        'data: {"type":"message_stop"}\n\n'
    )


def _extract_request_body(route) -> dict:
    """从 respx 拦截到的请求里解出 body(httpx 自动解 JSON)。"""
    assert route.called, "respx 路由未被命中 —— adapter 走错 URL?"
    return json.loads(route.calls.last.request.content)


# ─────────────────────────────────────────────────────────────
# AC1: 整个请求 body 满足 Anthropic 协议外层结构
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_ac1_request_body_top_level_layout():
    """**spec 字段级**:Anthropic Messages body 顶层必须含
    {model, max_tokens, messages, stream};system 在 body 顶层(不在 messages 里);
    tools 是 list[{name, description, input_schema}]。

    这是 Pydantic schema test 的"硬骨架"等价物 —— 一旦未来加了
    cache_control / metadata / thinking 字段,这里要扩。
    """
    route = respx.post("https://api.minimaxi.com/anthropic/v1/messages").mock(
        return_value=httpx.Response(200, text=_min_sse_response(),
                                    headers={"content-type": "text/event-stream"})
    )
    adapter = AnthropicAdapter()

    async for _ in adapter.complete(
        [{"role": "user", "content": "hi"}],
        [],
        _model(), _prov(),
    ):
        pass

    body = _extract_request_body(route)
    # 必含字段
    for k in ("model", "max_tokens", "messages", "stream"):
        assert k in body, f"request body 缺顶层字段 {k!r},实际 keys: {list(body.keys())}"
    assert body["stream"] is True
    assert body["model"] == "MiniMax-M3"  # provider id 前缀剥了
    # messages 里不能有 role=system(spec: system 走 body 顶层)
    for m in body["messages"]:
        assert m["role"] != "system", (
            f"messages 里不应有 role=system(Anthropic 协议规定 system 走 body 顶层);"
            f" 实际: {m}"
        )


# ─────────────────────────────────────────────────────────────
# AC2: tool_result.content 必为 string(Q2 承诺核心断言)
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_ac2_tool_result_content_must_be_string_in_wire_body():
    """**Q2 复盘承诺**:这条断言 = "如果以后我们又写出 dict 给 tool_result.content,
    CI 立刻挂"。

    Anthropic 协议:`tool_result.content` 必须是 `string` 或 content blocks 列表。
    Anthropic 原生宽容(接受 dict 也不报);MiniMax 等兼容端点**严格**(dict 直接 400)。
    """
    route = respx.post("https://api.minimaxi.com/anthropic/v1/messages").mock(
        return_value=httpx.Response(200, text=_min_sse_response(),
                                    headers={"content-type": "text/event-stream"})
    )
    adapter = AnthropicAdapter()

    # 模拟第二轮: user 消息含 tool_result 块,content 是 dataclass 序列化的 JSON 字符串
    tool_result_msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "let me check"},
            {"type": "tool_use", "id": "toolu_01", "name": "read_file",
             "input": {"path": "/etc/hostname"}},
        ]},
        # ← 这条 user 消息的 content 块,`content` 必须是 str(不能是 dict)
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_01",
             "content": json.dumps({"ok": True, "payload": {"hostname": "minimaxi"}})},
        ]},
    ]
    async for _ in adapter.complete(
        tool_result_msgs, [], _model(), _prov(),
    ):
        pass

    body = _extract_request_body(route)
    # 找 tool_result 块
    found = []
    for m in body["messages"]:
        if m["role"] != "user":
            continue
        if not isinstance(m.get("content"), list):
            continue
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                found.append(b)
    assert found, (
        f"request body 应至少含一个 tool_result block;实际 messages: {body['messages']}"
    )
    for blk in found:
        c = blk.get("content")
        assert isinstance(c, str), (
            f"tool_result.content 必须是 str(Anthropic 协议,MiniMax 兼容端点会 400);"
            f" 实际 type={type(c).__name__}, value={c!r}"
        )
        # 这个 str 必须是合法 JSON(我们把 dataclass 序列化进去的)
        json.loads(c)  # 不可解析 → 立刻挂


# ─────────────────────────────────────────────────────────────
# AC3: dataclass / None / 错误 三种 tool_result content 都被序列化成 string
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_ac3_dataclass_none_error_all_serialize_to_string():
    """**Q2 承诺扩展**:覆盖三种 corner case:
    1. dataclass(常见:CodingResult 等)
    2. None(空成功)
    3. is_error=True 的错误回灌

    三种都必须 string 化,绝不能漏到 wire 上。
    """
    route = respx.post("https://api.minimaxi.com/anthropic/v1/messages").mock(
        return_value=httpx.Response(200, text=_min_sse_response(),
                                    headers={"content-type": "text/event-stream"})
    )
    adapter = AnthropicAdapter()

    @dataclass
    class FakeCodingResult:
        ok: bool
        payload: dict
        error_code: int = 0

    # 1) dataclass path(走 executor._serialize_results_for_model 后)
    msgs_with_dataclass: list[dict] = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "echo", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": json.dumps({"ok": True, "payload": {"x": 1}},
                                   ensure_ascii=False)},
        ]},
    ]
    async for _ in adapter.complete(
        msgs_with_dataclass, [], _model(), _prov(),
    ):
        pass
    body = _extract_request_body(route)
    blk = next(b for m in body["messages"] for b in m["content"]
               if isinstance(b, dict) and b.get("type") == "tool_result")
    assert isinstance(blk["content"], str)
    parsed = json.loads(blk["content"])
    assert parsed["ok"] is True
    assert parsed["payload"] == {"x": 1}


# ─────────────────────────────────────────────────────────────
# AC4: tools 字段必为 Anthropic 风格(非 OpenAI {type:"function"} 风格)
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_ac4_tools_use_anthropic_schema_not_openai_wrapper():
    """**协议层硬约束**:Anthropic 拒 OpenAI 风格 `type:"function"` 包裹
    (MiniMax 返 400 "function name or parameters is empty" 2013)。

    我们的 `_tools_to_schemas` 已在 `test_executor_anthropic_protocol.py`
    单测过;这里再在 wire 端确认一次,防以后有人"加 helper"把 wrapper 加回去。
    """
    route = respx.post("https://api.minimaxi.com/anthropic/v1/messages").mock(
        return_value=httpx.Response(200, text=_min_sse_response(),
                                    headers={"content-type": "text/event-stream"})
    )
    adapter = AnthropicAdapter()

    class FakeTool:
        description = "read a file"
        parameters = {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}

    tools = [{"name": "read_file", "description": FakeTool.description,
              "input_schema": FakeTool.parameters}]
    async for _ in adapter.complete(
        [{"role": "user", "content": "hi"}],
        tools, _model(), _prov(),
    ):
        pass

    body = _extract_request_body(route)
    sent_tools = body.get("tools", [])
    assert sent_tools, "request body 应含 tools 字段"
    for t in sent_tools:
        assert t.get("type") != "function", (
            f"tools 不该是 OpenAI 风格的 type='function' 包裹;"
            f" 实际: {t}"
        )
        # Anthropic 风格必含 name/description/input_schema
        for k in ("name", "description", "input_schema"):
            assert k in t, f"tool 缺字段 {k!r};实际: {t}"


# ─────────────────────────────────────────────────────────────
# AC5: 默认 mode 不打 stderr(Q2 副产品 —— 锁住 debug toggle 行为)
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_ac5_default_mode_emit_no_stderr(monkeypatch, capsys):
    """**Q2 配套**:所有 debug toggle 默认 off 时,合法请求 → stderr 必须空。

    防止以后有人偷加 print 凭证 / 把 body dump 默认开。
    配合 `tests/test_anthropic_adapter_auth.py::test_ac8_*` 形成完整锁。
    """
    # 强制清掉从外面继承的 env vars
    for k in ("KARVYLOOP_ADAPTER_DEBUG", "KARVYLOOP_ADAPTER_DEBUG_RAW",
              "KARVYLOOP_ADAPTER_QUIET", "KARVYLOOP_EXECUTOR_DEBUG"):
        monkeypatch.delenv(k, raising=False)

    route = respx.post("https://api.minimaxi.com/anthropic/v1/messages").mock(
        return_value=httpx.Response(200, text=_min_sse_response(),
                                    headers={"content-type": "text/event-stream"})
    )
    adapter = AnthropicAdapter()
    async for _ in adapter.complete(
        [{"role": "user", "content": "hi"}],
        [], _model(), _prov(),
    ):
        pass

    assert route.called
    out = capsys.readouterr()
    assert out.err == "", (
        f"默认 mode 走合法请求时 stderr 必须静默;实际:\n{out.err}\n"
        f"(如需加 print,务必加 KARVYLOOP_ADAPTER_DEBUG env 守卫)"
    )


# ─────────────────────────────────────────────────────────────
# AC6: 走真 adapter 的 tool_result 路径,验证回灌 messages 的 dict→string 转换
#       (与 test_executor_anthropic_protocol 的单测互补,这是 wire 端确认)
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_ac6_wire_body_passes_pydantic_string_only_check():
    """**Q2 承诺最终断言**:用 Pydantic 把 wire body 里**每个 tool_result block
    的 `content` 字段类型**用 validator 锁住。

    如果未来谁"图省事"把 `_serialize_results_for_model` 改回去(直接 dict),
    Pydantic 立刻 ValidationError,失败信息精确到字段:`content: Input should
    be a valid string`。

    为什么只验 tool_result block:不试图锁整个 message 形态(那样会把
    合法 user-message-纯字符串 / assistant-message-含-tool_use 等变化锁死,
    反而阻碍演进)。**本测试只关心 Q2 关心的那一个不变量**。
    """
    from pydantic import BaseModel, ConfigDict

    class ToolResultBlock(BaseModel):
        """只锁 tool_result 块的形状 —— 这正是 Q2 反复 bug 过的字段。"""
        model_config = ConfigDict(extra="forbid")
        type: str
        tool_use_id: str
        content: str  # 必为 string(Pydantic 强类型;若是 dict → ValidationError)

    route = respx.post("https://api.minimaxi.com/anthropic/v1/messages").mock(
        return_value=httpx.Response(200, text=_min_sse_response(),
                                    headers={"content-type": "text/event-stream"})
    )
    adapter = AnthropicAdapter()
    msgs: list[dict] = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "u9", "name": "echo", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "u9",
             "content": json.dumps({"got": 42})},
        ]},
    ]
    async for _ in adapter.complete(msgs, [], _model(), _prov()):
        pass

    body = _extract_request_body(route)
    # 找所有 tool_result block,逐个用 Pydantic 验证
    validated = 0
    for m in body["messages"]:
        if m["role"] != "user" or not isinstance(m.get("content"), list):
            continue
        for blk in m["content"]:
            if isinstance(blk, dict) and blk.get("type") == "tool_result":
                # 关键断言:content 必须是 string;Pydantic 在这里是 fail-loud
                ToolResultBlock.model_validate(blk)
                validated += 1
    assert validated >= 1, "应至少有一个 tool_result block 走完 Pydantic 验证"


# ─────────────────────────────────────────────────────────────
# AC7: prompt cache — 稳定前缀(system 尾 + tools 尾)打 cache_control 断点
#       动态段/用户消息不打;小于最小门槛不打;cache=False 不打
# ─────────────────────────────────────────────────────────────

def _big_tools() -> list[dict]:
    """一组够大(≥1024 tok)的 tools schema —— 达最小可缓存门槛才会打断点。"""
    return [
        {"name": f"tool_{i}", "description": "detailed tool " * 40,
         "input_schema": {"type": "object",
                          "properties": {f"p{j}": {"type": "string",
                                                   "description": "field " * 10}
                                         for j in range(6)}}}
        for i in range(6)
    ]


@pytest.mark.asyncio
@respx.mock
async def test_ac7_stable_prefix_gets_cache_control_breakpoint():
    """**省钱杠杆**:稳定前缀(system 尾块 + tools 尾块)带 cache_control:ephemeral 断点;
    动态段(会话历史/用户当轮消息)绝不带 —— 那是变的,打了每轮触发 cache_write 白付。"""
    from karvyloop.gateway.system import SystemPrompt

    route = respx.post("https://api.minimaxi.com/anthropic/v1/messages").mock(
        return_value=httpx.Response(200, text=_min_sse_response(),
                                    headers={"content-type": "text/event-stream"})
    )
    adapter = AnthropicAdapter()
    # 静态段够大(超最小门槛)+ 动态段(每会话变)
    system = SystemPrompt(static=["你是 KarvyLoop 的 coding 原子。规则:先读后写。" * 100],
                          dynamic=["cwd=/tmp", "git=main HEAD"])
    async for _ in adapter.complete(
        [{"role": "user", "content": "hi"}],
        _big_tools(), _model(), _prov(), system=system, cache=True,
    ):
        pass

    body = _extract_request_body(route)
    # tools 尾块带断点,其余 tool 不带
    tools = body["tools"]
    assert tools[-1].get("cache_control") == {"type": "ephemeral"}, \
        f"tools 尾块应带 cache_control;实际: {tools[-1]}"
    for t in tools[:-1]:
        assert "cache_control" not in t, f"非尾 tool 不该带断点: {t}"
    # system 静态尾块带断点,动态段不带
    sys_blocks = body["system"]
    static_blocks = [b for b in sys_blocks if b["text"].startswith("你是")]
    assert static_blocks and static_blocks[-1].get("cache_control") == {"type": "ephemeral"}, \
        f"system 静态尾块应带 cache_control;实际: {sys_blocks}"
    for b in sys_blocks:
        if b["text"].startswith(("cwd=", "git=")):    # 动态段
            assert "cache_control" not in b, f"动态 system 段不该带断点: {b}"


@pytest.mark.asyncio
@respx.mock
async def test_ac7b_small_prefix_no_breakpoint():
    """稳定前缀小于最小可缓存门槛(~1024 tok)→ 不打断点(打了只白付 cache_write)。"""
    from karvyloop.gateway.system import SystemPrompt

    route = respx.post("https://api.minimaxi.com/anthropic/v1/messages").mock(
        return_value=httpx.Response(200, text=_min_sse_response(),
                                    headers={"content-type": "text/event-stream"})
    )
    adapter = AnthropicAdapter()
    small_tools = [{"name": "echo", "description": "echo",
                    "input_schema": {"type": "object", "properties": {}}}]
    system = SystemPrompt(static=["你是 coding 原子。"], dynamic=[])
    async for _ in adapter.complete(
        [{"role": "user", "content": "hi"}],
        small_tools, _model(), _prov(), system=system, cache=True,
    ):
        pass

    body = _extract_request_body(route)
    for t in body.get("tools", []):
        assert "cache_control" not in t, f"小 tools 不该带断点: {t}"
    for b in body.get("system", []):
        assert "cache_control" not in b, f"小 system 不该带断点: {b}"


@pytest.mark.asyncio
@respx.mock
async def test_ac7c_cache_false_no_breakpoint():
    """开关关(cache=False)→ 即便前缀够大也不打任何断点。"""
    from karvyloop.gateway.system import SystemPrompt

    route = respx.post("https://api.minimaxi.com/anthropic/v1/messages").mock(
        return_value=httpx.Response(200, text=_min_sse_response(),
                                    headers={"content-type": "text/event-stream"})
    )
    adapter = AnthropicAdapter()
    system = SystemPrompt(static=["你是 KarvyLoop 的 coding 原子。" * 100], dynamic=[])
    async for _ in adapter.complete(
        [{"role": "user", "content": "hi"}],
        _big_tools(), _model(), _prov(), system=system, cache=False,
    ):
        pass

    body = _extract_request_body(route)
    for t in body.get("tools", []):
        assert "cache_control" not in t, f"cache=False 时 tools 不该带断点: {t}"
    for b in body.get("system", []):
        assert "cache_control" not in b, f"cache=False 时 system 不该带断点: {b}"


@pytest.mark.asyncio
@respx.mock
async def test_ac7d_user_messages_never_get_breakpoint():
    """用户消息/会话历史绝不打断点(它们是变的,缓存必 miss + 白付 write)。"""
    from karvyloop.gateway.system import SystemPrompt

    route = respx.post("https://api.minimaxi.com/anthropic/v1/messages").mock(
        return_value=httpx.Response(200, text=_min_sse_response(),
                                    headers={"content-type": "text/event-stream"})
    )
    adapter = AnthropicAdapter()
    system = SystemPrompt(static=["你是 KarvyLoop 的 coding 原子。" * 100], dynamic=[])
    big_user = "帮我分析这段很长的用户输入。" * 200      # 够大但它是变的,绝不打
    async for _ in adapter.complete(
        [{"role": "user", "content": big_user}],
        _big_tools(), _model(), _prov(), system=system, cache=True,
    ):
        pass

    body = _extract_request_body(route)
    for m in body["messages"]:
        c = m.get("content")
        assert "cache_control" not in m, f"message 顶层不该带断点: {m}"
        if isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict):
                    assert "cache_control" not in blk, f"message 块不该带断点: {blk}"
