"""test_spend_budget — 花费预算刹车(token 花费硬控制)。

覆盖:config 解析(缺省无限 / warn / pause 三态)、成本换算(有价算钱 / 无价算 token)、
三级触发(75/90 告警、100 拦截)、前台不拦后台拦(token_source 区分)、达限出卡形状、
记账零改动回归(gateway 咽喉照记不受影响)。
"""
from __future__ import annotations

import asyncio

import pytest

from karvyloop.llm.config_budget import (
    ON_LIMIT_PAUSE, ON_LIMIT_WARN, SpendBudgetConfig,
    spend_budget_config_from_dict)
from karvyloop.llm.spend_budget import (
    ACTION_ALLOW, ACTION_BLOCK, ACTION_WARN, SpendBudget, SpendBudgetExceeded,
    is_automatic_source)
from karvyloop.llm.token_ledger import TokenLedger


# ---------- config 解析:缺省无限 / warn / pause 三态 ----------

def test_config_absent_is_unlimited():
    cfg = spend_budget_config_from_dict({})
    assert not cfg.enabled
    assert not cfg.blocks_on_limit
    cfg2 = spend_budget_config_from_dict({"budget": {}})
    assert not cfg2.enabled


def test_config_warn_default_and_explicit():
    # 配了上限但没写 on_limit → 默认 warn(保守:不静默 pause)
    cfg = spend_budget_config_from_dict({"budget": {"daily_usd": 5}})
    assert cfg.enabled and cfg.on_limit == ON_LIMIT_WARN and not cfg.blocks_on_limit
    cfg2 = spend_budget_config_from_dict({"budget": {"daily_usd": 5, "on_limit": "warn"}})
    assert cfg2.on_limit == ON_LIMIT_WARN


def test_config_pause():
    cfg = spend_budget_config_from_dict(
        {"budget": {"monthly_tokens": 1000, "on_limit": "pause"}})
    assert cfg.enabled and cfg.blocks_on_limit and cfg.monthly_tokens == 1000


def test_config_invalid_on_limit_falls_back_to_warn():
    cfg = spend_budget_config_from_dict({"budget": {"daily_usd": 5, "on_limit": "explode"}})
    assert cfg.on_limit == ON_LIMIT_WARN  # 非法 → 回落 warn(不静默当 pause)


def test_config_rejects_nonpositive_and_garbage():
    cfg = spend_budget_config_from_dict(
        {"budget": {"daily_usd": -1, "daily_tokens": 0, "monthly_usd": "abc",
                    "monthly_tokens": 100}})
    assert cfg.daily_usd is None and cfg.daily_tokens is None
    assert cfg.monthly_usd is None and cfg.monthly_tokens == 100
    assert cfg.enabled  # 还有 monthly_tokens


# ---------- 成本换算:有价算钱 / 无价算 token ----------

_PRICED = {"input": 3.0, "output": 15.0}   # USD / 百万 token


def _budget(cfg, led, *, prices=None, clock=None, emit=None):
    def cost(mid):
        return (prices or {}).get(mid)
    return SpendBudget(cfg, ledger_getter=lambda: led,
                       model_cost=cost, clock=clock or (lambda: 1_700_000_000.0),
                       emit_card=emit)


def test_cost_conversion_priced_model():
    """有价模型:1M input @ $3 + 1M output @ $15 = $18。"""
    led = TokenLedger(path=None, clock=lambda: 1_700_000_000.0)
    led.record(source="agent_import", model="p/model",
               input=1_000_000, output=1_000_000)
    cfg = SpendBudgetConfig(daily_usd=100.0)
    b = _budget(cfg, led, prices={"p/model": _PRICED})
    v = b.evaluate("agent_import")
    # $18 / $100 = 0.18 → allow(未达 75%)
    assert v["action"] == ACTION_ALLOW
    # 直接查窗口花费
    usd, tok = b._window_spend(0, 1_700_000_000.0)
    assert abs(usd - 18.0) < 1e-6 and tok == 2_000_000


