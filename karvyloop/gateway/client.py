"""GatewayClient（gateway/client.py）。

对外门面：resolve_model（软默认层叠）/ complete（按 api 方言 dispatch，统一 Event 流）/
embed（embedding 槽位）。adapters 可注入（测试用 mock，不触网）。规格：docs/modules/gateway.md §3。
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from .cost import CostMeter
from .events import Event, Usage
from .providers import default_adapters
from .providers.base import ProviderAdapter, UnsupportedApiError
from .registry import ModelRegistry
from .resolve import ResolveScope, resolve_model
from .system import SystemPrompt


class ContextCeilingError(Exception):
    """组装后的上下文超模型硬窗口 —— 网关咽喉 fail-loud 拒发(不打注定 4xx/被静默截断的请求)。"""


def _estimate_tokens(messages: list[dict], system: Optional[SystemPrompt]) -> int:
    """粗估请求 token(house 口径 len//4,与 context.budget 一致)。**不跨层 import**
    (gateway 是底层,不该反向依赖 context)—— 只做确定性地板判定,精度由预留裕度吸收。"""
    chars = 0
    for msg in messages or []:
        c = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(c, str):
            chars += len(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    chars += len(str(b.get("text", ""))) + len(str(b.get("content", ""))) + len(str(b.get("input", "")))
                else:
                    chars += len(str(b))
        elif c is not None:
            chars += len(str(c))
    if system is not None:
        try:
            chars += sum(len(s) for s in (system.static or [])) + sum(len(s) for s in (system.dynamic or []))
        except Exception:
            pass
    return chars // 4


class GatewayClient:
    def __init__(self, reg: ModelRegistry,
                 adapters: Optional[dict[str, ProviderAdapter]] = None):
        self.reg = reg
        self.cost = CostMeter()
        self._adapters = adapters if adapters is not None else default_adapters()

    def _adapter(self, api: str) -> ProviderAdapter:
        a = self._adapters.get(api)
        if a is None:
            raise UnsupportedApiError(api)
        return a

    def resolve_model(self, scope: ResolveScope) -> str:
        return resolve_model(scope, self.reg)

    async def complete(self, messages: list[dict], tools: list[dict],
                       model_ref: str, *, system: Optional[SystemPrompt] = None
                       ) -> AsyncIterator[Event]:
        m = self.reg.get(model_ref)
        prov = self.reg.provider_of(model_ref)
        adapter = self._adapter(m.api)
        # 确定性超限地板(唯一咽喉):任何直连 LLM 调用(含跳过 govern() 的 4 处治理缺口 ——
        # 导入拆解/模糊调度/ops/圆桌 goal)在这里兜底。组装后上下文超模型硬窗口 → fail-loud 拒发,
        # 不把注定 4xx / 被静默截断的请求打出去。这是"安全是地基"在上下文维度的兜底:
        # govern() 是每调用点的软压缩,漏了的由此硬兜。cw<=0(未知窗口)→ 不判(0 回归)。
        cw = getattr(m, "context_window", 0) or 0
        reserve = (getattr(m, "max_tokens", 0) or 0) + 2_000  # 留输出空间 + 安全裕度
        threshold = cw - reserve
        # cw<=0(未知窗口)或 threshold<=0(窗口比预留还小 = 退化/测试桩模型)→ 不判(0 回归)。
        if cw > 0 and threshold > 0:
            used = _estimate_tokens(messages, system)
            if used > threshold:
                raise ContextCeilingError(
                    f"上下文超模型「{m.id}」硬窗口:约 {used} tok > {cw - reserve}(窗口 {cw} − 预留 {reserve})"
                    f"——请先 govern()/compact 再调,网关拒发注定失败的请求。"
                )
        async for ev in adapter.complete(messages, tools, m, prov, system=system):
            self.cost.account(ev, m)        # 成本计量（密钥不经手 Event）
            # token 账本:gateway.complete 是**所有直连 LLM 调用**的唯一咽喉(导入拆解/模糊调度/
            # ops/圆桌goal…)—— 这些不走 forge,原来全漏记(实测导入 68 次真调用账本记 0、
            # by_source 单一 forge)。在此按 contextvar source 记一次。forge 走 executor 自己记
            # (另一条路径),不在这条上,故不重复计。
            if isinstance(ev, Usage):
                try:
                    from karvyloop.llm.token_ledger import record as _rec
                    _rec(model=m.id, input=ev.input_tokens, output=ev.output_tokens,
                         cache_read=ev.cache_read, cache_write=ev.cache_write)
                except Exception:
                    pass
            yield ev

    async def embed(self, text: str, *, model_ref: Optional[str] = None) -> list[float]:
        ref = model_ref or self.reg.default_embedding
        m = self.reg.get(ref)
        assert m.role == "embedding", f"{ref} 不是 embedding 槽位（role={m.role}）"
        prov = self.reg.provider_of(ref)
        return await self._adapter(m.api).embed(text, m, prov)
