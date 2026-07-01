"""LLM Config + Provider 测试(M3.0 批 1 拍 1:8 个测试 = 7 AC + 1 协议)。

设计:docs/21 §7 AC。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.llm import (  # noqa: E402
    AnthropicProvider,
    ChatRequest,
    ConfigNotFoundError,
    LLMConfig,
    Message,
    MiniMaxProvider,
    MissingApiKeyError,
    MissingDefaultError,
    MissingProvidersError,
    MockProvider,
    ProviderConfig,
    RealKeyInRepoError,
    create_provider,
    load_config,
)
from karvyloop.llm.shape import (  # noqa: E402
    assert_body_shape_valid,
    assert_chat_request_shape,
    assert_field_names_stable,
)


# ---------- fixtures ----------

@pytest.fixture
def fake_config_yaml(tmp_path: pathlib.Path) -> pathlib.Path:
    """写一个 fake config(5 问硬规则 L4:key 必带 FAKE/DO-NOT-LEAK)。"""
    p = tmp_path / "config.yaml"
    p.write_text(
        """\
llm:
  default: mock
  providers:
    anthropic:
      type: anthropic
      api_key: sk-ant-DO-NOT-LEAK-fake-12345678901234567890
      base_url: https://api.anthropic.com
      default_model: claude-3-5-sonnet-20241022
    minimax:
      type: minimax
      api_key: FAKE-MINIMAX-KEY-DO-NOT-LEAK-replace-with-real
      base_url: https://api.MiniMax.chat/anthropic
      default_model: MiniMax-Text-01
    mock:
      type: mock
      default_model: mock-1
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def real_key_yaml(tmp_path: pathlib.Path) -> pathlib.Path:
    """故意带真 key 形状(用于 L5 测试)。"""
    p = tmp_path / "config.yaml"
    p.write_text(
        """\
llm:
  default: anthropic
  providers:
    anthropic:
      type: anthropic
      api_key: sk-ant-1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop
      default_model: claude-3-5-sonnet-20241022
""",
        encoding="utf-8",
    )
    return p


# ---------- AC1: load_config 从 YAML 加载 ----------

class TestAC1LoadConfig:
    """AC1: 正常 YAML 返 LLMConfig。"""

    def test_load_fake_config(self, fake_config_yaml: pathlib.Path):
        cfg = load_config(fake_config_yaml)
        assert cfg.default == "mock"
        assert "anthropic" in cfg.providers
        assert "minimax" in cfg.providers
        assert "mock" in cfg.providers
        assert cfg.providers["anthropic"].type == "anthropic"
        assert cfg.providers["minimax"].type == "minimax"
        assert cfg.providers["mock"].type == "mock"


# ---------- AC2: 文件不存在抛 ConfigNotFoundError ----------

class TestAC2FileNotFound:
    """AC2: 配置文件不存在 → 抛 ConfigNotFoundError。"""

    def test_nonexistent_path_raises(self, tmp_path: pathlib.Path):
        with pytest.raises(ConfigNotFoundError):
            load_config(tmp_path / "nope.yaml")


# ---------- AC3: 缺 default 抛 MissingDefaultError ----------

class TestAC3MissingDefault:
    """AC3: 缺 llm.default → 抛 MissingDefaultError。"""

    def test_missing_default_raises(self, tmp_path: pathlib.Path):
        p = tmp_path / "config.yaml"
        p.write_text(
            "llm:\n  providers:\n    mock:\n      type: mock\n",
            encoding="utf-8",
        )
        with pytest.raises(MissingDefaultError):
            load_config(p)


# ---------- AC4: 缺 providers 抛 MissingProvidersError ----------

class TestAC4MissingProviders:
    """AC4: 缺 llm.providers → 抛 MissingProvidersError。"""

    def test_missing_providers_raises(self, tmp_path: pathlib.Path):
        p = tmp_path / "config.yaml"
        p.write_text("llm:\n  default: mock\n", encoding="utf-8")
        with pytest.raises(MissingProvidersError):
            load_config(p)


# ---------- AC5: type=mock 不需要 api_key(L2)----------

