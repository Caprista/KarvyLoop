"""test_context_pipeline_wiring — loop step4a:上下文治理管线接进 executor/forge。

既有 `context/pipeline.py::govern`(microcompact + autocompact + HR-3 断路器)以前从没被
executor 调用(M0 占位"直接跳过")。step4a 把它接进 executor 的 ReAct 循环(每轮调模型前),
forge 用 enable_compression 开关 + 构造 GovConfig/摘要函数/取模型真实窗口。

AC:
- AC1 enable_compression=True → govern 被调(每轮调模型前),且拿到模型真实 context_window + GovConfig + 摘要函数
- AC2 enable_compression=False(默认)→ govern **不**被调(0 回归,旧路径行为不变)
- AC3 govern 抛 BlockingLimitError → executor 终止为 BLOCKING_LIMIT(不崩)
- AC4 enable_compression=True 走**真** govern(大窗口,阈值不触发)→ 正常完成,不崩
- AC5 _make_summarizer 收集 TextDelta 文本拼成摘要
"""
from __future__ import annotations

import io

import pytest

import time

from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round
from karvyloop.atoms.terminal import Terminal
from karvyloop.gateway import GatewayClient, ModelRegistry
from karvyloop.sandbox import Sandbox
from karvyloop.schemas import Capability, CapabilityToken


# ---- 最小桩(自包含,不依赖 test_forge) ----
class FakeSandbox(Sandbox):
    def __init__(self, root):
        self.root = root
        self.files: dict = {}

    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0, env=None):
        from karvyloop.sandbox import ExecResult
        return ExecResult(returncode=0, stdout=b"", stderr=b"", duration_s=0.0)

    async def write_file(self, path, content, token):
        self.files[path] = content

    async def read_file(self, path, token):
        return self.files.get(path, b"")


def _tok() -> CapabilityToken:
    return CapabilityToken(
        task_id="t",
        grants=[
            Capability(resource="fs:/ws", ops=["read", "write"]),
            Capability(resource="fs:/ws", ops=["exec"]),
        ],
        expiry=time.time() + 3600,
    )


def _gw(adapter, *, window: int = 1000) -> GatewayClient:
    reg = ModelRegistry.from_config({
        "models": {"providers": {"p": {"base_url": "x", "models": [
            {"id": "p/a", "api": "anthropic-messages", "context_window": window, "max_tokens": 100},
        ]}}},
        "agents": {"defaults": {"model": "p/a"}},
        "embedding": {"model": "p/a"},
    })
    return GatewayClient(reg, adapters={"anthropic-messages": adapter})


# ---- AC1/AC2:govern 被调与否 + 拿到正确参数 ----
@pytest.mark.asyncio
async def test_govern_called_when_compression_enabled(tmp_path, monkeypatch):
    from karvyloop.coding.forge import generate_and_run
    calls = []

    async def spy_govern(messages, cfg, state, summarize, *, context_window=200_000):
        calls.append({"cw": context_window, "cfg": cfg, "summarize": summarize})
        return messages  # 不真压,只观察

    monkeypatch.setattr("karvyloop.context.pipeline.govern", spy_govern)
    gw = _gw(ScriptedMockAdapter(rounds=[text_round("ok")]), window=1000)
    res = await generate_and_run("hi", _tok(), FakeSandbox(str(tmp_path)),
                                 gateway=gw, workspace_root=str(tmp_path),
                                 model_ref="p/a", enable_compression=True)
    assert res.terminal == Terminal.COMPLETED
    assert len(calls) >= 1                       # 每轮调模型前都治理
    assert calls[0]["cw"] == 1000                # 取到模型真实窗口
    assert calls[0]["cfg"] is not None           # GovConfig 传进去了
    assert callable(calls[0]["summarize"])       # 摘要函数构造好了


