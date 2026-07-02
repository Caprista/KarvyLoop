"""test_console_render — 显示层:结构化事件 forge→DriveOutcome→serializer→chat_history(拍 9.4).

模型输出原本压成一坨 text 裸回显;本层把 forge 的结构化事件(text/tool_call/tool_result/terminal)
顺序收集 → 贯通到 UI 按类型渲染(markdown/tool 卡/输出面板)。

AC:
- AC1: RenderEventCollector 顺序收 + 连续 text delta 合并
- AC2: 工具夹中间 → 正文分块;tool_result 输出转字符串 + 超长截断
- AC3: forge_slow_brain_factory 把 emitter 透传给 generate_and_run(默认 None,0 回归)
- AC4: drive_in_tui 把 collector.events 填进 DriveOutcome.events
- AC5: drive_outcome_to_dict 带 events
- AC6: ChatEntry/push 带 events(持久,周期刷新不丢结构)
"""
from __future__ import annotations

import asyncio
import types

from karvyloop.coding.render_events import RenderEventCollector


# ---- AC1/AC2: collector ----
def test_collector_ordered_and_merge():
    c = RenderEventCollector()
    c.assistant_text_delta("hi ")
    c.assistant_text_delta("there")
    c.tool_call(id="1", name="read_file", input={"path": "a.py"})
    c.tool_result(tool_use_id="1", output="content", is_error=False)
    c.run_end(ok=True, reason="completed")
    assert [e["type"] for e in c.events] == ["text", "tool_call", "tool_result", "terminal"]
    assert c.events[0]["text"] == "hi there"
    assert c.events[1]["name"] == "read_file" and c.events[1]["input"] == {"path": "a.py"}
    assert c.events[3]["ok"] is True


def test_collector_split_and_truncate():
    c = RenderEventCollector()
    c.assistant_text_delta("before ")
    c.tool_call(id="1", name="bash", input={"command": "ls"})
    c.assistant_text_delta("after")
    assert [e["type"] for e in c.events] == ["text", "tool_call", "text"]
    c2 = RenderEventCollector()
    c2.tool_result(tool_use_id="x", output={"k": "v"}, is_error=False)
    assert isinstance(c2.events[0]["output"], str) and "k" in c2.events[0]["output"]
    c3 = RenderEventCollector()
    c3.tool_result(tool_use_id="y", output="a" * 9000, is_error=False)
    assert len(c3.events[0]["output"]) <= 8000 and c3.events[0]["truncated"] is True


# ---- AC3: factory 透传 emitter ----
def test_factory_passes_emitter(monkeypatch):
    import karvyloop.coding.forge as forge_mod
    captured = {}

    async def _fake_gen(*a, **kw):
        captured["emitter"] = kw.get("emitter", "MISSING")
        return types.SimpleNamespace(text="ok", run=types.SimpleNamespace(tool_calls=[]))

    monkeypatch.setattr(forge_mod, "generate_and_run", _fake_gen)
    from karvyloop.runtime.main_loop import forge_slow_brain_factory
    sentinel = RenderEventCollector()
    forge_slow_brain_factory(token=1, sandbox=2, gateway=3, workspace_root="/tmp", emitter=sentinel)("x")
    assert captured["emitter"] is sentinel
    forge_slow_brain_factory(token=1, sandbox=2, gateway=3, workspace_root="/tmp")("y")
    assert captured["emitter"] is None


