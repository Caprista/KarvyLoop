"""Provider Registry + Transports 集成测试（test_provider_registry.py）。

M3+ 批 8.5 验证：
  - 16 个 vendor profile 全部注册到 registry
  - aliases 解析工作（glm → zhipu, grok → xai, dashscope → qwen）
  - transport 注册：anthropic-messages + openai-completions
  - serialize_request 锁 wire format
  - create_provider 走 registry
  - 向后兼容：AnthropicProvider / MiniMaxProvider / MockProvider isinstance 仍工作
  - wizard.validate_api_key 走 profile.env_vars

12 AC。
"""
from __future__ import annotations

import pytest

from karvyloop.llm import (
    LLMConfig,
    ProviderConfig,
    create_provider,
    list_profiles,
    list_profile_names,
    get_profile,
    require_profile,
    resolve_api_key,
    ensure_profiles_loaded,
    ensure_transports_loaded,
    API_MODE_ANTHROPIC_MESSAGES,
    API_MODE_OPENAI_COMPLETIONS,
)
from karvyloop.llm.profile import (
    ProviderProfile,
    AUTH_TYPE_BEARER,
    AUTH_TYPE_API_KEY_HEADER,
    AUTH_TYPE_NONE,
)
from karvyloop.llm.provider import (
    AnthropicProvider,
    MiniMaxProvider,
    MockProvider,
    ChatRequest,
    Message,
)
from karvyloop.llm.transports import (
    Transport,
    get_transport,
    require_transport,
)
from karvyloop.cli.wizard import (
    PROVIDERS,
    _list_providers,
    _refresh_providers_cache,
    validate_api_key,
)


# ---- Fixtures ----

@pytest.fixture(autouse=True)
def _ensure_loaded():
    """每个测试前确保 profiles + transports 都已加载。"""
    ensure_profiles_loaded()
    ensure_transports_loaded()
    _refresh_providers_cache()


@pytest.fixture
def fake_llm_config():
    """构造一个 LLMConfig 含 anthropic / minimax / openai 3 个 provider(测试 create_provider)。"""
    return LLMConfig(
        default="anthropic",
        providers={
            "anthropic": ProviderConfig(
                type="anthropic", api_key="sk-ant-fake-fake-fake",
                base_url="https://api.anthropic.com", default_model="anthropic/claude-sonnet-4-6",
            ),
            "minimax": ProviderConfig(
                type="minimax", api_key="fk-mini-fake-fake-fake",
                base_url="https://api.MiniMax.chat/anthropic", default_model="minimax/MiniMax-M3",
            ),
            "openai": ProviderConfig(
                type="openai-completions", api_key="sk-fake-fake-fake",
                base_url="https://api.openai.com/v1", default_model="openai/gpt-4o",
            ),
            "mock": ProviderConfig(
                type="mock", api_key="", base_url="", default_model="mock-1",
            ),
        },
    )


# ---- AC1: 16 个 vendor profile 注册 ----

def test_registry_has_17_profiles():
    """AC1: 17 个 vendor profile 全部自动注册到 registry(M3+ 批 8.5 修问题 1 加 minimax-cn 后)。"""
    names = list_profile_names()
    # 本地 + anthropic 系(2 个:global + cn) + 14 个 openai-compat = 17
    assert len(names) == 17, f"期望 17 个 vendor, 实际 {len(names)}: {sorted(names)}"

    expected = {
        "ollama",         # 本地
        "anthropic",      # anthropic 系 (海外)
        "minimax",        # anthropic 系 (海外)
        "minimax-cn",     # anthropic 系 (国内,M3+ 批 8.5 修问题 1 加)
        "openai",         # openai-compat
        "deepseek", "zhipu", "moonshot", "kimi", "qwen", "gemini",
        "groq", "mistral", "xai", "openrouter", "together", "fireworks",
    }
    assert expected.issubset(set(names)), f"缺 vendor: {expected - set(names)}"


# ---- AC2: aliases 解析 ----

def test_registry_aliases():
    """AC2: aliases 解析（glm → zhipu, grok → xai, dashscope → qwen）。"""
    assert get_profile("glm").name == "zhipu"
    assert get_profile("zai").name == "zhipu"
    assert get_profile("grok").name == "xai"
    assert get_profile("dashscope").name == "qwen"
    # 主名直接查
    assert get_profile("zhipu").name == "zhipu"
    assert get_profile("xai").name == "xai"
    assert get_profile("qwen").name == "qwen"


