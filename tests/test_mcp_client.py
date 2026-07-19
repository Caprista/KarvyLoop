"""MCP client 适配层验收测试（tests/test_mcp_client.py）。

**M1.5 准备 ③**:接入 MCP 1-2 个 server 验证。**Q3 硬规则** + 03 §五:遵循 MCP spec。
**Q5 硬规则**:通用基建必借;我们只测自己那层"薄适配"。

ACs:
- AC1: 1 stdio server 连成功 + list_tools 返回 KarvyLoop Tool + 名字带 `mcp_<srv>_` 前缀
- AC2: 调 tool → 拿到正确 text result
- AC3: 不可用 command → `McpConnectError`(包装具体原因)
- AC4: tool call 报 isError=True → `McpToolCallError`
- AC5: 2 server 接入 → `tools_by_server` 字典 key 正确,各 server 工具不串
- AC6: group_ctx 退出后不再有子进程残留(`McpServerConfig` 启的 `python` 子进程)
- AC7: `flatten_mcp_content` 多形态正确(空 / 单 text / 多 text / 混合)
- AC8: input_schema 透传 MCP 的 `inputSchema`(JSON Schema 兼容)
"""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

# mcp 是可选的通用基建:没装 `mcp` 包就整模块跳过——`pytest -q` 照样全绿,
# 不需要任何 --ignore;想跑这组测试再 `pip install mcp` 即可。
pytest.importorskip("mcp")

from karvyloop.mcp_client import (  # noqa: E402
    McpConnectError,
    McpError,
    McpServerConfig,
    McpToolCallError,
    _flatten_mcp_content,
    connect_and_list_tools,
)


# ---- helper:写一个 stdio MCP server 到 tempdir 并 return config ----------

def _write_server(td: Path, *, name: str, tool_defs: str) -> Path:
    """生成一个 stdio MCP server 脚本到 td/{name}_server.py。"""
    # 注意:不传 f-string(tool_defs 含双花 brace)—— 用 .format 也不行(tool_defs 也有 {})
    # 改用拼接;tool_defs 自己的 dedent 留在调用方处理
    server_src = (
        "from mcp.server.fastmcp import FastMCP\n"
        f'mcp = FastMCP("test-{name}")\n'
        "\n"
        f"{tool_defs}\n"
        "\n"
        'if __name__ == "__main__":\n'
        '    mcp.run(transport="stdio")\n'
    )
    p = td / f"{name}_server.py"
    p.write_text(server_src, encoding="utf-8")
    return p


def _basic_tools() -> str:
    return textwrap.dedent("""\
        @mcp.tool()
        def echo(text: str) -> str:
            \"\"\"echo back the text verbatim\"\"\"
            return f"echo: {text}"

        @mcp.tool()
        def add(a: int, b: int) -> int:
            \"\"\"add two ints\"\"\"
            return a + b
    """)


def _failing_tool() -> str:
    """MCP server 里有 1 个 tool 永远报 isError=True。"""
    return textwrap.dedent("""\
        @mcp.tool()
        def always_fails(reason: str) -> str:
            \"\"\"always raises — used to test isError=True propagation\"\"\"
            raise ValueError(f"intentional failure: {reason}")
    """)


def _count_python_subprocesses() -> int:
    """当前 python 进程派生的子进程数(Windows 友好, 用 psutil)。"""
    me = psutil.Process(os.getpid())
    return len(me.children(recursive=True))


# ============ AC1:1 server 连成功 + list_tools + 名字带前缀 ============
@pytest.mark.asyncio
async def test_ac1_connect_one_server_lists_tools_with_prefix(tmp_path: Path):
    server = _write_server(tmp_path, name="srv1", tool_defs=_basic_tools())
    cfg = McpServerConfig(name="srv1", command=sys.executable, args=[str(server)])

    ctx, tools_by_server = await connect_and_list_tools([cfg])
    async with ctx:
        assert "srv1" in tools_by_server
        tool_names = [t.name for t in tools_by_server["srv1"]]
        # 名字必须带 mcp_srv1_ 前缀(防与 KarvyLoop 内部工具撞名)
        assert tool_names == ["mcp_srv1_echo", "mcp_srv1_add"]
        # 走的是 build_tool 工厂
        from karvyloop.registry.tool import is_factory_built
        for t in tools_by_server["srv1"]:
            assert is_factory_built(t) is True
        # description 来自 MCP tool description(不是兜底 "tool xxx")
        echo = next(t for t in tools_by_server["srv1"] if t.name == "mcp_srv1_echo")
        assert "echo" in (echo.description or "").lower()


