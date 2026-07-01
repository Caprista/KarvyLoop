"""LLM Shape Test(llm/shape.py)。

Q2 兑现式:CI 毫秒级 catch 协议变更。
参考 tests/test_anthropic_request_shape.py 的思路,泛化到所有 provider。

设计稿:docs/21 §3.4。
"""
from __future__ import annotations

import dataclasses
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from .config import LLMConfig
from .provider import ChatRequest, Message, create_provider


# ---------- Body Shape 锁(每 provider 一份)----------

class AnthropicBodyShape(BaseModel):
    """Anthropic body 形状(参考 docs/21 §3.4 + Anthropic spec)。"""
    model: str
    max_tokens: int
    messages: list[dict]
    system: Optional[str] = None
    tools: Optional[list[dict]] = None
    stream: Optional[bool] = None


class MiniMaxBodyShape(BaseModel):
    """MiniMax 兼容端点 body 形状(走 anthropic-messages,字段同 Anthropic)。

    注:MiniMax 不接受某些 Anthropic 字段(如 anthropic_version),这里锁
    **必**须有的字段(允许缺失)。
    """
    model: str
    max_tokens: int
    messages: list[dict]
    system: Optional[str] = None
    tools: Optional[list[dict]] = None
    stream: Optional[bool] = None


class MockBodyShape(BaseModel):
    """Mock body 形状(自定义 + 必有 mock=True 标志)。"""
    model: str
    max_tokens: int
    messages: list[dict]
    system: Optional[str] = None
    tools: Optional[list[dict]] = None
    mock: bool = True


_SHAPE_BY_TYPE = {
    "anthropic": AnthropicBodyShape,
    "minimax": MiniMaxBodyShape,
    "mock": MockBodyShape,
}


def assert_body_shape_valid(body: dict, provider_type: str) -> None:
    """断言 body 形状合法(Q2 兑现式)。

    Args:
        body: provider.serialize_request() 返回的 body。
        provider_type: "anthropic" / "minimax" / "mock"。

    Raises:
        ValueError: provider_type 不识别。
        ValidationError: 形状不匹配(pydantic 抛)。
    """
    shape_cls = _SHAPE_BY_TYPE.get(provider_type)
    if shape_cls is None:
        raise ValueError(f"未知 provider_type: {provider_type}")
    shape_cls.model_validate(body)  # pydantic 校验


# ---------- 协议字段名(锁住 spec 字段稳定性)----------

@dataclasses.dataclass(frozen=True)
class FieldNameContract:
    """一个 provider 的字段名契约(防止字段名漂移)。"""
    model_field: str                # "model"
    max_tokens_field: str           # "max_tokens"
    messages_field: str             # "messages"
    system_field: str               # "system"
    tools_field: str                # "tools"
    stream_field: Optional[str]     # "stream" / None(must not have)


_ANTHROPIC_FIELDS = FieldNameContract(
    model_field="model",
    max_tokens_field="max_tokens",
    messages_field="messages",
    system_field="system",
    tools_field="tools",
    stream_field="stream",
)

_MINIMAX_FIELDS = FieldNameContract(  # 与 Anthropic 同(走兼容端点)
    model_field="model",
    max_tokens_field="max_tokens",
    messages_field="messages",
    system_field="system",
    tools_field="tools",
    stream_field="stream",
)


def assert_field_names_stable(body: dict, provider_type: str) -> None:
    """断言字段名稳定(防止 spec 漂移)。

    这是 Q2 的硬底线:**字段名**不能变,变了 = breaking change。
    """
    if provider_type == "anthropic":
        expected = _ANTHROPIC_FIELDS
    elif provider_type == "minimax":
        expected = _MINIMAX_FIELDS
    elif provider_type == "mock":
        # mock 字段名可宽松(测试自己用),但锁 model / messages
        assert "model" in body, "mock body 缺 model"
        assert "messages" in body, "mock body 缺 messages"
        return
    else:
        raise ValueError(f"未知 provider_type: {provider_type}")

    assert expected.model_field in body, f"{provider_type} body 缺 {expected.model_field}"
    assert expected.max_tokens_field in body, f"{provider_type} body 缺 {expected.max_tokens_field}"
    assert expected.messages_field in body, f"{provider_type} body 缺 {expected.messages_field}"
    # system / tools / stream 可选


# ---------- ChatRequest 形状 ----------

class ChatRequestShape(BaseModel):
    """ChatRequest 形状(跨 provider 锁)。"""
    model: str
    messages: list[dict]
    tools: list[dict] = Field(default_factory=list)
    system: str = ""
    max_tokens: int = 1024
    stream: bool = False


def assert_chat_request_shape(request: ChatRequest) -> None:
    """断言 ChatRequest 形状合法。"""
    ChatRequestShape.model_validate(dataclasses.asdict(request))
