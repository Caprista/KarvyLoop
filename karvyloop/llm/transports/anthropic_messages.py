"""anthropic-messages transport（transports/anthropic_messages.py）。

M3+ 批 8.5 引入。

实现状态：完整（serialize_request + achat + astream 都委派给 karvyloop.gateway.providers.anthropic.AnthropicAdapter）。

Why 是 thin wrapper 而不是直接用 AnthropicAdapter：
  - 协议抽象（业界一致）：transports 字典是按 api_mode 查的
  - 未来切 vendor（Anthropic 原生 → minimax 兼容）只换 profile.base_url，
    transport 代码不动
  - 现有 AnthropicProvider / MiniMaxProvider 都用此 transport（共享同一份 HTTP 代码）
"""
from __future__ import annotations

import os
from typing import AsyncIterator, Optional

from ...gateway.events import Event, TextDelta, Done, Usage, ErrorEvent
from ...gateway.providers.anthropic import AnthropicAdapter
from ...gateway.system import SystemPrompt
from ...schemas import ModelDefinition, ProviderConfig as GwProviderConfig
from ..profile import (
    API_MODE_ANTHROPIC_MESSAGES,
    AUTH_TYPE_API_KEY_HEADER,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from ..provider import ChatRequest, ChatResponse
from . import Transport, register_transport


class AnthropicMessagesTransport(Transport):
    """anthropic-messages wire-protocol 适配器（Anthropic 原生 + minimax 兼容端点）。"""

    api_mode = API_MODE_ANTHROPIC_MESSAGES

    def serialize_request(self, request: ChatRequest, profile: ProviderProfile) -> dict:
        """Anthropic body 形状（参考 karvyloop/llm/provider.py:91-115）。

        {
            "model": "<id>",
            "max_tokens": int,
            "messages": [{"role": ..., "content": ...}],
            "system": "...",           # 可选
            "tools": [...],            # 可选
            "stream": bool,            # 调用方补
        }
        """
        body: dict = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [m.to_dict() for m in request.messages],
        }
        if request.system:
            body["system"] = request.system
        if request.tools:
            body["tools"] = [t.to_dict() for t in request.tools]
        return body

    def _build_gw_provider(self, profile: ProviderProfile, *, api_key: str = "") -> GwProviderConfig:
        """把 karvyloop.llm.profile.ProviderProfile 转成 gateway 的 GwProviderConfig。

        auth_type 处理：
          - api-key-header (Anthropic 原生): auth_header="x-api-key"
          - bearer (minimax 兼容): auth_header="Authorization"
          - none (本地): 不会到这里（minimax/anthropic 都需要 key）

        api_key 解析顺序(M3+ 批 8.5 修问题 2):
          1. 调用方传入的 api_key(从 LLMConfig 读 yaml 的 config.providers[name].api_key)
          2. env_vars 兜底链(os.environ.get 按顺序遍历)
          3. 空字符串(用户没配 / 没 export,后面会 401)
        """
        auth_header = profile.auth_header
        if profile.auth_type == AUTH_TYPE_BEARER:
            auth_header = auth_header or "Authorization"
        elif profile.auth_type == AUTH_TYPE_API_KEY_HEADER:
            auth_header = auth_header or "x-api-key"
        # 解析 api_key:1) caller-传入(yaml) 2) env_vars 兑底链
        resolved_key = api_key or ""
        if not resolved_key:
            for var in profile.env_vars:
                v = os.environ.get(var, "")
                if v:
                    resolved_key = v
                    break

        return GwProviderConfig(
            name=profile.name,
            base_url=profile.base_url,
            api_key=resolved_key,
            auth="api-key",
            auth_header=auth_header,
            messages_path="/v1/messages",
            models=[],
        )

    async def achat(self, request: ChatRequest, profile: ProviderProfile, *, api_key: str = "") -> ChatResponse:
        """同步回合（async 接口但调用方一般 asyncio.run）。

        api_key: 调用方从 LLMConfig 读 yaml 的 config.providers[name].api_key 传入;
                 为空时 transport 走 env_vars 兑底链。
        """
        gw_provider = self._build_gw_provider(profile, api_key=api_key)
        model = ModelDefinition(
            id=request.model,
            name=request.model,
            api="anthropic-messages",
            context_window=0,
            max_tokens=request.max_tokens or 4096,
        )
        system = SystemPrompt(blocks=[{"type": "text", "text": request.system}]) if request.system else None
        messages = [m.to_dict() for m in request.messages]
        tools = [t.to_dict() for t in request.tools] if request.tools else None

        adapter = AnthropicAdapter()
        text_parts: list[str] = []
        usage_data = None
        try:
            async for ev in adapter.astream(
                messages=messages,
                tools=tools,
                model=model,
                provider=gw_provider,
                system=system,
            ):
                if isinstance(ev, TextDelta):
                    text_parts.append(ev.text)
                elif isinstance(ev, Usage):
                    usage_data = {"prompt_tokens": ev.prompt_tokens, "completion_tokens": ev.completion_tokens}
                elif isinstance(ev, ErrorEvent):
                    raise RuntimeError(f"anthropic-messages chat failed: {ev.error}")
        except Exception as e:
            raise RuntimeError(f"anthropic-messages chat failed: {e}") from e

        return ChatResponse(
            content="".join(text_parts),
            model=request.model,
            usage=usage_data,
        )

    def astream(self, request: ChatRequest, profile: ProviderProfile, *, api_key: str = "") -> AsyncIterator[Event]:
        """流式：直接转给 AnthropicAdapter.astream。"""
        gw_provider = self._build_gw_provider(profile, api_key=api_key)
        model = ModelDefinition(
            id=request.model,
            name=request.model,
            api="anthropic-messages",
            context_window=0,
            max_tokens=request.max_tokens or 4096,
        )
        system = SystemPrompt(blocks=[{"type": "text", "text": request.system}]) if request.system else None
        messages = [m.to_dict() for m in request.messages]
        tools = [t.to_dict() for t in request.tools] if request.tools else None

        adapter = AnthropicAdapter()
        return adapter.astream(
            messages=messages,
            tools=tools,
            model=model,
            provider=gw_provider,
            system=system,
        )


# ---- 自注册 ----
register_transport(AnthropicMessagesTransport())
