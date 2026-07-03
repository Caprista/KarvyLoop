"""test_gateway_reasoning — 推理强度档位(Hardy 碎碎念⑩:"是否支持分 agent 或者全局,
设置推理强度(快速回答和深度思考)")。

覆盖:
1. 档位 → 参数映射(内置两方言 + reasoning_styles 配置覆盖 + 不支持优雅忽略)
2. **wire 端请求体形状**(respx 拦真 adapter,Q2 复盘承诺的 request-body-shape 先例):
   anthropic-messages → thinking.budget_tokens;openai-completions → reasoning_effort
3. 全局档继承 / 运行时覆盖 / 不支持 provider 不炸不污染请求
4. **记账回归**:注入推理参数后 Usage → token 账本照记(咽喉纪律,一字不动)
5. registry 解析 agents.defaults.reasoning(含手误 fail-loud)
"""
from __future__ import annotations

import asyncio
import json

import pytest

from karvyloop.gateway.client import GatewayClient
from karvyloop.gateway.events import Done, TextDelta, Usage
from karvyloop.gateway.reasoning import REASONING_LEVELS, reasoning_params
from karvyloop.schemas import ModelDefinition, ProviderConfig


def _model(api="anthropic-messages", *, reasoning=True, max_tokens=8192, styles=None):
    return ModelDefinition(
        id="p/m", name="m", api=api, context_window=200000,
        max_tokens=max_tokens, reasoning=reasoning,
        reasoning_styles=styles or {},
    )


# ---- 1. 档位 → 参数映射 ----

def test_anthropic_builtin_mapping_deep_balanced_fast():
    m = _model("anthropic-messages", max_tokens=8192)
    assert reasoning_params("deep", m) == {"thinking": {"type": "enabled", "budget_tokens": 4096}}
    assert reasoning_params("balanced", m) == {"thinking": {"type": "enabled", "budget_tokens": 2048}}
    assert reasoning_params("fast", m) == {}   # 不开 thinking = 最快


def test_openai_builtin_mapping_effort_levels():
    m = _model("openai-completions")
    assert reasoning_params("deep", m) == {"reasoning_effort": "high"}
    assert reasoning_params("balanced", m) == {"reasoning_effort": "medium"}
    assert reasoning_params("fast", m) == {"reasoning_effort": "low"}


def test_model_without_reasoning_support_ignored():
    """模型没声明 reasoning 支持 → 不注参(乱注 thinking = 白送 4xx)。"""
    for api in ("anthropic-messages", "openai-completions"):
        assert reasoning_params("deep", _model(api, reasoning=False)) == {}


def test_reasoning_styles_config_override_wins():
    """每模型 reasoning_styles 覆盖内置映射(配置说了算);空 dict = 该档显式不加参。"""
    styles = {"deep": {"thinking": {"type": "enabled", "budget_tokens": 999}}, "balanced": {}}
    m = _model("anthropic-messages", styles=styles)
    assert reasoning_params("deep", m)["thinking"]["budget_tokens"] == 999
    assert reasoning_params("balanced", m) == {}          # 显式不加参
    assert "thinking" in reasoning_params("deep", m)      # 覆盖生效
    # 没覆盖的档仍走内置(fast → 不开)
    assert reasoning_params("fast", m) == {}


def test_unknown_level_tiny_budget_and_unknown_api_all_ignored():
    assert reasoning_params("turbo", _model()) == {}                       # 未知档位
    assert reasoning_params("", _model()) == {}                            # 空 = 不设
    assert reasoning_params("deep", _model(max_tokens=1000)) == {}         # budget < 1024 下限
    assert reasoning_params("deep", _model("ollama")) == {}                # 方言无内置映射
    assert REASONING_LEVELS == ("fast", "balanced", "deep")


# ---- 2. wire 端请求体形状(respx 拦真 adapter;Q2 request-body-shape 先例) ----

respx = pytest.importorskip("respx")
httpx = pytest.importorskip("httpx")


def _anthropic_prov():
    return ProviderConfig(name="p", base_url="https://api.example-FAKE.com",
                          api_key="sk-reasoning-shape-FAKE-DO-NOT-LEAK",
                          messages_path="/v1/messages", models=[])


