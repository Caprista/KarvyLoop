"""gateway/reasoning — 推理强度档位(快答/深想)→ provider 请求参数(Hardy 碎碎念⑩)。

语义("接新模型=配置不是代码"纪律,全部配置驱动):
- **全局档**:config.yaml `agents.defaults.reasoning: fast|balanced|deep`。
  缺省 = 空 = **不注入任何参数**(零回归:没配这行的老配置行为一字不变)。
- **运行时覆盖**:`gateway.complete(..., reasoning=<档>)`;`reasoning=None` = 继承全局。
  (role/任务级覆盖的接口参数就是它;UI 不在本件范围。)
- **每模型覆盖**:模型条目 `reasoning_styles: {<档>: {<参数>}}` → 该档参数**原样** merge 进
  请求体(怪端点连推理落参也只靠配置,不改代码);`<档>: {}` = 该档显式不加参。
- **内置映射**(模型没写 reasoning_styles 且声明了 `reasoning: true` 时):
    anthropic-messages → `thinking: {type: enabled, budget_tokens}`(deep=max_tokens//2,
    balanced=max_tokens//4,fast=不开 thinking;budget 不足协议下限 1024 → 不注入);
    openai-completions → `reasoning_effort`(deep=high / balanced=medium / fast=low)。
- **都不支持** → 优雅忽略 + debug 日志(不刷 warning,不发坏请求)。

**只产参数,绝不碰记账**:Usage 事件 / token 账本路径一字不动(咽喉纪律)。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

REASONING_LEVELS = ("fast", "balanced", "deep")

_OPENAI_EFFORT = {"fast": "low", "balanced": "medium", "deep": "high"}
_MIN_THINKING_BUDGET = 1024   # Anthropic Messages thinking.budget_tokens 协议下限


def reasoning_params(level: str, model) -> dict:
    """档位 → 请求体注入参数(merge 进 body 顶层)。不认识/不支持 → {}(debug 日志)。"""
    if not level:
        return {}
    if level not in REASONING_LEVELS:
        log.debug("reasoning 档位 %r 不在 %s,忽略(不加参)", level, REASONING_LEVELS)
        return {}
    # 1) 每模型覆盖优先(配置说了算,含 "该档显式不加参" 的空 dict)
    styles = getattr(model, "reasoning_styles", None) or {}
    if level in styles:
        v = styles.get(level)
        if isinstance(v, dict):
            return dict(v)
        log.debug("模型 %s reasoning_styles.%s 不是 dict(%r),忽略",
                  getattr(model, "id", "?"), level, type(v).__name__)
        return {}
    # 2) 内置映射:仅对声明了 reasoning 支持的模型(乱注 thinking 给不支持的端点 = 白送 4xx)
    if not getattr(model, "reasoning", False):
        log.debug("模型 %s 未声明 reasoning 支持且无 reasoning_styles,档位 %s 忽略",
                  getattr(model, "id", "?"), level)
        return {}
    api = getattr(model, "api", "")
    if api == "anthropic-messages":
        if level == "fast":
            return {}   # 不开 thinking = 最快;要显式关请用 reasoning_styles.fast 覆盖
        mt = int(getattr(model, "max_tokens", 0) or 0)
        budget = mt // 2 if level == "deep" else mt // 4
        if budget < _MIN_THINKING_BUDGET:
            log.debug("模型 %s max_tokens=%s → thinking budget %s 低于协议下限 %s,档位 %s 忽略",
                      getattr(model, "id", "?"), mt, budget, _MIN_THINKING_BUDGET, level)
            return {}
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}
    if api == "openai-completions":
        return {"reasoning_effort": _OPENAI_EFFORT[level]}
    log.debug("api 方言 %r 无内置推理档位映射,档位 %s 忽略", api, level)
    return {}


__all__ = ["REASONING_LEVELS", "reasoning_params"]
