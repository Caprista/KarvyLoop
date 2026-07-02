"""app — build_console_app(M3+ 批 8.5-C)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-C。

构造 FastAPI app,挂载:
- /api/* 端点(routes.py)
- /ws 端点(ws.py)
- / → 静态 index.html; /static/* → 静态资源

显式 `app.state.{workbench, main_loop, runtime_kwargs, workbench_app, ws_clients}`,
无隐式全局。

K 边界:K3/K4/K5 由 routes / ws 模块各自把关,本模块只 wire。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from karvyloop.runtime.main_loop import MainLoop
from karvyloop.karvy.observer import WorkbenchObserver

from .routes import router as api_router
from .ws import router as ws_router

logger = logging.getLogger(__name__)

# 静态资源目录(与本文件同包下)
STATIC_DIR = Path(__file__).parent / "static"

# 自适应质量评节奏(dev-report #7):固定 24h 太慢——活跃用户(每天几十任务)差技能要污染召回排序
# 最多 24h。在 daily 整套维护之外,插入**积压触发**的质量评:每 _ACTIVE_TICK_S 看一眼待质量评积压,
# 攒够 _QUALITY_BACKLOG_TRIGGER 就提前评(只评质量、不跑整套;单轮仍 QUALITY_JUDGE_LIMIT 封顶成本)。
# daily 整套(建议/经验/临时原子巡检)仍按 interval 走,backlog 提前评**不重置** daily 时钟。
_ACTIVE_TICK_S = 600            # 活跃子 tick(10min)看一眼积压
_QUALITY_BACKLOG_TRIGGER = 20   # 待质量评积压 ≥ 此 → 提前评


def _review_decision(*, now: float, next_daily: float, backlog: int,
                     trigger: int = _QUALITY_BACKLOG_TRIGGER) -> str:
    """这个 tick 该干啥(纯函数,可测):
    - "daily":到 daily 截止 → 跑整套慢侧维护(建议/质量/经验/临时原子)。
    - "backlog":没到 daily 但待质量评积压够了 → 只提前补质量评(快速纠偏召回排序,不等 24h)。
    - "idle":都没到 → 继续等。"""
    if now >= next_daily:
        return "daily"
    if trigger > 0 and backlog >= trigger:
        return "backlog"
    return "idle"


def build_console_app(
    *,
    workbench: WorkbenchObserver,
    main_loop: Optional[MainLoop] = None,
    runtime_kwargs: Optional[dict] = None,
    workbench_app: Any = None,
    proposal_pump: Any = None,
) -> FastAPI:
    """构造 FastAPI app。

    Args:
        workbench: 必填 — WorkbenchObserver 注入(供 /api/snapshot 等读)。
        main_loop: 可选 — MainLoop 注入(供 /api/intent / /api/stats)。
        runtime_kwargs: 可选 — 慢脑工厂 kwargs(token/sandbox/gateway/workspace_root/model_ref)。
        workbench_app: 可选 — WorkbenchApp 注入(供 /api/chat_history 读 ring buffer)。
        proposal_pump: 可选 — ProposalPump 注入(9.0d:IntentAnalyst → console h2a_proposal 推送桥)。
            None 时 /api/propose 返优雅"未接 analyst"提示,不报错。

    Returns:
        FastAPI app。
    """
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio

        # 启动:init ws_clients
        app.state.ws_clients = set()
        logger.info(
            f"[karvyloop console] 启动 workbench={bool(workbench)} "
            f"main_loop={main_loop is not None} "
            f"workbench_app={workbench_app is not None} "
            f"proposal_pump={getattr(app.state, 'proposal_pump', None) is not None}"
        )

        # 9.0e:小卡每天后台看一次行为(daily poll → 有强建议推 h2a_proposal)。
        # 仅当 entry 接线了 pump + 设了正间隔才起;默认不开(0.1.0 §少脚手架)。
        daily_task = None
        pump = getattr(app.state, "proposal_pump", None)
        interval = getattr(app.state, "proposal_daily_interval_s", None)
        if pump is not None and interval and interval > 0:
            async def _daily_loop() -> None:
                import time as _t
                tick = min(interval, _ACTIVE_TICK_S)
                trigger = getattr(app.state, "quality_backlog_trigger", _QUALITY_BACKLOG_TRIGGER)
                next_daily = _t.monotonic() + interval
                while True:
                    try:
                        await asyncio.sleep(tick)
                        _ml = getattr(app.state, "main_loop", None)
                        # 看一眼待质量评积压(纯计数、cap 提前停;线程里跑不阻塞 loop)。
                        backlog = 0
                        if _ml is not None and hasattr(_ml, "pending_quality_count"):
                            try:
                                backlog = await asyncio.to_thread(_ml.pending_quality_count, cap=trigger)
                            except Exception:
                                backlog = 0
                        action = _review_decision(now=_t.monotonic(), next_daily=next_daily,
                                                  backlog=backlog, trigger=trigger)
                        if action == "idle":
                            continue
                        # 质量评:daily 与 backlog 两条路都补(纠偏召回排序)。线程里跑(LLM 可能慢),
                        # 不阻塞事件循环;离 drive 热路径(跑评分离)。单轮仍 QUALITY_JUDGE_LIMIT 封顶。
                        if _ml is not None and hasattr(_ml, "quality_review"):
                            judged = await asyncio.to_thread(_ml.quality_review)
                            if judged:
                                tag = "积压触发,不等 24h" if action == "backlog" else "daily"
                                logger.info(f"[karvyloop console] 慢侧 atom 质量评 {judged} 条({tag})")
                        if action == "backlog":
                            continue   # 只提前补质量评,不跑整套 daily(daily 时钟不重置)
                        # action == "daily":整套慢侧维护
                        next_daily = _t.monotonic() + interval
                        proposal, sent = await pump.daily()
                        if proposal is not None:
                            logger.info(
                                f"[karvyloop console] 小卡 daily 建议 → 推 {sent} client(s): "
                                f"{proposal.summary[:40]}"
                            )
                        # docs/40 §6 丙:更慢一档,跨-run 经验蒸馏(质量评之后,材料更全)。
                        if _ml is not None and hasattr(_ml, "lessons_review"):
                            learned = await asyncio.to_thread(_ml.lessons_review)
                            if learned:
                                logger.info(f"[karvyloop console] 跨-run 蒸出经验 {learned} 条")
                        # docs/02 §15.5:临时原子生命周期 —— 被角色复用的转正,孤儿撤回(护城河自清洁)。
                        _areg = getattr(app.state, "atom_registry", None)
                        _rreg = getattr(app.state, "role_registry", None)
                        if _areg is not None and _rreg is not None:
                            from karvyloop.atoms.provisional import review_provisional
                            res = await asyncio.to_thread(review_provisional, _areg, _rreg)
                            if res["confirmed"] or res["reverted"]:
                                logger.info(f"[karvyloop console] 临时原子巡检:转正 {len(res['confirmed'])} 个、"
                                            f"撤回孤儿 {len(res['reverted'])} 个")
                        # 知识库自动整理(Bug2 后台版):库变了才跑一次 LLM 聚类,近重复升 H2A 建议卡
                        # (ACCEPT 才合并,绝不自动)。watermark + 冷却防唠叨,离热路径。
                        from karvyloop.console.knowledge_tick import knowledge_consolidate_tick
                        kres = await knowledge_consolidate_tick(app)
                        if kres.get("suggested"):
                            logger.info(f"[karvyloop console] 知识整理:升 {kres['suggested']} 张合并建议卡")
                        # P3-c:技能语义标签回填(没标签的自家技能补一次;watermark=有标签即跳过)
                        from karvyloop.console.skill_tags_tick import skill_tags_tick
                        tres = await skill_tags_tick(app)
                        if tres.get("tagged"):
                            logger.info(f"[karvyloop console] 技能语义标签:补 {tres['tagged']} 个")
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        # 慢脑异常不打断 loop(下个周期再来)
                        logger.warning(f"[karvyloop console] daily poll 异常(下轮再试): {e}")
                        # §0.7 fail-loud:后台 daily 失败不再静默,主动 push 给 UI
                        try:
                            from karvyloop.console.task_events import schedule_system_error
                            schedule_system_error(app, "daily_poll", str(e))
                        except Exception:
                            pass
            daily_task = asyncio.create_task(_daily_loop())
            app.state.daily_task = daily_task
            logger.info(f"[karvyloop console] 小卡 daily 调度已起(间隔 {interval}s)")

        # 定时任务调度器:每 30s tick 一次,跑窗口内到点的任务(只有 Karvy 起的;角色没调度工具)。
        async def _scheduler_loop() -> None:
            import time as _time
            from karvyloop.console.routes import _scheduler_store, fire_schedule
            last = _time.time()
            while True:
                try:
                    await asyncio.sleep(30)
                    now = _time.time()
                    for t in _scheduler_store(app).due(since=last, now=now):
                        try:
                            await fire_schedule(app, t)
                        except Exception as fe:
                            logger.warning(f"[karvyloop console] 定时任务 {t.id} 执行异常: {fe}")
                    last = now
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"[karvyloop console] 定时调度 tick 异常(下轮再试): {e}")
        app.state.scheduler_task = asyncio.create_task(_scheduler_loop())
        logger.info("[karvyloop console] 定时任务调度器已起(30s tick)")

        # A:接 MCP server(若 config.yaml 配了 mcp.servers)→ **在主循环上**连,工具注入 agent 工具集。
        # agent 跑在 worker 线程的另一个 asyncio.run 循环里,McpAgentTool 会跨循环桥回这个主循环。
        # 没配 → 不连(0 影响);连失败 → 降级无 MCP 工具,不挡 console 启动。
        app.state.mcp_group_ctx = None
        try:
            from karvyloop.coding.tools.mcp_tool import (
                connect_mcp_agent_tools, read_mcp_server_configs)
            mcp_cfgs = read_mcp_server_configs(getattr(app.state, "config_path", "") or "")
            if mcp_cfgs:
                group_ctx, mcp_tools = await connect_mcp_agent_tools(mcp_cfgs)
                app.state.mcp_group_ctx = group_ctx   # 保活,关闭时 __aexit__
                rk = getattr(app.state, "runtime_kwargs", None)
                if isinstance(rk, dict):
                    rk["mcp_tools"] = mcp_tools        # 慢脑工厂取它注入 agent 工具集
                print(f"[karvyloop console] MCP 接入 {len(mcp_tools)} 个工具: {list(mcp_tools)}", flush=True)
        except Exception as e:
            logger.warning(f"[karvyloop console] MCP 接入失败(降级无 MCP 工具,不影响启动): {e}")

        # #39 ①:持久化执行 —— 续跑上次被中断的 workflow(console 崩/重启后,已完成步秒命中、剩余续)。
        try:
            from karvyloop.console.workflow_engine import resume_workflows
            n = await resume_workflows(app)
            if n:
                print(f"[karvyloop console] 续跑了 {n} 个被中断的 workflow", flush=True)
        except Exception as e:
            logger.warning(f"[karvyloop console] workflow 续跑失败(不影响启动): {e}")

        yield

        # A:关 MCP 会话(断子进程)
        _gctx = getattr(app.state, "mcp_group_ctx", None)
        if _gctx is not None:
            try:
                await _gctx.__aexit__(None, None, None)
            except Exception:
                pass

        # 关闭:取消 daily task + 定时调度器 + 清 ws clients + 关 pump 资源(trace/habit sqlite)
        if daily_task is not None:
            daily_task.cancel()
        _sched_task = getattr(app.state, "scheduler_task", None)
        if _sched_task is not None:
            _sched_task.cancel()
        app.state.ws_clients.clear()
        close_fn = getattr(app.state, "proposal_close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception as e:
                logger.warning(f"[karvyloop console] proposal_close 异常: {e}")
        logger.info("[karvyloop console] 关闭")

    app = FastAPI(
        title="KarvyLoop Console",
        version="0.1.0",
        lifespan=lifespan,
    )
    # 显式 state(无隐式全局)
    # 注:ws_clients 必须**立即**初始化,TestClient 在 lifespan 触发前就可能连 WS
    app.state.workbench = workbench
    app.state.main_loop = main_loop
    app.state.runtime_kwargs = runtime_kwargs or {}
    app.state.workbench_app = workbench_app
    app.state.proposal_pump = proposal_pump  # 9.0d:IntentAnalyst → console 推送桥(可 None)
    app.state.proposal_close = None           # 9.0e:entry 接线时设(关 trace/habit sqlite)
    app.state.proposal_daily_interval_s = None  # 9.0e:entry 接线时设(daily 调度间隔;None=不开)
    app.state.conversation_manager = None     # 9.1d:entry 接线时设(对话编排器;None=无对话上下文)
    app.state.domain_registry = None          # 9.2b:entry 接线时设(列业务域 peer;None=仅私聊)
    app.state.domain_store = None             # 9.2c-持久化:entry 接线时设(建域存盘)
    app.state.token_ledger = None             # 9.3a:entry 接线时设(token 账本/看板)
    app.state.ws_clients = set()  # 立即 set,lifespan 里也 set 同引用

    # mount routers
    app.include_router(api_router)
    app.include_router(ws_router)

    # 静态资源禁用浏览器**强缓存**(no-cache = 每次带 ETag 条件请求 → 没变 304、变了 200)。
    # 否则前端部署后,普通刷新常吃旧 JS(开发期反复"刷了还是旧的、要 Ctrl+Shift+R"就是这个)。
    @app.middleware("http")
    async def _static_no_cache(request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # 访问令牌门:本机(loopback)免 token;非本机必须带 token(?token= / cookie / header)。
    # 未设 app.state.access_token(编程式/测试)→ 不启用(TestClient 来源非 loopback 但无 token → 放行)。
    # CLI(cmd_console)启动时**必然**设 token,所以真实部署一定有门。绕不过 = 安全是地基。
    @app.middleware("http")
    async def _access_gate(request, call_next):  # type: ignore[no-untyped-def]
        from karvyloop.console import access as _acc
        # 同源门(**始终生效,不受 token/loopback 免密影响**):堵 CSRF —— 恶意网页从你本机浏览器
        # 跨源打过来,带的是 evil.com 的 Origin,这里拦掉;我们自己的前端同源(Origin==Host)放行,
        # curl/CLI/测试不带 Origin 放行。补上"loopback 无条件可信"的盲区。
        if not _acc.origin_ok(request.headers.get("origin", ""),
                              request.headers.get("sec-fetch-site", ""),
                              request.headers.get("host", "")):
            from starlette.responses import JSONResponse
            return JSONResponse({"ok": False, "reason": "跨源请求被拒(same-origin only)"}, status_code=403)
        token = getattr(app.state, "access_token", None)
        if not token:
            return await call_next(request)
        client = request.client.host if request.client else ""
        if _acc.is_loopback(client):
            return await call_next(request)
        if not _acc.token_ok(_acc.token_from_request(request), token):
            from starlette.responses import HTMLResponse, JSONResponse
            wants_json = request.url.path.startswith("/api") or "application/json" in (request.headers.get("accept") or "")
            reason = ("需要访问令牌:本机 localhost 免密;从别的设备访问,请在**运行 console 的机器上**"
                      "执行 `karvyloop url`(命令找不到就用 `python -m karvyloop url`,永远可用)取带 token 的链接。")
            if wants_json:
                return JSONResponse({"ok": False, "reason": reason}, status_code=401)
            return HTMLResponse(f"<!doctype html><meta charset=utf-8><h3>KarvyLoop</h3><p>{reason}</p>", status_code=401)
        resp = await call_next(request)
        # 首次用带 token 的链接进来 → 落 cookie,之后同源请求(含 WS)免再带
        try:
            if request.query_params.get("token"):
                resp.set_cookie(_acc.COOKIE, token, httponly=True, samesite="lax", path="/")
        except Exception:
            pass
        return resp

    # 静态文件
    if STATIC_DIR.is_dir():
        # index.html 由 / 路由直接返(避免 StaticFiles 默认 index.html 兜底)
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/")
        def index():
            # 9.4:把已保存的语言偏好注入页面(data-default-lang)→ 全新浏览器/清缓存后
            # GUI 也直接以保存的语言启动(i18n.js getLang 读 data-default-lang)。localStorage 仍可覆盖。
            from fastapi.responses import HTMLResponse
            from karvyloop.i18n import get_locale
            try:
                html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
                loc = get_locale()
                html = html.replace(
                    '<html lang="en">',
                    f'<html lang="{loc}" data-default-lang="{loc}">',
                )
                return HTMLResponse(html)
            except Exception:
                return FileResponse(str(STATIC_DIR / "index.html"))
    else:
        @app.get("/")
        def index_no_static() -> dict[str, str]:
            return {"error": f"static dir not found: {STATIC_DIR}"}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


__all__ = ["build_console_app", "STATIC_DIR"]