@pytest.mark.asyncio
async def test_govern_not_called_when_compression_disabled(tmp_path, monkeypatch):
    from karvyloop.coding.forge import generate_and_run
    calls = []

    async def spy_govern(*a, **k):
        calls.append(1)
        return a[0]

    monkeypatch.setattr("karvyloop.context.pipeline.govern", spy_govern)
    gw = _gw(ScriptedMockAdapter(rounds=[text_round("ok")]), window=1000)
    res = await generate_and_run("hi", _tok(), FakeSandbox(str(tmp_path)),
                                 gateway=gw, workspace_root=str(tmp_path),
                                 model_ref="p/a")  # enable_compression 默认 False
    assert res.terminal == Terminal.COMPLETED
    assert calls == []                           # 0 回归:旧路径完全不碰 govern


# ---- AC3:BlockingLimitError → 终止 BLOCKING_LIMIT ----
@pytest.mark.asyncio
async def test_blocking_limit_terminates(tmp_path, monkeypatch):
    from karvyloop.coding.forge import generate_and_run
    from karvyloop.context.budget import BlockingLimitError

    async def boom_govern(*a, **k):
        raise BlockingLimitError("超限且 autocompact 关")

    monkeypatch.setattr("karvyloop.context.pipeline.govern", boom_govern)
    gw = _gw(ScriptedMockAdapter(rounds=[text_round("ok")]), window=1000)
    res = await generate_and_run("hi", _tok(), FakeSandbox(str(tmp_path)),
                                 gateway=gw, workspace_root=str(tmp_path),
                                 model_ref="p/a", enable_compression=True)
    assert res.terminal == Terminal.BLOCKING_LIMIT  # 优雅终止,不崩


# ---- AC4:真 govern(大窗口,阈值不触发)→ 正常完成 ----
@pytest.mark.asyncio
async def test_real_govern_large_window_no_crash(tmp_path):
    from karvyloop.coding.forge import generate_and_run
    # window=200k → microcompact/autocompact 阈值都不触发 → govern 跑但 no-op
    gw = _gw(ScriptedMockAdapter(rounds=[text_round("ok")]), window=200_000)
    res = await generate_and_run("hi", _tok(), FakeSandbox(str(tmp_path)),
                                 gateway=gw, workspace_root=str(tmp_path),
                                 model_ref="p/a", enable_compression=True)
    assert res.terminal == Terminal.COMPLETED


# ---- AC5:摘要函数收集 TextDelta ----
@pytest.mark.asyncio
async def test_make_summarizer_collects_text():
    from karvyloop.coding.forge import _make_summarizer

    class TextDelta:  # 名字必须叫 TextDelta(executor/forge 按 type name 认)
        def __init__(self, t):
            self.text = t

    class FakeGW:
        def __init__(self):
            self.seen = None

        async def complete(self, messages, tools, model_ref, *, system=None):
            self.seen = messages
            for t in ["压", "缩", "摘要"]:
                yield TextDelta(t)

    gw = FakeGW()
    s = _make_summarizer(gw, "p/a")
    out = await s([{"role": "user", "content": "一些历史消息"}, {"role": "assistant", "content": "回复"}])
    assert out == "压缩摘要"
    assert "一些历史消息" in gw.seen[0]["content"]   # middle 段被渲染进摘要请求


# ---- 回归 checker 抓到的 CRITICAL/HIGH/MEDIUM:对**真实** Anthropic 消息形态生效 ----
def test_microcompact_trims_real_anthropic_tool_results():
    """executor 真实产出形态:user 消息 content=[tool_result block]。
    旧实现只认 role:'tool' → 永远空转;修后必须真裁这些 block。"""
    from karvyloop.context.microcompact import microcompact, PLACEHOLDER
    msgs = []
    for i in range(6):
        msgs.append({"role": "assistant",
                     "content": [{"type": "tool_use", "id": f"u{i}", "name": "read_file", "input": {}}]})
        msgs.append({"role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": f"u{i}", "content": f"out-{i}"}]})
    microcompact(msgs, keep_recent=2)
    results = [b for m in msgs if isinstance(m.get("content"), list)
               for b in m["content"] if b.get("type") == "tool_result"]
    assert len(results) == 6                                  # 一个都没删(只清 content)
    assert [r["content"] for r in results[:4]] == [PLACEHOLDER] * 4   # 旧的 4 个被裁
    assert results[4]["content"] == "out-4" and results[5]["content"] == "out-5"  # 最近 2 个留
    assert all(r["tool_use_id"] == f"u{i}" for i, r in enumerate(results))        # 配对 id 不破


