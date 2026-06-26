"""Renderer 事件分发测试 —— 对应修复:cli 非 json 路径不再静默吞文本。

Bug 背景:cmd_run_async 调 generate_and_run 时没传 emitter,Forge 内部
emit 出去的 TextEvent/ToolCallEvent 没人接,result.text 累不到(executor
yield 的 TextEvent 才是真源,result.text 是从这累的)。修复:Forge 接
renderer 参数,atom 事件透传给 renderer.render(ev)。

为不依赖真 LLM,直接构造 atoms.executor 真实事件对象喂 Renderer。
"""

from __future__ import annotations

import io

import pytest

from karvyloop.atoms.executor import (
    Terminal,
    TerminalEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from karvyloop.atoms.orchestration import ToolResult, ToolUseBlock
from karvyloop.cli.render import Renderer


def _make_renderer() -> tuple[Renderer, io.StringIO]:
    buf = io.StringIO()
    r = Renderer(out=buf, err=io.StringIO(), color=False)
    return r, buf


# -------- AC1:TextEvent.text 被实时打印(关键修复点)--------
def test_ac1_texevent_prints_to_out():
    r, buf = _make_renderer()
    r.render(TextEvent(text="Hello! "))
    r.render(TextEvent(text="How can I help?"))
    assert buf.getvalue() == "Hello! How can I help?"


# -------- AC2:ToolCallEvent.block.name 被取出来(之前 bug:ev.name 错路径)--------
def test_ac2_toolcall_uses_block_name():
    r, buf = _make_renderer()
    block = ToolUseBlock(id="t1", name="bash", input={"command": "echo hi"})
    r.render(ToolCallEvent(block=block))
    out = buf.getvalue()
    assert "bash" in out
    assert "echo hi" in out  # 摘要


# -------- AC3:ToolResultEvent.result 是 ToolResult,is_error 决定 ✓/✗ --------
def test_ac3_toolresult_ok_marks_check():
    r, buf = _make_renderer()
    r.render(ToolResultEvent(result=ToolResult(
        tool_use_id="t1", name="bash", content="hi\n", is_error=False)))
    assert "✓" in buf.getvalue()


def test_ac3_toolresult_error_marks_x():
    r, buf = _make_renderer()
    r.render(ToolResultEvent(result=ToolResult(
        tool_use_id="t1", name="bash", content="", is_error=True,
        error_reason="permission denied")))
    assert "✗" in buf.getvalue()
    assert "permission denied" in buf.getvalue()


# -------- AC4:TerminalEvent.reason 是 Terminal 枚举,走 _terminal 分支 --------
def test_ac4_terminal_completed_prints_green_check():
    r, buf = _make_renderer()
    r.render(TerminalEvent(run=None, reason=Terminal.COMPLETED))
    out = buf.getvalue()
    assert "✓" in out
    assert "completed" in out


def test_ac4_terminal_blocking_limit_prints_yellow():
    r, buf = _make_renderer()
    r.render(TerminalEvent(run=None, reason=Terminal.BLOCKING_LIMIT))
    out = buf.getvalue()
    assert "✗" in out
    assert "blocking_limit" in out


# -------- AC5:未知事件不抛错,静默忽略(防御)--------
def test_ac5_unknown_event_silently_dropped():
    r, buf = _make_renderer()
    r.render(object())  # 既无 kind 也无类名匹配
    assert buf.getvalue() == ""


# -------- AC6:fast_brain_note 标注省了 X token(结晶楔子闭环)--------
def test_ac6_fast_brain_note_includes_skill_name():
    r, buf = _make_renderer()
    r.fast_brain_note(skill_name="summarize-doc", saved_tokens=1234)
    out = buf.getvalue()
    assert "summarize-doc" in out
    assert "1234" in out


# -------- AC7:累计 stats 正确 --------
def test_ac7_render_stats_accumulate():
    r, _ = _make_renderer()
    r.render(TextEvent(text="abcd"))
    r.render(TextEvent(text="efgh"))
    block = ToolUseBlock(id="t1", name="read", input={"path": "/x"})
    r.render(ToolCallEvent(block=block))
    assert r.stats.text_chars == 8
    assert r.stats.tool_calls == 1


def test_collector_on_event_streams_deltas_and_tools():
    """P4 逐字流式:on_event 每个 delta/工具实时触发;批量 .events 仍合并(终态用)。"""
    from karvyloop.coding.render_events import RenderEventCollector
    fired = []
    c = RenderEventCollector(on_event=lambda ev: fired.append(ev))
    c.assistant_text_delta("你")
    c.assistant_text_delta("好")
    c.tool_call(id="t1", name="read_file", input={"p": "x"})
    # 流式:2 个 text_delta(不合并)+ 1 tool_call
    assert [e["type"] for e in fired] == ["text_delta", "text_delta", "tool_call"]
    assert fired[0]["text"] == "你" and fired[1]["text"] == "好"
    assert fired[2]["name"] == "read_file"
    # 批量:text 合并成一条 + tool_call(终态渲染/持久用)
    assert [e["type"] for e in c.events] == ["text", "tool_call"]
    assert c.events[0]["text"] == "你好"


def test_collector_no_on_event_batch_unchanged():
    """无回调 = 旧批量行为(0 回归)。"""
    from karvyloop.coding.render_events import RenderEventCollector
    c = RenderEventCollector()
    c.assistant_text_delta("a"); c.assistant_text_delta("b")
    assert c.events[0]["text"] == "ab"


def test_on_event_exception_does_not_break():
    """流式推送回调抛错绝不拖垮收集(drive 不能被流式失败打断)。"""
    from karvyloop.coding.render_events import RenderEventCollector
    def _boom(ev):
        raise RuntimeError("ws dead")
    c = RenderEventCollector(on_event=_boom)
    c.assistant_text_delta("x")   # 不该抛
    c.tool_call(id="t", name="f")
    assert len(c.events) == 2


def test_collector_thinking_separate_from_text():
    """P4 thinking 折叠:推理走独立 thinking 事件(不混进答案 text);流式推 thinking_delta。"""
    from karvyloop.coding.render_events import RenderEventCollector
    fired = []
    c = RenderEventCollector(on_event=lambda ev: fired.append(ev))
    c.assistant_thinking_delta("先想想")
    c.assistant_thinking_delta("再想想")
    c.assistant_text_delta("答案")
    # 批量:thinking 一条(合并)+ text 一条,互不混
    assert [e["type"] for e in c.events] == ["thinking", "text"]
    assert c.events[0]["text"] == "先想想再想想" and c.events[1]["text"] == "答案"
    # 流式:thinking_delta ×2 + text_delta ×1
    assert [e["type"] for e in fired] == ["thinking_delta", "thinking_delta", "text_delta"]


def test_forge_thinking_fallback_inline_for_old_emitter():
    """0 回归:emitter 无 assistant_thinking_delta → forge 回退 [thinking] 内联(旧行为)。"""
    from karvyloop.coding.ndjson import NdjsonEmitter
    assert hasattr(NdjsonEmitter, "assistant_thinking_delta")   # 新 emitter 有
    # 无该方法的 emitter(鸭子):forge 走 assistant_text_delta 回退(见 forge.py 分支)
    class _OldEmitter:
        def __init__(self): self.texts = []
        def assistant_text_delta(self, t): self.texts.append(t)
    e = _OldEmitter()
    assert not hasattr(e, "assistant_thinking_delta")