# ============ AC2:调 tool → 拿到正确 text result ============
@pytest.mark.asyncio
async def test_ac2_call_tool_returns_text(tmp_path: Path):
    server = _write_server(tmp_path, name="srv2", tool_defs=_basic_tools())
    cfg = McpServerConfig(name="srv2", command=sys.executable, args=[str(server)])

    ctx, tools_by_server = await connect_and_list_tools([cfg])
    async with ctx:
        from karvyloop.cognition.fence import DATA_FENCE_CLOSE

        add_t = next(t for t in tools_by_server["srv2"] if t.name == "mcp_srv2_add")
        result = await add_t.call({"a": 7, "b": 35}, token=None, sandbox=None)
        # 1 个 TextContent 块 → {"text": str};text 过统一不可信围栏(MCP 返回是数据
        # 不是指挥者,tests/test_untrusted_fence.py 锁对抗面),内容仍可读可用。
        assert set(result) == {"text"}
        assert "42" in result["text"] and DATA_FENCE_CLOSE in result["text"]

        echo_t = next(t for t in tools_by_server["srv2"] if t.name == "mcp_srv2_echo")
        r2 = await echo_t.call({"text": "hi karvyloop"}, token=None, sandbox=None)
        assert "echo: hi karvyloop" in r2["text"] and DATA_FENCE_CLOSE in r2["text"]


# ============ AC3:不可用 command → McpConnectError(包装具体原因)============
@pytest.mark.asyncio
async def test_ac3_bad_command_raises_mcp_connect_error():
    # 不存在的可执行文件
    cfg = McpServerConfig(
        name="ghost",
        command="this-command-definitely-does-not-exist-xyz123",
        args=[],
    )
    with pytest.raises(McpConnectError) as exc_info:
        await connect_and_list_tools([cfg])
    msg = str(exc_info.value)
    assert "ghost" in msg
    # 具体原因被透传进 message
    assert "this-command-definitely-does-not-exist-xyz123" in msg


# ============ AC4:tool call 报 isError=True → McpToolCallError ============
@pytest.mark.asyncio
async def test_ac4_server_side_tool_error_raises_mcp_tool_call_error(tmp_path: Path):
    server = _write_server(tmp_path, name="srv_err", tool_defs=_failing_tool())
    cfg = McpServerConfig(name="srv_err", command=sys.executable, args=[str(server)])

    ctx, tools_by_server = await connect_and_list_tools([cfg])
    async with ctx:
        bad = tools_by_server["srv_err"][0]  # always_fails
        with pytest.raises(McpToolCallError) as exc_info:
            await bad.call({"reason": "boom"}, token=None, sandbox=None)
        msg = str(exc_info.value)
        assert "always_fails" in msg
        assert "srv_err" in msg
        assert "boom" in msg or "intentional failure" in msg  # error text 透传


# ============ AC5:2 server 接入 → 字典 key 正确,工具不串 ============
@pytest.mark.asyncio
async def test_ac5_two_servers_independent_tools(tmp_path: Path):
    s1 = _write_server(tmp_path, name="alpha", tool_defs=_basic_tools())
    s2 = _write_server(tmp_path, name="beta", tool_defs=_failing_tool())
    cfgs = [
        McpServerConfig(name="alpha", command=sys.executable, args=[str(s1)]),
        McpServerConfig(name="beta", command=sys.executable, args=[str(s2)]),
    ]
    ctx, tools_by_server = await connect_and_list_tools(cfgs)
    async with ctx:
        assert set(tools_by_server.keys()) == {"alpha", "beta"}
        # alpha 有 echo + add;beta 有 always_fails
        alpha_names = sorted(t.name for t in tools_by_server["alpha"])
        beta_names = sorted(t.name for t in tools_by_server["beta"])
        assert alpha_names == ["mcp_alpha_add", "mcp_alpha_echo"]
        assert beta_names == ["mcp_beta_always_fails"]
        # alpha 和 beta 互不污染
        for t in tools_by_server["alpha"]:
            assert "alpha" in t.name
        for t in tools_by_server["beta"]:
            assert "beta" in t.name


