"""minimax-cn profile (profiles/minimax_cn.py)。

M3+ 批 8.5 修问题 1:把 minimax 的国内版(中国 MiniMax, platform.minimaxi.com)
拆成独立 profile,与海外版(platform.minimax.io)区分。

**借** hermes `plugins/model-providers/minimax/__init__.py:72-80` 的"2 个 profile 同协议不同 baseUrl"模式:
  - minimax       (海外):https://api.minimax.io/anthropic,env=MiniMax_API_KEY
  - minimax-cn    (国内):https://api.minimaxi.com/anthropic,env=MINIMAX_CN_API_KEY
  - aliases: minimax-china, minimax_cn, minicn 都钩到 minimax-cn

**不借**:
  ❌ openclaw extensions/minimax/oauth.ts (CLAUDE.md 0.1.0 禁 OAuth)
  ❌ hermes 的 MiniMaxProfile.build_api_kwargs_extras 写 M3 reasoning 逻辑
     (那是 minimax-M3 模型特定,KarvyLoop 0.1.0 不深入模型层;留 P1 排队)
  ❌ hermes minimax_oauth 那个 profile (OAuth 路径整个 0.1.0 不上)

Why: 用户实测"wizard 没分 cn / global"。openclaw + hermes 都用"2 个 profile"
模式(openclaw 走 region=cn|global dispatch,hermes 直接拆 2 个常量),
2 个 profile 比"1 个 profile + 动态 env override"简单 10 倍 — wizard 能直接
列 2 个 vendor,user 选哪个用哪个,不需 import `minimax-chat` 这种领域术语。
"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_ANTHROPIC_MESSAGES,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="minimax-cn",
    aliases=("minimax-china", "minimax_cn", "minicn"),
    api_mode=API_MODE_ANTHROPIC_MESSAGES,
    base_url="https://api.minimaxi.com/anthropic",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("MINIMAX_CN_API_KEY",),
    default_model="minimax/MiniMax-M3",
    fallback_models=("minimax/MiniMax-M3",),
    description="MiniMax 中国版(anthropic-messages 兼容,需 MINIMAX_CN_API_KEY)",
)
register(profile)
