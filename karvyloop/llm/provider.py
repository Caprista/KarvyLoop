"""LLM Provider 抽象层(llm/provider.py)。

M3+ 批 8.5 重构:协议-供应商两层解耦。

设计蓝本(参照业界成熟 agent 的做法,clean-room 改写):
  - ProviderProfile dataclass
  - 协议字符串作 join key
  - 借:协议字符串 + Map registry + per-vendor profile
  - 不借:filesystem dynamic discovery / OAuth device-code / multi-account store

本文件**不**包含 HTTP/SSE 实现 —— 委托给:
  - karvyloop.gateway.providers.anthropic.AnthropicAdapter(anthropic-messages 协议)
  - P1 排队:karvyloop.gateway.providers.openai_completions.OpenAICompletionsAdapter

本文件**不**包含 wire-format 序列化 —— 委托给:
  - karvyloop.llm.transports.anthropic_messages.AnthropicMessagesTransport
  - karvyloop.llm.transports.openai_completions.OpenAICompletionsTransport
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
from typing import Iterator, Optional, Protocol, runtime_checkable

from .config import LLMConfig, ProviderConfig
from .profile import (
    API_MODE_ANTHROPIC_MESSAGES,
    API_MODE_OPENAI_COMPLETIONS,
    ProviderProfile,
)


# ---------- 数据契约 ----------

@dataclasses.dataclass(frozen=True)
class Message:
    """一条消息。"""
    role: str                       # "user" / "assistant" / "system" / "tool"
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclasses.dataclass(frozen=True)
class Tool:
    """一个工具定义(供 function calling)。"""
    name: str
    description: str = ""
    parameters: dict = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        # OpenAI / Anthropic 通用:tool 必包 {type: function, function: {name, description, parameters}}
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclasses.dataclass(frozen=True)
class ChatRequest:
    """统一 ChatRequest(跨 provider)。"""
    model: str
    messages: list[Message]
    tools: list[Tool] = dataclasses.field(default_factory=list)
    system: str = ""
    max_tokens: int = 1024
    stream: bool = False


@dataclasses.dataclass(frozen=True)
class ChatResponse:
    """统一 ChatResponse(跨 provider,同步路径)。"""
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = "end_turn"
    usage: Optional[dict] = None  # raw usage dict(transport 可能返更细字段)


@dataclasses.dataclass(frozen=True)
class ChatEvent:
    """统一 ChatEvent(流式路径)。"""
    kind: str                       # "text_delta" / "done" / "error" / "tool_use"
    text: str = ""
    error: str = ""
    tool_name: str = ""


# ---------- Provider 协议 ----------

@runtime_checkable
class LLMProvider(Protocol):
    """统一 LLM provider 接口(不暴露 HTTP 细节)。"""
    type: str
    name: str

    def serialize_request(self, request: ChatRequest) -> dict:
        """把 ChatRequest 转成 provider 特定 body(**只**供 shape test 用,不下发)。"""
        ...

    def chat(self, request: ChatRequest) -> ChatResponse:
        """同步调用(MockProvider 实际走同步;真实 provider 走异步但这里包成同步壳)。"""
        ...

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        """流式调用(MockProvider 实际走同步迭代)。"""
        ...


# ---------- 真实 provider(委托给 transport + gateway)----------

class AnthropicProvider:
    """Anthropic provider:委托给 AnthropicMessagesTransport(再委托给 gateway.AnthropicAdapter)。"""

    type = "anthropic"
    name = "anthropic"
    api_mode = API_MODE_ANTHROPIC_MESSAGES

    def __init__(self, config: ProviderConfig):
        self.config = config

    def serialize_request(self, request: ChatRequest) -> dict:
        """Anthropic body 形状(委托给 transport)。

        {
            "model": "<id>",
            "max_tokens": int,
            "messages": [{"role": ..., "content": ...}],
            "system": "...",           # 可选
            "tools": [...],            # 可选
            "stream": bool,            # 调用方补
        }
        """
        from .registry import ensure_loaded, get as get_profile
        from .transports import require_transport

        ensure_loaded()
        profile = get_profile(self.name) or self._fallback_profile()
        body = require_transport(self.api_mode).serialize_request(request, profile)
        if request.stream:
            body["stream"] = True
        return body

    def chat(self, request: ChatRequest) -> ChatResponse:
        """同步壳:用 asyncio 跑 async complete()。"""
        return asyncio.run(self._achat(request))

    async def _achat(self, request: ChatRequest) -> ChatResponse:
        from .registry import ensure_loaded, get as get_profile
        from .transports import require_transport

        ensure_loaded()
        profile = get_profile(self.name) or self._fallback_profile()
        transport = require_transport(self.api_mode)
        # 修问题 2(yaml 优先):把 config.api_key(yaml 写)透传给 transport
        response = await transport.achat(request, profile, api_key=self.config.api_key or "")
        # ChatResponse(input_tokens/output_tokens/stop_reason 兼容)
        usage = response.usage or {}
        inp = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
        out = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
        # 拍 9.3a:记 token 账本(source 从 contextvar;无账本 no-op)
        from .token_ledger import record as _rec
        _rec(model=response.model or request.model, input=inp, output=out,
             cache_read=usage.get("cache_read_input_tokens", 0),
             cache_write=usage.get("cache_creation_input_tokens", 0))
        return ChatResponse(
            content=response.content, model=response.model,
            input_tokens=inp, output_tokens=out, stop_reason="end_turn",
        )

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        """流式:同步壳包 async generator。"""
        async def _collect():
            from .registry import ensure_loaded, get as get_profile
            from .transports import require_transport
            from karvyloop.gateway.events import TextDelta, Done, ErrorEvent, ToolUseStart

            ensure_loaded()
            profile = get_profile(self.name) or self._fallback_profile()
            transport = require_transport(self.api_mode)
            # 修问题 2(yaml 优先)
            agen = transport.astream(request, profile, api_key=self.config.api_key or "")
            async for ev in agen:
                if isinstance(ev, TextDelta):
                    yield ChatEvent(kind="text_delta", text=ev.text)
                elif isinstance(ev, Done):
                    yield ChatEvent(kind="done")
                    return
                elif isinstance(ev, ErrorEvent):
                    yield ChatEvent(kind="error", error=ev.error)
                    return
                elif isinstance(ev, ToolUseStart):
                    yield ChatEvent(kind="tool_use", tool_name=getattr(ev, "name", ""))
                # ToolUseStop / Usage / ThinkingDelta 暂不暴露,保持 ChatEvent 简单

        agen = _collect()
        while True:
            try:
                yield asyncio.run(anext(agen))
            except StopAsyncIteration:
                return
            except Exception as e:
                yield ChatEvent(kind="error", error=str(e))
                return

    def _fallback_profile(self) -> ProviderProfile:
        """registry 未命中时的硬编码兜底(测试 / config-only 场景)。"""
        return ProviderProfile(
            name="anthropic",
            api_mode=API_MODE_ANTHROPIC_MESSAGES,
            base_url=self.config.base_url or "https://api.anthropic.com",
            auth_type="api-key-header",
            auth_header="x-api-key",
            env_vars=("ANTHROPIC_API_KEY",),
        )


class MiniMaxProvider:
    """MiniMax provider:走 anthropic-messages 兼容端点(共用 AnthropicMessagesTransport,仅 profile 不同)。"""

    type = "minimax"
    name = "minimax"
    api_mode = API_MODE_ANTHROPIC_MESSAGES

    def __init__(self, config: ProviderConfig):
        self.config = config

    def serialize_request(self, request: ChatRequest) -> dict:
        """MiniMax body 形状(走 anthropic-messages 兼容,共用 transport)。"""
        from .registry import ensure_loaded, get as get_profile
        from .transports import require_transport

        ensure_loaded()
        profile = get_profile(self.name) or self._fallback_profile()
        body = require_transport(self.api_mode).serialize_request(request, profile)
        if request.stream:
            body["stream"] = True
        return body

    def chat(self, request: ChatRequest) -> ChatResponse:
        return asyncio.run(self._achat(request))

    async def _achat(self, request: ChatRequest) -> ChatResponse:
        from .registry import ensure_loaded, get as get_profile
        from .transports import require_transport

        ensure_loaded()
        profile = get_profile(self.name) or self._fallback_profile()
        transport = require_transport(self.api_mode)
        # 修问题 2(yaml 优先):把 config.api_key(yaml 写)透传给 transport
        response = await transport.achat(request, profile, api_key=self.config.api_key or "")
        usage = response.usage or {}
        inp = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
        out = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
        from .token_ledger import record as _rec
        _rec(model=response.model or request.model, input=inp, output=out,
             cache_read=usage.get("cache_read_input_tokens", 0),
             cache_write=usage.get("cache_creation_input_tokens", 0))
        return ChatResponse(
            content=response.content, model=response.model,
            input_tokens=inp, output_tokens=out, stop_reason="end_turn",
        )

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        """流式:同 AnthropicProvider(共用 transport,只是 profile 不同)。"""
        async def _collect():
            from .registry import ensure_loaded, get as get_profile
            from .transports import require_transport
            from karvyloop.gateway.events import TextDelta, Done, ErrorEvent, ToolUseStart

            ensure_loaded()
            profile = get_profile(self.name) or self._fallback_profile()
            transport = require_transport(self.api_mode)
            # 修问题 2(yaml 优先)
            agen = transport.astream(request, profile, api_key=self.config.api_key or "")
            async for ev in agen:
                if isinstance(ev, TextDelta):
                    yield ChatEvent(kind="text_delta", text=ev.text)
                elif isinstance(ev, Done):
                    yield ChatEvent(kind="done")
                    return
                elif isinstance(ev, ErrorEvent):
                    yield ChatEvent(kind="error", error=ev.error)
                    return
                elif isinstance(ev, ToolUseStart):
                    yield ChatEvent(kind="tool_use", tool_name=getattr(ev, "name", ""))

        agen = _collect()
        while True:
            try:
                yield asyncio.run(anext(agen))
            except StopAsyncIteration:
                return
            except Exception as e:
                yield ChatEvent(kind="error", error=str(e))
                return

    def _fallback_profile(self) -> ProviderProfile:
        return ProviderProfile(
            name="minimax",
            api_mode=API_MODE_ANTHROPIC_MESSAGES,
            base_url=self.config.base_url or "https://api.MiniMax.chat/anthropic",
            auth_type="bearer",
            auth_header="Authorization",
            env_vars=("MiniMax_API_KEY",),
        )


class _OpenAICompletionsProvider:
    """openai-completions 协议的 thin shim(覆盖 14 个 openai-compat vendor)。

    实现状态(0.1.0):
      - serialize_request 完整(走 OpenAICompletionsTransport)
      - chat/stream 走 stub raise NotImplementedError(P1 排队:补 gateway OpenAICompletionsAdapter)
      - **不**继承 AnthropicProvider(协议不同)

    0.1.0 期间,create_provider("openai"/"deepseek"/...) 返此类的实例;
    调用 chat() 会 raise NotImplementedError,wizard 能看到 vendor,真发请求 P1 排队。
    """

    type = "openai-completions"
    name = ""  # 由 create_provider 注入

    def __init__(self, config: ProviderConfig, profile: ProviderProfile):
        self.config = config
        self.profile = profile
        self.name = profile.name
        self.api_mode = profile.api_mode

    def serialize_request(self, request: ChatRequest) -> dict:
        from .transports import require_transport

        body = require_transport(self.api_mode).serialize_request(request, self.profile)
        if request.stream:
            body["stream"] = True
        return body

    def chat(self, request: ChatRequest) -> ChatResponse:
        from .transports import require_transport

        # 0.1.0 走 stub raise
        try:
            return asyncio.run(require_transport(self.api_mode).achat(request, self.profile))
        except NotImplementedError as e:
            raise NotImplementedError(
                f"openai-completions 真 HTTP 路径 P1 排队(profile='{self.profile.name}')。"
                f"serialize_request 已可用(wizard 验证),真发请求请补 gateway OpenAICompletionsAdapter。"
            ) from e

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        from .transports import require_transport

        async def _collect():
            agen = require_transport(self.api_mode).astream(request, self.profile)
            async for _ev in agen:
                yield ChatEvent(kind="text_delta", text="")

        agen = _collect()
        try:
            # 立即触发 NotImplementedError(0.1.0 stub)
            first = asyncio.run(anext(agen))
            yield first
        except NotImplementedError as e:
            yield ChatEvent(kind="error", error=str(e))
            return
        except StopAsyncIteration:
            return
        while True:
            try:
                yield asyncio.run(anext(agen))
            except StopAsyncIteration:
                return


# ---------- Mock provider(测试用,0 LLM,0 网络)----------

class MockProvider:
    """Mock provider:确定性、无网络、0 LLM,用于单测 + 离线开发。"""

    type = "mock"
    name = "mock"

    def __init__(self, config: Optional[ProviderConfig] = None):
        self.config = config or ProviderConfig(type="mock", default_model="mock-1")

    def serialize_request(self, request: ChatRequest) -> dict:
        """Mock body 形状(只给 shape test 看,**不**下发):
          {
            "model": "<id>",
            "max_tokens": int,
            "messages": [{"role": ..., "content": ...}],
            "system": "...",
            "mock": true,
          }
        """
        body: dict = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [m.to_dict() for m in request.messages],
            "mock": True,
        }
        if request.system:
            body["system"] = request.system
        if request.tools:
            body["tools"] = [t.to_dict() for t in request.tools]
        return body

    def chat(self, request: ChatRequest) -> ChatResponse:
        last_user = next(
            (m.content for m in reversed(request.messages) if m.role == "user"),
            "",
        )
        # 确定性响应:echo 最后一个 user message
        return ChatResponse(
            content=f"[mock echo] {last_user}",
            model=request.model,
            input_tokens=len(last_user),
            output_tokens=len(last_user) + 8,
            stop_reason="end_turn",
        )

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        last_user = next(
            (m.content for m in reversed(request.messages) if m.role == "user"),
            "",
        )
        for word in ("[mock ", "echo] ", last_user):
            yield ChatEvent(kind="text_delta", text=word)
        yield ChatEvent(kind="done")


# ---------- 工厂 ----------

def create_provider(name: str, config: LLMConfig) -> LLMProvider:
    """根据 name 从 LLMConfig 创建 provider(M3+ 批 8.5 走 registry)。

    流程:
      1. 确保 profiles 已自动加载
      2. config.providers[name] 必存在
      3. registry.get(name) 查 profile(找不到则按 config.type 兜底)
      4. 按 profile.api_mode 决定实例化哪个类:
           - anthropic-messages → AnthropicProvider(若 name=="minimax" 则 MiniMaxProvider)
           - openai-completions → _OpenAICompletionsProvider(0.1.0 stub)
           - mock → MockProvider

    Args:
        name: provider name(必须存在于 config.providers)。
        config: LLMConfig 实例(load_config 返回)。

    Returns:
        LLMProvider 实例。

    Raises:
        KeyError: name 不在 config.providers。
        ValueError: provider.type 不识别。
    """
    from .registry import ensure_loaded, get as get_profile

    ensure_loaded()

    if name not in config.providers:
        raise KeyError(f"provider '{name}' 不在 config.providers: {list(config.providers)}")

    p = config.providers[name]

    # 优先从 registry 查 profile;registry 没的(用户自配)走 config.type 兜底
    profile = get_profile(name)
    if profile is None:
        # registry 没注册 → 走 config.type 判定(用户自配 provider)
        if p.type == "anthropic":
            profile = ProviderProfile(
                name=name, api_mode=API_MODE_ANTHROPIC_MESSAGES,
                base_url=p.base_url or "https://api.anthropic.com",
                auth_type="api-key-header", auth_header="x-api-key",
                env_vars=("ANTHROPIC_API_KEY",),
            )
        elif p.type == "minimax":
            profile = ProviderProfile(
                name=name, api_mode=API_MODE_ANTHROPIC_MESSAGES,
                base_url=p.base_url or "https://api.MiniMax.chat/anthropic",
                auth_type="bearer", auth_header="Authorization",
                env_vars=("MiniMax_API_KEY",),
            )
        elif p.type == "openai-completions":
            profile = ProviderProfile(
                name=name, api_mode=API_MODE_OPENAI_COMPLETIONS,
                base_url=p.base_url, auth_type="bearer", auth_header="Authorization",
                env_vars=(f"{name.upper()}_API_KEY",),
            )
        elif p.type == "mock":
            profile = ProviderProfile(
                name=name, api_mode="mock",
                base_url="", auth_type="none", auth_header="",
                env_vars=(),
            )
        else:
            raise ValueError(
                f"未知 provider type: '{p.type}'(name='{name}')。"
                f"已支持: anthropic / minimax / openai-completions / mock"
            )

    # 按 api_mode 实例化
    if profile.api_mode == API_MODE_ANTHROPIC_MESSAGES:
        # minimax 走 anthropic-messages 但保留独立类(测试 isinstance 兼容)
        if name == "minimax":
            return MiniMaxProvider(p)
        return AnthropicProvider(p)
    if profile.api_mode == API_MODE_OPENAI_COMPLETIONS:
        return _OpenAICompletionsProvider(p, profile)
    if profile.api_mode == "mock" or p.type == "mock":
        return MockProvider(p)

    raise ValueError(
        f"无法为 provider '{name}' 选 protocol adapter。"
        f"api_mode='{profile.api_mode}' 不在 KarvyLoop 0.1.0 支持列表。"
    )


__all__ = [
    "Message",
    "Tool",
    "ChatRequest",
    "ChatResponse",
    "ChatEvent",
    "LLMProvider",
    "AnthropicProvider",
    "MiniMaxProvider",
    "MockProvider",
    "create_provider",
]