def _openai_prov():
    return ProviderConfig(name="p", base_url="https://api.example-FAKE.com/v1",
                          api_key="sk-reasoning-shape-FAKE-DO-NOT-LEAK",
                          auth_header="Authorization",
                          messages_path="/chat/completions", models=[])


_ANTHROPIC_SSE = (
    'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}\n\n'
    'data: {"type":"content_block_stop","index":0}\n\n'
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    '"usage":{"input_tokens":7,"output_tokens":3}}\n\n'
    'data: {"type":"message_stop"}\n\n'
)

_OPENAI_SSE = (
    'data: {"choices":[{"delta":{"content":"ok"},"index":0}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop","index":0}]}\n\n'
    'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3}}\n\n'
    'data: [DONE]\n\n'
)


def _body(route) -> dict:
    assert route.called, "respx 路由未命中 —— adapter 走错 URL?"
    return json.loads(route.calls.last.request.content)


@pytest.mark.asyncio
@respx.mock
async def test_wire_anthropic_deep_injects_thinking_budget():
    """anthropic-messages 方言:deep 档 → body 顶层 thinking:{type:enabled,budget_tokens}。"""
    from karvyloop.gateway.providers.anthropic import AnthropicAdapter
    route = respx.post("https://api.example-FAKE.com/v1/messages").mock(
        return_value=httpx.Response(200, text=_ANTHROPIC_SSE,
                                    headers={"content-type": "text/event-stream"}))
    m = _model("anthropic-messages", max_tokens=8192)
    async for _ in AnthropicAdapter().complete(
            [{"role": "user", "content": "hi"}], [], m, _anthropic_prov(),
            extra_body=reasoning_params("deep", m)):
        pass
    body = _body(route)
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096}, body
    # 原有骨架不被污染
    for k in ("model", "max_tokens", "messages", "stream"):
        assert k in body
    assert body["max_tokens"] == 8192 and body["max_tokens"] > body["thinking"]["budget_tokens"]


@pytest.mark.asyncio
@respx.mock
async def test_wire_anthropic_fast_leaves_body_clean():
    """fast 档(anthropic)→ 请求体无 thinking(与没设档完全同形,零污染)。"""
    from karvyloop.gateway.providers.anthropic import AnthropicAdapter
    route = respx.post("https://api.example-FAKE.com/v1/messages").mock(
        return_value=httpx.Response(200, text=_ANTHROPIC_SSE,
                                    headers={"content-type": "text/event-stream"}))
    m = _model("anthropic-messages")
    async for _ in AnthropicAdapter().complete(
            [{"role": "user", "content": "hi"}], [], m, _anthropic_prov(),
            extra_body=reasoning_params("fast", m)):
        pass
    assert "thinking" not in _body(route)


@pytest.mark.asyncio
@respx.mock
async def test_wire_openai_deep_injects_reasoning_effort():
    """openai-completions 方言:deep 档 → body 顶层 reasoning_effort:"high"。"""
    from karvyloop.gateway.providers.openai_completions import OpenAICompletionsAdapter
    route = respx.post("https://api.example-FAKE.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=_OPENAI_SSE,
                                    headers={"content-type": "text/event-stream"}))
    m = _model("openai-completions")
    async for _ in OpenAICompletionsAdapter().complete(
            [{"role": "user", "content": "hi"}], [], m, _openai_prov(),
            extra_body=reasoning_params("deep", m)):
        pass
    body = _body(route)
    assert body["reasoning_effort"] == "high", body
    for k in ("model", "messages", "stream"):
        assert k in body


@pytest.mark.asyncio
@respx.mock
async def test_wire_unsupported_model_body_unchanged():
    """不支持的模型(reasoning:false)+ deep 档 → 请求体与不设档**完全一致**(不炸不污染)。"""
    from karvyloop.gateway.providers.openai_completions import OpenAICompletionsAdapter
    route = respx.post("https://api.example-FAKE.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=_OPENAI_SSE,
                                    headers={"content-type": "text/event-stream"}))
    m = _model("openai-completions", reasoning=False)
    async for _ in OpenAICompletionsAdapter().complete(
            [{"role": "user", "content": "hi"}], [], m, _openai_prov(),
            extra_body=reasoning_params("deep", m)):
        pass
    body = _body(route)
    assert "reasoning_effort" not in body and "thinking" not in body