# ============ AC6:ctx 退出后子进程被清理(无残留)============
@pytest.mark.asyncio
async def test_ac6_group_ctx_exits_cleanly_no_zombie_subprocess(tmp_path: Path):
    before = _count_python_subprocesses()
    server = _write_server(tmp_path, name="srv_clean", tool_defs=_basic_tools())
    cfg = McpServerConfig(name="srv_clean", command=sys.executable, args=[str(server)])

    ctx, tools_by_server = await connect_and_list_tools([cfg])
    # 进入 ctx 时有 1 个子进程(server)
    async with ctx:
        during = _count_python_subprocesses()
        assert during >= before + 1, (
            f"应有 ≥{before + 1} 个子进程(server), 实际 {during}"
        )
    # 退出 ctx 后子进程应被清理(给点时间让 OS 回收)
    await asyncio.sleep(0.2)
    after = _count_python_subprocesses()
    assert after <= before, (
        f"ctx 退出后子进程应清理到 ≤{before}, 实际 {after} (泄漏了 {after - before} 个)"
    )


# ============ AC7:_flatten_mcp_content 多形态 ============
def test_ac7_flatten_mcp_content_shapes():
    """`_flatten_mcp_content` 是 KarvyLoop 内部 dict-shape 输出(与
    `_serialize_results_for_model` 的"给 LLM 看"不同 —— 后者走 Anthropic
    协议必须是 string/content-blocks,本函数是 registry 内部 dict)。

    text 字段过统一不可信围栏(cognition.fence.fence_untrusted;MCP 返回是数据
    不是指挥者)——内容仍在、可读可用;对抗面在 tests/test_untrusted_fence.py 锁。
    """
    import dataclasses

    from karvyloop.cognition.fence import DATA_FENCE_CLOSE

    @dataclasses.dataclass
    class _Text:
        text: str
        type: str = "text"

    @dataclasses.dataclass
    class _Img:
        type: str = "image"
        data: str = "abc"
        mimeType: str = "image/png"

    # 空 content(不伪造空围栏)
    assert _flatten_mcp_content([]) == {"text": ""}
    # 单 TextContent-like → 内容在围栏里
    out1 = _flatten_mcp_content([_Text("hello")])
    assert set(out1) == {"text"}
    assert "hello" in out1["text"] and DATA_FENCE_CLOSE in out1["text"]
    # 多 TextContent → \n join 后整段一层围栏(不嵌套)
    out2 = _flatten_mcp_content([_Text("a"), _Text("b")])
    assert "a\nb" in out2["text"] and out2["text"].count(DATA_FENCE_CLOSE) == 1
    # 混合 → blocks 列表(text 字段围栏,非 text 字段原样透传)
    out = _flatten_mcp_content([_Text("caption"), _Img()])
    assert "blocks" in out
    assert isinstance(out["blocks"], list)
    assert len(out["blocks"]) == 2
    assert "caption" in out["blocks"][0]["text"]
    assert DATA_FENCE_CLOSE in out["blocks"][0]["text"]
    assert out["blocks"][1]["data"] == "abc"


# ============ AC8:input_schema 透传(从 MCP 端到 KarvyLoop Tool 端)============
@pytest.mark.asyncio
async def test_ac8_input_schema_passthrough(tmp_path: Path):
    """MCP tool 的 `inputSchema` 必须**原样**进 KarvyLoop Tool 的 `input_schema`,
    这样上层(registry.exposed_tools → 模型 tool 定义)才能正确暴露参数。
    """
    tool_with_schema = textwrap.dedent('''
        @mcp.tool()
        def typed_op(
            text: str,
            count: int = 5,
            tags: list[str] | None = None,
        ) -> str:
            """a tool with non-trivial input schema"""
            return f"got text={text} count={count} tags={tags}"
    ''').strip()
    server = _write_server(tmp_path, name="srv_sch", tool_defs=tool_with_schema)
    cfg = McpServerConfig(name="srv_sch", command=sys.executable, args=[str(server)])

    ctx, tools_by_server = await connect_and_list_tools([cfg])
    async with ctx:
        t = tools_by_server["srv_sch"][0]
        # schema 透传(是 dict,有 properties)
        sch = t.input_schema
        assert sch["type"] == "object"
        # FastMCP 会把参数塞进 properties
        assert "text" in sch["properties"]
        assert sch["properties"]["text"]["type"] == "string"
        assert "count" in sch["properties"]
        assert sch["properties"]["count"]["type"] == "integer"
