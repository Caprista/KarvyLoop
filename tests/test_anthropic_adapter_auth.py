"""AnthropicAdapter 鉴权 header 切换测试。

为什么需要这个测试:karvyloop 原生 Anthropic 走 `x-api-key`;但 MiniMax / 自建
Anthropic 兼容网关走 `Authorization: Bearer`。ProviderConfig.auth_header 决定
发哪个 —— 默认 `x-api-key`(不破坏原生 Anthropic),配置里改
`auth_header: Authorization` 即可切到 Bearer 模式。

不在沙箱内发真请求,用 respx 拦截 httpx,验证请求 header。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# 延迟导入:respx 是 dev 依赖,顶层引会污染主路径
respx = pytest.importorskip("respx")
httpx = pytest.importorskip("httpx")

from karvyloop.gateway.providers.anthropic import AnthropicAdapter
from karvyloop.schemas import ModelDefinition, ProviderConfig


def _prov(auth_header: str = "x-api-key", base_url: str = "https://api.minimaxi.com") -> ProviderConfig:
    return ProviderConfig(
        name="MiniMax",
        base_url=base_url,
        api_key="sk-cp-FAKE",
        auth="api-key",
        auth_header=auth_header,  # type: ignore[arg-type]
        models=[],
    )


def _model() -> ModelDefinition:
    return ModelDefinition(
        id="MiniMax/MiniMax-M3",
        name="MiniMax-M3",
        api="anthropic-messages",
        context_window=1000000,
        max_tokens=8192,
    )


# -------- AC1:默认走 x-api-key(原生 Anthropic 习惯, 不破坏现有)--------
@pytest.mark.asyncio
@respx.mock
async def test_ac1_default_auth_sends_x_api_key():
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, text="")
    )
    adapter = AnthropicAdapter()
    prov = ProviderConfig(  # 显式不传 auth_header → 默认 x-api-key
        name="anthropic", base_url="https://api.anthropic.com",
        api_key="sk-ant-FAKE", auth="api-key", models=[],
    )
    try:
        async for _ in adapter.complete(
            [{"role": "user", "content": "hi"}], [], _model(), prov,
        ):
            pass
    except Exception:
        pass  # 响应体是空字符串, parse 会炸, 不关心 —— 我们只验 header

    assert route.called
    sent = route.calls.last.request
    assert sent.headers.get("x-api-key") == "sk-ant-FAKE"
    assert "authorization" not in {k.lower() for k in sent.headers.keys()}


# -------- AC2:显式 Authorization → 发 Bearer --------
@pytest.mark.asyncio
@respx.mock
async def test_ac2_authorization_sends_bearer():
    route = respx.post("https://api.minimaxi.com/v1/messages").mock(
        return_value=httpx.Response(200, text="")
    )
    adapter = AnthropicAdapter()
    prov = _prov(auth_header="Authorization", base_url="https://api.minimaxi.com")
    try:
        async for _ in adapter.complete(
            [{"role": "user", "content": "hi"}], [], _model(), prov,
        ):
            pass
    except Exception:
        pass

    assert route.called
    sent = route.calls.last.request
    assert sent.headers.get("Authorization") == "Bearer sk-cp-FAKE"
    # x-api-key 不该出现(MiniMax 不认)
    assert "x-api-key" not in {k.lower() for k in sent.headers.keys()}


# -------- AC3:配置 schema 拒非法 auth_header(白名单限定)--------
def test_ac3_auth_header_whitelist():
    # Literal 白名单, pydantic 应当拒掉 "cookie" 之类
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        ProviderConfig(
            name="x", base_url="https://x", api_key="k", auth="api-key",
            auth_header="cookie",  # type: ignore[arg-type]
            models=[],
        )


# -------- AC4:URL 拼装(base_url 末尾不带斜杠也对)--------
@pytest.mark.asyncio
@respx.mock
async def test_ac4_url_strips_trailing_slash():
    route = respx.post("https://api.minimaxi.com/v1/messages").mock(
        return_value=httpx.Response(200, text="")
    )
    adapter = AnthropicAdapter()
    # base_url 末尾带斜杠, 不应拼成 //v1/messages
    prov = _prov(auth_header="Authorization", base_url="https://api.minimaxi.com/")
    try:
        async for _ in adapter.complete(
            [{"role": "user", "content": "hi"}], [], _model(), prov,
        ):
            pass
    except Exception:
        pass
    assert route.called


# -------- AC5:自定义 messages_path (MiniMax 兼容端点用 /anthropic/v1/messages)--------
@pytest.mark.asyncio
@respx.mock
async def test_ac5_custom_messages_path():
    """MiniMax 兼容端点路径是 /anthropic/v1/messages, 而非原生 /v1/messages。"""
    route = respx.post("https://api.minimaxi.com/anthropic/v1/messages").mock(
        return_value=httpx.Response(200, text="")
    )
    # 默认 base_url + /v1/messages 应 404
    respx.post("https://api.minimaxi.com/v1/messages").mock(
        return_value=httpx.Response(404, text="not found")
    )
    adapter = AnthropicAdapter()
    prov = ProviderConfig(
        name="MiniMax", base_url="https://api.minimaxi.com",
        api_key="sk-cp-fake", auth="api-key", auth_header="Authorization",
        messages_path="/anthropic/v1/messages",  # ← 关键
        models=[],
    )
    try:
        async for _ in adapter.complete(
            [{"role": "user", "content": "hi"}], [], _model(), prov,
        ):
            pass
    except Exception:
        pass
    assert route.called


# -------- AC6:默认 messages_path = /v1/messages(原生 Anthropic 习惯不破坏)--------
def test_ac6_default_messages_path():
    prov = ProviderConfig(name="x", base_url="https://x", models=[])
    assert prov.messages_path == "/v1/messages"


# -------- AC7:thinking block → ThinkingDelta 事件(M3 reasoning model)--------
def test_ac7_thinking_block_yields_thinking_delta():
    """M3 等 reasoning model 会发 thinking block(Anthropic native 协议);
    adapter 必须 yield ThinkingDelta 事件,executor 才不会"卡在 thinking 静默"。

    单元测试不走 HTTP,直接调 _normalize 验事件形态。
    """
    from karvyloop.gateway.providers.anthropic import AnthropicAdapter
    from karvyloop.gateway.events import ThinkingDelta
    adapter = AnthropicAdapter()
    chunks = [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "用户想知道项目名"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": ",README 里有"}},
        {"type": "content_block_stop", "index": 0},
    ]
    events = []
    for c in chunks:
        for ev in adapter._normalize(c, cur_tool=None):
            events.append(ev)
    td = [e for e in events if isinstance(e, ThinkingDelta)]
    # 起始空 thinking + 2 个 delta = 3
    assert len(td) == 3
    assert td[0].text == ""
    assert td[1].text == "用户想知道项目名"
    assert td[2].text == ",README 里有"


# -------- AC8:默认(全 toggle off)→ 已知 chunk 走完后 stderr 完全静默 --------
def test_ac8_default_mode_writes_nothing_to_stderr(capsys):
    """**production-safe 锁**:所有 KARVYLOOP_*_DEBUG 默认关时,_normalize 走**合法
    已知形态**的 chunk(lifecycle / thinking / text / tool_use + 配套 input_json_delta)
    都不能打 stderr。

    故意**不**测"未知 chunk"路径:那个走 QUIET=off 的设计性警告(让用户知道
    provider 协议有变),AC9 单测其 QUIET 开关。

    配套:karvyloop/gateway/providers/anthropic.py 文件 docstring 列出 4 个 toggle。
    """
    import os
    from karvyloop.gateway.providers.anthropic import AnthropicAdapter

    # 强制所有 toggle 关(测试进程可能从外面继承)
    for k in ("KARVYLOOP_ADAPTER_DEBUG", "KARVYLOOP_ADAPTER_DEBUG_RAW",
              "KARVYLOOP_ADAPTER_QUIET", "KARVYLOOP_EXECUTOR_DEBUG"):
        os.environ.pop(k, None)

    adapter = AnthropicAdapter()
    cur_tool: dict | None = None
    # 1) 先建起 thinking / text / tool_use 块
    blocks = [
        {"type": "message_start", "message": {"id": "m1", "type": "message",
            "role": "assistant", "content": [], "model": "x",
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}}},
        {"type": "ping"},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "thinking"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "text_delta", "text": "hello"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "tool_use", "id": "u1", "name": "t", "input": {}}},
    ]
    for c in blocks:
        for ev in adapter._normalize(c, cur_tool):
            if ev.__class__.__name__ == "_ToolState":
                cur_tool = ev.value
    # 2) cur_tool 已建,后面 input_json_delta / stop 走合法路径
    for c in [
        {"type": "content_block_delta", "index": 2,
         "delta": {"type": "input_json_delta", "partial_json": "{}"}},
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta",
         "delta": {"stop_reason": "end_turn"},
         "usage": {"input_tokens": 1, "output_tokens": 1}},
        {"type": "message_stop"},
    ]:
        list(adapter._normalize(c, cur_tool))

    captured = capsys.readouterr()
    assert captured.err == "", (
        f"默认 mode 走合法 chunk 必须 stderr 静默,实际输出:\n{captured.err}\n"
        f"(如需加 print,记得用 KARVYLOOP_ADAPTER_DEBUG / QUIET 等 env 守卫)"
    )


# -------- AC9:QUIET=1 → 未知 chunk 不打 stderr --------
def test_ac9_quiet_suppresses_unknown_chunk_warnings(capsys, monkeypatch):
    """QUIET=1 → 未知 SSE chunk 类型的"协议有变"警告**显式**关掉。

    配套 AC8:默认 QUIET=off 时**故意**打(让用户知道协议有变),
    确认协议变更 OK 后开 QUIET 屏蔽。
    """
    monkeypatch.setenv("KARVYLOOP_ADAPTER_QUIET", "1")
    monkeypatch.delenv("KARVYLOOP_ADAPTER_DEBUG", raising=False)
    monkeypatch.delenv("KARVYLOOP_ADAPTER_DEBUG_RAW", raising=False)

    from karvyloop.gateway.providers.anthropic import AnthropicAdapter
    adapter = AnthropicAdapter()
    list(adapter._normalize({"type": "totally_made_up_chunk"}, cur_tool=None))
    list(adapter._normalize({"type": "content_block_delta", "index": 0,
                             "delta": {"type": "weird_delta"}}, cur_tool=None))

    captured = capsys.readouterr()
    assert captured.err == "", (
        f"QUIET=1 时未知 chunk 警告必须关,实际输出:\n{captured.err}"
    )