@pytest.mark.asyncio
@respx.mock
async def test_wire_thinking_signature_delta_is_silent(monkeypatch, capsys):
    """thinking 开启后协议发 signature_delta(thinking 块签名)—— 是预期事件,
    stderr 必须静默(真调取证时它曾被当"未知 delta"刷屏,已修)。"""
    for k in ("KARVYLOOP_ADAPTER_DEBUG", "KARVYLOOP_ADAPTER_DEBUG_RAW", "KARVYLOOP_ADAPTER_QUIET"):
        monkeypatch.delenv(k, raising=False)
    from karvyloop.gateway.providers.anthropic import AnthropicAdapter
    sse = (
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n\n'
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"hmm"}}\n\n'
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"sig-FAKE"}}\n\n'
        'data: {"type":"content_block_stop","index":0}\n\n'
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"input_tokens":7,"output_tokens":3}}\n\n'
        'data: {"type":"message_stop"}\n\n'
    )
    respx.post("https://api.example-FAKE.com/v1/messages").mock(
        return_value=httpx.Response(200, text=sse,
                                    headers={"content-type": "text/event-stream"}))
    m = _model("anthropic-messages")
    async for _ in AnthropicAdapter().complete(
            [{"role": "user", "content": "hi"}], [], m, _anthropic_prov(),
            extra_body=reasoning_params("deep", m)):
        pass
    assert capsys.readouterr().err == "", "signature_delta 是 thinking 的预期协议事件,不该刷 stderr"


# ---- 3+4. gateway 全局档继承 / 覆盖 / 降级 + 记账回归 ----

from karvyloop.llm.token_ledger import TokenLedger, register_ledger, token_source


class _Reg:
    def __init__(self, model, default_reasoning=""):
        self._m = model
        self.default_reasoning = default_reasoning

    def get(self, ref):
        return self._m

    def provider_of(self, ref):
        return None


class _CaptureAdapter:
    """记录收到的 extra_body,并发 Usage(记账回归用)。"""
    def __init__(self):
        self.seen = "NOT-CALLED"

    async def complete(self, messages, tools, m, prov, system=None, extra_body=None):
        self.seen = extra_body
        yield TextDelta(text="ok")
        yield Usage(input_tokens=100, output_tokens=20)
        yield Done(stop_reason="end_turn")


class _LegacyAdapter:
    """不认 extra_body 的旧 adapter(第三方/测试桩)—— gateway 必须优雅降级。"""
    called = False

    async def complete(self, messages, tools, m, prov, system=None):
        _LegacyAdapter.called = True
        yield TextDelta(text="ok")
        yield Usage(input_tokens=10, output_tokens=2)
        yield Done(stop_reason="end_turn")


@pytest.fixture
def ledger():
    led = TokenLedger(path=None)
    register_ledger(led)
    try:
        yield led
    finally:
        register_ledger(None)


def _drive(gw, **kw):
    async def go():
        with token_source("reasoning_test"):
            async for _ in gw.complete([{"role": "user", "content": "x"}], [], "p/m", **kw):
                pass
    asyncio.run(go())


def test_gateway_inherits_global_level_and_records_tokens(ledger):
    """全局 agents.defaults.reasoning=deep → adapter 收到落参;Usage 照记账(咽喉回归)。"""
    ad = _CaptureAdapter()
    gw = GatewayClient(_Reg(_model("anthropic-messages"), "deep"),
                       adapters={"anthropic-messages": ad})
    _drive(gw)
    assert ad.seen == {"thinking": {"type": "enabled", "budget_tokens": 4096}}
    t = ledger.totals()
    assert t["calls"] == 1 and t["total"] == 120, f"注入推理参数后记账断了: {t}"
    assert ledger.by_source()[0]["source"] == "reasoning_test"


def test_gateway_per_call_override_beats_global(ledger):
    """运行时覆盖(role/任务级接口):reasoning="fast" 压过全局 deep;"" = 本次显式关。"""
    ad = _CaptureAdapter()
    gw = GatewayClient(_Reg(_model("openai-completions"), "deep"),
                       adapters={"openai-completions": ad})
    _drive(gw, reasoning="fast")
    assert ad.seen == {"reasoning_effort": "low"}
    _drive(gw, reasoning="")
    assert ad.seen is None          # 显式关 → 不带 extra_body
    _drive(gw)                       # None → 继承全局 deep
    assert ad.seen == {"reasoning_effort": "high"}


