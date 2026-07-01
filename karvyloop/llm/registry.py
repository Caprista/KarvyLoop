"""LLM Provider Registry（llm/registry.py）。

M3+ 批 8.5 引入：注册表 + 别名 + 自动加载 profiles/。

设计蓝本（业界一致的 Map registry 模式）：
  - _REGISTRY + _ALIASES 双 map
  - Map<api, ApiProvider> 形式的协议注册表

**借**：
  ✅ 简单 dict + 别名 map
  ✅ get(name) 走 alias 解析
  ✅ list_profiles() 返回排序后的 list

**不借**：
  ❌ 文件系统动态发现（那种 importlib 动态发现 dance）
  ❌ sourceId 作用域（插件热卸载）
  ❌ profile 自动扩展 auth registry（双注册表）

Why: 0.1.0 单用户场景，profile 集合是静态的（16 个 vendor），
     写死 import profiles/<vendor> 比动态 import 简单 10 倍。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from .profile import ProviderProfile


# ---- 内部状态 ----

_REGISTRY: Dict[str, ProviderProfile] = {}     # name → profile
_ALIASES: Dict[str, str] = {}                   # alias → canonical name


# ---- 注册 / 查询 API ----

def register(profile: ProviderProfile) -> None:
    """注册一个 provider profile（idempotent：同 name 重复注册会覆盖）。"""
    if not profile.name:
        raise ValueError("profile.name 不能为空")
    if profile.api_mode not in ("anthropic-messages", "openai-completions"):
        raise ValueError(
            f"profile '{profile.name}' 的 api_mode='{profile.api_mode}' "
            f"不在 KarvyLoop 0.1.0 支持的协议列表: ['anthropic-messages', 'openai-completions']"
        )
    _REGISTRY[profile.name] = profile
    for alias in profile.aliases:
        _ALIASES[alias.lower()] = profile.name


def get(name: str) -> Optional[ProviderProfile]:
    """按主名或别名查 profile（找不到返 None）。"""
    if not name:
        return None
    if name in _REGISTRY:
        return _REGISTRY[name]
    canonical = _ALIASES.get(name.lower())
    if canonical:
        return _REGISTRY.get(canonical)
    return None


def require(name: str) -> ProviderProfile:
    """按主名或别名查 profile（找不到 raise KeyError）。"""
    p = get(name)
    if p is None:
        raise KeyError(
            f"provider '{name}' 不在 registry。"
            f"已注册: {sorted(_REGISTRY.keys())} (含别名 {sorted(_ALIASES.keys())})"
        )
    return p


def list_profiles() -> List[ProviderProfile]:
    """返回所有已注册 profile（按 name 排序）。"""
    return sorted(_REGISTRY.values(), key=lambda p: p.name)


def list_names() -> List[str]:
    """返回所有已注册主名 + 别名（wizard 提示用）。"""
    return sorted(_REGISTRY.keys())


def resolve_api_key(profile: ProviderProfile) -> str:
    """按 env_vars tuple 兜底链查 api key（第一个非空命中）。

    设计：业界 get_anthropic_key 思路的 KarvyLoop 简化版。
    本地（auth_type=none）返空字符串。
    """
    if profile.auth_type == "none" or not profile.env_vars:
        return ""
    for var in profile.env_vars:
        v = os.environ.get(var, "")
        if v:
            return v
    return ""


def clear() -> None:
    """测试用 —— 清空 registry（不删 _ALIASES，避免测试间污染）。"""
    _REGISTRY.clear()
    _ALIASES.clear()


# ---- 自动加载（启动时调一次） ----

_AUTO_LOADED = False

def ensure_loaded() -> None:
    """首次调用时自动 import karvyloop.llm.profiles 包（注册 16 个 vendor）。"""
    global _AUTO_LOADED
    if _AUTO_LOADED:
        return
    # import side-effect：profiles/__init__.py 内部逐个 import 16 个 vendor
    from . import profiles  # noqa: F401
    _AUTO_LOADED = True


__all__ = [
    "register",
    "get",
    "require",
    "list_profiles",
    "list_names",
    "resolve_api_key",
    "clear",
    "ensure_loaded",
]
