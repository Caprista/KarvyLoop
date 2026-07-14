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
from .routes_atoms import router as atoms_router
from .routes_budget import router as budget_router
from .routes_capability import router as capability_router
from .routes_conversations import router as conversations_router
from .routes_decision_prefs import router as decision_prefs_router
from .routes_demo import router as demo_router
from .routes_domain import router as domain_router
from .routes_butler import router as butler_router
from .routes_external import router as external_router
from .routes_files import router as files_router
from .routes_lines import router as lines_router
from .routes_memory import router as memory_router
from .routes_models import router as models_router
from .routes_onboarding import router as onboarding_router
from .routes_ops import router as ops_router
from .routes_mesh import router as mesh_router
from .routes_pair import router as pair_router
from .routes_peers import router as peers_router
from .routes_workflow import router as workflow_router
from .routes_roles import router as roles_router
from .routes_schedules import router as schedules_router
from .routes_system import router as system_router
from .routes_tokens import router as tokens_router
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

# predict(你可能想做)启动兜底:console 起来后延迟这么多秒自动跑一次 boot_poll
# (让系统先稳);此前第一条建议要等 24h daily,用户体感 = 页签永远空。
# 测试可用 app.state.boot_poll_delay_s 覆盖(0=立即);负数 = 关闭。
BOOT_POLL_DELAY_S = 30.0

# 慢侧维护 loop 的默认间隔(闭环审计 WEAK⑨):pump 没接线时维护也要跑,自带 24h 兜底时钟
# (数值与 pump 的 daily_poll 间隔一致 → 搬家前后节奏等价)。
_MAINTENANCE_INTERVAL_S = 24 * 60 * 60


async def _supervised_bg(app: Any, name: str, coro_factory: Any, *,
                         max_crashes: int = 3, base_backoff_s: float = 1.0,
                         steady_run_s: float = 60.0) -> None:
    """后台长生协程的 supervisor(闭环审计断⑦:此前三个 create_task 无 done-callback,
    初始化段一炸 = 协程静默死,调度器/daily/邮件通道死了用户以为还在跑)。

    - 协程异常退出(非 CancelledError)→ logger.error + WS system_error 上冒 + **重启**;
    - 重启带指数退避(base × 2^n);跑满 `steady_run_s` 再崩算"新的一连串"(计数重置);
    - **连续**崩满 `max_crashes` 次 → 停止重启并大声说(需人工介入,不无限空转);
    - 正常 return(= 内部 cancel-break 收尾)→ 不重启;CancelledError 原样上抛(关停路径)。
    """
    import asyncio
    crashes = 0
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        try:
            await coro_factory()
            return   # 正常收尾(cancel-break)→ 不重启
        except asyncio.CancelledError:
            raise    # 关停路径:不吞、不重启
        except Exception as e:
            ran = loop.time() - started
            crashes = 1 if ran >= steady_run_s else crashes + 1
            logger.error(
                f"[karvyloop console] 后台协程 {name} 意外退出"
                f"(第 {crashes}/{max_crashes} 次,已运行 {ran:.1f}s): {e}", exc_info=True)
            try:
                from karvyloop.console.task_events import schedule_system_error
                schedule_system_error(app, f"bg:{name}", str(e))
            except Exception:
                pass
            if crashes >= max_crashes:
                logger.error(
                    f"[karvyloop console] 后台协程 {name} 连续崩 {crashes} 次 —— **停止重启**,"
                    f"该后台能力已停摆,请查日志修复后重启 console")
                try:
                    from karvyloop.console.task_events import schedule_system_error
                    schedule_system_error(
                        app, f"bg:{name}",
                        f"后台协程 {name} 连续崩 {crashes} 次,已停止重启(重启 console 前该能力停摆)")
                except Exception:
                    pass
                return
            await asyncio.sleep(base_backoff_s * (2 ** (crashes - 1)))


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