def test_gateway_no_level_no_extra_body(ledger):
    """没配全局档(缺省)→ adapter 收 extra_body=None,行为与旧版一字不差(零回归)。"""
    ad = _CaptureAdapter()
    gw = GatewayClient(_Reg(_model("anthropic-messages"), ""),
                       adapters={"anthropic-messages": ad})
    _drive(gw)
    assert ad.seen is None


def test_gateway_legacy_adapter_graceful_fallback(ledger):
    """不认 extra_body 的 adapter + 有档位 → 不炸,降级重调,流照走、账照记。"""
    gw = GatewayClient(_Reg(_model("anthropic-messages"), "deep"),
                       adapters={"anthropic-messages": _LegacyAdapter()})
    _drive(gw)   # 不抛 TypeError
    assert _LegacyAdapter.called
    assert ledger.totals()["total"] == 12   # 记账回归:降级路径也照记


# ---- 5. registry 解析全局档 ----

def _cfg(reasoning=None):
    defaults = {"model": "p/m"}
    if reasoning is not None:
        defaults["reasoning"] = reasoning
    return {
        "models": {"providers": {"p": {
            "base_url": "https://x-FAKE", "api_key": "sk-FAKE",
            "models": [{"id": "p/m", "api": "anthropic-messages",
                        "context_window": 100, "max_tokens": 10}],
        }}},
        "agents": {"defaults": defaults},
    }


def test_registry_parses_default_reasoning():
    from karvyloop.gateway.registry import ModelRegistry
    assert ModelRegistry.from_config(_cfg()).default_reasoning == ""          # 缺省=不设
    assert ModelRegistry.from_config(_cfg("balanced")).default_reasoning == "balanced"


def test_registry_rejects_invalid_reasoning_level():
    """配置手误 fail-loud(no silent drift):写错档位不许静默当没配。"""
    from karvyloop.gateway.registry import ModelRegistry
    with pytest.raises(ValueError, match="reasoning"):
        ModelRegistry.from_config(_cfg("ultra"))


def test_registry_parses_model_reasoning_styles():
    """yaml 模型条目的 reasoning_styles 能过 schema(extra=forbid)进 ModelDefinition。"""
    from karvyloop.gateway.registry import ModelRegistry
    cfg = _cfg("deep")
    cfg["models"]["providers"]["p"]["models"][0]["reasoning_styles"] = {
        "deep": {"reasoning_effort": "high"}}
    reg = ModelRegistry.from_config(cfg)
    assert reg.get("p/m").reasoning_styles == {"deep": {"reasoning_effort": "high"}}


# ---- config_models 管理面透出(全局档 + 每模型落参表) ----

def test_config_models_reasoning_roundtrip(tmp_path):
    from karvyloop.gateway.config_models import (
        list_models, set_default_reasoning, upsert_model)
    p = tmp_path / "config.yaml"
    ok, msg = upsert_model({
        "provider": "p", "model_id": "p/m", "api": "openai-completions",
        "base_url": "https://x-FAKE", "api_key": "sk-cfg-FAKE",
        "reasoning": True,
        "reasoning_styles": {"deep": {"reasoning_effort": "high"},
                             "bogus_level": {"x": 1},        # 非法档位被滤
                             "fast": "not-a-dict"},           # 非 dict 被滤
    }, cfg_path=p)
    assert ok, msg
    ok, msg = set_default_reasoning("deep", cfg_path=p)
    assert ok, msg
    j = list_models(cfg_path=p)
    assert j["default_reasoning"] == "deep" and j["valid_reasoning"] == ["fast", "balanced", "deep"]
    assert j["models"][0]["reasoning_styles"] == {"deep": {"reasoning_effort": "high"}}
    # 空 = 删档;非法档拒绝
    assert set_default_reasoning("", cfg_path=p)[0]
    assert list_models(cfg_path=p)["default_reasoning"] == ""
    assert not set_default_reasoning("ultra", cfg_path=p)[0]