# ---- AC3: profile 元数据正确 ----

def test_profile_metadata_anthropic():
    """AC3: anthropic profile 元数据正确。"""
    p = require_profile("anthropic")
    assert p.api_mode == API_MODE_ANTHROPIC_MESSAGES
    assert p.base_url == "https://api.anthropic.com"
    assert p.auth_type == AUTH_TYPE_API_KEY_HEADER
    assert p.auth_header == "x-api-key"
    assert p.env_vars == ("ANTHROPIC_API_KEY",)
    # fallback_models 形如 "anthropic/claude-sonnet-4-6"(带 vendor prefix)
    assert "anthropic/claude-sonnet-4-6" in p.fallback_models


def test_profile_metadata_minimax():
    """AC3: minimax profile 元数据正确（走 anthropic-messages 兼容 + Bearer）。"""
    p = require_profile("minimax")
    assert p.api_mode == API_MODE_ANTHROPIC_MESSAGES
    assert p.base_url == "https://api.MiniMax.chat/anthropic"
    assert p.auth_type == AUTH_TYPE_BEARER
    assert p.auth_header == "Authorization"


def test_profile_metadata_ollama():
    """AC3: ollama profile 元数据正确（本地, auth_type=none）。"""
    p = require_profile("ollama")
    assert p.api_mode == API_MODE_OPENAI_COMPLETIONS
    assert p.auth_type == AUTH_TYPE_NONE
    assert p.env_vars == ()


# ---- AC4: 2 个 transport 注册 ----

def test_transports_registered():
    """AC4: 2 个 wire-protocol transport 已注册。"""
    assert get_transport(API_MODE_ANTHROPIC_MESSAGES) is not None
    assert get_transport(API_MODE_OPENAI_COMPLETIONS) is not None
    # 未注册协议返 None
    assert get_transport("unknown-protocol") is None
    # require_transport 不存在时 raise
    with pytest.raises(KeyError):
        require_transport("unknown-protocol")


# ---- AC5: anthropic-messages transport serialize_request ----

def test_anthropic_messages_serialize():
    """AC5: AnthropicMessagesTransport.serialize_request 锁 wire format。"""
    t = require_transport(API_MODE_ANTHROPIC_MESSAGES)
    p = require_profile("anthropic")
    req = ChatRequest(
        model="claude-sonnet-4-6",
        messages=[Message(role="user", content="hi")],
        max_tokens=1024,
    )
    body = t.serialize_request(req, p)
    assert body["model"] == "claude-sonnet-4-6"
    assert body["max_tokens"] == 1024
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    # system/tools 可选,默认不在 body
    assert "system" not in body
    assert "tools" not in body


# ---- AC6: openai-completions transport serialize_request ----

def test_openai_completions_serialize():
    """AC6: OpenAICompletionsTransport.serialize_request 锁 wire format。"""
    t = require_transport(API_MODE_OPENAI_COMPLETIONS)
    p = require_profile("openai")
    req = ChatRequest(
        model="gpt-4o",
        messages=[Message(role="system", content="you are helpful"),
                  Message(role="user", content="hi")],
        max_tokens=512,
    )
    body = t.serialize_request(req, p)
    assert body["model"] == "gpt-4o"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["content"] == "hi"
    assert body["max_tokens"] == 512


# ---- AC7: openai-completions achat/astream 走 stub(0.1.0 P1 排队) ----

def test_openai_completions_achat_stub():
    """AC7: OpenAICompletionsTransport.achat 走 stub raise NotImplementedError。"""
    t = require_transport(API_MODE_OPENAI_COMPLETIONS)
    p = require_profile("deepseek")
    req = ChatRequest(model="deepseek-chat", messages=[Message(role="user", content="hi")])
    with pytest.raises(NotImplementedError) as exc_info:
        import asyncio
        asyncio.run(t.achat(req, p))
    assert "openai-completions" in str(exc_info.value)
    assert "P1 排队" in str(exc_info.value)


# ---- AC8: create_provider 走 registry,isinstance 向后兼容 ----

