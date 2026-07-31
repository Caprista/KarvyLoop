"""mcp_manager — MCP 连接组的可重入生命周期管理(docs/96 刀1:消灭「装完要重启」)。

之前:MCP server 只在 console lifespan 启动时连一次(app.py 内联)→ apply 预设后要重启
才生效。现在:连接生命周期抽进本模块 —— **启动 = 第一次 reconnect**;apply/add/手动重连
端点复用同一 `reconnect()`,装上即用。

设计要点(事实对齐,别发明):

- **每个 server 一个 keeper task**:anyio cancel scope 要求"进栈 / 收栈在同一个 task"
  (见 mcp_client.py 连接生命周期注释)。keeper 在自己 task 里 open → ready → 等 close
  → aclose,天然满足;reconnect/shutdown 只 await ready / 设 close 事件,绝不跨 task 碰栈。
  这也是为什么不能沿用 app.py 旧的"lifespan 里一个 group ctx"写法 —— endpoint task 和
  lifespan task 不是同一个 task,热加载必须换成 task 自包生命周期。

- **换组不破正在跑的 drive**:`runtime_kwargs["mcp_tools"]` 的消费时点是**每次 drive 开始**
  (routes.py/ws.py 每个请求先读 `app.state.runtime_kwargs` 再 `**rk` splat 进 drive_in_tui)
  → 换组时塞一个**新 dict**(绝不原地改老 dict),在跑的 drive 继续持有旧工具/旧会话,
  新 drive 拿新组。旧 server 退休后**排水**再断:等 inflight tool call 归零、且距最近一次
  调用有一段静默期(默认 180s,防 LLM 长思考的间隙被误判为"用完了"),硬上限 30min。
  诚实局限:drive 若在旧组关断后又调旧工具,会拿到干净的失败 CodingResult(与 remote
  server 瞬断同一形状),由 role 兜底 —— 不是静默丢。

- **fail-loud 按 server**:连不上哪个明说(name + 原因;凭证已被 mcp_client._fail_connect
  抹掉),连得上的照常装上 —— 不再"一个坏全组无",也绝不静默装成功。

- 凭证纪律:本模块不读不存任何 header/env 值;错误文本来自 mcp_client(已 scrub)。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class _ServerHandle:
    """一个已连(或曾连)MCP server 的活账:keeper task + 工具 + 排水计数。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: dict = {}
        self.task: Optional[asyncio.Task] = None
        self.close_evt = asyncio.Event()
        self.force = False          # True=不排水直接断(shutdown)
        from karvyloop.coding.tools.mcp_tool import McpUsageCounter
        self.usage = McpUsageCounter()

    def retire(self, *, force: bool = False) -> None:
        if force:
            self.force = True
        self.close_evt.set()


