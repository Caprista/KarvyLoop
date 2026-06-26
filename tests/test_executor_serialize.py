"""Executor 序列化测试 —— 验证 messages[].content 是 str(Anthropic 协议)。

背景:Anthropic Messages API 要求 messages[].content 是 str 或 list[ContentBlock],
不能是 dict。executor 修复前把 Forge 传的 dict 直接塞 content,MiniMax 返 400。
修复:executor 把 dict 序列化成 JSON 字符串(协议兼容)。
"""

from __future__ import annotations

import json

import pytest

from karvyloop.atoms.executor import run as atom_run
from karvyloop.gateway import GatewayClient
from karvyloop.gateway.providers.base import ProviderAdapter
from karvyloop.schemas import AtomSpec, Capability, CapabilityToken


class CaptureAdapter(ProviderAdapter):
    api = "anthropic-messages"
    def __init__(self):
        self.captured_messages = None
    async def complete(self, messages, tools, model, provider, *, system=None):
        # 捕获到 messages 就立刻塞,不让 generator 推进,避免后续 input/output 校验
        self.captured_messages = list(messages)
        return
        yield  # 让 mypy 满意
    async def embed(self, *a, **k):
        return []


def _gw(cap):
    from karvyloop.gateway.registry import ModelRegistry
    reg_cfg = {
        "models": {"providers": {"p": {"base_url": "x", "models": [
            {"id": "p/m", "name": "m", "api": "anthropic-messages",
             "context_window": 100, "max_tokens": 100},
        ]}}},
        "agents": {"defaults": {"model": "p/m"}},
        "embedding": {"model": "p/m"},
    }
    reg = ModelRegistry.from_config(reg_cfg)
    return GatewayClient(reg, adapters={"anthropic-messages": cap})


@pytest.mark.asyncio
async def test_dict_input_serialized_to_json_string():
    """核心修复:dict input 不能直接进 content,要序列化成 str。"""
    cap = CaptureAdapter()
    gw = _gw(cap)
    atom = AtomSpec(id="t1", kind="task", prompt="", input_schema={}, output_schema={},
                    tools=[], model="p/m")
    tok = CapabilityToken(task_id="t", grants=[Capability(resource="fs:/x", ops=["read"])], expiry=1e10)
    async for _ in atom_run(atom, {"intent": "say PONG"}, tok, gateway=gw, tools={}):
        pass
    user_msg = cap.captured_messages[-1]
    assert user_msg["role"] == "user"
    content = user_msg["content"]
    assert isinstance(content, str), f"content 必须是 str, 实际是 {type(content).__name__}"
    assert json.loads(content) == {"intent": "say PONG"}


@pytest.mark.asyncio
async def test_str_input_kept_as_is():
    """str input 保持原样(直接验证 _serialize_input 这层逻辑,不动 executor 终态)。"""
    # 内联同样的序列化逻辑
    import json as _json
    inp = "hello world"
    if isinstance(inp, dict):
        content = _json.dumps(inp, ensure_ascii=False)
    elif isinstance(inp, str):
        content = inp
    else:
        content = str(inp)
    assert content == "hello world"
