"""可观测性收敛②:异常真因上冒 —— infra-dead 判定白名单化。

病根(本周真痛):执行器把调模型路径的**一切**异常吞成 INFRA_DEAD("模型/网络调不通")。
一个 TypeError(代码 bug:persona to_blocks 少 cache kwarg)被误诊成网络问题,
整条慢脑全灭、排查方向全错。

纪律:
- infra-dead 判定必须**白名单式**(网络/超时/认证/限流/5xx 才算 infra);
- TypeError/AttributeError/KeyError 等代码缺陷**绝不**归为 infra-dead ——
  fail-loud 上冒原始异常链,真因(异常类名 + traceback)落 Trace;
- 预算/上下文天花板(系统**有意**拒发)按 BLOCKING_LIMIT 报,提示语才对得上真因。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from karvyloop.atoms import Terminal, TerminalEvent, run
from karvyloop.atoms.terminal import (
    classify_model_call_exception,
    classify_resolve_exception,
)
from karvyloop.gateway import GatewayClient, ModelRegistry
from karvyloop.schemas import AtomRun, AtomSpec, Capability, CapabilityToken


# ============ 分类器单测(白名单式)============

def test_code_defects_are_never_infra():
    """TypeError/AttributeError/KeyError = 代码缺陷 → None(调用方必须上冒)。"""
    for exc in (TypeError("bad kwarg"), AttributeError("no attr"),
                KeyError("missing"), IndexError("oob"), ZeroDivisionError()):
        assert classify_model_call_exception(exc) is None, type(exc).__name__


def test_network_and_timeout_are_infra():
    for exc in (ConnectionError("down"), ConnectionRefusedError(),
                TimeoutError("slow"), OSError("socket broke")):
        assert classify_model_call_exception(exc) == Terminal.INFRA_DEAD, type(exc).__name__


def test_httpx_transport_and_auth_status_are_infra():
    httpx = pytest.importorskip("httpx")
    req = httpx.Request("POST", "http://example.invalid/v1/messages")
    assert classify_model_call_exception(httpx.ConnectError("boom", request=req)) == Terminal.INFRA_DEAD
    assert classify_model_call_exception(httpx.ReadTimeout("slow", request=req)) == Terminal.INFRA_DEAD
    for status in (401, 403, 429, 500, 503):
        err = httpx.HTTPStatusError("x", request=req, response=httpx.Response(status, request=req))
        assert classify_model_call_exception(err) == Terminal.INFRA_DEAD, status


def test_httpx_bad_request_4xx_is_not_infra():
    """400/422 = 请求体/协议 bug(MiniMax 400 一类)→ 不吞成"网络调不通",上冒真因。"""
    httpx = pytest.importorskip("httpx")
    req = httpx.Request("POST", "http://example.invalid/v1/messages")
    for status in (400, 404, 422):
        err = httpx.HTTPStatusError("x", request=req, response=httpx.Response(status, request=req))
        assert classify_model_call_exception(err) is None, status


def test_deliberate_gates_map_to_blocking_limit():
    from karvyloop.gateway.client import ContextCeilingError
    from karvyloop.llm.spend_budget import SpendBudgetExceeded
    assert classify_model_call_exception(SpendBudgetExceeded("budget")) == Terminal.BLOCKING_LIMIT
    assert classify_model_call_exception(ContextCeilingError("ceiling")) == Terminal.BLOCKING_LIMIT


def test_resolve_whitelist():
    from karvyloop.gateway.registry import UnknownModelError
    assert classify_resolve_exception(UnknownModelError("p/x")) == Terminal.INFRA_DEAD
    assert classify_resolve_exception(ValueError("bad config")) == Terminal.INFRA_DEAD
    assert classify_resolve_exception(RuntimeError("no usable model")) == Terminal.INFRA_DEAD
    # 代码缺陷不白:裸 KeyError ≠ UnknownModelError
    assert classify_resolve_exception(KeyError("oops")) is None
    assert classify_resolve_exception(TypeError("bad call")) is None
    assert classify_resolve_exception(AttributeError("no attr")) is None


def test_classify_error_event_kind_based():
    """adapter 把流内异常归一化成 ErrorEvent(kind=原异常类名)→ 按 kind 同一白名单纪律分类。"""
    from karvyloop.atoms.terminal import classify_error_event
    # 传输层 kind → infra
    for kind in ("ConnectError", "ReadTimeout", "ConnectionError", "OSError",
                 "TimeoutException", "RemoteProtocolError", "JSONDecodeError"):
        assert classify_error_event(kind, "boom") == Terminal.INFRA_DEAD, kind
    # 代码缺陷 kind → None(上冒)
    for kind in ("TypeError", "AttributeError", "KeyError"):
        assert classify_error_event(kind, "bug") is None, kind
    # HTTPStatusError:按 message 里的状态码分(httpx 消息格式)
    msg401 = "Client error '401 Unauthorized' for url 'http://x/v1/messages'"
    msg400 = "Client error '400 Bad Request' for url 'http://x/v1/messages'"
    msg503 = "Server error '503 Service Unavailable' for url 'http://x/v1'"
    assert classify_error_event("HTTPStatusError", msg401) == Terminal.INFRA_DEAD
    assert classify_error_event("HTTPStatusError", msg503) == Terminal.INFRA_DEAD
    assert classify_error_event("HTTPStatusError", msg400) is None
    # 状态码解析不出 → 保守 infra
    assert classify_error_event("HTTPStatusError", "weird message") == Terminal.INFRA_DEAD


def test_workflow_fingerprint_rejects_code_defect_messages():
    """驱动层指纹判(workflow_engine):带异常类名前缀的代码缺陷消息哪怕碰巧含
    "gateway"/"connection" 字样也不判 infra(误诊 = 盲中止/查错方向)。"""
    from karvyloop.console.workflow_engine import _is_infra_dead_error
    assert _is_infra_dead_error("TypeError: 'GatewayClient' object is not callable") is False
    assert _is_infra_dead_error("KeyError: 'connection'") is False
    # 真 infra 指纹照常命中
    assert _is_infra_dead_error("infra_dead: 模型解析失败") is True
    assert _is_infra_dead_error("ConnectionError: ECONNREFUSED to gateway") is True


# ============ 执行器:对抗测试(桩内部 TypeError)============

def _atom(model: str = "p/a") -> AtomSpec:
    return AtomSpec(
        id="a1", kind="task", prompt="you are a test atom",
        input_schema={"type": "object"}, output_schema={"type": "object"},
        tools=["read_file"], model=model,
    )


def _tok() -> CapabilityToken:
    return CapabilityToken(
        task_id="t",
        grants=[Capability(resource="fs:/tmp", ops=["read"])],
        expiry=time.time() + 3600,
    )


def _gw(adapter) -> GatewayClient:
    reg = ModelRegistry.from_config({
        "models": {"providers": {"p": {"base_url": "x", "models": [
            {"id": "p/a", "api": "anthropic-messages", "context_window": 1000, "max_tokens": 100},
        ]}}},
        "agents": {"defaults": {"model": "p/a"}},
        "embedding": {"model": "p/a"},
    })
    return GatewayClient(reg, adapters={"anthropic-messages": adapter})


class _TypeErrorAdapter:
    """流式一开始就抛 TypeError —— 模拟真实病根(to_blocks 少 cache kwarg 一类代码 bug)。"""
    api = "anthropic-messages"

    async def complete(self, messages, tools, model, provider, *, system=None, **kw):
        raise TypeError("to_blocks() got an unexpected keyword argument 'cache'")
        yield  # pragma: no cover — 让它成为 async generator(永不到达)

    async def embed(self, text, model, provider):  # pragma: no cover
        return [0.0]


class _BudgetAdapter:
    """模拟预算闸有意拒发(SpendBudgetExceeded 从 gateway.complete 冒出)。"""
    api = "anthropic-messages"

    async def complete(self, messages, tools, model, provider, *, system=None, **kw):
        from karvyloop.llm.spend_budget import SpendBudgetExceeded
        raise SpendBudgetExceeded("monthly budget exhausted")
        yield  # pragma: no cover — 让它成为 async generator(永不到达)

    async def embed(self, text, model, provider):  # pragma: no cover
        return [0.0]


@pytest.mark.asyncio
async def test_internal_typeerror_is_not_reported_as_infra_dead():
    """对抗:内部 TypeError 绝不能被报成"模型/网络调不通"。
    原始异常链上冒(TypeError 直接 raise),且**不**发半截 TerminalEvent(假成功/假 infra)。"""
    gw = _gw(_TypeErrorAdapter())
    events: list = []
    with pytest.raises(TypeError, match="cache"):
        async for ev in run(_atom(), {"q": "hi"}, _tok(), gateway=gw, tools={}):
            events.append(ev)
    assert not any(isinstance(e, TerminalEvent) for e in events), \
        f"代码缺陷路径不该吐 TerminalEvent(拿到了 {events})"


@pytest.mark.asyncio
async def test_resolve_typeerror_bubbles_up(monkeypatch):
    gw = _gw(_TypeErrorAdapter())

    def _boom(scope):
        raise TypeError("resolve_model() takes 1 positional argument")

    monkeypatch.setattr(gw, "resolve_model", _boom)
    with pytest.raises(TypeError, match="resolve_model"):
        async for _ in run(_atom(), {"q": "hi"}, _tok(), gateway=gw, tools={}):
            pass


@pytest.mark.asyncio
async def test_spend_budget_maps_to_blocking_limit_not_infra():
    gw = _gw(_BudgetAdapter())
    events = [ev async for ev in run(_atom(), {"q": "hi"}, _tok(), gateway=gw, tools={})]
    term = events[-1]
    assert isinstance(term, TerminalEvent)
    assert term.reason == Terminal.BLOCKING_LIMIT   # 不是 INFRA_DEAD:预算闸是有意拒发
    assert term.run.terminal == "blocking_limit"


class _ErrorEventAdapter:
    """真实 adapter 契约:流内异常不穿透,归一化成 ErrorEvent 后流结束。"""
    api = "anthropic-messages"

    def __init__(self, kind: str, message: str, lead_text: str = ""):
        self.kind, self.message, self.lead_text = kind, message, lead_text

    async def complete(self, messages, tools, model, provider, *, system=None, **kw):
        from karvyloop.gateway.events import ErrorEvent, TextDelta
        if self.lead_text:
            yield TextDelta(text=self.lead_text)
        yield ErrorEvent(kind=self.kind, message=self.message)

    async def embed(self, text, model, provider):  # pragma: no cover
        return [0.0]


@pytest.mark.asyncio
async def test_error_event_network_is_infra_dead_not_silent_success():
    """真实 adapter 路径的病根:网络断被归一化成 ErrorEvent,executor 此前无分支 →
    COMPLETED + 空输出的静默假成功。现在必须是 INFRA_DEAD(success=False)。"""
    gw = _gw(_ErrorEventAdapter("ConnectError", "All connection attempts failed"))
    events = [ev async for ev in run(_atom(), {"q": "hi"}, _tok(), gateway=gw, tools={})]
    term = events[-1]
    assert isinstance(term, TerminalEvent)
    assert term.reason == Terminal.INFRA_DEAD
    assert term.run.success is False


@pytest.mark.asyncio
async def test_error_event_code_defect_kind_bubbles_with_true_cause():
    """ErrorEvent 携带代码缺陷 kind(如 _normalize 里的 TypeError)→ fail-loud 上冒,
    异常文本带真实类名;不发半截 TerminalEvent。"""
    from karvyloop.atoms.executor import AdapterStreamError
    gw = _gw(_ErrorEventAdapter("TypeError", "unhashable type: 'dict'", lead_text="部分输出"))
    events: list = []
    with pytest.raises(AdapterStreamError, match="TypeError: unhashable"):
        async for ev in run(_atom(), {"q": "hi"}, _tok(), gateway=gw, tools={}):
            events.append(ev)
    assert not any(isinstance(e, TerminalEvent) for e in events)


@pytest.mark.asyncio
async def test_error_event_bad_request_400_bubbles():
    """400 坏请求 = 请求体/协议 bug → 上冒(此前:静默假成功;绝不是"网络调不通")。"""
    from karvyloop.atoms.executor import AdapterStreamError
    gw = _gw(_ErrorEventAdapter(
        "HTTPStatusError", "Client error '400 Bad Request' for url 'http://x/v1/messages'"))
    with pytest.raises(AdapterStreamError, match="400"):
        async for _ in run(_atom(), {"q": "hi"}, _tok(), gateway=gw, tools={}):
            pass


# ============ drive:真因(异常类名 + traceback)落 Trace 再上冒 ============

def test_drive_records_true_cause_in_trace_and_reraises(tmp_path: Path):
    from karvyloop.runtime.main_loop import MainLoop

    def slow_brain(intent: str):
        raise TypeError("'NoneType' object is not subscriptable")

    ml = MainLoop(skills_dir=tmp_path / "skills")
    ml.bootstrap()
    with pytest.raises(TypeError, match="NoneType"):
        ml.drive("crash please", slow_brain=slow_brain)

    # 真因落 Trace:kind=error 的条目带真实异常类名 + traceback
    errs = [e for tid in ml.trace.all_tasks() for e in ml.trace.query(tid, kind="error")]
    assert errs, "慢脑崩溃必须留 Trace 真因条目"
    p = errs[-1].payload
    assert p.get("error_type") == "TypeError"
    assert "TypeError" in (p.get("traceback") or "")
    assert "NoneType" in (p.get("error") or "")
    # 绝不误诊:真因条目/本次 Trace 里不得出现 infra-dead 归类
    assert "调不通" not in str(p)
    runs = [e for tid in ml.trace.all_tasks() for e in ml.trace.query(tid, kind="atom_run")]
    assert not any((e.payload or {}).get("terminal") == "infra_dead" for e in runs)


async def test_bridge_error_carries_exception_class(monkeypatch, tmp_path: Path):
    """桥边界:DriveOutcome.error 带真实异常类名(TypeError…),不再抹成无名字符串;
    用户可见侧不再是"模型/网络调不通"的误诊文案。"""
    from karvyloop.runtime.main_loop import MainLoop
    from karvyloop.workbench import main_loop_bridge as mlb

    def _factory(**kwargs):
        def slow_brain(intent: str):
            raise TypeError("to_blocks() got an unexpected keyword argument 'cache'")
        return slow_brain

    monkeypatch.setattr(mlb, "forge_slow_brain_factory", _factory)
    ml = MainLoop(skills_dir=tmp_path / "skills")
    ml.bootstrap()
    out = await mlb.drive_in_tui(
        "do something", ml, token=None, sandbox=None, gateway=None, workspace_root=".")
    assert out.error and out.error.startswith("TypeError:"), out.error
    assert "调不通" not in out.error
