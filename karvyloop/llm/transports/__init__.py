"""Wire-protocol transports (llm/transports/__init__.py).

M3+ 批 8.5 引入：协议 join key 上的适配器层。

设计蓝本（openclaw + hermes 一致的两层解耦）：
  - openclaw packages/llm-core/src/types.ts 的 KnownApi
  - openclaw packages/llm-runtime/src/api-registry.ts: Map<api, ApiProvider>
  - hermes agent/transports/__init__.py: _REGISTRY[api_mode] = transport_cls

**借**：
  ✅ 协议字符串作 join key（api_mode 字段）
  ✅ Map registry（_TRANSPORTS: dict[api_mode, Transport]）
  ✅ per-protocol 文件（transports/anthropic_messages.py + openai_completions.py）

**不借**：
  ❌ lazy dynamic import + SDK 缓存（openclaw 那种，0.1.0 不需要）
  ❌ sourceId 作用域（plugin 热卸载）
  ❌ NormalizedResponse / ToolCall 中间表示（KarvyLoop gateway 已有 Event 抽象）

KarvyLoop 0.1.0 实现状态：
  - anthropic-messages: 完整（AnthropicAdapter 在 karvyloop.gateway.providers.anthropic 已有 HTTP/SSE）
  - openai-completions: serialize_request 完整；achat/astream 走 stub（raise NotImplementedError）
    → P1 排队：补 OpenAICompletionsAdapter 在 karvyloop.gateway.providers.openai_completions
"""
from __future__ import annotations

from typing import Dict, Optional, Type

from ..profile import ProviderProfile


# ---- Transport 协议（duck-type ABC）----

class Transport:
    """wire-protocol 适配器基类（duck-type；不强制继承）。

    每个 transport 负责一个 api_mode（如 'anthropic-messages'），包含：
      - serialize_request: 把 karvyloop.llm.ChatRequest 转成该协议的 body dict
                            （**只**供 shape test / 调试用，**不**下发）
      - achat: 异步同步返回 ChatResponse（一个回合）
      - astream: 异步流式返回 karvyloop.gateway.Event 序列
    """
    api_mode: str  # 子类必须填（如 "anthropic-messages" / "openai-completions"）

    def serialize_request(self, request, profile: ProviderProfile) -> dict:
        raise NotImplementedError(f"{type(self).__name__}.serialize_request 尚未实现")

    async def achat(self, request, profile: ProviderProfile):
        raise NotImplementedError(
            f"{type(self).__name__}.achat 尚未实现。"
            f"api_mode='{self.api_mode}' profile='{profile.name}' 的真 HTTP 路径 P1 排队。"
        )

    def astream(self, request, profile: ProviderProfile):
        raise NotImplementedError(
            f"{type(self).__name__}.astream 尚未实现。"
            f"api_mode='{self.api_mode}' profile='{profile.name}' 的真流式路径 P1 排队。"
        )


# ---- Transport Registry ----

_TRANSPORTS: Dict[str, Transport] = {}


def register_transport(transport: Transport) -> None:
    """注册一个 transport 实例（按 transport.api_mode 去重）。"""
    if not transport.api_mode:
        raise ValueError(f"{type(transport).__name__}.api_mode 不能为空")
    _TRANSPORTS[transport.api_mode] = transport


def get_transport(api_mode: str) -> Optional[Transport]:
    """按 api_mode 查 transport（找不到返 None）。"""
    return _TRANSPORTS.get(api_mode)


def require_transport(api_mode: str) -> Transport:
    """按 api_mode 查 transport（找不到 raise KeyError）。"""
    t = get_transport(api_mode)
    if t is None:
        raise KeyError(
            f"api_mode '{api_mode}' 没有注册的 transport。"
            f"已注册: {sorted(_TRANSPORTS.keys())}"
        )
    return t


def clear() -> None:
    """测试用 —— 清空 transport registry。"""
    _TRANSPORTS.clear()


# ---- 自动加载（启动时调一次） ----

_AUTO_LOADED = False

def ensure_loaded() -> None:
    """首次调用时 import anthropic_messages + openai_completions 触发自注册。"""
    global _AUTO_LOADED
    if _AUTO_LOADED:
        return
    from . import anthropic_messages  # noqa: F401
    from . import openai_completions  # noqa: F401
    _AUTO_LOADED = True


__all__ = [
    "Transport",
    "register_transport",
    "get_transport",
    "require_transport",
    "clear",
    "ensure_loaded",
]