def test_create_provider_anthropic_backward_compat(fake_llm_config):
    """AC8a: create_provider('anthropic') 返 AnthropicProvider(向后兼容 isinstance)。"""
    p = create_provider("anthropic", fake_llm_config)
    assert isinstance(p, AnthropicProvider)
    assert p.name == "anthropic"


def test_create_provider_minimax_backward_compat(fake_llm_config):
    """AC8b: create_provider('minimax') 返 MiniMaxProvider(向后兼容)。"""
    p = create_provider("minimax", fake_llm_config)
    assert isinstance(p, MiniMaxProvider)
    assert p.name == "minimax"


def test_create_provider_mock_backward_compat(fake_llm_config):
    """AC8c: create_provider('mock') 返 MockProvider(向后兼容)。"""
    p = create_provider("mock", fake_llm_config)
    assert isinstance(p, MockProvider)
    assert p.name == "mock"


def test_create_provider_openai_returns_openai_provider(fake_llm_config):
    """AC8d: create_provider('openai') 返 _OpenAICompletionsProvider(新类,0.1.0 stub)。"""
    p = create_provider("openai", fake_llm_config)
    assert p.name == "openai"
    assert p.api_mode == API_MODE_OPENAI_COMPLETIONS
    # 0.1.0 期间 chat() 应 raise NotImplementedError
    req = ChatRequest(model="gpt-4o", messages=[Message(role="user", content="hi")])
    with pytest.raises(NotImplementedError):
        p.chat(req)


# ---- AC9: resolve_api_key 走 env_vars 兜底链 ----

def test_resolve_api_key_fallback_chain(monkeypatch):
    """AC9: resolve_api_key 走 env_vars 兜底链(第一个非空命中)。"""
    p = require_profile("zhipu")
    # env_vars=("ZHIPU_API_KEY", "GLM_API_KEY", "ZAI_API_KEY")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    assert resolve_api_key(p) == ""

    # 设 ZAI_API_KEY(第 3 个),应命中
    monkeypatch.setenv("ZAI_API_KEY", "fake-zai-key")
    assert resolve_api_key(p) == "fake-zai-key"

    # 再设 ZHIPU_API_KEY(第 1 个),应优先
    monkeypatch.setenv("ZHIPU_API_KEY", "fake-zhipu-key")
    assert resolve_api_key(p) == "fake-zhipu-key"

    # GLM_API_KEY 单独设(第 2 个),应命中
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.setenv("GLM_API_KEY", "fake-glm-key")
    assert resolve_api_key(p) == "fake-glm-key"


def test_resolve_api_key_ollama_no_env():
    """AC9: ollama(auth_type=none) 不需 key,返空字符串。"""
    p = require_profile("ollama")
    assert resolve_api_key(p) == ""


# ---- AC10: wizard.validate_api_key 走 profile ----

def test_validate_api_key_anthropic():
    """AC10: validate_api_key 走 profile.env_vars + 特定 prefix 检查。"""
    # 错的 prefix
    ok, err = validate_api_key("anthropic", "wrong-prefix-1234567890")
    assert not ok
    assert "sk-ant-" in err

    # 正确的
    ok, _ = validate_api_key("anthropic", "sk-ant-" + "x" * 100)
    assert ok

    # 占位符
    ok, err = validate_api_key("anthropic", "FAKE_KEY_12345")
    assert not ok
    assert "占位符" in err


def test_validate_api_key_ollama_no_check():
    """AC10: ollama validate 不查 prefix(本地不需要)。"""
    ok, _ = validate_api_key("ollama", "anything-not-empty")
    assert ok

    # 但占位符仍挡
    ok, err = validate_api_key("ollama", "FAKE")
    assert not ok
    assert "占位符" in err


def test_validate_api_key_minimax_no_prefix():
    """AC10: minimax 不强 prefix(各家不同,通用 10 字符即可)。"""
    ok, _ = validate_api_key("minimax", "x" * 50)  # 50 字符,无特定 prefix
    assert ok


# ---- AC11: wizard PROVIDERS 列表 16 个 ----

