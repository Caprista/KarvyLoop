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
