"""readiness — 判断"有没有可用模型/Key",网页端与 TUI 共用。

Hardy 定的策略:启动**不强制**写 Key(resolve_runtime 缺 config 也照常起,降级);
**进系统后**判断有没有可用模型——没有就**强制引导**(网页 + TUI 一致),因为没 Key 用不了。
这同时覆盖两种情况:① 首次安装从没配过;② Key 后续被删/环境变量没设。

判定(配置级,轻量,不发真请求):
- 没有 registry(没 config / 没起 gateway)→ 未就绪(no_config)。
- 默认 chat 模型不在注册表 → 未就绪(no_default_model)。
- 该模型是**本地 provider**(ollama 等,无需真 key)→ 就绪(本地起没起是另一回事,不在此判)。
- **云端 provider**:registry 加载时已展开 ${ENV};若解析后 key 为空/占位 → 未就绪(no_key)。
"""
from __future__ import annotations

from typing import Any

# 本地 provider:不需要真实云端 Key(local-first 默认)
LOCAL_PROVIDERS = {"ollama", "llamacpp", "lmstudio", "vllm-local"}
# 明显是占位、不算真 Key 的值
_PLACEHOLDER_KEYS = {"", "dummy", "changeme", "your-key", "xxx", "todo"}


def is_ready(reg: Any) -> tuple[bool, str]:
    """返回 (就绪?, 原因码)。原因码:'' / no_config / no_default_model / no_key / error:..."""
    if reg is None:
        return False, "no_config"
    try:
        dc = getattr(reg, "default_chat", "") or ""
        models = getattr(reg, "models", {}) or {}
        if not dc or dc not in models:
            return False, "no_default_model"
        provider_name = dc.split("/", 1)[0]
        if provider_name in LOCAL_PROVIDERS:
            return True, ""           # 本地默认 → 视为已配(起没起 ollama 是运行时另说)
        prov = reg.provider_of(dc)
        key = (getattr(prov, "api_key", "") or "").strip()
        if key.lower() in _PLACEHOLDER_KEYS:
            return False, "no_key"    # 云端但 key 空/占位(没配 or 被删 or env 没设)
        return True, ""
    except Exception as e:            # 任何异常 → 保守判未就绪(宁可引导,不静默不可用)
        return False, f"error:{e}"


def setup_status(app: Any) -> dict:
    """给 /api/setup_status 用:综合 no_llm 显式模式 + registry 就绪。"""
    no_llm = bool(getattr(app.state, "no_llm", False))
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    reg = getattr(gw, "reg", None) if gw is not None else None
    ready, reason = is_ready(reg)
    return {
        "ready": ready,
        "reason": reason,
        "no_llm_mode": no_llm,        # 用户显式 --no-llm:网页不强制引导(是他主动选的只读模式)
        # 网页据此决定:not ready 且 not no_llm_mode → 强制录入模型
        "must_setup": (not ready) and (not no_llm),
    }


__all__ = ["is_ready", "setup_status", "LOCAL_PROVIDERS"]