def test_wizard_providers_count_17():
    """AC11: wizard PROVIDERS 列表有 17 个(M3+ 批 8.5 修问题 1 加 minimax-cn)。"""
    assert len(PROVIDERS) == 17
    names = [n for n, _ in PROVIDERS]
    # ollama 排第一(本地优先)
    assert names[0] == "ollama"
    # 含 anthropic + minimax + minimax-cn
    assert "anthropic" in names
    assert "minimax" in names
    assert "minimax-cn" in names
    # 含 14 个 openai-compat
    openai_compat = {"openai", "deepseek", "zhipu", "moonshot", "kimi", "qwen",
                     "gemini", "groq", "mistral", "xai", "openrouter",
                     "together", "fireworks"}
    assert openai_compat.issubset(set(names))


# ---- AC12: serialize_request shape 不下发(stream 标志) ----

def test_anthropic_serialize_with_stream():
    """AC12: AnthropicProvider.serialize_request 含 stream 标志(向后兼容旧测试)。"""
    p = create_provider("anthropic", LLMConfig(
        default="anthropic",
        providers={"anthropic": ProviderConfig(type="anthropic", api_key="sk-ant-x" * 10,
                                               base_url="https://api.anthropic.com",
                                               default_model="claude-sonnet-4-6")},
    ))
    req = ChatRequest(model="claude-sonnet-4-6", messages=[Message(role="user", content="hi")], stream=True)
    body = p.serialize_request(req)
    assert body["stream"] is True


# ---- AC13: transport 优先读 yaml api_key(M3+ 批 8.5 修问题 2) ----

def test_transport_yaml_api_key_wins_over_env(monkeypatch):
    """AC13: provider.chat() 透传 self.config.api_key(yaml)给 transport,不走 env_vars。

    问题 2 根因:旧 AnthropicMessagesTransport._build_gw_provider 只读 os.environ,
    忽略 yaml,导致 wizard 输的 key 被丢。修法:Provider 透传 yaml api_key,
    transport 优先用它,env_vars 仅作 fallback。

    这里用 monkeypatch 拦截 AnthropicAdapter.astream,捕获传给它的 provider 参数,
    验证 api_key 字段是 yaml 里的 key,而不是 env var。
    """
    from unittest.mock import patch
    from karvyloop.gateway.events import TextDelta, Done

    # 设一个 env var,模拟"用户也 export 了"(优先级应被 yaml 覆盖)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ENV-var-key-priority-test")

    yaml_key = "sk-ant-YAML-key-from-config-priority-test"
    cfg = LLMConfig(
        default="anthropic",
        providers={"anthropic": ProviderConfig(
            type="anthropic",
            api_key=yaml_key,
            base_url="https://api.anthropic.com",
            default_model="claude-sonnet-4-6",
        )},
    )
    p = create_provider("anthropic", cfg)

    # 桩 AnthropicAdapter.astream:只回 1 个 TextDelta + Done
    captured_provider = {}

    async def _fake_astream(*, messages, tools, model, provider, system):
        # 捕获 provider 给的 api_key
        captured_provider["api_key"] = provider.api_key
        captured_provider["base_url"] = provider.base_url
        captured_provider["name"] = provider.name
        yield TextDelta(text="ok")
        yield Done(stop_reason="end_turn")

    # Patch 正确路径:AnthropicAdapter 在 anthropic_messages 顶部 import
    with patch("karvyloop.llm.transports.anthropic_messages.AnthropicAdapter") as MockAdapter:
        MockAdapter.return_value.astream = _fake_astream
        req = ChatRequest(model="claude-sonnet-4-6", messages=[Message(role="user", content="hi")])
        resp = p.chat(req)

    assert resp.content == "ok"
    # 核心断言:yaml 的 key 赢
    assert captured_provider["api_key"] == yaml_key, (
        f"yaml api_key 应优先,实际拿到: {captured_provider['api_key']!r} "
        f"(env var 才是 'sk-ant-ENV-var-key-priority-test')"
    )
    assert captured_provider["base_url"] == "https://api.anthropic.com"


