"""openai-completions transport (transports/openai_completions.py)。

M3+ 批 8.5 引入。

实现状态：serialize_request 完整；achat/astream 走 stub（NotImplementedError，P1 排队）。
覆盖 vendor：openai / ollama / deepseek / zhipu / moonshot / kimi / qwen / gemini / groq /
              mistral / xai / openrouter / together / fireworks 等 14 个 openai-compat 供应商。

Why serialize_request 完整但 achat 走 stub：
  - wizard 只需要 serialize_request 锁 wire format（不真发请求）
  - 16 个 vendor profile 注册到 registry（wizard 可见）
  - 实际 HTTP/SSE 实现 P1 排队：补 karvyloop.gateway.providers.openai_completions.OpenAICompletionsAdapter
  - 0.1.0 期间如果用户选 openai-completions 跑 karvyloop run，会 raise NotImplementedError 提示
"""
from __future__ import annotations

import os
from typing import AsyncIterator

from ...gateway.events import Event
from ..profile import (
    API_MODE_OPENAI_COMPLETIONS,
    ProviderProfile,
)
from ..provider import ChatRequest, ChatResponse
from . import Transport, register_transport


class OpenAICompletionsTransport(Transport):
    """openai-completions wire-protocol 适配器（OpenAI 原生 + 14 个 openai-compat 供应商）。"""

    api_mode = API_MODE_OPENAI_COMPLETIONS

    def serialize_request(self, request: ChatRequest, profile: ProviderProfile) -> dict:
        """OpenAI Chat Completions body 形状。

        {
            "model": "<id>",
            "messages": [{"role": "system|user|assistant|tool", "content": "..."}],
            "tools": [...],                # 可选
            "tool_choice": "...",          # 可选
            "temperature": 1.0,            # 可选
            "max_tokens": int,             # 可选
            "top_p": 1.0,                  # 可选
            "stream": bool,                # 调用方补
        }
        """
        body: dict = {
            "model": request.model,
            "messages": [m.to_dict() for m in request.messages],
        }
        if request.tools:
            body["tools"] = [t.to_dict() for t in request.tools]
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens
        return body

    def _resolve_api_key(self, profile: ProviderProfile) -> str:
        """env_vars 兜底链（hermes 模式）。"""
        for var in profile.env_vars:
            v = os.environ.get(var, "")
            if v:
                return v
        # 本地（auth_type=none / 空 env_vars）返 dummy
        if profile.auth_type == "none":
            return "dummy"
        return ""

    async def achat(self, request: ChatRequest, profile: ProviderProfile) -> ChatResponse:
        raise NotImplementedError(
            f"openai-completions 真 HTTP/SSE 实现 P1 排队。"
            f"profile='{profile.name}' base_url='{profile.base_url}'。"
            f"目前 serialize_request 已锁 wire format，可被 wizard 验证。"
            f"如需真发请求，请先在 karvyloop/gateway/providers/openai_completions.py 补 OpenAICompletionsAdapter。"
        )

    def astream(self, request: ChatRequest, profile: ProviderProfile) -> AsyncIterator[Event]:
        raise NotImplementedError(
            f"openai-completions 真流式 P1 排队（profile='{profile.name}'）。"
        )


# ---- 自注册 ----
register_transport(OpenAICompletionsTransport())
