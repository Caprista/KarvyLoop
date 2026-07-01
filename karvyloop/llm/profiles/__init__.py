"""LLM Provider Profiles (llm/profiles/__init__.py)。

M3+ 批 8.5 引入：自动加载 16 个 vendor profile。

组织方式（per-vendor 一个 .py 文件）：
  - 每个文件就 1 个 register() 调用
  - 16 个 vendor × ~15 行 = 240 行（vs 1 个 1000 行 god file，可读性 +10）
  - 加新 vendor = 加 1 个 .py（不动其他文件；registry 自动 pick up）

不抄那种 plugin discovery filesystem dance（内部 0.1.0 拒绝清单）：
  ❌ 不用 importlib 扫目录
  ❌ 不用用户插件层目录
  ❌ 不用 profile.yaml 配置文件
  - 直接 import 列表，简单且显式

vendors 列表（M3+ 批 8.5 16 个）：
  本地：ollama
  Anthropic 系：anthropic, minimax
  OpenAI-compat 系（14 个）：openai, deepseek, zhipu, moonshot, kimi, qwen, gemini,
                              groq, mistral, xai, openrouter, together, fireworks
"""
from __future__ import annotations

# ---- 显式 import 列表（**不**用 importlib 扫目录）----

# 本地
from . import ollama  # noqa: F401

# Anthropic 系
from . import anthropic  # noqa: F401
from . import minimax  # noqa: F401
from . import minimax_cn  # noqa: F401  # M3+ 批 8.5 修问题 1:cn/global 拆分

# OpenAI-compat 系（按字母序）
from . import deepseek  # noqa: F401
from . import fireworks  # noqa: F401
from . import gemini  # noqa: F401
from . import groq  # noqa: F401
from . import kimi  # noqa: F401
from . import mistral  # noqa: F401
from . import moonshot  # noqa: F401
from . import openai  # noqa: F401
from . import openrouter  # noqa: F401
from . import qwen  # noqa: F401
from . import together  # noqa: F401
from . import xai  # noqa: F401
from . import zhipu  # noqa: F401


__all__: list[str] = []  # profiles/<vendor>.py 各自 module-level register；不 re-export