def test_microcompact_below_threshold_noop_real_schema():
    from karvyloop.context.microcompact import microcompact
    msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "u1", "content": "x"}]}]
    microcompact(msgs, keep_recent=5)
    assert msgs[0]["content"][0]["content"] == "x"            # ≤keep_recent → 不动


@pytest.mark.asyncio
async def test_govern_microcompacts_on_budget_not_just_window():
    """成本触发口:累积 tool 结果体超 tool_result_budget → microcompact 早裁,
    不必等接近窗口(否则长 coding 任务在大窗口下永远不压 = O(n²) 烧钱)。"""
    from karvyloop.context.pipeline import govern
    from karvyloop.context.budget import GovConfig, GovState
    from karvyloop.context.microcompact import PLACEHOLDER

    # 6 个工具结果,每个 ~2k 字符;budget 设 1000 token(~4k 字符)→ 远超 → 早裁
    msgs = []
    for i in range(6):
        msgs.append({"role": "assistant",
                     "content": [{"type": "tool_use", "id": f"u{i}", "name": "run_command", "input": {}}]})
        msgs.append({"role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": f"u{i}", "content": "x" * 2000}]})
    cfg = GovConfig(keep_recent_tool_results=2, tool_result_budget=1000)
    out = await govern(msgs, cfg, GovState(), summarize=None, context_window=200_000)  # 大窗口
    results = [b for m in out if isinstance(m.get("content"), list)
               for b in m["content"] if b.get("type") == "tool_result"]
    # 大窗口下 window 触发口不会响,但 budget 触发口响了 → 旧的 4 个被裁
    assert sum(1 for r in results if r["content"] == PLACEHOLDER) == 4
    assert results[-1]["content"] == "x" * 2000   # 最近的留全


@pytest.mark.asyncio
async def test_autocompact_strips_orphan_tool_result():
    """autocompact 把含 tool_use 的 middle 摘掉后,tail 里对应的 tool_result 成孤儿 →
    必须删掉(否则 Anthropic 400)。"""
    from karvyloop.context.autocompact import autocompact
    from karvyloop.context.budget import GovConfig, GovState

    async def fake_summarize(middle):
        return "SUMMARY"

    msgs = [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "u1", "name": "read_file", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "u1", "content": "out1"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        {"role": "user", "content": "more"},
    ]
    out = await autocompact(msgs, GovState(), GovConfig(), fake_summarize,
                            keep_tail=3, context_window=200_000)
    # u1 的 tool_use 进了 middle 被摘 → tail 里 u1 的 tool_result 必须不再存在(孤儿已删)
    orphans = [b for m in out if isinstance(m.get("content"), list)
               for b in m["content"]
               if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == "u1"]
    assert orphans == []


@pytest.mark.asyncio
async def test_summarizer_renders_tool_use_and_result():
    """摘要请求里要真带上工具名/输入/输出(否则 tool-heavy 历史摘成空)。"""
    from karvyloop.coding.forge import _make_summarizer

    class TextDelta:
        def __init__(self, t):
            self.text = t

    class FakeGW:
        def __init__(self):
            self.seen = None

        async def complete(self, messages, tools, model_ref, *, system=None):
            self.seen = messages
            yield TextDelta("ok")

    gw = FakeGW()
    s = _make_summarizer(gw, "p/a")
    middle = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "u1", "name": "run_command",
                                            "input": {"command": "pytest"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "u1", "content": "2 passed"}]},
    ]
    await s(middle)
    req = gw.seen[0]["content"]
    assert "run_command" in req and "pytest" in req and "2 passed" in req
