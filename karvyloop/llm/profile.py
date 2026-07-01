"""LLM Provider Profile 数据模型（llm/profile.py）。

M3+ 批 8.5 引入：协议-供应商两层解耦。

设计蓝本（参照业界成熟 agent 的做法，clean-room 重写、只借数据模型不抄运行时）：
  - ProviderProfile dataclass
    （name / api_mode / env_vars / base_url / auth_type / fallback_models）
  - 协议字符串作 join key

**借**（业界 ProviderProfile 抽象思想）：
  ✅ 协议字符串 join key（api_mode 字段）
  ✅ tuple-of-env-vars 兜底链
  ✅ per-vendor profile 文件组织
  ✅ alias map（短名 → 主名）

**不借**（那种 plugin discovery filesystem dance + 用户插件层目录 —— 0.1.0 用不上）：
  ❌ filesystem 动态发现
  ❌ 用户插件层
  ❌ OAuth device-code 字段
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


# ---- API 模式（协议 join key；业界协议字符串方案的 KarvyLoop 简化版）----

# 开箱即用的 2 个 wire 协议（与 karvyloop.gateway 已实现 / 计划实现对齐）
API_MODE_ANTHROPIC_MESSAGES = "anthropic-messages"  # Anthropic 原生 + minimax 兼容端点
API_MODE_OPENAI_COMPLETIONS = "openai-completions"  # OpenAI / ollama / zhipu / moonshot / 等 ~16 vendor

SUPPORTED_API_MODES: Tuple[str, ...] = (
    API_MODE_ANTHROPIC_MESSAGES,
    API_MODE_OPENAI_COMPLETIONS,
)


# ---- 鉴权类型（auth_type 简化版）----

AUTH_TYPE_API_KEY_HEADER = "api-key-header"  # 自定义 header（如 x-api-key）—— Anthropic 原生
AUTH_TYPE_BEARER = "bearer"                   # Authorization: Bearer —— openai-completions 系
AUTH_TYPE_NONE = "none"                       # 本地不校验（ollama）


# ---- ProviderProfile ----

@dataclass(frozen=True)
class ProviderProfile:
    """一个 LLM 供应商的元数据（不包含密钥；密钥走 env_vars 链）。

    设计原则：
      1. 协议-供应商解耦 —— profile 只声明"我是谁、走什么协议、URL 在哪"，
         实际 wire-format 逻辑在 karvyloop/llm/transports/<api_mode>.py。
      2. 密钥零侵入 —— api_key 字段**不存在**；运行时按 env_vars 兜底链查
         os.environ（业界 api_key_env_vars tuple 模式）。
      3. 别名机制 —— aliases 是短名 → 主名（kimi → moonshot，glm → zhipu）。
      4. fallback_models 是离线默认列表（离线 fallback_models 模式）；
         运行时探测 /v1/models **不**做（P1 排队）。
    """
    # 标识
    name: str                                    # 主名（如 "anthropic" / "minimax" / "ollama"）
    api_mode: str                                # 协议 join key（API_MODE_*）

    # HTTP
    base_url: str                                # 完整 base URL（如 https://api.anthropic.com）

    # 鉴权（auth_type 简化）
    auth_type: str = AUTH_TYPE_BEARER            # 默认 bearer（openai-completions 系多）
    auth_header: str = "Authorization"           # header 名（Anthropic 用 x-api-key）

    # 密钥来源：tuple-of-env-vars 兜底链（业界通用模式）
    # 顺序遍历，第一个非空 os.environ.get() 命中即用
    # 空 tuple = 本地不校验（ollama）
    env_vars: Tuple[str, ...] = ()

    # 别名（短名 → 主名）
    aliases: Tuple[str, ...] = ()

    # 模型 catalog 离线默认值
    default_model: str = ""                      # 该 provider 的默认 chat model id
    fallback_models: Tuple[str, ...] = ()        # 离线默认模型列表（wizard 显示用）

    # 文案（wizard 显示）
    description: str = ""                        # 人类可读描述


__all__ = [
    "API_MODE_ANTHROPIC_MESSAGES",
    "API_MODE_OPENAI_COMPLETIONS",
    "SUPPORTED_API_MODES",
    "AUTH_TYPE_API_KEY_HEADER",
    "AUTH_TYPE_BEARER",
    "AUTH_TYPE_NONE",
    "ProviderProfile",
]