def _resolve_maintenance_interval(explicit: Any, pump_interval: Any) -> float:
    """慢侧维护 loop 的间隔(纯函数,可测;闭环审计 WEAK⑨ 去单点寄生):
    - 显式 `app.state.maintenance_interval_s`:>0 用它;<=0 = 显式关闭(返 0)。
    - 否则跟 pump 的 daily 间隔(搬家前维护和 pump 共用一个时钟 → 行为等价)。
    - pump 没接线(None / 非正)→ 默认 24h —— pump 挂了/没配 LLM,维护不再陪葬。"""
    if explicit is not None:
        try:
            v = float(explicit)
        except (TypeError, ValueError):
            return float(_MAINTENANCE_INTERVAL_S)
        return v if v > 0 else 0.0
    try:
        if pump_interval is not None and float(pump_interval) > 0:
            return float(pump_interval)
    except (TypeError, ValueError):
        pass
    return float(_MAINTENANCE_INTERVAL_S)


def _maintenance_item_failed(app: Any, item: str, e: Exception) -> None:
    """慢侧维护**单项**失败:响一声(log + WS system_error 上冒),**不连坐**其他维护项
    (§0.7 fail-loud;下轮 daily 再试)。"""
    logger.warning(
        f"[karvyloop console] 慢侧维护项 {item} 异常(其余维护项照跑,下轮再试): {e}")
    try:
        from karvyloop.console.task_events import schedule_system_error
        schedule_system_error(app, f"maintenance:{item}", str(e))
    except Exception:
        pass


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
        # 主事件循环句柄:给线程池里跑的 sync 路径(REST /api/h2a_decide 等)把
        # fire-and-forget 协程桥回环上用(run_coroutine_threadsafe;decision_wire P0-6)。
        app.state.main_event_loop = asyncio.get_running_loop()
        logger.info(
            f"[karvyloop console] 启动 workbench={bool(workbench)} "
            f"main_loop={main_loop is not None} "
            f"workbench_app={workbench_app is not None} "
            f"proposal_pump={getattr(app.state, 'proposal_pump', None) is not None}"
        )

        # 9.0e:小卡每天后台看一次行为(daily poll → 有强建议推 h2a_proposal)。
        # 仅当 entry 接线了 pump + 设了正间隔才起;默认不开(0.1.0 §少脚手架)。
        # 闭环审计 WEAK⑨:这条 loop 现在**只剩** pump 的预判建议 —— 它才真依赖 pump;
        # 质量评/经验/修订/周报/巡检/知识整理已拆去下面的维护 loop,pump 挂了不再陪葬。
        daily_task = None
        pump = getattr(app.state, "proposal_pump", None)
        interval = getattr(app.state, "proposal_daily_interval_s", None)
        if pump is not None and interval and interval > 0:
            async def _daily_loop() -> None:
                while True:
                    try:
                        # 时钟语义与拆分前等价:原实现按 600s 子 tick 数到 next_daily 才 fire,
                        # 对 pump.daily 而言等效于"每 interval 醒一次";子 tick 只服务质量评积压,
                        # 已随质量评搬去维护 loop。这里整段沉睡 → 没到点零工作(idle=0 契约)。
                        await asyncio.sleep(interval)
                        # 可观测性①:daily 是非 drive 入口,自带 run_scope —— 本轮建议链上的
                        # Trace/token 行带同一 run_id(内层 drive 自己再开新 scope,不冲突)。
                        from karvyloop.cognition.trace import run_scope
                        with run_scope():
                            proposal, sent = await pump.daily()
                        if proposal is not None:
                            logger.info(
                                f"[karvyloop console] 小卡 daily 建议 → 推 {sent} client(s): "
                                f"{proposal.summary[:40]}"
                            )
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
            # 断⑦:supervisor 包一层 —— 初始化段炸了也会 log+上冒+带退避重启,不再静默死
            daily_task = asyncio.create_task(_supervised_bg(app, "daily_poll", _daily_loop))
            app.state.daily_task = daily_task
            logger.info(f"[karvyloop console] 小卡 daily 调度已起(间隔 {interval}s)")

        # 慢侧维护 loop(闭环审计 WEAK⑨ 去单点寄生):质量评(积压提前评 + daily)/跨-run 经验/
        # 技能修订/周报卡/临时原子巡检/知识整理/技能标签回填 —— 这些从不依赖 pump
        # (周报纯确定性、质量评走 main_loop 自己注入的裁判),所以搬出上面那条 pump loop:
        # pump 构造失败 / --no-llm 没建 pump,慢侧维护照跑,不再整条无声死。
        # 时钟:pump 接了 → 跟 pump 同 interval(搬家前后节奏等价);没接 → 默认 24h;
        # 显式 app.state.maintenance_interval_s 可覆盖(<=0=关)。各项内部自己判空 + 兜异常,
        # 单项缺料(没接 main_loop / 没 Trace / 没注入裁判)= debug 跳过,不连坐其他项。
        maintenance_task = None
        m_interval = _resolve_maintenance_interval(
            getattr(app.state, "maintenance_interval_s", None), interval)
        if m_interval > 0:
            if getattr(app.state, "no_llm", False):
                # 启动时说一次,别让用户以为慢侧全死了(LLM 项各自静默 0 回归)
                logger.info(
                    "[karvyloop console] --no-llm:慢侧 LLM 维护(质量评/修订/经验蒸馏/知识聚类)停用,"
                    "确定性维护(周报/知识 watermark/临时原子巡检)照跑")

            async def _maintenance_loop() -> None:
                import time as _t
                tick = min(m_interval, _ACTIVE_TICK_S)
                trigger = getattr(app.state, "quality_backlog_trigger", _QUALITY_BACKLOG_TRIGGER)
                next_daily = _t.monotonic() + m_interval
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
                            try:
                                judged = await asyncio.to_thread(_ml.quality_review)
                                if judged:
                                    tag = "积压触发,不等 24h" if action == "backlog" else "daily"
                                    logger.info(f"[karvyloop console] 慢侧 atom 质量评 {judged} 条({tag})")
                            except Exception as ie:
                                _maintenance_item_failed(app, "quality_review", ie)
                        else:
                            logger.debug("[karvyloop console] 维护项 quality_review 跳过(main_loop 未接)")
                        if action == "backlog":
                            continue   # 只提前补质量评,不跑整套 daily(daily 时钟不重置)
                        # action == "daily":整套慢侧维护(每项各自兜异常,不连坐)
                        next_daily = _t.monotonic() + m_interval
                        # docs/40 §6 丙:更慢一档,跨-run 经验蒸馏(质量评之后,材料更全)。
                        if _ml is not None and hasattr(_ml, "lessons_review"):
                            try:
                                learned = await asyncio.to_thread(_ml.lessons_review)
                                if learned:
                                    logger.info(f"[karvyloop console] 跨-run 蒸出经验 {learned} 条")
                            except Exception as ie:
                                _maintenance_item_failed(app, "lessons_review", ie)
                        else:
                            logger.debug("[karvyloop console] 维护项 lessons_review 跳过(main_loop 未接)")
                        # Trace-conditioned 技能修订(crystallize.revision,同 lessons_review 节奏):
                        # 客观信号差的技能 → LLM 修 Steps;小改自动落 + Changelog,大改出 revise_skill 卡。
                        if _ml is not None and hasattr(_ml, "revision_review"):
                            try:
                                rres = await asyncio.to_thread(_ml.revision_review)
                                if rres.get("revised") or rres.get("proposed"):
                                    logger.info(f"[karvyloop console] 技能修订:小改自动落 {rres.get('revised', 0)} 个、"
                                                f"大改出卡 {rres.get('proposed', 0)} 张")
                            except Exception as ie:
                                _maintenance_item_failed(app, "revision_review", ie)
                        else:
                            logger.debug("[karvyloop console] 维护项 revision_review 跳过(main_loop 未接)")
                        # 周报卡(cognition.weekly_digest):7 天一发,幂等防重(水位落盘);
                        # 数字全部从 Trace/tokens.db/决策流水确定性汇总,零 LLM。发卡后推前端。
                        try:
                            _wtrace = getattr(_ml, "trace", None) if _ml is not None else None
                            if _wtrace is None:
                                _wtrace = getattr(app.state, "trace", None)   # 无 main_loop 时的备选源
                            if _wtrace is not None:
                                from karvyloop.cognition.weekly_digest import weekly_digest_tick
                                wres = await weekly_digest_tick(
                                    trace=_wtrace,
                                    token_ledger=getattr(app.state, "token_ledger", None),
                                    taste_store=getattr(app.state, "taste_predictions", None),
                                    registry=getattr(app.state, "proposal_registry", None),
                                    decision_log=getattr(app.state, "decision_log", None))
                                if wres.get("ran"):
                                    _wreg = getattr(app.state, "proposal_registry", None)
                                    _wcard = (_wreg.get(wres.get("proposal_id", ""))
                                              if _wreg is not None else None)
                                    if _wcard is not None:
                                        from karvyloop.console.proposals import broadcast_proposal
                                        sent = await broadcast_proposal(app, _wcard)
                                        logger.info(f"[karvyloop console] 周报卡已发(推 {sent} client(s))")
                            else:
                                logger.debug("[karvyloop console] 维护项 weekly_digest 跳过(无 Trace 源)")
                        except Exception as ie:
                            _maintenance_item_failed(app, "weekly_digest", ie)
                        # docs/02 §15.5:临时原子生命周期 —— 被角色复用的转正,孤儿撤回(护城河自清洁)。
                        try:
                            _areg = getattr(app.state, "atom_registry", None)
                            _rreg = getattr(app.state, "role_registry", None)
                            if _areg is not None and _rreg is not None:
                                from karvyloop.atoms.provisional import review_provisional
                                res = await asyncio.to_thread(review_provisional, _areg, _rreg)
                                if res["confirmed"] or res["reverted"]:
                                    logger.info(f"[karvyloop console] 临时原子巡检:转正 {len(res['confirmed'])} 个、"
                                                f"撤回孤儿 {len(res['reverted'])} 个")
                        except Exception as ie:
                            _maintenance_item_failed(app, "provisional_review", ie)
                        # 知识库自动整理(Bug2 后台版):库变了才跑一次 LLM 聚类,近重复升 H2A 建议卡
                        # (ACCEPT 才合并,绝不自动)。watermark + 冷却防唠叨,离热路径。
                        try:
                            from karvyloop.console.knowledge_tick import knowledge_consolidate_tick
                            kres = await knowledge_consolidate_tick(app)
                            if kres.get("suggested"):
                                logger.info(f"[karvyloop console] 知识整理:升 {kres['suggested']} 张合并建议卡")
                        except Exception as ie:
                            _maintenance_item_failed(app, "knowledge_tick", ie)
                        # P3-c:技能语义标签回填(没标签的自家技能补一次;watermark=有标签即跳过)
                        try:
                            from karvyloop.console.skill_tags_tick import skill_tags_tick
                            tres = await skill_tags_tick(app)
                            if tres.get("tagged"):
                                logger.info(f"[karvyloop console] 技能语义标签:补 {tres['tagged']} 个")
                        except Exception as ie:
                            _maintenance_item_failed(app, "skill_tags_tick", ie)
                        # #61 研判①b:知识概念标签回填(存量老条补进 ConceptCache,同义改写
                        # 召回渐进增强;watermark=缓存命中即跳过;新条由写入路径打,这里收存量的尾)
                        try:
                            from karvyloop.console.belief_tags_tick import belief_tags_tick
                            bres = await belief_tags_tick(app)
                            if bres.get("tagged"):
                                logger.info(f"[karvyloop console] 知识概念标签:补 {bres['tagged']} 条")
                        except Exception as ie:
                            _maintenance_item_failed(app, "belief_tags_tick", ie)
                        # 兵法回流(docs/78):域角色经验里可泛化的 → LLM 判+脱敏改写 → denylist →
                        # 攒批出卡(ACCEPT 才升镜像)。池指纹 watermark + 冷却,同 knowledge_tick 纪律。
                        try:
                            from karvyloop.console.promotion_tick import maybe_promotion_tick
                            pres = await maybe_promotion_tick(app)
                            if pres:
                                logger.info(f"[karvyloop console] 兵法回流:升 {pres} 张升层建议卡")
                        except Exception as ie:
                            _maintenance_item_failed(app, "promotion_tick", ie)
                        # 反向标签护栏③:同义标签收敛进别名表(标签是派生数据,可自动合并;
                        # 审计=别名表 via/ts + Trace tag_merged。watermark=词表指纹,零变零 LLM)
                        try:
                            from karvyloop.console.tag_merge_tick import tag_merge_tick
                            mres = await tag_merge_tick(app)
                            if mres.get("merged"):
                                logger.info(f"[karvyloop console] 同义标签收敛:并 {mres['merged']} 对(别名表)")
                        except Exception as ie:
                            _maintenance_item_failed(app, "tag_merge_tick", ie)
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        # tick 级兜底(单项已各自兜;这里接住积压计数/决策等骨架异常),下轮再来
                        logger.warning(f"[karvyloop console] 慢侧维护 tick 异常(下轮再试): {e}")
                        try:
                            from karvyloop.console.task_events import schedule_system_error
                            schedule_system_error(app, "maintenance", str(e))
                        except Exception:
                            pass
            # 断⑦:supervisor 包一层 —— 崩了 log+上冒+带退避重启,不静默死
            maintenance_task = asyncio.create_task(
                _supervised_bg(app, "maintenance", _maintenance_loop))
            app.state.maintenance_task = maintenance_task
            logger.info(
                f"[karvyloop console] 慢侧维护 loop 已起(间隔 {m_interval:g}s,不依赖 pump)")

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
        app.state.scheduler_task = asyncio.create_task(
            _supervised_bg(app, "scheduler", _scheduler_loop))   # 断⑦ supervisor
        logger.info("[karvyloop console] 定时任务调度器已起(30s tick)")

        # 邮件决策通道心跳(docs/43 ⑤a):digest 自带 min_interval 节流,60s 拍无妨;
        # 未配置(email_channel=None)时 tick 空转零开销。
        async def _email_channel_loop() -> None:
            from karvyloop.channels.email_channel import email_channel_tick
            while True:
                try:
                    await asyncio.sleep(60)
                    await email_channel_tick(getattr(app.state, "email_channel", None))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"[karvyloop console] 邮件通道 tick 异常(下轮再试): {e}")
        app.state.email_channel_task = asyncio.create_task(
            _supervised_bg(app, "email_channel", _email_channel_loop))   # 断⑦ supervisor

        # Webhook 推送通道心跳(channels 广度):与邮件通道**同一分发点**(proposal_registry
        # 的 pending 卡)、并行推;推送自带 min_interval 节流,60s 拍无妨;
        # 未配置(webhook_channel=None)时 tick 空转零开销。
        async def _webhook_channel_loop() -> None:
            from karvyloop.channels.webhook_channel import webhook_channel_tick
            while True:
                try:
                    await asyncio.sleep(60)
                    await webhook_channel_tick(getattr(app.state, "webhook_channel", None))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"[karvyloop console] webhook 通道 tick 异常(下轮再试): {e}")
        app.state.webhook_channel_task = asyncio.create_task(
            _supervised_bg(app, "webhook_channel", _webhook_channel_loop))   # 断⑦ supervisor

        # 收件箱→决策卡管道心跳(docs/49 ⑲-①,inbox_pipe):出站 IMAP 轮询 UNSEEN → 分诊 →
        # 需拍板/需回复出 H2A 卡(纯通知归档)。**只进不出**:模块结构上发不了信。
        # 未配置(channels.inbox 缺 → build 返 None)→ tick 空转零开销。gateway 从 runtime_kwargs
        # 取(复用主 loop 已接好的,models.* 单一真理源);None = 全当纯通知不出卡不烧 token。
        # 接线全在本模块(inbox_pipe.inbox_pipe_tick 明确"接线由主线做");handler 已在
        # build_proposal_handlers 注册(entry 起 console 时),这里只挂 poll 循环。
        app.state.inbox_pipe = None
        try:
            from karvyloop.channels.inbox_pipe import build_inbox_pipe
            _ipreg = getattr(app.state, "proposal_registry", None)
            if _ipreg is not None:
                _iprk = getattr(app.state, "runtime_kwargs", None) or {}
                app.state.inbox_pipe = build_inbox_pipe(
                    registry=_ipreg,
                    config_path=getattr(app.state, "config_path", "") or None,
                    gateway=_iprk.get("gateway"),
                    model_ref=_iprk.get("model_ref", "") or "")
                if app.state.inbox_pipe is not None:
                    logger.info("[karvyloop console] 收件箱决策管道已接线(IMAP 轮询→分诊→出卡)")
        except Exception as e:
            app.state.inbox_pipe = None
            logger.warning(f"[karvyloop console] 收件箱管道接线失败(不影响启动): {e}")

        async def _inbox_pipe_loop() -> None:
            from karvyloop.channels.inbox_pipe import inbox_pipe_tick
            # 轮询间隔:配了 pipe → 用其 cfg.poll_interval_s;没配 → 300s 空转(pipe=None 零开销)。
            _pipe = getattr(app.state, "inbox_pipe", None)
            _interval = 300.0
            try:
                if _pipe is not None:
                    _interval = float(getattr(_pipe._cfg, "poll_interval_s", 300) or 300)
            except Exception:
                _interval = 300.0
            while True:
                try:
                    await asyncio.sleep(max(30.0, _interval))
                    await inbox_pipe_tick(getattr(app.state, "inbox_pipe", None))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"[karvyloop console] 收件箱管道 tick 异常(下轮再试): {e}")
        app.state.inbox_pipe_task = asyncio.create_task(
            _supervised_bg(app, "inbox_pipe", _inbox_pipe_loop))   # 断⑦ supervisor

        # predict 启动兜底:延迟一次 boot_poll(修"第一条建议要等 24h"→ 页签体感永远空)。
        # pump 在(有 LLM)→ 真习惯分析;pump 沉默/未接 → proactive_from_state 确定性兜底
        # (任务看板有失败任务 → 提议重试)。没 WS client 也照样进 proposal_registry 待决表,
        # 前端开机 fetchPendingProposals 会捞回来 —— 不丢。
        boot_poll_task = None
        _boot_delay = getattr(app.state, "boot_poll_delay_s", None)
        if _boot_delay is None:
            _boot_delay = BOOT_POLL_DELAY_S
        if _boot_delay >= 0:
            async def _boot_poll_once() -> None:
                try:
                    await asyncio.sleep(_boot_delay)
                    from karvyloop.console.proposals import proactive_from_state
                    proposal, sent = None, 0
                    _pump = getattr(app.state, "proposal_pump", None)
                    if _pump is not None:
                        # 线程里跑(内含 LLM 调用,可能慢),不阻塞事件循环… pump.boot 是
                        # async(推 WS)→ 直接 await;真正慢的 LLM 在 analyst 里同步跑,
                        # 但这是低频后台 task,一次性的,可接受。
                        proposal, sent = await _pump.boot()
                    if proposal is None:
                        proposal, sent = await proactive_from_state(app)
                    if proposal is not None:
                        logger.info(
                            f"[karvyloop console] 启动 boot_poll 出建议 → 推 {sent} client(s): "
                            f"{getattr(proposal, 'summary', '')[:40]}"
                        )
                    else:
                        logger.info("[karvyloop console] 启动 boot_poll:暂无可提建议(继续观察)")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # fail-loud:predict 启动链失败要响,别静默空着
                    logger.warning(f"[karvyloop console] 启动 boot_poll 失败(不影响运行): {e}")
            boot_poll_task = asyncio.create_task(_boot_poll_once())
            app.state.boot_poll_task = boot_poll_task
            logger.info(f"[karvyloop console] predict 启动 boot_poll 已排程(+{_boot_delay:g}s)")

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

        # #39 ① + #54 逃生门:持久化执行的中断 workflow **不再无条件复活**。
        # 超时的标 abandoned(不复活);其余挂起待人拍板(续/丢),不自动烧 token。让重启成为真正的逃生门。
        try:
            from karvyloop.console.workflow_engine import resume_workflows
            summary = await resume_workflows(app)
            _ab, _pd = summary.get("abandoned", 0), summary.get("pending", 0)
            if _ab or _pd:
                print(f"[karvyloop console] 中断 workflow:{_ab} 条超时丢弃 / {_pd} 条挂起待拍板"
                      f"(不自动续跑,逃生门)", flush=True)
        except Exception as e:
            logger.warning(f"[karvyloop console] workflow 续跑处置失败(不影响启动): {e}")

        yield

        # A:关 MCP 会话(断子进程)
        _gctx = getattr(app.state, "mcp_group_ctx", None)
        if _gctx is not None:
            try:
                await _gctx.__aexit__(None, None, None)
            except Exception:
                pass

        # 关闭:取消 daily task + 维护 loop + boot poll + 定时调度器 + 清 ws clients + 关 pump 资源(trace/habit sqlite)
        if daily_task is not None:
            daily_task.cancel()
        if maintenance_task is not None:
            maintenance_task.cancel()
        if boot_poll_task is not None:
            boot_poll_task.cancel()
        _sched_task = getattr(app.state, "scheduler_task", None)
        if _sched_task is not None:
            _sched_task.cancel()
        _email_task = getattr(app.state, "email_channel_task", None)
        if _email_task is not None:
            _email_task.cancel()
        _webhook_task = getattr(app.state, "webhook_channel_task", None)
        if _webhook_task is not None:
            _webhook_task.cancel()
        _inbox_task = getattr(app.state, "inbox_pipe_task", None)
        if _inbox_task is not None:
            _inbox_task.cancel()
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
    app.state.citizen_registry = None         # 外部 runtime 公民注册表(C1 接线点;None=无外部公民,管理面返空)
    # M2(#71 §7):外部公民进圆桌/workflow 派活用的桥工厂 + token 记账口(entry 接线时设;
    # None → 圆桌/workflow 执行外部步时回退到内置 external_runtime.bridge_factory)。
    app.state.external_bridge_factory = None
    app.state.external_token_recorder = None
    app.state.ws_clients = set()  # 立即 set,lifespan 里也 set 同引用

    # mount routers
    app.include_router(api_router)
    app.include_router(files_router)       # /api/files/*(P2-② 从 routes.py 拆出)
    app.include_router(budget_router)      # /api/budget(P2-② 从 routes.py 拆出)
    app.include_router(schedules_router)   # /api/schedule*(P2-② 从 routes.py 拆出)
    app.include_router(models_router)      # /api/model/* + /api/providers/*(P2-② 从 routes.py 拆出)
    app.include_router(ops_router)         # /api/update* + /api/ops/* + /api/search/config + /api/doctor/fix(P2-② 从 routes.py 拆出)
    app.include_router(workflow_router)    # /api/workflow/{pending_resume,resume,discard}(2026-07-11 从 routes.py carve 给红线头寸)
    app.include_router(mesh_router)        # /api/mesh/{frontier,sync}(设备 mesh 日志同步,docs/74)
    app.include_router(pair_router)        # /api/pair/*(📱 设备配对管理:颁发/列表/吊销,管理权本地锁)
    app.include_router(tokens_router)      # /api/tokens*(P2-② 从 routes.py 拆出)
    app.include_router(decision_prefs_router)  # /api/decision_prefs*(P2-② 从 routes.py 拆出)
    app.include_router(atoms_router)       # /api/atoms* + /api/atom/*(P2-② 从 routes.py 拆出)
    app.include_router(memory_router)      # /api/memory*(P2-② 从 routes.py 拆出)
    app.include_router(capability_router)  # /skills,/capability,/fs_grants,/silence,/mcp,/skill,/domains 等(P2-② 从 routes.py 拆出)
    app.include_router(roles_router)       # /roles,/models,/role/*,/agent/import(P2-② 从 routes.py 拆出)
    app.include_router(domain_router)      # /api/domain/*(建域/归档/编辑/恢复)(P2-② 从 routes.py 拆出)
    app.include_router(lines_router)       # /api/line*,/api/lines(P2-② 从 routes.py 拆出)
    app.include_router(system_router)      # /api/tasks,/task/*,/decisions/*,/proposals/pending,/setup_status,/health,/lang(P2-② 从 routes.py 拆出)
    app.include_router(conversations_router)  # /api/conversation*,/api/conversations(P2-② 从 routes.py 拆出)
    app.include_router(peers_router)       # /api/peers,/api/peer/switch(P2-② 从 routes.py 拆出)
    app.include_router(onboarding_router)  # /api/onboarding/*(「第一个 10 分钟」新手旅程 + 人格采集器)
    app.include_router(demo_router)        # /api/demo/*(随包演示实例「小林/Lin」只读浏览,GET-only)
    app.include_router(butler_router)      # /api/butler/*(文件管家第一课:扫描→方案预览卡)
    app.include_router(external_router)    # /api/external/*(跨 runtime 协作:外部公民管理面 + 按需接入引导)
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

    if STATIC_DIR.is_dir():
        @app.get("/m")
        def mobile_page():
            """📱 手机拍板页(R1 切片一):一屏=待拍板卡+大按钮,低地板零生造名词。
            token 门走同一中间件(非本机必带 ?token=,首访落 cookie);语言注入同 index。"""
            from fastapi.responses import HTMLResponse
            from karvyloop.i18n import get_locale
            try:
                html = (STATIC_DIR / "m.html").read_text(encoding="utf-8")
                loc = get_locale()
                html = html.replace('<html lang="en">',
                                    f'<html lang="{loc}" data-default-lang="{loc}">')
                return HTMLResponse(html)
            except Exception:
                return FileResponse(str(STATIC_DIR / "m.html"))

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


__all__ = ["build_console_app", "STATIC_DIR"]
