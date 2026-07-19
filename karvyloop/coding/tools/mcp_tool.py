"""mcp_tool — 把 MCP server 的 tool 适配成 agent 工具集里的 CodingTool 形状(A 步骤 1a)。

为什么:agent 的工具集是 `coding/tools` 的 CodingTool 形状(`name`/`description`/`parameters`/
`is_concurrency_safe(inp)`/`async __call__(inp)->CodingResult`,executor 里 `await tool(inp)` 调用);
而 `karvyloop/mcp_client.py` 连 MCP server 拿到的是 `registry.Tool`(`call(inp,token,sandbox)`)。
两套形状不一样,所以要个**适配器**把 MCP tool 包成 agent 能直接用的工具。

这一层**不持有会话生命周期**:MCP `session` 由调用方(console 启动时连、关闭时断)持有并保活,
本适配器只在被调用那一刻用 `session.call_tool`。这样"通用 MCP→agent"接线就和具体 provider
(minimax/其它)解耦了——任何搜索/工具类 MCP server 都能即插即用,不写死某个模型。

设计取向(对齐 Hardy):不为每个模型写死;keyless 是地板;provider 复用走这条通用 MCP 通道
(minimax 的 search MCP 即其一)+ 原生 tool 适配(anthropic/openai/gemini)另走 adapter。
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from karvyloop.mcp_client import sanitize_untrusted_text

from ._result import CodingResult


def _first_text(content: list) -> str:
    for c in content or []:
        if getattr(c, "type", None) == "text":
            return getattr(c, "text", "") or ""
    return ""


def _flatten(content: list, *, source: str = "mcp") -> Any:
    """MCP tool result 的 content 块 → 给 agent/LLM 看的文本(多块/非文本则保留结构)。

    统一不可信围栏(OWASP LLM01/ASI01,与 mcp_client._flatten_mcp_content 同一收口):
    server 返回的文本过 cognition.fence.fence_untrusted(数据不是指挥者;包裹+双向假标签
    擦除)。"[+N non-text block(s)]" 是**我们自己的**标注,不是 server 数据,留在围栏外。
    """
    from karvyloop.cognition.fence import fence_untrusted
    if not content:
        return ""
    texts = [c for c in content if getattr(c, "type", None) == "text"]
    if len(texts) == len(content):
        return fence_untrusted("\n".join(getattr(c, "text", "") or "" for c in texts),
                               source=source)
    # 混合/非文本:尽量给出文本 + 标注还有其它块,别静默丢
    head = fence_untrusted("\n".join(getattr(c, "text", "") or "" for c in texts),
                           source=source)
    return (head + ("\n" if head else "") + f"[+{len(content) - len(texts)} non-text block(s)]").strip()


class McpAgentTool:
    """一个 MCP tool 的 agent 侧适配器(CodingTool 形状)。

    保守默认(MCP 是不可信 remote):`is_concurrency_safe=False`(可能有副作用)。
    名字加 `mcp_<server>_` 前缀,防与内置 read/write/edit/bash/web 撞名。
    """

    def __init__(self, *, server_name: str, mcp_tool_name: str, description: str,
                 parameters: dict, session: Any, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.name = f"mcp_{server_name}_{mcp_tool_name}"
        # 描述来自 server(untrusted,remote 尤甚)→ 只当数据:去控制字符+封长。
        # 它改不了权限:capability 下限由工具名 `mcp_` 前缀定(policy.required_mode),
        # server 自称 "read-only"/"safe" 之类的元数据一概不参与授权。
        self.description = sanitize_untrusted_text(description) \
            or f"MCP tool {mcp_tool_name} from {server_name}"
        self.parameters = parameters or {"type": "object", "properties": {}}
        self._server_name = server_name
        self._mcp_tool_name = mcp_tool_name
        self._session = session
        # MCP 会话绑定它创建时的事件循环(console 主循环);agent 跑在 worker 线程的**另一个**
        # asyncio.run 循环里(main_loop.py:asyncio.run(generate_and_run))。所以调用要跨循环桥回去。
        self._loop = loop

    def is_concurrency_safe(self, inp: dict) -> bool:
        return False   # 保守:MCP 工具可能触网/写文件/有副作用

    async def _call_tool(self, inp: dict):
        coro = self._session.call_tool(self._mcp_tool_name, arguments=inp or {})
        if self._loop is not None:
            # 跨循环:把 call 调度回会话所在的主循环,在当前(worker)循环上 await 结果。
            # 同循环也安全(await 会让出,主循环能跑起被调度的协程,不死锁)。
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._loop))
        return await coro

    async def __call__(self, inp: dict) -> CodingResult:
        try:
            result = await self._call_tool(inp)
        except Exception as e:
            return CodingResult(ok=False, payload=None, error_code=4,
                                error_message=f"MCP 工具 '{self._mcp_tool_name}'"
                                              f"(server={self._server_name})调用失败: {type(e).__name__}: {e}")
        if getattr(result, "isError", False):
            # 错误文本也是 server 可控的不可信内容(会进模型上下文)→ 过假标签擦除再携带。
            from karvyloop.cognition.fence import scrub_untrusted
            return CodingResult(ok=False, payload=None, error_code=4,
                                error_message="MCP 工具报错: "
                                              f"{scrub_untrusted(_first_text(getattr(result, 'content', [])))}")
        return CodingResult(ok=True, payload=_flatten(
            getattr(result, "content", []) or [],
            source=f"mcp:{self._server_name}:{self._mcp_tool_name}"))


def mcp_tools_from_session(session: Any, tools_list: list, server_name: str,
                           loop: Optional[asyncio.AbstractEventLoop] = None) -> dict:
    """把一个 MCP server 的 list_tools 结果包成 {tool_name: McpAgentTool}(键带 mcp_ 前缀)。"""
    out: dict = {}
    for t in tools_list or []:
        tool = McpAgentTool(
            server_name=server_name,
            mcp_tool_name=getattr(t, "name", ""),
            description=getattr(t, "description", "") or "",
            parameters=getattr(t, "inputSchema", None) or {"type": "object", "properties": {}},
            session=session,
            loop=loop,
        )
        out[tool.name] = tool
    return out


async def connect_mcp_agent_tools(configs: list) -> tuple:
    """连一组 MCP server(stdio 本地 / streamable HTTP 远端),返回
    `(group_ctx, {tool_name: McpAgentTool})`。

    在**当前事件循环**(应是 console 主循环)上连;把该 loop 记进每个工具,调用时跨线程桥回。
    `group_ctx` 由调用方保活:`await group_ctx.__aenter__()` 连上后**别退出**,直到 console 关闭再
    `await group_ctx.__aexit__(...)`(否则子进程会被回收、会话断;连接/关闭须同一 task,
    console lifespan 天然满足)。configs 里每项是 `McpServerConfig`
    (stdio 用 `.command/.args/.env`;http 用 `.url/.headers`)。
    任一 server 连失败 → 抛 McpConnectError(全或无);错误文本**绝不含 headers/env 值**。
    """
    import contextlib

    from karvyloop.mcp_client import McpConnectError, _fail_connect, _open_session
    try:
        import mcp  # noqa: F401
    except ImportError as e:
        raise McpConnectError(f"需要 `pip install mcp` 才能接 MCP server: {e}") from e

    loop = asyncio.get_running_loop()
    stack = contextlib.AsyncExitStack()

    class _GroupCtx:
        async def __aenter__(self):
            return stack
        async def __aexit__(self, *exc):
            await stack.aclose()

    tools: dict = {}
    for cfg in configs:
        try:
            session = await _open_session(stack, cfg)
            resp = await session.list_tools()
        except BaseException as e:  # noqa: BLE001 —— _fail_connect 同 task 收栈后必抛
            await _fail_connect(stack, e, cfg)
        tools.update(mcp_tools_from_session(session, resp.tools, cfg.name, loop=loop))
    return _GroupCtx(), tools


def _resolve_env_value(v: str, data: dict) -> str:
    """展开 MCP server env 的值。支持:
    - `${VAR}` → 从环境变量展开(密钥不写明文进 yaml,沿用既有约定);
    - `@provider:<name>` → **复用你已配的某 provider 的 api_key**(零额外设置,这是"复用 LLM key"的关键);
    - `@provider_host:<name>` → 取该 provider base_url 的 scheme://host(如 minimax 的区域 host)。
    """
    import os
    v = str(v)
    if v.startswith("@provider:") or v.startswith("@provider_host:"):
        host_mode = v.startswith("@provider_host:")
        name = v.split(":", 1)[1].strip()
        prov = ((data.get("models") or {}).get("providers") or {}).get(name) or {}
        if host_mode:
            base = os.path.expandvars(str(prov.get("base_url", "") or ""))
            import urllib.parse as _up
            u = _up.urlsplit(base if "://" in base else "https://" + base)
            return f"{u.scheme or 'https'}://{u.netloc}" if u.netloc else base
        return os.path.expandvars(str(prov.get("api_key", "") or ""))
    return os.path.expandvars(v)


def read_mcp_server_configs(config_path: str) -> list:
    """从 config.yaml 的 `mcp.servers` 读 MCP server 配置。没配 → [](不连、0 影响)。

    两种形状,按有没有 `url`/`command` 区分 transport(也可显式 `transport:`):

        mcp:
          servers:
            # ① stdio(本地子进程)—— 原有形状,不变
            - name: minimax
              command: uvx                                  # 需 PATH 有 uvx(pip install uv),或填绝对路径
              args: ["minimax-coding-plan-mcp", "-y"]
              env:
                MINIMAX_API_KEY: "@provider:minimax"        # 复用你已配的 minimax key,不用再填
                MINIMAX_API_HOST: "@provider_host:minimax"  # 自动用你 minimax 的区域 host
            # ② streamable HTTP(remote/vendor 托管)—— 贴 URL + 可选 token
            - name: notion
              url: https://mcp.notion.com/mcp
              token: "${NOTION_MCP_TOKEN}"                  # 糖:→ Authorization: Bearer <token>
              # 或自定义 header(API-key 风格):
              # headers: { X-API-Key: "${SOME_KEY}" }

    env/headers/token 的值都支持 `${VAR}`(环境变量)/ `@provider:<name>`(复用某
    provider 的 key)/ `@provider_host:<name>`(该 provider 的 host)。
    凭证只住 config.yaml(仓外,export 排除),这里读进内存后**绝不 log**。
    """
    from karvyloop.mcp_client import McpServerConfig
    out: list = []
    try:
        import pathlib
        import yaml
        if not config_path:
            return []
        p = pathlib.Path(config_path)
        if not p.exists():
            return []
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for s in ((data.get("mcp") or {}).get("servers") or []):
            if not isinstance(s, dict):
                continue
            name = str(s.get("name", "") or "").strip()
            command = str(s.get("command", "") or "").strip()
            url = str(s.get("url", "") or "").strip()
            transport = str(s.get("transport", "") or "").strip().lower()
            if not name or (not command and not url):
                continue
            # remote(streamable HTTP):有 url(或显式 transport: http)
            if url and transport != "stdio":
                headers = {str(k): _resolve_env_value(v, data)
                           for k, v in (s.get("headers") or {}).items()}
                token = _resolve_env_value(s.get("token", "") or "", data).strip()
                if token and "authorization" not in {k.lower() for k in headers}:
                    headers["Authorization"] = f"Bearer {token}"
                out.append(McpServerConfig(name=name, url=url, transport="http",
                                           headers=headers))
                continue
            # local(stdio):原有形状
            if not command:
                continue
            env = None
            if s.get("env"):
                env = {str(k): _resolve_env_value(v, data) for k, v in s["env"].items()}
            out.append(McpServerConfig(name=name, command=command,
                                       args=[str(a) for a in (s.get("args") or [])], env=env))
    except Exception:
        return []
    return out


__all__ = ["McpAgentTool", "mcp_tools_from_session", "connect_mcp_agent_tools",
           "read_mcp_server_configs", "sanitize_untrusted_text"]