class McpConnectionManager:
    """可重入的 MCP 连接组管理器(docs/96 刀1)。

    用法:
        mgr = McpConnectionManager()
        res = await mgr.reconnect(config_path, runtime_kwargs=rk)   # 启动/热加载都是它
        ...
        await mgr.shutdown()                                        # console 关停

    `reconnect` 返回 {ok, connected: [{name, tools}], failed: [{name, reason}],
    tool_names: [...]};并把合并后的工具塞进 runtime_kwargs["mcp_tools"](新 dict)。
    """

    def __init__(self, *, connect_timeout_s: float = 120.0, drain_quiet_s: float = 180.0,
                 drain_max_s: float = 1800.0, drain_poll_s: float = 0.25) -> None:
        self.connect_timeout_s = connect_timeout_s
        self.drain_quiet_s = drain_quiet_s
        self.drain_max_s = drain_max_s
        self.drain_poll_s = drain_poll_s
        self._handles: dict[str, _ServerHandle] = {}
        self._retired: list[_ServerHandle] = []
        self._lock = asyncio.Lock()
        self._last_failed: list[dict[str, str]] = []

    # ---- 对外只读状态(GET /mcp/presets 的状态灯用;sync,不碰栈)----------

    def status(self) -> dict[str, Any]:
        return {
            "connected": {name: sorted(h.tools) for name, h in self._handles.items()},
            "failed": list(self._last_failed),
            "retiring": sum(1 for h in self._retired
                            if h.task is not None and not h.task.done()),
        }

    # ---- 核心:可重入重连(启动=第一次;apply/add/手动重连=再次)----------

    async def reconnect(self, config_path: str,
                        runtime_kwargs: Optional[dict] = None) -> dict[str, Any]:
        """读 config.yaml → 连新组(按 server fail-loud)→ 换组(新 dict)→ 旧组退休排水。

        必须在**持有 MCP 会话的事件循环**上调用(console 主循环;endpoint/lifespan 都是)。
        并发 reconnect 串行化(asyncio.Lock)。
        """
        from karvyloop.coding.tools.mcp_tool import read_mcp_server_configs
        async with self._lock:
            cfgs = read_mcp_server_configs(config_path or "")
            # docs/96 刀0:登记各 server 的策展外发名单(显式 > 名字猜测 —— 标注过的工具
            # 即使名字判不出外发也会在执行咽喉被截成草稿卡)。来源两路合并:
            #   ① config.yaml 手写的 `outbound_tools:`(read_mcp_server_configs 读回);
            #   ② 预设目录 PRESETS 的策展标注(按 server 名 = preset id 回查 —— 预设标注
            #     不落 config entry,老 config 也吃得到;mcp_presets 只供数据不做判定)。
            # 登记短名 + 全名(mcp_<server>_<tool>)两种形态;集合只增不减 = 只收紧。
            # fail-soft:登记失败只 warning,不挡接入(方向只可能少拦,不会误放行已登记的)。
            try:
                from karvyloop.capability.outbound_gate import (
                    register_curated_outbound, register_curated_server)
                try:
                    from karvyloop.console.mcp_presets import PRESETS as _PRESETS
                    _preset_ob = {str(p.get("id") or ""): list(p.get("outbound_tools") or [])
                                  for p in _PRESETS}
                except Exception:
                    _preset_ob = {}
                for cfg in cfgs:
                    # docs/98 刀1:预设目录里的 = 我们 vet 过的已策展 server;不在预设的
                    # (官方 Registry / 任意 URL)= 未策展,其名字判不出的工具吃 fail-safe 走卡。
                    if cfg.name in _preset_ob:
                        register_curated_server(cfg.name)
                    _obs = [str(t).strip() for t in (getattr(cfg, "outbound_tools", None) or ())
                            if str(t).strip()]
                    _obs += [str(t).strip() for t in _preset_ob.get(cfg.name, [])
                             if str(t).strip()]
                    if _obs:
                        register_curated_outbound(
                            _obs + [f"mcp_{cfg.name}_{t}" for t in _obs])
            except Exception:
                logger.warning("[mcp_manager] 外发策展名单登记失败(名字判定仍在)", exc_info=True)
            connected: list[dict[str, Any]] = []
            failed: list[dict[str, str]] = []
            new_handles: dict[str, _ServerHandle] = {}
            for cfg in cfgs:
                if cfg.name in new_handles:   # 手改 config 重名:后者明说跳过,不悄悄覆盖
                    failed.append({"name": cfg.name,
                                   "reason": "duplicate server name in config.yaml"})
                    continue
                handle, reason = await self._connect_one(cfg)
                if handle is not None:
                    new_handles[cfg.name] = handle
                    connected.append({"name": cfg.name, "tools": sorted(handle.tools)})
                else:
                    failed.append({"name": cfg.name, "reason": reason})
                    logger.warning("[mcp_manager] server '%s' 连失败(其余照常): %s",
                                   cfg.name, reason)
            # 换组:合并成**新 dict** 塞进 runtime_kwargs —— 在跑的 drive 早已 splat 走旧
            # dict 的引用,原样继续;新 drive 从 app.state.runtime_kwargs 取到新组。
            tools: dict = {}
            for h in new_handles.values():
                tools.update(h.tools)
            if isinstance(runtime_kwargs, dict):
                runtime_kwargs["mcp_tools"] = tools
            # 旧组退休(排水在各自 keeper task 里后台进行,不阻塞本次响应)
            old = self._handles
            self._handles = new_handles
            for h in old.values():
                h.retire()
                self._retired.append(h)
            self._gc_retired()
            self._last_failed = failed
            return {"ok": not failed, "connected": connected, "failed": failed,
                    "tool_names": sorted(tools)}

    async def shutdown(self, timeout_s: float = 10.0) -> None:
        """console 关停:全部强断(不排水),限时等 keeper 收栈。"""
        async with self._lock:
            all_handles = list(self._handles.values()) + list(self._retired)
            self._handles = {}
            self._retired = []
        for h in all_handles:
            h.retire(force=True)
        tasks = [h.task for h in all_handles if h.task is not None and not h.task.done()]
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)

    # ---- 内部 ----------

    async def _connect_one(self, cfg: Any) -> tuple[Optional[_ServerHandle], str]:
        """起 keeper task 连一个 server;成功回 (handle, "")、失败回 (None, 原因)。"""
        handle = _ServerHandle(cfg.name)
        loop = asyncio.get_running_loop()
        ready: asyncio.Future = loop.create_future()
        handle.task = asyncio.create_task(self._keeper(cfg, ready, handle))
        try:
            await asyncio.wait_for(asyncio.shield(ready), timeout=self.connect_timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            ready.cancel()
            handle.task.cancel()
            return None, f"connect timeout after {self.connect_timeout_s:g}s"
        except asyncio.CancelledError:
            handle.task.cancel()
            raise
        except Exception as e:
            # McpConnectError 等;文本已由 mcp_client 抹掉凭证
            return None, str(e) or type(e).__name__
        return handle, ""

    async def _keeper(self, cfg: Any, ready: asyncio.Future, handle: _ServerHandle) -> None:
        """一个 server 的完整生命周期,全程同一个 task(anyio cancel scope 要求):
        open session → list_tools → set ready → 等 close → (排水) → 收栈。"""
        from karvyloop.coding.tools.mcp_tool import mcp_tools_from_session
        from karvyloop.mcp_client import _fail_connect, _open_session
        stack = contextlib.AsyncExitStack()
        try:
            session = await _open_session(stack, cfg)
            resp = await session.list_tools()
        except BaseException as e:  # noqa: BLE001 —— _fail_connect 同 task 收栈后必抛
            try:
                await _fail_connect(stack, e, cfg)
            except asyncio.CancelledError:
                # 外部取消(connect 超时 / console 关停):栈已收,安静退出
                return
            except Exception as err:
                if not ready.done():
                    ready.set_exception(err)
                return
            except BaseException as err:  # KeyboardInterrupt/SystemExit:栈已收,继续冒
                if not ready.done():
                    ready.cancel()
                raise err
            return
        handle.tools = mcp_tools_from_session(
            session, resp.tools, cfg.name,
            loop=asyncio.get_running_loop(), usage=handle.usage)
        if ready.done():        # connect 超时侧已放弃(几乎不可能,但别泄漏连接)
            await stack.aclose()
            return
        ready.set_result(True)
        try:
            await handle.close_evt.wait()
            if not handle.force:
                await self._drain(handle)
        finally:
            # 同 task 收栈(keeper 从不被 cancel 到这里:shutdown 走 close_evt+force)
            try:
                await stack.aclose()
            except Exception as e:
                logger.warning("[mcp_manager] server '%s' 关闭异常(忽略): %s",
                               handle.name, type(e).__name__)

    async def _drain(self, handle: _ServerHandle) -> None:
        """退休排水:等 inflight 归零且静默 drain_quiet_s(硬上限 drain_max_s)。
        正在跑的 drive 还握着旧工具 —— 给它把手头的 tool call 跑完。"""
        deadline = time.monotonic() + self.drain_max_s
        while time.monotonic() < deadline:
            if handle.force:
                return
            u = handle.usage
            if u.inflight == 0 and (time.monotonic() - u.last_activity) >= self.drain_quiet_s:
                return
            await asyncio.sleep(self.drain_poll_s)
        logger.warning("[mcp_manager] server '%s' 排水超时(%gs),强制关断",
                       handle.name, self.drain_max_s)

    def _gc_retired(self) -> None:
        self._retired = [h for h in self._retired
                         if h.task is not None and not h.task.done()]


__all__ = ["McpConnectionManager"]