def test_cost_conversion_unpriced_model_counts_tokens_only():
    """无价模型:对 *_usd 上限贡献 0 美元(不猜价),但 *_tokens 上限照算。"""
    led = TokenLedger(path=None, clock=lambda: 1_700_000_000.0)
    led.record(source="weekly_digest", model="free/local", input=800, output=200)
    # 按美元:0 花费 → allow
    b_usd = _budget(SpendBudgetConfig(daily_usd=1.0), led, prices={})
    assert b_usd.evaluate("weekly_digest")["action"] == ACTION_ALLOW
    # 按 token:1000 / 1000 = 100% → block(pause + 后台)
    b_tok = _budget(SpendBudgetConfig(daily_tokens=1000, on_limit=ON_LIMIT_PAUSE), led)
    assert b_tok.evaluate("weekly_digest")["action"] == ACTION_BLOCK


# ---------- 三级触发:75 / 90 告警,100 拦截 ----------

@pytest.mark.parametrize("used_tok,expect,tier", [
    (700, ACTION_ALLOW, None),       # 70% → 放行
    (750, ACTION_WARN, "75"),        # 75% → 告警
    (900, ACTION_WARN, "90"),        # 90% → 告警
    (1000, ACTION_BLOCK, "100"),     # 100% → 拦截(pause+后台)
    (1500, ACTION_BLOCK, "100"),     # 超额 → 拦截
])
def test_three_tiers(used_tok, expect, tier):
    led = TokenLedger(path=None, clock=lambda: 1_700_000_000.0)
    led.record(source="consolidate", model="m", input=used_tok, output=0)
    cfg = SpendBudgetConfig(daily_tokens=1000, on_limit=ON_LIMIT_PAUSE)
    b = _budget(cfg, led)
    v = b.evaluate("consolidate")
    assert v["action"] == expect, v
    if tier:
        assert b._warn_tier(v["ratio"]) == tier


# ---------- 前台不拦,后台拦(token_source 区分)----------

def test_foreground_never_blocked_background_blocked():
    led = TokenLedger(path=None, clock=lambda: 1_700_000_000.0)
    led.record(source="unknown", model="m", input=5000, output=0)  # 已远超上限
    cfg = SpendBudgetConfig(daily_tokens=1000, on_limit=ON_LIMIT_PAUSE)
    b = _budget(cfg, led)
    # 前台("unknown" 主聊天 drive / forge / fuzzy_dispatch / roundtable_host)→ 至多 warn,永不 block
    for fg in ("unknown", "forge", "fuzzy_dispatch", "roundtable_host", "some_new_source"):
        v = b.evaluate(fg)
        assert v["action"] == ACTION_WARN, (fg, v)   # 达 100% 但前台 → 降级为 warn
    # 后台自动 → block
    for bg in ("consolidate", "weekly_digest", "agent_import", "凝习惯"):
        assert b.evaluate(bg)["action"] == ACTION_BLOCK, bg


def test_check_raises_only_for_background_under_pause():
    led = TokenLedger(path=None, clock=lambda: 1_700_000_000.0)
    led.record(source="x", model="m", input=5000, output=0)
    cfg = SpendBudgetConfig(daily_tokens=1000, on_limit=ON_LIMIT_PAUSE)
    b = _budget(cfg, led)
    # 前台:check 不抛
    b.check("unknown")
    b.check("forge")
    # 后台:check 抛 SpendBudgetExceeded
    with pytest.raises(SpendBudgetExceeded):
        b.check("weekly_digest")