class TestAC5MockNoKey:
    """AC5: mock provider 不需要 api_key。"""

    def test_mock_provider_constructs_without_key(self, fake_config_yaml: pathlib.Path):
        cfg = load_config(fake_config_yaml)
        # mock 在 config.yaml 里没 api_key
        assert cfg.providers["mock"].api_key == ""
        # 构造 mock provider 不抛
        provider = create_provider("mock", cfg)
        assert isinstance(provider, MockProvider)
        # chat 也能跑
        resp = provider.chat(ChatRequest(
            model="mock-1",
            messages=[Message(role="user", content="hi")],
        ))
        assert "[mock echo]" in resp.content


# ---------- AC6: type=anthropic 缺 api_key 抛 MissingApiKeyError ----------

class TestAC6AnthropicRequiresKey:
    """AC6: anthropic provider 缺 api_key → 抛。"""

    def test_anthropic_without_key_raises(self, tmp_path: pathlib.Path):
        p = tmp_path / "config.yaml"
        p.write_text(
            """\
llm:
  default: anthropic
  providers:
    anthropic:
      type: anthropic
      base_url: https://api.anthropic.com
      default_model: claude-3-5-sonnet-20241022
""",
            encoding="utf-8",
        )
        with pytest.raises(MissingApiKeyError):
            load_config(p)


# ---------- AC7: 真 key 形状进仓库抛 RealKeyInRepoError(L5)----------

class TestAC7RealKeyDetected:
    """AC7: 真 key 形状(sk-ant-*) 进仓库 → 抛 RealKeyInRepoError。"""

    def test_real_anthropic_key_blocked(self, real_key_yaml: pathlib.Path):
        """不允许 allow_real_keys 时,真 key 进 → 抛。"""
        with pytest.raises(RealKeyInRepoError):
            load_config(real_key_yaml)

    def test_real_openai_key_blocked(self, tmp_path: pathlib.Path):
        p = tmp_path / "config.yaml"
        p.write_text(
            """\
llm:
  default: openai
  providers:
    openai:
      type: anthropic
      api_key: sk-1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijkl
      default_model: gpt-4
""",
            encoding="utf-8",
        )
        with pytest.raises(RealKeyInRepoError):
            load_config(p)

    def test_real_jwt_key_blocked(self, tmp_path: pathlib.Path):
        p = tmp_path / "config.yaml"
        p.write_text(
            """\
llm:
  default: anthropic
  providers:
    anthropic:
      type: anthropic
      api_key: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U
      default_model: claude-3-5-sonnet-20241022
""",
            encoding="utf-8",
        )
        with pytest.raises(RealKeyInRepoError):
            load_config(p)

    def test_allow_real_keys_passes(self, real_key_yaml: pathlib.Path):
        """allow_real_keys=True 放行(生产环境手工用)。"""
        cfg = load_config(real_key_yaml, allow_real_keys=True)
        assert cfg.providers["anthropic"].api_key.startswith("sk-ant-")

    def test_fake_keys_pass(self, fake_config_yaml: pathlib.Path):
        """FAKE/DO-NOT-LEAK 字样的 key 放行(测试 fixture)。"""
        cfg = load_config(fake_config_yaml)
        # FAKE 字样不匹配 sk-ant- 模式
        assert "DO-NOT-LEAK" in cfg.providers["anthropic"].api_key


# ---------- AC8: Mock provider 形状 test(L7)----------

