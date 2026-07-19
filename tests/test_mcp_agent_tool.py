"""test_mcp_agent_tool — MCP→agent 工具适配器(A 步骤 1a):形状对、调用通、错误降级。

用假 session(不起子进程、不要 key、不碰真 MCP),只验适配器把 MCP tool 包成 agent 工具集
能直接用的 CodingTool 形状,且 isError/异常都老实返回 ok=False(不伪造)。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.coding.tools.mcp_tool import (  # noqa: E402
    McpAgentTool, mcp_tools_from_session,
)


class _Text:
    type = "text"
    def __init__(self, text): self.text = text


class _Result:
    def __init__(self, content, is_error=False):
        self.content = content
        self.isError = is_error


class _FakeSession:
    def __init__(self, result=None, raise_exc=None):
        self._result = result
        self._raise = raise_exc
        self.calls = []
    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        if self._raise:
            raise self._raise
        return self._result


class _McpToolDef:
    def __init__(self, name, description, schema):
        self.name = name
        self.description = description
        self.inputSchema = schema


def test_adapter_shape_matches_codingtool():
    sess = _FakeSession(_Result([_Text("ok")]))
    t = McpAgentTool(server_name="websearch", mcp_tool_name="search",
                     description="search the web", parameters={"type": "object"}, session=sess)
    assert t.name == "mcp_websearch_search"          # 带前缀防撞名
    assert t.description == "search the web"
    assert t.parameters == {"type": "object"}
    assert t.is_concurrency_safe({}) is False        # 保守


async def test_adapter_call_returns_text():
    sess = _FakeSession(_Result([_Text("result A"), _Text("result B")]))
    t = McpAgentTool(server_name="ws", mcp_tool_name="search", description="", parameters={}, session=sess)
    r = await t({"query": "x"})
    # 返回文本过统一不可信围栏(数据仍可读;对抗面在 tests/test_untrusted_fence.py)
    from karvyloop.cognition.fence import DATA_FENCE_CLOSE
    assert r.ok is True
    assert "result A\nresult B" in r.payload and DATA_FENCE_CLOSE in r.payload
    assert sess.calls == [("search", {"query": "x"})]   # 透传 input


async def test_adapter_is_error_degrades():
    sess = _FakeSession(_Result([_Text("rate limited")], is_error=True))
    t = McpAgentTool(server_name="ws", mcp_tool_name="search", description="", parameters={}, session=sess)
    r = await t({"query": "x"})
    assert r.ok is False and "rate limited" in r.error_message   # 老实报错,不伪造


async def test_adapter_exception_degrades():
    sess = _FakeSession(raise_exc=RuntimeError("transport dead"))
    t = McpAgentTool(server_name="ws", mcp_tool_name="search", description="", parameters={}, session=sess)
    r = await t({})
    assert r.ok is False and "transport dead" in r.error_message


def test_build_tools_from_session_keys_by_prefixed_name():
    sess = _FakeSession(_Result([_Text("ok")]))
    defs = [_McpToolDef("search", "web search", {"type": "object", "properties": {"q": {}}}),
            _McpToolDef("fetch", "fetch url", {})]
    tools = mcp_tools_from_session(sess, defs, "minimax")
    assert set(tools) == {"mcp_minimax_search", "mcp_minimax_fetch"}
    assert tools["mcp_minimax_search"].description == "web search"
    assert tools["mcp_minimax_search"].parameters == {"type": "object", "properties": {"q": {}}}


# ---- 配置读取(从 config.yaml 的 mcp.servers;env 的 ${VAR} 展开)----
def test_read_mcp_server_configs(tmp_path, monkeypatch):
    from karvyloop.coding.tools.mcp_tool import read_mcp_server_configs
    assert read_mcp_server_configs("") == []                 # 没配 → 空
    assert read_mcp_server_configs(str(tmp_path / "nope.yaml")) == []
    monkeypatch.setenv("MM_KEY", "SECRET-FAKE")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcp:\n  servers:\n    - name: websearch\n      command: uvx\n"
        "      args: ['--from', 'git+x', 'minimax-search']\n"
        "      env: { MINIMAX_API_KEY: '${MM_KEY}' }\n", encoding="utf-8")
    got = read_mcp_server_configs(str(cfg))
    assert len(got) == 1 and got[0].name == "websearch" and got[0].command == "uvx"
    assert got[0].args == ["--from", "git+x", "minimax-search"]
    assert got[0].env == {"MINIMAX_API_KEY": "SECRET-FAKE"}   # ${MM_KEY} 已从环境展开


def test_read_mcp_provider_ref_reuses_configured_key(tmp_path):
    """@provider:<name> 复用已配 provider 的 key;@provider_host 取其 host —— "复用你的 key"零额外设置。"""
    from karvyloop.coding.tools.mcp_tool import read_mcp_server_configs
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models:\n  providers:\n    minimax:\n      base_url: https://api.minimaxi.com/v1\n"
        "      api_key: MM-REAL-FAKE-DO-NOT-LEAK\n"
        "mcp:\n  servers:\n    - name: minimax\n      command: uvx\n"
        "      args: ['minimax-coding-plan-mcp', '-y']\n"
        "      env:\n        MINIMAX_API_KEY: '@provider:minimax'\n"
        "        MINIMAX_API_HOST: '@provider_host:minimax'\n", encoding="utf-8")
    got = read_mcp_server_configs(str(cfg))
    assert len(got) == 1
    assert got[0].env["MINIMAX_API_KEY"] == "MM-REAL-FAKE-DO-NOT-LEAK"      # 复用已配 key
    assert got[0].env["MINIMAX_API_HOST"] == "https://api.minimaxi.com"    # 取区域 host(去掉 /v1)


# ---- 跨事件循环桥(agent 在 worker 线程的另一个 loop 调用,MCP 会话在主 loop)----
def test_cross_loop_bridge_calls_on_session_loop():
    """McpAgentTool 带 loop 时,把 call 调度回会话所在 loop;在另一个 loop 上 await 到结果。
    这正是 console 真实形态:会话在主循环,agent 跑在 worker 线程的 asyncio.run 循环里。"""
    import asyncio
    import threading

    # 1) 在线程 A 起一个"会话循环",FakeSession 记录它在哪个 loop 被调用
    sess_loop_holder = {}
    class _LoopAwareSession:
        async def call_tool(self, name, arguments=None):
            sess_loop_holder["called_on"] = asyncio.get_running_loop()
            return _Result([_Text("bridged ok")])
    session_loop = asyncio.new_event_loop()
    t = threading.Thread(target=session_loop.run_forever, daemon=True)
    t.start()
    try:
        tool = McpAgentTool(server_name="ws", mcp_tool_name="search", description="",
                            parameters={}, session=_LoopAwareSession(), loop=session_loop)
        # 2) 在"另一个 loop"(模拟 agent 的 asyncio.run)上调用工具
        agent_loop = asyncio.new_event_loop()
        try:
            r = agent_loop.run_until_complete(tool({"q": "x"}))
        finally:
            agent_loop.close()
        assert r.ok is True and "bridged ok" in r.payload   # 围栏内,数据仍可读
        # call_tool 真的跑在**会话循环**上,而不是 agent 循环 → 桥成功
        assert sess_loop_holder["called_on"] is session_loop
    finally:
        session_loop.call_soon_threadsafe(session_loop.stop)
        t.join(timeout=3)