def test_transport_yaml_key_used_when_env_unset(monkeypatch):
    """AC13: env var 没设,只 yaml 有 key,transport 也能拿到(yaml 优先 + 兜底链有效)。"""
    from unittest.mock import patch
    from karvyloop.gateway.events import TextDelta, Done

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yaml_key = "sk-ant-only-yaml-no-env-fallback"
    cfg = LLMConfig(
        default="anthropic",
        providers={"anthropic": ProviderConfig(
            type="anthropic", api_key=yaml_key,
            base_url="https://api.anthropic.com",
            default_model="claude-sonnet-4-6",
        )},
    )
    p = create_provider("anthropic", cfg)

    captured = {}

    async def _fake_astream(*, messages, tools, model, provider, system):
        captured["api_key"] = provider.api_key
        yield TextDelta(text="x")
        yield Done(stop_reason="end_turn")

    with patch("karvyloop.llm.transports.anthropic_messages.AnthropicAdapter") as MockAdapter:
        MockAdapter.return_value.astream = _fake_astream
        p.chat(ChatRequest(model="claude-sonnet-4-6", messages=[Message(role="user", content="hi")]))

    assert captured["api_key"] == yaml_key


def test_transport_env_fallback_when_yaml_empty(monkeypatch):
    """AC13: yaml.api_key 空时,transport 走 env_vars 兜底链(用户跳 wizard 时)。"""
    from unittest.mock import patch
    from karvyloop.gateway.events import TextDelta, Done

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ENV-fallback-when-yaml-empty")
    cfg = LLMConfig(
        default="anthropic",
        providers={"anthropic": ProviderConfig(
            type="anthropic", api_key="",  # yaml 留空(占位)
            base_url="https://api.anthropic.com",
            default_model="claude-sonnet-4-6",
        )},
    )
    p = create_provider("anthropic", cfg)

    captured = {}

    async def _fake_astream(*, messages, tools, model, provider, system):
        captured["api_key"] = provider.api_key
        yield TextDelta(text="x")
        yield Done(stop_reason="end_turn")

    with patch("karvyloop.llm.transports.anthropic_messages.AnthropicAdapter") as MockAdapter:
        MockAdapter.return_value.astream = _fake_astream
        p.chat(ChatRequest(model="claude-sonnet-4-6", messages=[Message(role="user", content="hi")]))

    assert captured["api_key"] == "sk-ant-ENV-fallback-when-yaml-empty"


# ---- AC14: wizard 写完 yaml 不再提示"export X" ----

def test_wizard_run_wizard_no_export_prompt(capsys, tmp_path, monkeypatch):
    """AC14: 修问题 2 —— wizard 写完 yaml 后,**不**再让用户 export env。

    旧版最后一行 stdout 是 "下一步:export ANTHROPIC_API_KEY=... 然后 karvyloop run ...",
    用户跟着 export 一次,但 transport 仍不读 yaml,所以还得在 wizard 再输一次。
    修法:wizard 写 yaml 是 source of truth,transport 走 yaml,不再 prompt export。
    """
    import sys as _sys
    from karvyloop.cli import wizard as _wiz

    target = tmp_path / "config.yaml"
    valid_key = "sk-ant-zzzzZZZZ0123456789ABCDE"  # 26 字符,前缀对
    inputs = iter(["2", valid_key])  # 选 2=anthropic, 然后 key
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    rc = _wiz.run_wizard(target=target, renderer=_wiz.Renderer(color=False))
    captured = capsys.readouterr()
    text = target.read_text(encoding="utf-8")

    assert rc == 0
    # 关键断言:不再有"export X="的提示(旧版会打印这一行)
    assert "export ANTHROPIC_API_KEY" not in captured.out, (
        f"wizard 不应再提示 export env var,实际 stdout 含: {captured.out!r}"
    )
    # 改写为:下一步直接 karvyloop run
    assert "直接 karvyloop run" in captured.out or "karvyloop run" in captured.out
    # yaml 里 key 已写
    assert valid_key in text
    # 占位已替换
    assert "${ANTHROPIC_API_KEY}" not in text


# ---- AC15: minimax-cn 独立 profile(M3+ 批 8.5 修问题 1) ----

