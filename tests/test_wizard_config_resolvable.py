"""test_wizard_config_resolvable — wizard 生成的 config 必须能 load + 解析(修首跑崩溃,拍 9.4-P0).

门1 亲身踩到:`karvyloop init` 给 minimax 写的 config 让新用户首次 `run` 当场崩 ——
① provider 块 key 用 profile.name(minimax-cn)而 model 是 minimax/... → runtime
   `provider_of(model)` 按前缀 "minimax" 查 providers["minimax"] → **KeyError**;
② 漏 auth_header → 默认 x-api-key,而 MiniMax 要 Bearer → **500**;
③ 默认 chat 模型仍 ollama → run 用本地(没起)→ 失败。

本测试锁住:wizard 生成的 config 对**所有非 ollama provider**都能 from_config 加载 +
provider_of(默认模型) 不崩 + auth_header 与 profile 一致 + 默认模型已切到所选 provider。
"""
from __future__ import annotations

import pytest
import yaml

from karvyloop.cli.wizard import _build_config_for
from karvyloop.gateway.registry import ModelRegistry
from karvyloop.llm.registry import get as get_profile_by_name


FAKE = "sk-cp-FAKE-DO-NOT-LEAK"


def _provider_key_of(model_id: str) -> str:
    return model_id.split("/", 1)[0]


@pytest.mark.parametrize("provider", ["minimax-cn", "minimax", "anthropic"])
def test_wizard_config_loads_and_resolves(provider):
    profile = get_profile_by_name(provider)
    if profile is None:
        pytest.skip(f"profile {provider} 不存在")
    txt = _build_config_for(provider, FAKE)
    cfg = yaml.safe_load(txt)

    # ① 能 load(schema 合法)+ provider_of(默认模型) 不崩(块 key = model 前缀)
    reg = ModelRegistry.from_config(cfg)
    default_model = cfg["agents"]["defaults"]["model"]
    pc = reg.provider_of(default_model)  # 原 bug 这里 KeyError
    assert pc is not None

    # ③ 默认 chat 模型已切到所选 provider(不再停留 ollama)
    assert default_model != "ollama/qwen2.5-coder:7b"
    assert _provider_key_of(default_model) == _provider_key_of(profile.default_model)

    # 块 key = model 前缀(provider_of 一致)
    assert _provider_key_of(default_model) in cfg["models"]["providers"]


def test_minimax_cn_auth_header_is_bearer():
    """② minimax-cn 必须写 auth_header: Authorization(Bearer),否则 MiniMax 500。"""
    if get_profile_by_name("minimax-cn") is None:
        pytest.skip("minimax-cn profile 不存在")
    cfg = yaml.safe_load(_build_config_for("minimax-cn", FAKE))
    block = cfg["models"]["providers"]["minimax"]
    assert block.get("auth_header") == "Authorization"
    assert "minimaxi.com" in block["base_url"]  # CN 端点


def test_ollama_config_unchanged():
    """ollama(本地默认)config 不动:默认模型仍 ollama,无需 key。"""
    cfg = yaml.safe_load(_build_config_for("ollama", None))
    assert cfg["agents"]["defaults"]["model"] == "ollama/qwen2.5-coder:7b"
