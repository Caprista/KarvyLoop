"""karvyloop init 默认 config 模板的 schema 合规测试。

为什么需要: init 写死的 DEFAULT_CONFIG_YAML 跟真实 ProviderConfig/ModelDefinition
schema 必须对得上 —— 否则任何跑 karvyloop init 的人都会撞 ValidationError。
这种 bug 在我自己迭代时反复出现过(2026-06 fix: 把 api 字段从 provider 层挪
到 model 层),靠这个测试锁住。

注意: init 模板里的 ollama 走 openai-completions 协议(因为 ollama 兼容
OpenAI 风格 /v1/chat/completions 端点),不是 anthropic-messages。
embedding 模型用 ollama 专用协议(不是 openai-completions)。
"""

from __future__ import annotations

import yaml

from karvyloop.cli.init import DEFAULT_CONFIG_YAML
from karvyloop.gateway.registry import ModelRegistry


def test_init_default_config_loads_without_error():
    """init 生成的 yaml 必须能过 ModelRegistry.from_config(不抛 ValidationError)。"""
    cfg = yaml.safe_load(DEFAULT_CONFIG_YAML)
    # 任何 ValidationError 都会让 load 失败;成功 = schema 合规
    reg = ModelRegistry.from_config(cfg)
    assert reg.default_chat in reg.models
    assert reg.default_embedding in reg.models


def test_init_default_config_has_minimax_or_ollama_and_anthropic_optional():
    """默认配置至少要有 ollama 本地优先(护城河: 数据不出门), anthropic 走云。"""
    cfg = yaml.safe_load(DEFAULT_CONFIG_YAML)
    providers = cfg["models"]["providers"]
    # 本地优先 (HR: 数据不出门)
    assert "ollama" in providers
    # 至少有一个云端 provider 可选
    assert any(p in providers for p in ("anthropic", "minimax", "openai"))


def test_init_default_config_no_legacy_fields():
    """防回归: init 模板不能用已被 schema 拒绝的字段(auth: none / provider.api)。"""
    cfg = yaml.safe_load(DEFAULT_CONFIG_YAML)
    for prov_name, p in cfg["models"]["providers"].items():
        # auth 必须是 schema 白名单里的字面量
        assert p.get("auth") in ("api-key", "oauth", "aws-sdk", "token"), (
            f"provider {prov_name} 的 auth={p.get('auth')!r} 不在 schema 白名单"
        )
        # api 字段不能出现在 provider 层
        assert "api" not in p, (
            f"provider {prov_name} 不该有 api 字段(api 属于每个 model)"
        )
        # 每个 model 都有 api 字段
        for m in p.get("models", []):
            assert "api" in m, f"model {m.get('id')} 缺 api 字段"