def test_warn_mode_never_blocks_even_background():
    """on_limit=warn:即便后台自动 + 超 100%,也只告警不拦。"""
    led = TokenLedger(path=None, clock=lambda: 1_700_000_000.0)
    led.record(source="weekly_digest", model="m", input=5000, output=0)
    cfg = SpendBudgetConfig(daily_tokens=1000, on_limit=ON_LIMIT_WARN)
    b = _budget(cfg, led)
    assert b.evaluate("weekly_digest")["action"] == ACTION_WARN
    b.check("weekly_digest")  # 不抛


def test_automatic_source_classification():
    assert is_automatic_source("weekly_digest")
    assert is_automatic_source("consolidate")
    assert not is_automatic_source("unknown")
    assert not is_automatic_source("forge")
    assert not is_automatic_source("roundtable_host")
    assert not is_automatic_source("")


# ---------- 达限出卡形状 + 去重 ----------

def test_card_shape_warn_and_block():
    led = TokenLedger(path=None, clock=lambda: 1_700_000_000.0)
    led.record(source="agent_import", model="p/model",
               input=1_000_000, output=1_000_000)  # $18
    # 上限 $20 → 90% → warn
    b = _budget(SpendBudgetConfig(daily_usd=20.0), led, prices={"p/model": _PRICED})
    card = b.build_card(b.evaluate("agent_import"))
    assert card["kind"] == "spend_budget_alert"
    assert card["proposal_id"].startswith("spend_budget_alert-")
    assert "daily_usd" in card["proposal_id"] and card["proposal_id"].endswith("-90")
    assert card["payload"]["blocked"] is False
    assert "$" in card["summary"]
    # block 卡
    b2 = _budget(SpendBudgetConfig(daily_usd=10.0, on_limit=ON_LIMIT_PAUSE), led,
                 prices={"p/model": _PRICED})
    card2 = b2.build_card(b2.evaluate("agent_import"))
    assert card2["payload"]["blocked"] is True
    assert card2["proposal_id"].endswith("-100")


def test_warn_card_dedup_same_tier_once_per_day():
    led = TokenLedger(path=None, clock=lambda: 1_700_000_000.0)
    led.record(source="weekly_digest", model="m", input=800, output=0)  # 80% of 1000
    cards = []
    cfg = SpendBudgetConfig(daily_tokens=1000, on_limit=ON_LIMIT_WARN)
    b = _budget(cfg, led, emit=cards.append)
    b.check("weekly_digest")
    b.check("weekly_digest")
    b.check("weekly_digest")
    assert len(cards) == 1, "同级一天只出一次卡"


# ---------- 记账零改动回归:gateway 咽喉照记 ----------

def _drain(gw, model="test-model"):
    async def go():
        async for _ in gw.complete([{"role": "user", "content": "x"}], [], model):
            pass
    asyncio.run(go())


def test_gateway_recording_unchanged_with_budget_active():
    """预算启用(且未达限)时,gateway 记账逻辑一字不动 —— Usage 照记。"""
    from karvyloop.gateway.client import GatewayClient
    from karvyloop.gateway.events import Done, TextDelta, Usage
    from karvyloop.llm.spend_budget import register_spend_budget
    from karvyloop.llm.token_ledger import register_ledger, token_source

    class _M:
        id = "test-model"; api = "fake"; cost: dict = {}; role = "chat"

    class _Reg:
        def get(self, r): return _M()
        def provider_of(self, r): return None

    class _Adapter:
        async def complete(self, messages, tools, m, prov, system=None):
            yield TextDelta(text="hi")
            yield Usage(input_tokens=100, output_tokens=20)
            yield Done(stop_reason="end_turn")

    led = TokenLedger(path=None, clock=lambda: 1_700_000_000.0)
    register_ledger(led)
    # 预算启用但上限很高 → 永不拦,记账不受影响
    b = _budget(SpendBudgetConfig(daily_tokens=10_000_000), led)
    register_spend_budget(b)
    try:
        gw = GatewayClient(_Reg(), adapters={"fake": _Adapter()})

        async def go():
            with token_source("agent_import"):
                async for _ in gw.complete([{"role": "user", "content": "x"}], [], "test-model"):
                    pass
        asyncio.run(go())
        t = led.totals()
        assert t["calls"] == 1 and t["input"] == 100 and t["output"] == 20
    finally:
        register_spend_budget(None)
        register_ledger(None)


