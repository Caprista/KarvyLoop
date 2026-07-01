"""test_openai_completions_adapter — P3 协议层:OpenAI Chat Completions adapter(真 HTTP/SSE)。

5问复盘 Q2:协议层必须有 request body-shape 测(respx 拦截,不靠 user 跑)。这里 respx mock
SSE 流,验:① 请求体形状(messages/tools/stream)② 文本流式 ③ 工具流式累积 ④ 格式转换。
"""
from __future__ import annotations

import pathlib
import sys

import httpx
import pytest
import respx

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.gateway.events import Done, TextDelta, ToolUseStart, ToolUseStop, Usage  # noqa: E402
from karvyloop.gateway.providers import default_adapters  # noqa: E402
from karvyloop.gateway.providers.openai_completions import (  # noqa: E402
    OpenAICompletionsAdapter,
    messages_to_openai,
    tools_to_openai,
)
from karvyloop.gateway.system import SystemPrompt  # noqa: E402
from karvyloop.schemas import ModelDefinition, ProviderConfig  # noqa: E402


def _model():
    return ModelDefinition(id="openai/gpt-4o", name="gpt-4o", api="openai-completions",
                           context_window=0, max_tokens=1024)


def _provider():
    return ProviderConfig(name="openai", api_key="FAKE-DO-NOT-LEAK", base_url="https://api.test",
                          auth="api-key", auth_header="Authorization",
                          messages_path="/v1/chat/completions", models=[])


def _sse(*chunks: str) -> str:
    return "".join(f"data: {c}\n\n" for c in chunks) + "data: [DONE]\n\n"


# ---- 注册 ----


def test_registered_in_default_adapters():
    a = default_adapters()["openai-completions"]
    assert a.api == "openai-completions"
    assert not type(a).__name__.startswith("_Stub")     # 不再是 stub


# ---- 格式转换(纯函数) ----


def test_messages_str_passthrough_and_system_first():
    out = messages_to_openai([{"role": "user", "content": "hi"}],
                             SystemPrompt(static=["你是助手"]))
    assert out[0] == {"role": "system", "content": "你是助手"}
    assert out[1] == {"role": "user", "content": "hi"}


def test_messages_tool_use_to_tool_calls():
    msgs = [{"role": "assistant", "content": [
        {"type": "text", "text": "我查一下"},
        {"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "x"}},
    ]}]
    out = messages_to_openai(msgs, None)
    assert out[0]["role"] == "assistant" and out[0]["content"] == "我查一下"
    tc = out[0]["tool_calls"][0]
    assert tc["id"] == "call_1" and tc["function"]["name"] == "read_file"
    assert '"path"' in tc["function"]["arguments"]       # input → json 字符串


def test_messages_tool_result_to_tool_message():
    msgs = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "文件内容"},
    ]}]
    out = messages_to_openai(msgs, None)
    assert out[0] == {"role": "tool", "tool_call_id": "call_1", "content": "文件内容"}


def test_tools_schema_conversion():
    out = tools_to_openai([{"name": "f", "description": "do f",
                            "input_schema": {"type": "object", "properties": {"a": {}}}}])
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "f"
    assert out[0]["function"]["parameters"]["properties"] == {"a": {}}


# ---- HTTP/SSE(respx mock) ----


@pytest.mark.asyncio
@respx.mock
async def test_text_streaming_and_body_shape():
    captured = {}

    def _resp(req):
        import json as _j
        captured["body"] = _j.loads(req.content)
        return httpx.Response(200, text=_sse(
            '{"choices":[{"delta":{"content":"你"},"finish_reason":null}]}',
            '{"choices":[{"delta":{"content":"好"},"finish_reason":null}]}',
            '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
            '{"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2}}',
        ))

    respx.post("https://api.test/v1/chat/completions").mock(side_effect=_resp)
    evs = []
    async for ev in OpenAICompletionsAdapter().complete(
            [{"role": "user", "content": "在吗"}], [], _model(), _provider(),
            system=SystemPrompt(static=["S"])):
        evs.append(ev)
    # 请求体形状(Q2):messages/stream
    assert captured["body"]["model"] == "gpt-4o"
    assert captured["body"]["stream"] is True
    assert captured["body"]["messages"][0]["role"] == "system"
    # 事件:文本 + Usage + Done
    text = "".join(e.text for e in evs if isinstance(e, TextDelta))
    assert text == "你好"
    assert any(isinstance(e, Usage) and e.input_tokens == 10 for e in evs)
    assert any(isinstance(e, Done) and e.stop_reason == "stop" for e in evs)


