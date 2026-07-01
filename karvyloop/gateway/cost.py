"""成本计量（gateway/cost.py）。按 ModelDefinition.cost（USD/百万 token）累加。"""

from __future__ import annotations

from karvyloop.schemas import ModelDefinition

from .events import Event, Usage


class CostMeter:
    def __init__(self) -> None:
        self.totals: dict[str, float] = {}     # model_id -> 累计 USD

    def account(self, ev: Event, model: ModelDefinition) -> None:
        if not isinstance(ev, Usage):
            return
        c = model.cost or {}
        usd = (
            ev.input_tokens * c.get("input", 0)
            + ev.output_tokens * c.get("output", 0)
            + ev.cache_read * c.get("cache_read", 0)
            + ev.cache_write * c.get("cache_write", 0)
        ) / 1_000_000
        self.totals[model.id] = self.totals.get(model.id, 0.0) + usd