def test_gateway_blocks_background_before_adapter():
    """预算耗尽 + pause + 后台 source → gateway 在打 adapter 前抛 SpendBudgetExceeded。"""
    from karvyloop.gateway.client import GatewayClient
    from karvyloop.gateway.events import Done, TextDelta, Usage
    from karvyloop.llm.spend_budget import register_spend_budget
    from karvyloop.llm.token_ledger import register_ledger, token_source

    class _M:
        id = "test-model"; api = "fake"; cost: dict = {}; role = "chat"

    class _Reg:
        def get(self, r): return _M()
        def provider_of(self, r): return None

    class _Adapter:
        def __init__(self): self.called = False
        async def complete(self, messages, tools, m, prov, system=None):
            self.called = True
            yield TextDelta(text="hi")
            yield Usage(input_tokens=1, output_tokens=1)
            yield Done(stop_reason="end_turn")

    led = TokenLedger(path=None, clock=lambda: 1_700_000_000.0)
    led.record(source="weekly_digest", model="test-model", input=5000, output=0)
    register_ledger(led)
    b = _budget(SpendBudgetConfig(daily_tokens=1000, on_limit=ON_LIMIT_PAUSE), led)
    register_spend_budget(b)
    try:
        adapter = _Adapter()
        gw = GatewayClient(_Reg(), adapters={"fake": adapter})

        async def go():
            with token_source("weekly_digest"):
                async for _ in gw.complete([{"role": "user", "content": "x"}], [], "test-model"):
                    pass
        with pytest.raises(SpendBudgetExceeded):
            asyncio.run(go())
        assert adapter.called is False, "超预算的后台调用绝不该打给 adapter"
    finally:
        register_spend_budget(None)
        register_ledger(None)


def test_gateway_foreground_passes_when_budget_exhausted():
    """预算耗尽,但前台(unknown/default source)照常放行 —— 用户正等的 drive 永不罢工。"""
    from karvyloop.gateway.client import GatewayClient
    from karvyloop.gateway.events import Done, TextDelta, Usage
    from karvyloop.llm.spend_budget import register_spend_budget
    from karvyloop.llm.token_ledger import register_ledger

    class _M:
        id = "test-model"; api = "fake"; cost: dict = {}; role = "chat"

    class _Reg:
        def get(self, r): return _M()
        def provider_of(self, r): return None

    class _Adapter:
        def __init__(self): self.called = False
        async def complete(self, messages, tools, m, prov, system=None):
            self.called = True
            yield TextDelta(text="hi")
            yield Usage(input_tokens=1, output_tokens=1)
            yield Done(stop_reason="end_turn")

    led = TokenLedger(path=None, clock=lambda: 1_700_000_000.0)
    led.record(source="weekly_digest", model="test-model", input=99999, output=0)
    register_ledger(led)
    b = _budget(SpendBudgetConfig(daily_tokens=1000, on_limit=ON_LIMIT_PAUSE), led)
    register_spend_budget(b)
    try:
        adapter = _Adapter()
        gw = GatewayClient(_Reg(), adapters={"fake": adapter})
        # 无 token_source 包裹 → default "unknown" = 前台 → 放行
        _drain(gw)
        assert adapter.called is True, "前台调用即便超预算也必须放行"
    finally:
        register_spend_budget(None)
        register_ledger(None)


def test_no_budget_registered_is_noop():
    """未注册预算 → check_spend_budget 是 no-op(0 回归)。"""
    from karvyloop.llm.spend_budget import check_spend_budget, register_spend_budget
    register_spend_budget(None)
    check_spend_budget("weekly_digest")  # 不抛,不做任何事