# ---- AC4: drive_in_tui 填 events ----
def test_drive_in_tui_fills_events(monkeypatch):
    import karvyloop.workbench.main_loop_bridge as bridge

    def _stub_factory(*, token, sandbox, gateway, workspace_root, model_ref="", governance="", emitter=None, persona=None, **_):
        def slow_brain(intent, *, ctx=None):
            if emitter is not None:
                emitter.assistant_text_delta("hello")
                emitter.tool_call(id="1", name="write_file", input={"path": "x"})
                emitter.tool_result(tool_use_id="1", output="ok", is_error=False)
                emitter.run_end(ok=True, reason="completed")
            from karvyloop.schemas.atom import AtomRun
            return "hello", AtomRun(atom_id="a", input={"intent": intent}, output={"text": "hello"},
                                    success=True, tool_calls=[], trace_ref="t", ts=1.0)
        return slow_brain

    monkeypatch.setattr(bridge, "forge_slow_brain_factory", _stub_factory)

    class _Res:
        brain = types.SimpleNamespace(value="slow"); text = "hello"; skill_name = ""
        fast_brain_hit = False; crystallized = False; task_id = "t"; ctx_dependent = False

    class _ML:
        def drive(self, intent, *, slow_brain, ctx=None, scope=None, fresh=False):
            slow_brain(intent, ctx=ctx)
            return _Res()

    outcome = asyncio.run(bridge.drive_in_tui("do x", _ML(), token=1, sandbox=2, gateway=3, workspace_root="/tmp"))
    assert [e["type"] for e in outcome.events] == ["text", "tool_call", "tool_result", "terminal"]


def test_drive_in_tui_accepts_and_forwards_mcp_tools(monkeypatch):
    """回归(2026-06-25 线上崩):runtime_kwargs 带了 mcp_tools,聊天路径 `**runtime_kwargs`
    splat 进 drive_in_tui → 必须接受**且**透传给 forge 工厂(否则要么崩,要么 MCP 工具白连)。"""
    import karvyloop.workbench.main_loop_bridge as bridge
    seen = {}

    def _stub_factory(*, token, sandbox, gateway, workspace_root, model_ref="",
                      governance="", emitter=None, persona=None, mcp_tools=None, **_):
        seen["mcp_tools"] = mcp_tools

        def slow_brain(intent, *, ctx=None):
            from karvyloop.schemas.atom import AtomRun
            return "ok", AtomRun(atom_id="a", input={"intent": intent}, output={"text": "ok"},
                                 success=True, tool_calls=[], trace_ref="t", ts=1.0)
        return slow_brain

    monkeypatch.setattr(bridge, "forge_slow_brain_factory", _stub_factory)

    class _Res:
        brain = types.SimpleNamespace(value="slow"); text = "ok"; skill_name = ""
        fast_brain_hit = False; crystallized = False; task_id = "t"; ctx_dependent = False

    class _ML:
        def drive(self, intent, *, slow_brain, ctx=None, scope=None, fresh=False):
            slow_brain(intent, ctx=ctx); return _Res()

    # 模拟真实调用形态:**runtime_kwargs(含 mcp_tools)splat 进来
    rk = {"token": 1, "sandbox": 2, "gateway": 3, "workspace_root": "/tmp",
          "model_ref": "", "mcp_tools": {"mcp_x_search": object()}}
    outcome = asyncio.run(bridge.drive_in_tui("do x", _ML(), **rk))
    assert not getattr(outcome, "error", "")           # 不再崩
    assert seen["mcp_tools"] == rk["mcp_tools"]         # 且真透传给 forge(MCP 工具进 agent 集)


# ---- AC5: serializer ----
def test_serializer_includes_events():
    from karvyloop.console.serializers import drive_outcome_to_dict
    from karvyloop.workbench.main_loop_bridge import DriveOutcome
    from karvyloop.runtime.main_loop import Brain
    o = DriveOutcome(intent="x", brain=Brain.SLOW, text="t", skill_name="", fast_brain_hit=False,
                     crystallized=False, events=[{"seq": 1, "type": "text", "text": "hi"}])
    assert drive_outcome_to_dict(o)["events"] == [{"seq": 1, "type": "text", "text": "hi"}]


# ---- AC6: chat_history 带 events ----
def test_chat_history_carries_events():
    from karvyloop.workbench.chat_history import ChatHistory
    h = ChatHistory()
    h.push("agent", "hi", "2026-06-18T00:00:00Z", events=[{"seq": 1, "type": "text", "text": "hi"}])
    h.push("user", "yo", "2026-06-18T00:00:01Z")
    snap = h.snapshot()
    assert snap[0]["events"] == [{"seq": 1, "type": "text", "text": "hi"}]
    assert snap[1]["events"] == []
