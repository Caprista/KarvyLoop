"""LLM 接入(llm/__init__.py)。

M3.0 批 1 拍 1:从 `~/.karvyloop/config.yaml` 读 LLM 配置,动态切换 provider。
包装 karvyloop.gateway 的 adapter(**不**重新发明 HTTP/SSE 解析)。

M3+ 批 8.5 改造:协议-供应商两层解耦(参考 hermes ProviderProfile + openclaw KnownApi 模式)。
- `profile` 是元数据(name / api_mode / base_url / env_vars / ...)
- `transports` 是 wire-format 适配器(anthropic-messages / openai-completions)
- `registry` 是注册表(16 个 vendor + aliases)
- `provider.AnthropicProvider` / `MiniMaxProvider` / `MockProvider` 变 thin shim,
  委派给 transports[<api_mode>]
- `_OpenAICompletionsProvider` 是新加的 stub(serialize_request 完整,chat/stream P1 排队)

设计稿:docs/21 §3 + CONTEXT/03-feature-knowledge-base.md §1。
"""
from .config import (
    LLMConfig,
    ProviderConfig,
    load_config,
    ConfigNotFoundError,
    MissingDefaultError,
    MissingProvidersError,
    MissingApiKeyError,
    RealKeyInRepoError,
)
from .profile import (
    API_MODE_ANTHROPIC_MESSAGES,
    API_MODE_OPENAI_COMPLETIONS,
    SUPPORTED_API_MODES,
    AUTH_TYPE_API_KEY_HEADER,
    AUTH_TYPE_BEARER,
    AUTH_TYPE_NONE,
    ProviderProfile,
)
from .registry import (
    register as register_profile,
    get as get_profile,
    require as require_profile,
    list_profiles,
    list_names as list_profile_names,
    resolve_api_key,
    ensure_loaded as ensure_profiles_loaded,
)
from .transports import (
    Transport,
    register_transport,
    get_transport,
    require_transport,
    ensure_loaded as ensure_transports_loaded,
)
from .provider import (
    LLMProvider,
    AnthropicProvider,
    MiniMaxProvider,
    MockProvider,
    create_provider,
    ChatRequest,
    ChatResponse,
    ChatEvent,
    Message,
    Tool,
)

__all__ = [
    # config
    "LLMConfig",
    "ProviderConfig",
    "load_config",
    "ConfigNotFoundError",
    "MissingDefaultError",
    "MissingProvidersError",
    "MissingApiKeyError",
    "RealKeyInRepoError",
    # profile (M3+ 批 8.5)
    "API_MODE_ANTHROPIC_MESSAGES",
    "API_MODE_OPENAI_COMPLETIONS",
    "SUPPORTED_API_MODES",
    "AUTH_TYPE_API_KEY_HEADER",
    "AUTH_TYPE_BEARER",
    "AUTH_TYPE_NONE",
    "ProviderProfile",
    "register_profile",
    "get_profile",
    "require_profile",
    "list_profiles",
    "list_profile_names",
    "resolve_api_key",
    "ensure_profiles_loaded",
    # transports (M3+ 批 8.5)
    "Transport",
    "register_transport",
    "get_transport",
    "require_transport",
    "ensure_transports_loaded",
    # provider
    "LLMProvider",
    "AnthropicProvider",
    "MiniMaxProvider",
    "MockProvider",
    "create_provider",
    "ChatRequest",
    "ChatResponse",
    "ChatEvent",
    "Message",
    "Tool",
]


# ---- 模块导入时自动 ensure_loaded(M3+ 批 8.5)----
# Why: wizard / create_provider 任意路径 import karvyloop.llm 就能拿到 16 个 vendor,
#      无需每个调用方先 ensure_loaded()。
ensure_profiles_loaded()
ensure_transports_loaded()