def test_minimax_cn_registered_separately():
    """AC15a: minimax-cn 是独立 profile,不是 minimax 的 alias。

    参照工程的"2 个 profile 同协议不同 baseUrl"模式;clean-room 重写。

    关键差异:
      - base_url: cn 用 api.minimaxi.com/anthropic(国内),global 用 api.MiniMax.chat/anthropic
      - env_vars: cn 用 MINIMAX_CN_API_KEY(国内账号体系),global 用 MiniMax_API_KEY
    """
    cn = require_profile("minimax-cn")
    gl = require_profile("minimax")

    # 关键:不是 alias,是独立 profile
    assert cn.name == "minimax-cn"
    assert cn is not gl
    # baseUrl 不一样
    assert cn.base_url == "https://api.minimaxi.com/anthropic"
    assert gl.base_url == "https://api.MiniMax.chat/anthropic"
    assert cn.base_url != gl.base_url, "cn 和 global 必须有不同 baseUrl"
    # env vars 不一样
    assert cn.env_vars == ("MINIMAX_CN_API_KEY",)
    assert gl.env_vars == ("MiniMax_API_KEY", "MiniMax_API_KEY")
    assert cn.env_vars != gl.env_vars, "cn 和 global 必须有不同 env var(账号体系独立)"
    # 协议都是 anthropic-messages(共用 transport)
    assert cn.api_mode == gl.api_mode == API_MODE_ANTHROPIC_MESSAGES


def test_minimax_cn_aliases():
    """AC15b: minimax-cn 的 aliases 解析(参照工程的 minimax-china/minimax_cn 模式)。"""
    # 主名直查
    assert get_profile("minimax-cn").name == "minimax-cn"
    # aliases
    assert get_profile("minimax-china").name == "minimax-cn"
    assert get_profile("minimax_cn").name == "minimax-cn"
    assert get_profile("minicn").name == "minimax-cn"
    # 不应误钩到 global
    for alias in ("minimax-china", "minimax_cn", "minicn"):
        p = get_profile(alias)
        assert p.name == "minimax-cn", (
            f"alias {alias!r} 应钩到 minimax-cn,实际 {p.name!r}"
        )


def test_minimax_cn_resolve_api_key_uses_cn_env_only(monkeypatch):
    """AC15c: cn profile 走 cn env,不读 global env(账号体系隔离)。"""
    cn = require_profile("minimax-cn")
    monkeypatch.delenv("MINIMAX_CN_API_KEY", raising=False)
    monkeypatch.setenv("MiniMax_API_KEY", "fake-global-key")
    # cn 只查 MINIMAX_CN_API_KEY,即使 global env 有,也不读
    assert resolve_api_key(cn) == ""


def test_minimax_cn_in_wizard_provider_list():
    """AC15d: wizard.PROVIDERS 含 minimax-cn(动态拉自 registry,17 个 vendor)。"""
    names = [n for n, _ in PROVIDERS]
    assert "minimax-cn" in names, f"minimax-cn 应在 wizard 列表,实际 17 选: {names}"
    # 总数变 17(原 16 + minimax-cn)
    assert len(PROVIDERS) == 17, f"期望 17 个 vendor,实际 {len(PROVIDERS)}"


def test_minimax_cn_create_provider_uses_cn_base_url(fake_llm_config):
    """AC15e: create_provider('minimax-cn') 走 AnthropicProvider(共用 transport)+ cn baseUrl。

    minimax-cn 不需要单独 class(不像 minimax 那样有 MiniMaxProvider isinstance 兼容)，
    因为 minimax-cn 没有早期 isinstance 依赖;走 AnthropicProvider + profile.base_url 即可。
    """
    # 加 cn 到 fake config(yaml 写法:type 留空,create_provider 走 registry 查 profile)
    fake_llm_config.providers["minimax-cn"] = ProviderConfig(
        type="",  # 让 registry 走 minimax_cn profile(name 优先)
        api_key="fk-cn-fake-key",
        base_url="https://api.minimaxi.com/anthropic",
        default_model="minimax/MiniMax-M3",
    )
    p = create_provider("minimax-cn", fake_llm_config)
    # 走 AnthropicProvider(共用 anthropic-messages transport)
    # 注意:AnthropicProvider.name 是 class attr 'anthropic',per-instance 标识靠 config
    assert isinstance(p, AnthropicProvider)
    assert p.api_mode == API_MODE_ANTHROPIC_MESSAGES
    # base_url 来自 yaml(用户写),保留到 ProviderConfig
    assert p.config.base_url == "https://api.minimaxi.com/anthropic"
    # type 也对(generic anthropic-messages,bearer 走 AnthropicMessagesTransport)
    assert p.config.type in ("", "minimax", "anthropic")  # user 写什么都行,profile override
