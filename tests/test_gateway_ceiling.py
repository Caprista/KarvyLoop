"""test_gateway_ceiling — 网关咽喉确定性超限地板(P2-b).

病根(全盘 review 揪出):`govern()`(每轮软压缩)只在 executor 接了,4 处直连 gateway.complete
(导入拆解/模糊调度/ops/圆桌 goal)跳过它 —— 组装后上下文可无界地打给 provider,注定 4xx 或被静默截断。
修:在唯一咽喉 GatewayClient.complete 加**确定性硬地板** —— 超模型硬窗口 → fail-loud 拒发。

不变量:① 未超窗 → 照常调 ② 超窗 → raise ContextCeilingError,且**adapter 从没被调到**
③ context_window 未知(cw<=0)→ 不判(0 回归,老 mock 模型不受影响)。
"""
from __future__ import annotations

import asyncio

import pytest

from karvyloop.gateway.client import ContextCeilingError, GatewayClient


class _Adapter:
    def __init__(self):
        self.called = False

    async def complete(self, messages, tools, m, prov, system=None):
        self.called = True
        from karvyloop.gateway.events import Done, TextDelta
        yield TextDelta(text="hi")
        yield Done(stop_reason="end_turn")


class _M:
    def __init__(self, cw, max_tokens=1000):
        self.id = "test/model"
        self.api = "fake"
        self.cost: dict = {}
        self.role = "chat"
        self.context_window = cw
        self.max_tokens = max_tokens


class _Reg:
    def __init__(self, m):
        self._m = m

    def get(self, ref):
        return self._m

    def provider_of(self, ref):
        return None


def _drain(gw, messages):
    async def go():
        async for _ in gw.complete(messages, [], "test/model"):
            pass
    asyncio.run(go())


def test_under_window_passes():
    adapter = _Adapter()
    gw = GatewayClient(_Reg(_M(cw=10_000)), adapters={"fake": adapter})
    _drain(gw, [{"role": "user", "content": "x" * 100}])   # ~25 tok，远低于窗口
    assert adapter.called


def test_over_window_refused_before_adapter():
    adapter = _Adapter()
    # 窗口 8000，预留 = max_tokens(1000)+2000=3000 → 阈值 5000 tok ≈ 20000 字符
    gw = GatewayClient(_Reg(_M(cw=8_000, max_tokens=1000)), adapters={"fake": adapter})
    big = "字" * 40_000   # ≈ 10000 tok，稳超阈值
    with pytest.raises(ContextCeilingError):
        _drain(gw, [{"role": "user", "content": big}])
    assert adapter.called is False, "超窗请求绝不该打给 adapter"


def test_unknown_window_no_enforcement():
    """context_window<=0(未知)→ 不判,0 回归(老 mock 模型无该字段/为 0)。"""
    adapter = _Adapter()
    gw = GatewayClient(_Reg(_M(cw=0)), adapters={"fake": adapter})
    _drain(gw, [{"role": "user", "content": "x" * 100_000}])   # 巨大也放行
    assert adapter.called


def test_tools_schema_counted():
    """tools 定义也计入(对抗验收:MCP 工具 schema 动辄数 KB,不计=低估放行注定 4xx 的请求)。"""
    adapter = _Adapter()
    gw = GatewayClient(_Reg(_M(cw=8_000, max_tokens=1000)), adapters={"fake": adapter})
    big_tools = [{"name": "t", "description": "d" * 30_000, "input_schema": {}}]  # ≈7500 tok > 阈值 5000

    async def go():
        async for _ in gw.complete([{"role": "user", "content": "hi"}], big_tools, "test/model"):
            pass
    with pytest.raises(ContextCeilingError):
        asyncio.run(go())
    assert adapter.called is False


def test_cjk_counted_realistically():
    """CJK ≈ 1 tok/字(对抗验收:len//4 对中文低估 4x,中文超窗会溜过)。
    8000 字中文 ≈ 8000 tok > 阈值 5000 —— len//4 只算 2000 会放行,CJK 感知必须拦。"""
    adapter = _Adapter()
    gw = GatewayClient(_Reg(_M(cw=8_000, max_tokens=1000)), adapters={"fake": adapter})
    with pytest.raises(ContextCeilingError):
        _drain(gw, [{"role": "user", "content": "汉" * 8_000}])
    assert adapter.called is False


def test_over_window_counts_system_and_blocks():
    """system prompt 也计入 —— messages 不大但 system 超窗也拦。"""
    from karvyloop.gateway.system import SystemPrompt
    adapter = _Adapter()
    gw = GatewayClient(_Reg(_M(cw=8_000, max_tokens=1000)), adapters={"fake": adapter})
    sys_p = SystemPrompt(static=["规" * 30_000], dynamic=[])
    async def go():
        async for _ in gw.complete([{"role": "user", "content": "hi"}], [], "test/model", system=sys_p):
            pass
    with pytest.raises(ContextCeilingError):
        asyncio.run(go())
    assert adapter.called is False
