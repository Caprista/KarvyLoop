"""gateway 验收测试 —— 逐条对应 docs/modules/gateway.md §5 验收标准。

每个测试函数名标注它验收哪一条（AC1..AC7）。全部用 mock adapter，不触网。
"""

from __future__ import annotations

import os

import pytest

from karvyloop.gateway import (
    Done,
    GatewayClient,
    ModelRegistry,
    MockAdapter,
    ResolveScope,
    SystemPrompt,
    TextDelta,
    UnknownModelError,
    Usage,
)


def _cfg(default_chat="anthropic/claude-opus", default_emb="ollama/bge-m3", dup=False):
    models = [
        {"id": "anthropic/claude-opus", "api": "anthropic-messages",
         "context_window": 200000, "max_tokens": 64000, "cost": {"input": 15, "output": 75}},
    ]
    if dup:
        models.append({"id": "anthropic/claude-opus", "api": "anthropic-messages",
                       "context_window": 1, "max_tokens": 1})
    return {
        "models": {"providers": {
            "anthropic": {"base_url": "https://api.anthropic.com", "api_key": "${TEST_KEY}",
                          "models": models},
            "ollama": {"base_url": "http://localhost:11434",
                       "models": [{"id": "ollama/bge-m3", "api": "ollama", "role": "embedding",
                                   "context_window": 8192, "max_tokens": 0}]},
        }},
        "agents": {"defaults": {"model": default_chat}},
        "embedding": {"model": default_emb},
    }


# ---- AC1：load 重复 id 报错；default 不在 models 报错 ----
def test_ac1_duplicate_id_raises():
    with pytest.raises(ValueError, match="duplicate model id"):
        ModelRegistry.from_config(_cfg(dup=True))

def test_ac1_default_not_in_models_raises():
    with pytest.raises(ValueError, match="不在 models"):
        ModelRegistry.from_config(_cfg(default_chat="nope/x"))


# ---- AC2：resolve 层叠（原子>角色>域>default；查不到报错）----
def test_ac2_resolve_cascade():
    reg = ModelRegistry.from_config(_cfg())
    g = GatewayClient(reg, adapters={})
    # 全空 → default
    assert g.resolve_model(ResolveScope()) == "anthropic/claude-opus"
    # 域填了 → 域（但需存在；用 default 同一个）
    assert g.resolve_model(ResolveScope(domain_model="anthropic/claude-opus")) == "anthropic/claude-opus"
    # 原子优先于角色/域
    reg.models["anthropic/claude-opus"]  # exists
    # 填了但查不到 → UnknownModelError（fail-closed）
    with pytest.raises(UnknownModelError):
        g.resolve_model(ResolveScope(atom_model="ghost/model"))

def test_ac2_atom_beats_role():
    cfg = _cfg()
    cfg["models"]["providers"]["anthropic"]["models"].append(
        {"id": "anthropic/claude-haiku", "api": "anthropic-messages",
         "context_window": 200000, "max_tokens": 8000})
    reg = ModelRegistry.from_config(cfg)
    g = GatewayClient(reg, adapters={})
    assert g.resolve_model(ResolveScope(atom_model="anthropic/claude-haiku",
                                        role_model="anthropic/claude-opus")) == "anthropic/claude-haiku"


# ---- AC3：三种 api 方言都产出统一 Event 流（mock 测）----
@pytest.mark.asyncio
async def test_ac3_unified_events_three_dialects():
    cfg = {
        "models": {"providers": {"p": {"base_url": "x", "models": [
            {"id": "p/a", "api": "anthropic-messages", "context_window": 1000, "max_tokens": 100},
            {"id": "p/o", "api": "openai-completions", "context_window": 1000, "max_tokens": 100},
            {"id": "p/g", "api": "google-generative-ai", "context_window": 1000, "max_tokens": 100},
        ]}}},
        "agents": {"defaults": {"model": "p/a"}},
        "embedding": {"model": "p/a"},  # 仅为通过校验
    }
    reg = ModelRegistry.from_config(cfg)
    adapters = {api: MockAdapter(api=api,
                                 script=[TextDelta("hi"), Usage(input_tokens=2, output_tokens=1),
                                         Done("end_turn")])
                for api in ("anthropic-messages", "openai-completions", "google-generative-ai")}
    g = GatewayClient(reg, adapters=adapters)
    for ref in ("p/a", "p/o", "p/g"):
        evs = [e async for e in g.complete([{"role": "user", "content": "x"}], [], ref)]
        kinds = [type(e).__name__ for e in evs]
        assert kinds == ["TextDelta", "Usage", "Done"], f"{ref}: {kinds}"


# ---- AC4：用非 embedding 模型 embed → 断言失败 ----
@pytest.mark.asyncio
async def test_ac4_embed_non_embedding_asserts():
    reg = ModelRegistry.from_config(_cfg())
    g = GatewayClient(reg, adapters={"anthropic-messages": MockAdapter("anthropic-messages")})
    with pytest.raises(AssertionError, match="不是 embedding"):
        await g.embed("hello", model_ref="anthropic/claude-opus")

@pytest.mark.asyncio
async def test_ac4_embed_default_slot_ok():
    reg = ModelRegistry.from_config(_cfg())
    g = GatewayClient(reg, adapters={"ollama": MockAdapter("ollama")})
    vec = await g.embed("hello")          # 默认 embedding 槽位
    assert isinstance(vec, list) and len(vec) == 8


# ---- AC5：密钥不出现在任何 Event / cost 记录里 ----
@pytest.mark.asyncio
async def test_ac5_secret_not_leaked():
    os.environ["TEST_KEY"] = "sk-secret-DO-NOT-LEAK"
    reg = ModelRegistry.from_config(_cfg())
    assert reg.providers["anthropic"].api_key == "sk-secret-DO-NOT-LEAK"  # 已展开
    g = GatewayClient(reg, adapters={"anthropic-messages": MockAdapter("anthropic-messages")})
    evs = [e async for e in g.complete([{"role": "user", "content": "x"}], [], "anthropic/claude-opus")]
    blob = repr(evs) + repr(g.cost.totals)
    assert "sk-secret-DO-NOT-LEAK" not in blob


# ---- AC6：system 静态段多轮字节稳定 + 静态前缀带 cache_control ----
def test_ac6_system_static_stable_and_cached():
    sp = SystemPrompt(static=["你是 KarvyLoop 的 coding 原子。", "规则：先读后写。"],
                      dynamic=["cwd=/tmp", "git=main"])
    b1 = sp.to_blocks()
    b2 = SystemPrompt(static=list(sp.static), dynamic=["cwd=/other"]).to_blocks()
    # 静态块字节稳定（与动态段无关）
    assert b1[:2] == b2[:2]
    # 静态前缀末块带 ephemeral 缓存断点
    assert b1[1]["cache_control"] == {"type": "ephemeral"}
    # 动态段不带缓存
    assert "cache_control" not in b1[2]


# ---- AC7：Usage → cost 正确累加 ----
@pytest.mark.asyncio
async def test_ac7_cost_accounting():
    reg = ModelRegistry.from_config(_cfg())
    g = GatewayClient(reg, adapters={"anthropic-messages": MockAdapter(
        "anthropic-messages", script=[Usage(input_tokens=1_000_000, output_tokens=1_000_000)])})
    _ = [e async for e in g.complete([{"role": "user", "content": "x"}], [], "anthropic/claude-opus")]
    # cost: input 15 + output 75 = 90 USD/百万；各 1e6 token → 15 + 75 = 90
    assert g.cost.totals["anthropic/claude-opus"] == pytest.approx(90.0)
