"""result_classifier — #2 §13.3:语义判一个任务的结果**能不能缓存回放**。

Hardy 决定:这事**没法写成硬规则**(联网查历史数据可复用、查实时数据必重算)→ 让模型判。
- 返回 "stable":结果语义稳定、与时间/实时/外部状态无关、确定性、无副作用 → 可回放(罕见)。
- 返回 "dynamic"(默认/兜底):结果会变(实时/搜索/diff/外部状态/时间相关)→ 只存方法、命中重跑。

铁律:**拿不准一律 dynamic**(宁重跑不投毒)。任何异常 / 空输出 / 解析不出 → dynamic。
成本:仅在**结晶时**调一次(结晶很稀疏),提示极短、只要一个词。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_SYS = (
    "你判断一个任务的**结果是否语义稳定、可被缓存复用**。\n"
    "stable = 结果与时间/实时/外部状态无关、确定性、可复现(如固定换算、固定事实、纯文本变换)。\n"
    "dynamic = 结果会变:联网搜索/查实时或最新数据/比对会变的文件/依赖外部状态/与时间相关。\n"
    "**只输出一个词:stable 或 dynamic。拿不准就输出 dynamic。**"
)


def make_result_classifier(gateway: Any, model_ref: str = "") -> Optional[Callable[[str, str, list], str]]:
    """造一个同步判定器闭包(intent, answer, tool_calls)→ 'stable'|'dynamic'。

    gateway 为空 → 返回 None(MainLoop 收到 None 即默认 dynamic)。
    判定器内部用 asyncio.run 调 gateway(结晶在 worker 线程同步上下文跑,无运行中 loop,合法)。
    """
    if gateway is None:
        return None

    def classify(intent: str, answer: str, tool_calls: list) -> str:
        import asyncio
        from karvyloop.gateway import ResolveScope
        from karvyloop.gateway.system import SystemPrompt
        # 强信号兜底:用过联网/搜索类工具 → 直接 dynamic(不浪费一次 LLM 调用,也更稳)
        names = " ".join(
            (t.get("name", "") if isinstance(t, dict) else getattr(t, "name", "")) for t in (tool_calls or [])
        ).lower()
        if any(k in names for k in ("web_search", "web_fetch", "search", "fetch", "http")):
            return "dynamic"
        usr = f"任务:{(intent or '')[:300]}"
        out = ""

        async def _go():
            nonlocal out
            ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
            async for ev in gateway.complete([{"role": "user", "content": usr}], [], ref,
                                             system=SystemPrompt(static=[_SYS])):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
        try:
            asyncio.run(_go())
        except Exception as e:
            logger.warning(f"[result_classifier] 判定失败,默认 dynamic: {e}")
            return "dynamic"
        return "stable" if "stable" in (out or "").strip().lower() else "dynamic"

    return classify


__all__ = ["make_result_classifier"]