@pytest.mark.asyncio
@respx.mock
async def test_extra_headers_sent_without_clobbering_auth():
    """配置驱动的额外静态头(如 Kimi For Coding 的 UA 放行门)必须真发出去,
    且**不能覆盖 Authorization**(密钥唯一来源是 api_key)。"""
    captured = {}

    def _resp(req):
        captured["headers"] = dict(req.headers)
        return httpx.Response(200, text=_sse('{"choices":[{"delta":{},"finish_reason":"stop"}]}'))

    prov = ProviderConfig(name="kimi-coding", api_key="FAKE-DO-NOT-LEAK",
                          base_url="https://api.test", auth="api-key", auth_header="Authorization",
                          messages_path="/v1/chat/completions",
                          # 故意塞一个想覆盖鉴权头的恶意项 —— 必须被忽略
                          extra_headers={"User-Agent": "KarvyLoop-Forge/0.1", "Authorization": "Bearer HACK"})
    respx.post("https://api.test/v1/chat/completions").mock(side_effect=_resp)
    async for _ in OpenAICompletionsAdapter().complete(
            [{"role": "user", "content": "hi"}], [], _model(), prov):
        pass
    h = {k.lower(): v for k, v in captured["headers"].items()}
    assert h["user-agent"] == "KarvyLoop-Forge/0.1"         # UA 放行门:真发出去(用自己真实 UA)
    assert h["authorization"] == "Bearer FAKE-DO-NOT-LEAK"  # 鉴权头来自 api_key,没被 extra_headers 篡改


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_default_path_self_heals_to_openai():
    """旧配置没设 messages_path → schema 默认是 anthropic 的 /v1/messages(对 openai 端点会 404)。
    adapter 必须自愈回落 /v1/chat/completions,让旧/错配置也开箱即跑。"""
    prov = ProviderConfig(name="deepseek", api_key="FAKE-DO-NOT-LEAK",
                          base_url="https://api.test/v1", auth="api-key", auth_header="Authorization")
    assert prov.messages_path == "/v1/messages"   # schema 默认确实是 anthropic 的
    hit = respx.post("https://api.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=_sse('{"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}')))
    txt = ""
    async for ev in OpenAICompletionsAdapter().complete([{"role": "user", "content": "hi"}], [], _model(), prov):
        if isinstance(ev, TextDelta):
            txt += ev.text
    assert hit.called and txt == "ok"   # 打到了 /v1/chat/completions,不是 /v1/v1/messages


@pytest.mark.asyncio
@respx.mock
async def test_tool_call_streaming_accumulates():
    respx.post("https://api.test/v1/chat/completions").mock(return_value=httpx.Response(200, text=_sse(
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_9","function":{"name":"read_file","arguments":""}}]},"finish_reason":null}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"path\\":"}}]},"finish_reason":null}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"a.txt\\"}"}}]},"finish_reason":null}]}',
        '{"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
    )))
    evs = []
    async for ev in OpenAICompletionsAdapter().complete(
            [{"role": "user", "content": "读 a.txt"}],
            [{"name": "read_file", "description": "", "input_schema": {}}],
            _model(), _provider()):
        evs.append(ev)
    starts = [e for e in evs if isinstance(e, ToolUseStart)]
    stops = [e for e in evs if isinstance(e, ToolUseStop)]
    assert len(starts) == 1 and starts[0].id == "call_9" and starts[0].name == "read_file"
    assert len(stops) == 1 and stops[0].input == {"path": "a.txt"}   # 增量 args 累积+解析
    assert any(isinstance(e, Done) and e.stop_reason == "tool_calls" for e in evs)


@pytest.mark.asyncio
@respx.mock
async def test_http_error_to_error_event():
    from karvyloop.gateway.events import ErrorEvent
    respx.post("https://api.test/v1/chat/completions").mock(return_value=httpx.Response(401, text="bad key"))
    evs = [ev async for ev in OpenAICompletionsAdapter().complete(
        [{"role": "user", "content": "x"}], [], _model(), _provider())]
    assert any(isinstance(e, ErrorEvent) for e in evs)   # 4xx → ErrorEvent,不穿透
