"""karvyloop init wizard(M3+ 批 8 + 批 8.5)——引导式写 config.yaml。

设计:plans/snoopy-singing-sunbeam.md §批 8 + CONTEXT/03-feature-knowledge-base.md §1。

M3+ 批 8.5 改造:
  - PROVIDERS 不再硬编码 3 个,改为动态从 karvyloop.llm.registry 拉
  - 0.1.0 默认显示 16 个 vendor(本地 ollama 优先 + anthropic + minimax + 14 个 openai-compat)
  - validate_api_key 改成走 profile.env_vars 兜底链(不写死 anthropic/openai)
  - _build_config_for 改成 generic YAML 注入(按 profile 模板)

- 交互式:问 provider / API key / 写 yaml
- 错误友好:`Renderer.render_error_with_hint` 2 行格式
- 非 TTY 跳过 wizard → 写默认 config(本地 ollama 占位 + anthropic 占位 ${ANTHROPIC_API_KEY})

Why: 真用户第一次跑 karvyloop init, 99% 不读 README, 不会写 yaml;
     友好向导比"已存在覆盖 y/N"重要 100 倍。

不依赖 LLM(纯字符串提示),遵循 onboarding/__init__.py 不变量 I4(本拍不调 LLM)。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from karvyloop.llm.profile import AUTH_TYPE_NONE
from karvyloop.llm.registry import (
    ensure_loaded as ensure_profiles_loaded,
    list_profiles as list_registered_profiles,
    get as get_profile,
)
from .render import Renderer


# ---- 校验器 ----

class WizardError(Exception):
    """wizard 流程中断(用户 Ctrl-C / 错误输入)。"""


def validate_api_key(provider: str, key: str) -> Tuple[bool, str]:
    """校验 API key 格式(从 M3+ 批 8.5 走 profile 兜底)。

    规则(按 profile 动态):
      - 本地(ollama, auth_type=none): 任何非空字符串(或不需 key)
      - 通用:不能含换行 / 不能是占位符(FAKE/TODO/PLACEHOLDER)/ 长度 >= 10
      - anthropic: 'sk-ant-' 前缀(真 key 约 108 字符)
      - openai: 'sk-' 前缀(真 key 约 51 字符)
      - minimax / openai-compat 其他:通用 10 字符以上即可(各产品前缀不统一)

    Returns: (is_valid, error_message)
    """
    ensure_profiles_loaded()
    profile = get_profile(provider)
    if profile is None:
        return False, f"未知 provider: {provider!r}"

    # 本地(ollama)不真校验,只挡占位符
    if profile.auth_type == AUTH_TYPE_NONE:
        if "FAKE" in key or "TODO" in key or "PLACEHOLDER" in key:
            return False, "API key 是占位符(本地 ollama 用 'dummy' 即可)"
        return True, ""

    # 通用检查
    if not key or not key.strip():
        return False, "API key 不能为空"
    if "\n" in key or "\r" in key:
        return False, "API key 含换行(可能粘贴时混入了)"
    if "FAKE" in key or "TODO" in key or "PLACEHOLDER" in key:
        return False, "API key 是占位符(请复制真 key)"
    if len(key) < 10:
        return False, f"API key 长度 {len(key)} 太短(>= 10 字符)"
    if key != key.strip():
        return False, "API key 首尾有空格(可能多粘贴了)"

    # 特定 prefix 检查(仅对已知的)
    if profile.name == "anthropic":
        if not key.startswith("sk-ant-"):
            return False, "Anthropic key 应以 'sk-ant-' 开头"
        if len(key) < 20:
            return False, f"Anthropic key 长度 {len(key)} 太短(真 key 约 108 字符)"
    elif profile.name == "openai":
        if not key.startswith("sk-"):
            return False, "OpenAI key 应以 'sk-' 开头"
        if len(key) < 20:
            return False, f"OpenAI key 长度 {len(key)} 太短(真 key 约 51 字符)"
    # minimax / 其他 openai-compat 暂不强制 prefix(各产品不同;CLAUDE.md Q1 WebFetch
    # 失败,fallback 到通用 10 字符以上即可)

    return True, ""


# ---- provider 选择 ----

def _list_providers() -> List[Tuple[str, str]]:
    """从 registry 拉所有 vendor profile,返 [(name, description), ...] 排序。

    本地优先(ollama 排第一),然后按 api_mode 分组:
      - anthropic-messages: anthropic / minimax
      - openai-completions: 14 个 vendor
    """
    ensure_profiles_loaded()
    profiles = list_registered_profiles()
    # 本地优先(ollama 排第一);其余按 api_mode 再按 name 排
    def sort_key(p):
        is_local = 0 if p.auth_type == AUTH_TYPE_NONE else 1
        return (is_local, p.api_mode, p.name)

    return [(p.name, p.description) for p in sorted(profiles, key=sort_key)]


# 保留旧名 PROVIDERS 供测试 + 外部引用(底层是动态生成)
def _get_providers() -> List[Tuple[str, str]]:
    return _list_providers()


def _ask_provider(renderer: Renderer, out) -> str:
    """问用户选 provider(数字 / 名字)。"""
    providers = _list_providers()
    n = len(providers)

    from karvyloop.i18n import t
    out.write("\n" + t("wizard.choose_provider") + "\n")
    for i, (name, desc) in enumerate(providers, 1):
        out.write(f"  {i}) {name:<12s} — {desc}\n")
    out.write("\n" + t("wizard.choose_prompt", n=n))
    out.flush()
    try:
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        raise WizardError("用户取消")
    if not raw:
        return providers[0][0]  # 默认第一个(本地优先)
    # 数字选
    if raw.isdigit() and 1 <= int(raw) <= n:
        return providers[int(raw) - 1][0]
    # 名字 / alias 选
    for name, _ in providers:
        if raw.lower() == name:
            return name
    # 兼容 alias
    from karvyloop.llm.registry import get as get_by_name
    if get_by_name(raw) is not None:
        return get_by_name(raw).name
    from karvyloop.i18n import t
    renderer.render_error_with_hint(
        code="E_PROVIDER",
        message=t("wizard.unknown_provider", raw=repr(raw)),
        hint=t("wizard.provider_hint", n=n, names=", ".join(nm for nm, _ in providers)),
    )
    raise WizardError("provider 无效")


def _ask_api_key(provider: str, renderer: Renderer) -> Optional[str]:
    """问 API key(本地不需要,直接 None)。

    返回 key 字符串,或 None(本地 ollama / 用户跳过)。
    """
    ensure_profiles_loaded()
    profile = get_profile(provider)
    if profile is None:
        return None

    # 本地(ollama, auth_type=none)不需 key
    if profile.auth_type == AUTH_TYPE_NONE:
        return None

    # 找 env_vars(显示用户该 export 哪个)
    from karvyloop.i18n import t
    env_var = profile.env_vars[0] if profile.env_vars else f"{provider.upper()}_API_KEY"
    out = sys.stdout
    out.write("\n" + t("wizard.apikey_prompt", env_var=env_var))
    out.flush()
    try:
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        raise WizardError("用户取消")

    if not raw:
        # 用户跳过 → 写占位 ${ENV_VAR}
        out.write(t("wizard.apikey_skipped", env_var=env_var) + "\n")
        return f"${{{env_var}}}"

    ok, err = validate_api_key(provider, raw)
    if not ok:
        renderer.render_error_with_hint(
            code="E_API_KEY",
            message=t("wizard.apikey_bad", err=err),
            hint=t("wizard.apikey_hint", env_var=env_var),
        )
        raise WizardError("API key 格式错")

    return raw


# ---- 主入口 ----

def run_wizard(
    *,
    target: Path,
    renderer: Renderer,
    stdin=None,
    stdout=None,
) -> int:
    """跑 wizard 流程: provider → API key → 写 yaml。

    Returns 0 成功 / 1 失败(wizard 流程中断)。
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    # 1) provider
    provider = _ask_provider(renderer, stdout)

    # 2) API key(可选)
    api_key = _ask_api_key(provider, renderer)

    # 3) 写 yaml(基于 DEFAULT_CONFIG_YAML,替换对应字段)
    config_text = _build_config_for(provider, api_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(config_text, encoding="utf-8")

    from karvyloop.i18n import t
    stdout.write("\n" + t("wizard.written", target=target) + "\n")
    if provider == "ollama":
        stdout.write(t("wizard.next_ollama") + "\n")
    else:
        # M3+ 批 8.5 修问题 2:key 已写入 yaml,transport 走 yaml 优先,不需要再 export env
        # (旧版 wizard 提示"export X=... 再跑"是 bug,用户在 wizard 输的 key 被 transport 丢了)
        stdout.write(t("wizard.next_apikey") + "\n")
    return 0


def _build_config_for(provider: str, api_key: Optional[str]) -> str:
    """基于 DEFAULT_CONFIG_YAML 模板,生成给指定 provider 的 config。

    通用方案(M3+ 批 8.5):
      - 如果 provider 已在模板里(ollama / anthropic),替换 api_key 字段
      - 如果 provider 不在模板里(minimax / openai / 14 个其他),通用插入:
        在 providers 段末尾追加该 vendor 块

    Why: 用户选哪个 provider 就把那个 provider 放最前(本地优先原则)。
    """
    from .init import DEFAULT_CONFIG_YAML

    ensure_profiles_loaded()
    profile = get_profile(provider)
    if profile is None:
        return DEFAULT_CONFIG_YAML

    # 默认 chat 模型行(注意:只换 agents.defaults 那行,不碰 `- id:` 行)
    _DEFAULT_MODEL_LINE = "    model: ollama/qwen2.5-coder:7b"

    # 1) 已在模板里的 provider(ollama / anthropic)
    if provider == "ollama":
        return DEFAULT_CONFIG_YAML  # ollama 不需真 key,默认模型本就是 ollama
    if provider == "anthropic":
        cfg = DEFAULT_CONFIG_YAML
        if api_key and not api_key.startswith("${"):
            cfg = cfg.replace("api_key: ${ANTHROPIC_API_KEY}", f"api_key: {api_key}")
        # 修首跑:选了 anthropic 就把默认 chat 模型切过去(否则默认仍 ollama,run 用本地→没起→失败)
        cfg = cfg.replace(_DEFAULT_MODEL_LINE, "    model: anthropic/claude-sonnet-4-6")
        return cfg

    # 2) 不在模板里的 provider:按 api_mode 决定插入格式
    env_var = profile.env_vars[0] if profile.env_vars else f"{provider.upper()}_API_KEY"
    api_key_value = api_key if (api_key and not api_key.startswith("${")) else f"${{{env_var}}}"

    # 9.4 修首跑崩溃(门1 亲身踩):
    #  ① provider **块 key 必须 = model id 前缀** —— runtime `provider_of` 按 model_ref.split("/")[0]
    #     查 providers[key];若 key 用 profile.name(如 minimax-cn)而 model 是 minimax/... → KeyError 崩。
    #  ② **必须写 auth_header** —— ProviderConfig 默认 x-api-key;Bearer 系(minimax 等)漏了 → 401/500。
    model_id = profile.default_model or f"{provider}/default"
    provider_key = model_id.split("/", 1)[0] or provider
    auth_header = profile.auth_header or "x-api-key"

    if profile.api_mode == "anthropic-messages":
        # minimax 走 anthropic-messages 兼容
        new_block = f"""    {provider_key}:
      base_url: {profile.base_url}
      auth: api-key
      auth_header: {auth_header}
      api_key: {api_key_value}
      models:
        - id: {model_id}
          name: {profile.name}
          api: anthropic-messages
          context_window: 200000
          max_tokens: 8192
"""
    elif profile.api_mode == "openai-completions":
        # openai-compat 系通用
        new_block = f"""    {provider_key}:
      base_url: {profile.base_url}
      auth: api-key
      auth_header: {auth_header}
      api_key: {api_key_value}
      models:
        - id: {model_id}
          name: {profile.name}
          api: openai-completions
          context_window: 128000
          max_tokens: 4096
"""
    else:
        return DEFAULT_CONFIG_YAML

    # 插在 anthropic 块后(保持 anthropic 块可见)+ 把默认 chat 模型切到所选 provider
    cfg = DEFAULT_CONFIG_YAML
    if "    anthropic:" in cfg:
        cfg = cfg.replace("    anthropic:", new_block + "    anthropic:")
    # 修首跑:选了云 provider 就把默认 chat 模型切过去(否则默认仍 ollama → run 失败)
    cfg = cfg.replace(_DEFAULT_MODEL_LINE, f"    model: {model_id}")
    return cfg


# 旧名兼容(外部可能 import 这个常量,改成 dynamic)
PROVIDERS: List[Tuple[str, str]] = []  # 由 ensure_loaded() 时填充


def _refresh_providers_cache() -> None:
    """测试 / 外部可调:刷新 PROVIDERS 缓存。"""
    global PROVIDERS
    PROVIDERS = _list_providers()


# 启动时自动刷新
ensure_profiles_loaded()
_refresh_providers_cache()


__all__ = [
    "WizardError",
    "validate_api_key",
    "PROVIDERS",
    "run_wizard",
]
