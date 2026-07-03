"""模型注册表（#1 §3.1 / #7 §1）。

设计原则（用户拍板）：模型「定义 + 密钥」只活在**全局注册表**（config.yaml）；
agent（AtomSpec/RoleSpec/DomainManifest）只持一个 `model` **引用串**
（"<provider>/<model-id>"），不内嵌完整配置——这样镜像保持可分享而不泄密（#0 §2.1）。

数据模型蓝本 = 业界 model-definition config（只借数据模型，clean-room 重写）。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from ._base import Schema

# API 方言：一个网关说话给多家 provider 的关键抽象。
ModelApi = Literal[
    "anthropic-messages",
    "openai-completions",
    "openai-responses",
    "google-generative-ai",
    "ollama",
    "bedrock-converse",
]

# 模型槽位：chat 走软默认层叠（per-agent）；embedding 是独立全局槽位（给 #4 记忆用）。
ModelRole = Literal["chat", "embedding"]

InputModality = Literal["text", "image", "audio", "video"]

ProviderAuthMode = Literal["api-key", "oauth", "aws-sdk", "token"]


class ModelDefinition(Schema):
    """全局模型注册表的一条：能力 / 成本 / 兼容性元数据。"""

    id: str  # "<provider>/<model-id>"，全局唯一引用键
    name: str
    api: ModelApi
    role: ModelRole = "chat"
    reasoning: bool = False
    # 推理强度落参表(配置驱动,gateway/reasoning.py):档位(fast|balanced|deep)→ 原样 merge 进
    # 请求体的参数 dict。例 anthropic-messages: {deep: {thinking: {type: enabled, budget_tokens: 4096}}};
    # openai-completions: {deep: {reasoning_effort: high}}。缺省 {} = 用 api 方言内置映射
    # (仅 reasoning: true 的模型);内置也不会 → 忽略档位(debug 日志,不加参、不发坏请求)。
    reasoning_styles: dict = Field(default_factory=dict)
    input_modalities: list[InputModality] = Field(default_factory=lambda: ["text"])
    context_window: int
    max_tokens: int
    cost: dict = Field(default_factory=dict)  # {input,output,cache_read,cache_write} USD/百万token
    supports_tools: bool = True
    compat: dict = Field(default_factory=dict)  # 每家怪癖（thinking 格式 / tool schema profile…）


class ProviderConfig(Schema):
    """一个 provider 的全局配置。密钥只在这里，绝不进可分享的镜像（AtomSpec 等）。"""

    name: str
    base_url: str
    api_key: Optional[str] = None
    auth: ProviderAuthMode = "api-key"
    # 鉴权 header 名 — 大多数 Anthropic 兼容端点用 x-api-key,
    # MiniMax/部分网关用 Authorization: Bearer。默认 x-api-key(原生 Anthropic 习惯)。
    auth_header: Literal["x-api-key", "Authorization"] = "x-api-key"
    # messages 路径 — 原生 Anthropic 是 /v1/messages, MiniMax 兼容端点是 /anthropic/v1/messages。
    # 默认 /v1/messages 保持原生 Anthropic 习惯不变。
    messages_path: str = "/v1/messages"
    # 额外静态请求头 — 让"奇怪但合法"的端点也只靠配置接入,不用改代码。
    # 例:Kimi For Coding(api.kimi.com/coding/v1)按 User-Agent 放行编码 agent,
    # 配 {"User-Agent": "claude-code/1.0.0"} 即可过门。密钥仍走 api_key,绝不放这里。
    extra_headers: dict[str, str] = Field(default_factory=dict)
    models: list[ModelDefinition] = Field(default_factory=list)