class TestAC8MockShapeAndChat:
    """AC8: Mock provider 形状 + 同步 chat + 流式 stream 全部锁。"""

    def test_serialize_request_shape(self, fake_config_yaml: pathlib.Path):
        cfg = load_config(fake_config_yaml)
        provider = create_provider("mock", cfg)
        request = ChatRequest(
            model="mock-1",
            messages=[Message(role="user", content="hi")],
            system="test",
        )
        body = provider.serialize_request(request)
        # 1. 字段名稳定
        assert_field_names_stable(body, "mock")
        # 2. body 形状合法
        assert_body_shape_valid(body, "mock")
        # 3. mock 必有 mock=True
        assert body["mock"] is True

    def test_chat_request_shape(self):
        """ChatRequest 形状锁。"""
        req = ChatRequest(
            model="m",
            messages=[Message(role="user", content="x")],
        )
        assert_chat_request_shape(req)  # 不抛

    def test_mock_chat_returns_response(self, fake_config_yaml: pathlib.Path):
        cfg = load_config(fake_config_yaml)
        provider = create_provider("mock", cfg)
        resp = provider.chat(ChatRequest(
            model="mock-1",
            messages=[Message(role="user", content="hello")],
        ))
        assert resp.model == "mock-1"
        assert "hello" in resp.content
        assert resp.stop_reason == "end_turn"

    def test_mock_stream_yields_events(self, fake_config_yaml: pathlib.Path):
        cfg = load_config(fake_config_yaml)
        provider = create_provider("mock", cfg)
        events = list(provider.stream(ChatRequest(
            model="mock-1",
            messages=[Message(role="user", content="hi")],
        )))
        # 至少 1 个 text_delta + 1 个 done
        assert any(e.kind == "text_delta" for e in events)
        assert events[-1].kind == "done"

    def test_anthropic_serialize_body(self, fake_config_yaml: pathlib.Path):
        """AnthropicProvider serialize 形状锁(不真发请求,只看 body 字段)。"""
        cfg = load_config(fake_config_yaml)
        provider = create_provider("anthropic", cfg)
        assert isinstance(provider, AnthropicProvider)
        body = provider.serialize_request(ChatRequest(
            model="claude-3-5-sonnet-20241022",
            messages=[Message(role="user", content="hi")],
            system="sys",
        ))
        # 字段名稳定
        assert_field_names_stable(body, "anthropic")
        # 形状合法
        assert_body_shape_valid(body, "anthropic")
        # anthropic 必带 model / max_tokens / messages
        assert body["model"] == "claude-3-5-sonnet-20241022"
        assert body["max_tokens"] == 1024
        assert body["system"] == "sys"

    def test_minimax_serialize_body(self, fake_config_yaml: pathlib.Path):
        """MiniMaxProvider serialize 形状锁(走 anthropic-messages 兼容端点)。"""
        cfg = load_config(fake_config_yaml)
        provider = create_provider("minimax", cfg)
        assert isinstance(provider, MiniMaxProvider)
        body = provider.serialize_request(ChatRequest(
            model="MiniMax-Text-01",
            messages=[Message(role="user", content="hi")],
        ))
        assert_field_names_stable(body, "minimax")
        assert_body_shape_valid(body, "minimax")
        # MiniMax 必带 model
        assert body["model"] == "MiniMax-Text-01"

    def test_create_provider_unknown_raises(self, fake_config_yaml: pathlib.Path):
        cfg = load_config(fake_config_yaml)
        with pytest.raises(KeyError):
            create_provider("nope", cfg)


# ---------- AC9: 协议不变量 ----------

class TestAC9ProtocolInvariants:
    """AC9: 协议不变量 — 0 LLM(单测路径)、无全局单例、注入式。"""

    def test_no_global_singleton(self):
        """每次 create_provider 都返新实例(可多次创建)。"""
        cfg = LLMConfig(
            default="mock",
            providers={"mock": ProviderConfig(type="mock", default_model="m")},
        )
        p1 = create_provider("mock", cfg)
        p2 = create_provider("mock", cfg)
        assert p1 is not p2  # 新实例,无全局单例

    def test_default_config_path_is_home(self):
        """默认路径是 ~/.karvyloop/config.yaml。"""
        from karvyloop.llm.config import DEFAULT_CONFIG_PATH
        assert DEFAULT_CONFIG_PATH == pathlib.Path.home() / ".karvyloop" / "config.yaml"

    def test_provider_types_match_3(self):
        """只 3 个 provider type(不抄 LiteLLM 100+)。"""
        from karvyloop.llm.provider import (
            AnthropicProvider, MiniMaxProvider, MockProvider,
        )
        assert AnthropicProvider.type == "anthropic"
        assert MiniMaxProvider.type == "minimax"
        assert MockProvider.type == "mock"
