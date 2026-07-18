"""test_provider_stream_deadline — LLM 流整段墙钟上限 + provider_timeout 真读配置(docs/87 §五)。

病根:provider_timeout() 恒返回 120 且忽略形参 → 配置里配的超时被无视;而且 120 是**每次**
read/connect 上限,不是整段流墙钟 → provider 周期吐 keepalive/注释行就能让每次 read 都不超时、
把 drive worker 无限吊住。
修:provider_timeout/stream_deadline 真读 provider 配置;流式循环加整段墙钟,超了抛
StreamTimeoutError(fail-loud → 归一化成 ErrorEvent),不静默挂死。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.gateway.events import ErrorEvent  # noqa: E402
from karvyloop.gateway.providers.anthropic import (  # noqa: E402
    AnthropicAdapter,
    StreamTimeoutError,
    provider_timeout,
    stream_deadline,
)
from karvyloop.gateway.providers.openai_completions import OpenAICompletionsAdapter  # noqa: E402
from karvyloop.schemas import ModelDefinition, ProviderConfig  # noqa: E402


def _model(api: str) -> ModelDefinition:
    return ModelDefinition(id="p/m", name="m", api=api, context_window=1000, max_tokens=256)


def _provider(**kw) -> ProviderConfig:
    return ProviderConfig(name="p", base_url="https://api.test", api_key="FAKE-DO-NOT-LEAK",
                          auth="api-key", auth_header="Authorization", models=[], **kw)


# ---- 配置读取:形参真用上(此前恒 120 / 忽略配置)----


def test_provider_timeout_reads_config_else_default():
    assert provider_timeout(_provider()) == 120.0             # 缺省
    assert provider_timeout(_provider(timeout=45)) == 45.0    # 配置生效(形参用上了)
    assert provider_timeout(_provider(timeout=0)) == 120.0    # ≤0 回落(不把坏值配进去)
    assert provider_timeout(_provider(timeout=None)) == 120.0


def test_stream_deadline_reads_config_else_default():
    default = stream_deadline(_provider())
    assert default > 0                                        # 有个缺省〔待标定〕
    assert stream_deadline(_provider(stream_deadline=30)) == 30.0
    assert stream_deadline(_provider(stream_deadline=0)) == default   # ≤0 回落缺省


def test_stream_timeout_error_is_timeout_error():
    assert issubclass(StreamTimeoutError, TimeoutError)


# ---- 整段墙钟:一直吐 keepalive 不给完成 → 触发超时、抛诚实错(不无限挂)----


class _EndlessKeepaliveStream:
    """模拟 provider 只周期吐 keepalive/注释行、永不发完成事件的流(单次 read 永远成功)。"""

    def __init__(self, delay: float, count: int = 200) -> None:
        self._delay = delay
        self._count = count
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for _ in range(self._count):
            await asyncio.sleep(self._delay)
            yield ": keepalive"   # SSE 注释行:不以 data: 开头 → 循环 continue,但整段墙钟仍推进


class _FakeClient:
    def __init__(self, *a, **k) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, **k):
        return _EndlessKeepaliveStream(delay=0.002)


async def _collect(adapter, provider, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    evs = []
    async for ev in adapter.complete([{"role": "user", "content": "hi"}], [],
                                     _model(adapter.api), provider):
        evs.append(ev)
        if len(evs) > 5:   # 安全阀:真超时只会有 1 条 ErrorEvent;多了说明没停 → 别把测试挂死
            break
    return evs


@pytest.mark.asyncio
async def test_anthropic_stream_walltime_trips(monkeypatch):
    provider = _provider(stream_deadline=0.001)   # 极小整段墙钟 → 第一条 keepalive 后即超
    evs = await _collect(AnthropicAdapter(), provider, monkeypatch)
    errs = [e for e in evs if isinstance(e, ErrorEvent)]
    assert errs, "整段墙钟到点必须抛 ErrorEvent(fail-loud),而不是无限挂在 keepalive 流上"
    assert errs[0].kind == "StreamTimeoutError"


@pytest.mark.asyncio
async def test_openai_stream_walltime_trips(monkeypatch):
    provider = _provider(stream_deadline=0.001)
    evs = await _collect(OpenAICompletionsAdapter(), provider, monkeypatch)
    errs = [e for e in evs if isinstance(e, ErrorEvent)]
    assert errs and errs[0].kind == "StreamTimeoutError"
