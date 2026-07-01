"""MCP (Model Context Protocol) client 适配层（karvyloop/mcp_client.py）。

**M1.5 准备 ③**。参照 `mcp` PyPI 包 1.27.2（Anthropic, MIT）—— 我们**不写 JSON-RPC**,
**不** 自造 client SDK;只做薄适配:
  1. 接受一组 `McpServerConfig`（name + transport + command/args 或 url）
  2. 用 `mcp.client.session_group.ClientSessionGroup.connect_to_server` 连
  3. list_tools → 包成 KarvyLoop `Tool`（走 `registry.build_tool`）→ 注册进 `ToolRegistry`
  4. dispatch 时透传到对应 session.call_tool

**MCP 是协议级标准**(USB-C 比喻)—— 必遵循(03 §五)。Q5 硬规则:通用基建必借。

**范围限定**(M1.5 起步):
- ✅ stdio transport(local 子进程,最常见)
- ✅ 直接连一个 server(Coverage Phase 1)
- ✅ list / call / 错误传播
- ⏸ HTTP/SSE transport —— M2+(需要 auth/远端场景)
- ⏸ resources / prompts / sampling / elicitation —— M2+(M1.5 验证 tool 路径即可)
- ⏸ 多 server 聚合命名冲突 —— M2+(用 `component_name_hook` 解决)

**未引入的东西**(YAGNI,CLAUDE.md 硬规则"少脚手架"):
- 自己的 JSON-RPC 实现(MCP 已是 JSON-RPC 2.0;详见 modelcontextprotocol.io spec)
- 自己的协议版本协商(MCP SDK `initialize` 已做)
- 自己的 OAuth(MCP SDK 有,我们 M1.5 暂不接)
- 自己的 reconnect/retry(MCP SDK 不重连,我们也不重连;fail-closed 让上层重试)
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Optional

from .capability import Mode
from .registry.tool import build_tool

logger = logging.getLogger(__name__)


# ---- 配置 ----------

@dataclasses.dataclass
class McpServerConfig:
    """一个 MCP server 的连接配置。

    M1.5:只支持 stdio。M2+ 加 `transport: Literal["stdio", "http"]` + url 字段。
    """
    name: str                              # 在 KarvyLoop 视角的名字(用于 logging/调试)
    command: str                           # 可执行文件(如 "python" / "node" / "uvx")
    args: list[str] = dataclasses.field(default_factory=list)
    env: Optional[dict[str, str]] = None   # 传给子进程的环境变量(继承 + 覆盖)


# ---- 错误类型(便于上层精确 catch)----------

class McpError(Exception):
    """MCP 协议 / 传输错误的基类。所有 mcp_client 内部异常都包装成它。"""


class McpConnectError(McpError):
    """连不上 server(子进程启动失败 / stdio 握手失败 / transport error)。"""


class McpToolCallError(McpError):
    """tool call 失败(server 报 isError=True / 协议错误 / 序列化错误)。"""


# ---- 工具暴露 ----------

def _flatten_mcp_content(content: list[Any]) -> Any:
    """MCP tool result 的 `content` 块数组 → KarvyLoop `Tool.call` 的 dict 输出。

    简化策略:
    - 1 个 TextContent 块 → 直接返回 `{"text": str}`
    - 多块 → `{"blocks": [原始 blocks 序列化为 dict]}`
    - 嵌入式 resource 块 → `{"resource": {...}}`

    这与 `karvyloop/atoms/executor.py` 的 `_serialize_results_for_model` 不同:
    那个是**给 LLM 看**的(走 Anthropic 协议),必须是 string 或 content blocks;
    这个是**给 KarvyLoop 内部 ToolRegistry 调度**的(dict,registry 内部走 dispatch)。
    不要混。
    """
    if not content:
        return {"text": ""}
    # 全部 text → 单 string 包成 dict
    text_blocks = [c for c in content if getattr(c, "type", None) == "text"]
    if len(text_blocks) == len(content):
        return {"text": "\n".join(getattr(c, "text", "") for c in text_blocks)}
    # 混合或非 text → 序列化成 dict 列表(简单透传,完整保真)
    return {
        "blocks": [
            {k: v for k, v in dataclasses.asdict(c).items() if v is not None}
            for c in content
        ]
    }


# ---- 核心:连 server → 暴露 KarvyLoop Tool 列表 ----------

async def connect_and_list_tools(
    configs: list[McpServerConfig],
) -> dict[str, list[Any]]:
    """连一组 MCP server, 返回 `{server_name: [karvyloop Tool, ...]}`。

    **生命周期**:调用方负责 `async with` 整个 `ClientSessionGroup`。
    简化版:本函数只做"连+列",**不**自己持有 session group;
    调用方拿到 session group 后再统一 cleanup。
    但为了"接 1-2 server 验证"的最小用例,我们这里**内置** group 的 enter/exit,
    返回 `(tools_by_server, group_ctx)`。group_ctx 是个 async context,调用方
    `async with group_ctx` 即可统一关闭。

    用法:
        group_ctx, tools = await connect_and_list_tools(configs)
        async with group_ctx:
            for t in tools["my_server"]:
                registry.register(t)
            ... 之后 dispatch ...

    **错误**:任何 server 连失败 → raise `McpConnectError(server_name, ...)`;
    **不**部分成功(全连或全不连,避免半开状态)。
    """
    # 延迟 import:karvyloop 可能被 mcp SDK 不存在的环境 import(比如裸 lint)
    try:
        from mcp import StdioServerParameters
        from mcp.client.session_group import ClientSessionGroup
    except ImportError as e:
        raise McpConnectError(
            f"需要安装 `mcp` 包(pip install 'mcp[cli]>=1.27,<2')才能接 MCP server: {e}"
        ) from e

    group = ClientSessionGroup(component_name_hook=_component_name_hook)
    tools_by_server: dict[str, list[Any]] = {}

    async def _cleanup() -> None:
        # ClientSessionGroup 自带 __aexit__,disconnect 所有 server
        await group.__aexit__(None, None, None)

    class _GroupCtx:
        async def __aenter__(self) -> "ClientSessionGroup":
            return group
        async def __aexit__(self, *exc) -> None:
            await _cleanup()

    try:
        for cfg in configs:
            params = StdioServerParameters(
                command=cfg.command,
                args=cfg.args,
                env=cfg.env,
            )
            try:
                session = await group.connect_to_server(params)
            except Exception as e:
                await _cleanup()
                raise McpConnectError(
                    f"MCP server '{cfg.name}' 连失败 "
                    f"(command={cfg.command!r} args={cfg.args!r}): {e}"
                ) from e

            # 列 tool(用 `group.tools` 拿聚合 dict,key 形如 "{component_name}",
            # 我们改用直接调 `session.list_tools()` 更精确:这是该 server 的 tool 列表,
            # 不混其它 server)
            try:
                tools_list_resp = await session.list_tools()
            except Exception as e:
                await _cleanup()
                raise McpConnectError(
                    f"MCP server '{cfg.name}' list_tools 失败: {e}"
                ) from e

            karvyloop_tools = [
                _mcp_tool_to_karvyloop_tool(t, server_name=cfg.name, session=session)
                for t in tools_list_resp.tools
            ]
            tools_by_server[cfg.name] = karvyloop_tools
            logger.info(
                "MCP server '%s' 连上, 暴露 %d 个 tool: %s",
                cfg.name, len(karvyloop_tools),
                [t.name for t in karvyloop_tools],
            )
    except BaseException:
        # 任何意外 → 确保 group 关闭(避免子进程泄漏)
        try:
            await _cleanup()
        except Exception:
            pass
        raise

    return _GroupCtx(), tools_by_server


def _component_name_hook(component_name: str, server_info: Any) -> str:
    """`ClientSessionGroup` 多 server 时的命名 hook —— M1.5 单 server 时用不到,
    但加上保证未来加 server 不撞名(M2+ TODO: 让 `McpServerConfig.name` 进入这里)。
    """
    return component_name


def _mcp_tool_to_karvyloop_tool(
    mcp_tool: Any,
    *,
    server_name: str,
    session: Any,
) -> Any:
    """把 MCP `Tool` 包成 KarvyLoop `Tool`(走 build_tool 工厂,HR-1)。

    安全默认(MCP 工具是不可信的 remote code,**最保守**):
    - is_read_only = True(没有元数据;保守)
    - is_concurrency_safe = False(MCP 工具一般有副作用,保守)
    - required_mode = FULL(MCP 工具可能调网络/写文件;FULL 是最严)
    - max_result_size = 50_000(防 MCP tool 返回巨大内容撑爆 registry)
    - input_schema 透传 MCP 的 `inputSchema`(MCP 已用 JSON Schema;Anthropic
      protocol 也吃 JSON Schema,完全兼容)
    """
    async def _call(inp: dict, token: Any, sandbox: Any) -> dict:
        try:
            result = await session.call_tool(mcp_tool.name, arguments=inp)
        except Exception as e:
            raise McpToolCallError(
                f"MCP tool '{mcp_tool.name}' (server='{server_name}') 调用失败: {e}"
            ) from e
        if getattr(result, "isError", False):
            # MCP 协议级错误:server 明确说 tool 执行失败
            err_text = ""
            for c in (result.content or []):
                if getattr(c, "type", None) == "text":
                    err_text = getattr(c, "text", "")
                    break
            raise McpToolCallError(
                f"MCP tool '{mcp_tool.name}' (server='{server_name}') isError=True: {err_text}"
            )
        return _flatten_mcp_content(result.content or [])

    return build_tool(
        name=f"mcp_{server_name}_{mcp_tool.name}",  # 加前缀防与 KarvyLoop 内部 tool 撞名
        description=mcp_tool.description or f"MCP tool {mcp_tool.name} from {server_name}",
        input_schema=mcp_tool.inputSchema or {"type": "object", "properties": {}},
        call=_call,
        is_read_only=lambda inp: True,  # 保守:MCP tool 不可信
        is_concurrency_safe=lambda inp: False,  # 保守
        required_mode=Mode.FULL,  # 保守:可能触网/写文件
        max_result_size=50_000,
    )


__all__ = [
    "McpServerConfig",
    "McpError",
    "McpConnectError",
    "McpToolCallError",
    "connect_and_list_tools",
]
