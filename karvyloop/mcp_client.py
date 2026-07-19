"""MCP (Model Context Protocol) client 适配层（karvyloop/mcp_client.py）。

**M1.5 准备 ③**。参照 `mcp` PyPI 包 1.27.2（Anthropic, MIT）—— 我们**不写 JSON-RPC**,
**不** 自造 client SDK;只做薄适配:
  1. 接受一组 `McpServerConfig`（name + transport + command/args 或 url）
  2. 用 `mcp.client.session_group.ClientSessionGroup.connect_to_server` 连
  3. list_tools → 包成 KarvyLoop `Tool`（走 `registry.build_tool`）→ 注册进 `ToolRegistry`
  4. dispatch 时透传到对应 session.call_tool

**MCP 是协议级标准**(USB-C 比喻)—— 必遵循(03 §五)。Q5 硬规则:通用基建必借。

**范围限定**:
- ✅ stdio transport(local 子进程,最常见)
- ✅ streamable HTTP transport(remote/vendor 托管 server,MCP 2025 规范的现行 remote
  transport;SSE 是旧的,不做)—— 鉴权 v1 = bearer token / 自定义 header(config 里配)
- ✅ 直接连一组 server / list / call / 错误传播
- ⏸ 完整 OAuth 2.1 授权码流程(浏览器跳转拿 token)—— P2(SDK 有 OAuthClientProvider,
  但要 token 存储 + 本地回调 server + 开浏览器,v1 先 bearer)
- ⏸ resources / prompts / sampling / elicitation —— M2+(验证 tool 路径即可)
- ⏸ 多 server 聚合命名冲突 —— M2+(用 `component_name_hook` 解决)

**未引入的东西**(YAGNI,CLAUDE.md 硬规则"少脚手架"):
- 自己的 JSON-RPC 实现(MCP 已是 JSON-RPC 2.0;详见 modelcontextprotocol.io spec)
- 自己的协议版本协商(MCP SDK `initialize` 已做)
- 自己的 HTTP 客户端(SDK `streamable_http_client` + httpx 已做)
- 自己的 reconnect/retry(MCP SDK 不重连,我们也不重连;fail-closed 让上层重试)

**凭证纪律**(remote server 常带 Authorization/API-key header):headers 只住 config.yaml
(仓外,export 排除),**绝不进 log / 异常文本 / repr**;错误信息里 URL 一律去 query
(防 token-in-query 泄露)。
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from typing import Any, NoReturn, Optional

from .capability import Mode
from .registry.tool import build_tool

logger = logging.getLogger(__name__)


# ---- 配置 ----------

@dataclasses.dataclass
class McpServerConfig:
    """一个 MCP server 的连接配置(stdio 本地子进程 / streamable HTTP 远端)。

    区分方式:有 `url` → http(remote);有 `command` → stdio(local)。
    `transport` 可显式指定("http"/"stdio"),不指定按上面规则推断。
    `headers` 是 remote 的鉴权/自定义 header(如 Authorization: Bearer …)——
    **凭证级数据**:绝不 log、绝不进异常文本、绝不出现在 repr。
    """
    name: str                              # 在 KarvyLoop 视角的名字(用于 logging/调试)
    command: str = ""                      # stdio:可执行文件(如 "python" / "node" / "uvx")
    args: list[str] = dataclasses.field(default_factory=list)
    env: Optional[dict[str, str]] = None   # stdio:传给子进程的环境变量(继承 + 覆盖)
    url: str = ""                          # http:server 端点(https://…/mcp)
    transport: str = ""                    # ""=按 url/command 推断;或显式 "http"/"stdio"
    headers: dict[str, str] = dataclasses.field(
        default_factory=dict, repr=False)  # http:鉴权 header(repr=False 防泄露)

    @property
    def transport_kind(self) -> str:
        """实际生效的 transport:显式指定优先,否则 url→http、command→stdio。"""
        t = (self.transport or "").strip().lower()
        if t in ("http", "streamable-http", "streamable_http"):
            return "http"
        if t == "stdio":
            return "stdio"
        return "http" if self.url else "stdio"


def _redact_url(url: str) -> str:
    """错误信息/日志里的 URL 一律去 query+fragment(有人把 token 放 query 里)。"""
    return (url or "").split("?", 1)[0].split("#", 1)[0]


def _server_params(cfg: McpServerConfig) -> Any:
    """`McpServerConfig` → MCP SDK 的 ServerParameters(stdio 或 streamable HTTP)。

    校验 fail-loud:http 缺 url / stdio 缺 command → McpConnectError(信息不含凭证)。
    """
    kind = cfg.transport_kind
    if kind == "http":
        if not cfg.url:
            raise McpConnectError(
                f"MCP server '{cfg.name}' 声明 transport=http 但没配 url")
        if not str(cfg.url).lower().startswith(("http://", "https://")):
            raise McpConnectError(
                f"MCP server '{cfg.name}' url 必须是 http(s)://…: {_redact_url(str(cfg.url))!r}")
        try:
            from mcp.client.session_group import StreamableHttpParameters
        except ImportError as e:
            raise McpConnectError(
                "接 remote MCP server 需要 mcp SDK ≥1.9"
                "(pip install 'mcp>=1.9'): " + str(e)) from e
        return StreamableHttpParameters(url=cfg.url, headers=dict(cfg.headers) or None)
    if not cfg.command:
        raise McpConnectError(
            f"MCP server '{cfg.name}' 没配 command(stdio)也没配 url(http),连不了")
    from mcp import StdioServerParameters
    return StdioServerParameters(command=cfg.command, args=list(cfg.args or []), env=cfg.env)


def _conn_target(cfg: McpServerConfig) -> str:
    """错误信息里的"连接目标"描述 —— 只含 command/args 或去 query 的 url,绝不含 headers/env。"""
    if cfg.transport_kind == "http":
        return f"url={_redact_url(cfg.url)!r}"
    return f"command={cfg.command!r} args={cfg.args!r}"


def _scrub_secrets(text: str, cfg: McpServerConfig) -> str:
    """把下游异常文本里可能回显的凭证(headers/env 的值)抹成 ***。

    httpx/SDK 的异常我们不可控 —— 宁可多抹。太短的值(<6)不抹(避免把 "1"/"true"
    这类值全文替换搞花错误信息;凭证不可能那么短)。
    """
    values: list[str] = [str(v) for v in (cfg.headers or {}).values()]
    values += [str(v) for v in (cfg.env or {}).values()]
    for v in values:
        for candidate in (v, v[7:] if v.lower().startswith("bearer ") else ""):
            if candidate and len(candidate) >= 6 and candidate in text:
                text = text.replace(candidate, "***")
    return text


# ---- 连接生命周期(自管 AsyncExitStack;不用 SDK ClientSessionGroup)----------
#
# 为什么不用 ClientSessionGroup:它的失败路径只 `except Exception`,而 streamable
# HTTP 的传输错误(如 401)在 SDK 里是任务组先取消宿主 task —— 到达我们这层的是
# CancelledError(BaseException),真实错误憋在任务组里。Group 因此**不收栈**,
# anyio cancel scope 泄漏,之后在别的 task 被 GC 关闭 → "Attempted to exit cancel
# scope in a different task"。自管栈 + 同 task `BaseException` 收栈就没这个洞,
# 收栈时还能把真实错误(HTTPStatusError 等)捞出来放进 McpConnectError。


async def _open_session(stack: contextlib.AsyncExitStack, cfg: McpServerConfig) -> Any:
    """在 stack 上打开一个 MCP session(按 cfg 的 transport)并 initialize。

    失败时**不**自己收栈 —— 由调用方在**同一个 task** 里 aclose(anyio cancel
    scope 必须在进入它的 task 退出)。
    """
    import mcp as _mcp
    params = _server_params(cfg)   # 校验 + 类型分派(fail-loud)
    if cfg.transport_kind == "http":
        import httpx
        from mcp.client.streamable_http import streamable_http_client
        # headers(含 Authorization)只进 httpx client,绝不 log
        http_client = httpx.AsyncClient(
            headers=dict(cfg.headers) or None,
            timeout=httpx.Timeout(params.timeout.total_seconds(),
                                  read=params.sse_read_timeout.total_seconds()),
            follow_redirects=True,
        )
        await stack.enter_async_context(http_client)
        read, write, _get_sid = await stack.enter_async_context(
            streamable_http_client(url=cfg.url, http_client=http_client,
                                   terminate_on_close=params.terminate_on_close))
    else:
        read, write = await stack.enter_async_context(_mcp.stdio_client(params))
    session = await stack.enter_async_context(_mcp.ClientSession(read, write))
    await session.initialize()
    return session


async def _drain(stack: contextlib.AsyncExitStack) -> Optional[BaseException]:
    """同 task 收栈;把收栈时冒出来的真实错误(任务组里的 HTTPStatusError 等)带回来。"""
    try:
        await stack.aclose()
    except BaseException as ce:  # noqa: BLE001 —— 收栈错误就是我们要捞的东西
        return ce
    return None


def _leaf_exception(e: BaseException) -> BaseException:
    """ExceptionGroup → 第一个叶子(错误信息给人看,别糊一层 group 包装)。"""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


async def _fail_connect(stack: contextlib.AsyncExitStack, e: BaseException,
                        cfg: McpServerConfig) -> NoReturn:
    """连接失败统一出口:同 task 收栈 → 分类 → 抛 McpConnectError(凭证已抹)。"""
    drained = await _drain(stack)
    if isinstance(e, (McpConnectError, KeyboardInterrupt, SystemExit)):
        raise e
    if isinstance(e, asyncio.CancelledError) and drained is None:
        raise e   # 真·外部取消(如 console 关停),不吞
    cause = drained if drained is not None else e
    leaf = _leaf_exception(cause)
    raise McpConnectError(
        f"MCP server '{cfg.name}' 连失败 ({_conn_target(cfg)}): "
        f"{type(leaf).__name__}: {_scrub_secrets(str(leaf), cfg)}"
    ) from cause


# ---- 错误类型(便于上层精确 catch)----------

class McpError(Exception):
    """MCP 协议 / 传输错误的基类。所有 mcp_client 内部异常都包装成它。"""


class McpConnectError(McpError):
    """连不上 server(子进程启动失败 / stdio 握手失败 / transport error)。"""


class McpToolCallError(McpError):
    """tool call 失败(server 报 isError=True / 协议错误 / 序列化错误)。"""


# ---- 工具暴露 ----------

# 工具描述封顶:MCP server(尤其 remote)是 untrusted,描述是**数据不是指令**。
_DESC_CAP = 4000


def sanitize_untrusted_text(text: str, cap: int = _DESC_CAP) -> str:
    """server 提供的自由文本(工具描述等)→ 只当数据:去控制字符(留 \\n\\t)、封长。

    注意这**不是**注入防御的全部:真正的护栏是 ①描述改不了 capability(下限由
    policy.required_mode 按 `mcp_` 前缀定 / 本模块 build_tool 固定 Mode.FULL,
    server 元数据一概不参与授权);②我们的代码从不解析/执行描述内容。这里只是
    卫生层:去隐藏控制字符、封长,防污染 prompt。
    """
    s = str(text or "")
    s = "".join(ch for ch in s if ch in "\n\t" or ord(ch) >= 32)
    if len(s) > cap:
        s = s[:cap] + " …[truncated]"
    return s


def _fence_text_fields(obj: Any, source: str) -> Any:
    """递归把 dict/list 里的 "text" 字符串字段过统一不可信围栏(混合 blocks / 嵌入 resource 同防)。"""
    from karvyloop.cognition.fence import fence_untrusted
    if isinstance(obj, dict):
        return {
            k: (fence_untrusted(v, source=source) if k == "text" and isinstance(v, str)
                else _fence_text_fields(v, source))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_fence_text_fields(x, source) for x in obj]
    return obj


def _flatten_mcp_content(content: list[Any], *, source: str = "mcp") -> Any:
    """MCP tool result 的 `content` 块数组 → KarvyLoop `Tool.call` 的 dict 输出。

    简化策略:
    - 1 个 TextContent 块 → 直接返回 `{"text": str}`
    - 多块 → `{"blocks": [原始 blocks 序列化为 dict]}`
    - 嵌入式 resource 块 → `{"resource": {...}}`

    这与 `karvyloop/atoms/executor.py` 的 `_serialize_results_for_model` 不同:
    那个是**给 LLM 看**的(走 Anthropic 协议),必须是 string 或 content blocks;
    这个是**给 KarvyLoop 内部 ToolRegistry 调度**的(dict,registry 内部走 dispatch)。
    不要混。

    **统一不可信围栏(OWASP LLM01/ASI01)**:MCP server 是 untrusted,返回的文本是
    **数据不是指挥者**——所有 text 字段过 cognition.fence.fence_untrusted(包裹 +
    双向假标签擦除)再回给 registry(最终经 _serialize_results_for_model 进模型)。
    模型仍能用结果内容答题;只是结果里夹带的"忽略上文/去删文件"不构成合法指令来源。
    """
    from karvyloop.cognition.fence import fence_untrusted
    if not content:
        return {"text": ""}
    # 全部 text → 单 string 包成 dict(围栏包整段,一层不嵌套)
    text_blocks = [c for c in content if getattr(c, "type", None) == "text"]
    if len(text_blocks) == len(content):
        joined = "\n".join(getattr(c, "text", "") for c in text_blocks)
        return {"text": fence_untrusted(joined, source=source)}
    # 混合或非 text → 序列化成 dict 列表(结构透传;text 字段逐个过围栏)
    return {
        "blocks": [
            _fence_text_fields(
                {k: v for k, v in dataclasses.asdict(c).items() if v is not None}, source)
            for c in content
        ]
    }


# ---- 核心:连 server → 暴露 KarvyLoop Tool 列表 ----------

async def connect_and_list_tools(
    configs: list[McpServerConfig],
) -> dict[str, list[Any]]:
    """连一组 MCP server(stdio 本地 / streamable HTTP 远端),
    返回 `(group_ctx, {server_name: [karvyloop Tool, ...]})`。

    **生命周期**:所有连接(httpx client / 子进程 / session)都挂在一个自管
    AsyncExitStack 上;group_ctx 是它的 async context 包装,调用方
    `async with group_ctx` 即可统一关闭。**连接和关闭必须在同一个 task**
    (anyio cancel scope 的要求;console lifespan 天然满足)。

    用法:
        group_ctx, tools = await connect_and_list_tools(configs)
        async with group_ctx:
            for t in tools["my_server"]:
                registry.register(t)
            ... 之后 dispatch ...

    **错误**:任何 server 连失败 → raise `McpConnectError(server_name, ...)`;
    **不**部分成功(全连或全不连,避免半开状态)。错误文本**绝不含 headers/env**。
    """
    # 延迟 import:karvyloop 可能被 mcp SDK 不存在的环境 import(比如裸 lint)
    try:
        import mcp  # noqa: F401
    except ImportError as e:
        raise McpConnectError(
            f"需要安装 `mcp` 包(pip install 'mcp[cli]>=1.27,<2')才能接 MCP server: {e}"
        ) from e

    stack = contextlib.AsyncExitStack()
    tools_by_server: dict[str, list[Any]] = {}

    class _GroupCtx:
        async def __aenter__(self) -> contextlib.AsyncExitStack:
            return stack
        async def __aexit__(self, *exc) -> None:
            await stack.aclose()

    for cfg in configs:
        try:
            session = await _open_session(stack, cfg)
            tools_list_resp = await session.list_tools()
        except BaseException as e:  # noqa: BLE001 —— 见 _fail_connect(同 task 收栈)
            await _fail_connect(stack, e, cfg)

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

    return _GroupCtx(), tools_by_server


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
            # MCP 协议级错误:server 明确说 tool 执行失败。错误文本也是 server 可控的
            # 不可信内容(最终会进 tool_result 喂模型)→ 过假标签擦除再携带。
            from karvyloop.cognition.fence import scrub_untrusted
            err_text = ""
            for c in (result.content or []):
                if getattr(c, "type", None) == "text":
                    err_text = getattr(c, "text", "")
                    break
            raise McpToolCallError(
                f"MCP tool '{mcp_tool.name}' (server='{server_name}') isError=True: "
                f"{scrub_untrusted(err_text)}"
            )
        return _flatten_mcp_content(result.content or [],
                                    source=f"mcp:{server_name}:{mcp_tool.name}")

    return build_tool(
        name=f"mcp_{server_name}_{mcp_tool.name}",  # 加前缀防与 KarvyLoop 内部 tool 撞名
        description=sanitize_untrusted_text(mcp_tool.description or "")
        or f"MCP tool {mcp_tool.name} from {server_name}",
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
    "sanitize_untrusted_text",
]
