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


def main_loop_absence(app: Any) -> dict:
    """main_loop=None 时诚实说清**到底为什么**(UX 修:页面能开、发消息才报一句误导的
    "请先 karvyloop init")。返回 {"code","text"};main_loop 在位 → {"code":"","text":""}。

    四种缺席(互斥,按优先级判):
    - no_llm    : 用户显式 --no-llm 只读模式(不是坏,是他主动选的)。
    - build_failed: config 在但 build_main_loop 抛异常(引擎真坏)→ 端出真原因。
    - needs_init: 根本没 config.yaml(纯新机器)→ 这时"请先 karvyloop init"才是对的。
    - needs_setup: config 在、无构造错,但没可用模型/Key(被删 / env 没设)→ 去设置里补。
    """
    from karvyloop.i18n import t
    if getattr(app.state, "main_loop", None) is not None:
        return {"code": "", "text": ""}
    if bool(getattr(app.state, "no_llm", False)):
        return {"code": "no_llm", "text": t("setup.absent.no_llm")}
    build_error = getattr(app.state, "build_error", None)
    if build_error:
        return {"code": "build_failed",
                "text": t("setup.absent.build_failed", reason=str(build_error))}
    cfg_path = getattr(app.state, "config_path", None)
    try:
        from pathlib import Path
        exists = bool(cfg_path and Path(cfg_path).exists())
    except Exception:
        exists = False
    if not exists:
        return {"code": "needs_init", "text": t("setup.absent.needs_init")}
    return {"code": "needs_setup", "text": t("setup.absent.needs_setup")}


def setup_status(app: Any) -> dict:
    """给 /api/setup_status 用:综合 no_llm 显式模式 + registry 就绪。"""
    no_llm = bool(getattr(app.state, "no_llm", False))
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    reg = getattr(gw, "reg", None) if gw is not None else None
    ready, reason = is_ready(reg)
    absence = main_loop_absence(app)   # UX 诚实修:前端据此置灰聊天框 + 显真原因 banner
    return {
        "ready": ready,
        "reason": reason,
        "no_llm_mode": no_llm,        # 用户显式 --no-llm:网页不强制引导(是他主动选的只读模式)
        # 网页据此决定:not ready 且 not no_llm_mode → 强制录入模型
        "must_setup": (not ready) and (not no_llm),
        # main_loop 缺席(发消息会撞 stub)→ 前端把聊天框置灰、显 absence_text(诚实文案)
        "absent": bool(absence["code"]),
        "absence_code": absence["code"],
        "absence_text": absence["text"],
    }


__all__ = ["is_ready", "setup_status", "main_loop_absence", "LOCAL_PROVIDERS"]
