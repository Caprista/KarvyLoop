"""routes_schedules — /api/schedule* 端点(定时任务:列/解析/建/开关/删/立即跑)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import/monkeypatch 可达。

Hardy 2026-06-25:只有 Karvy 能起定时任务(角色无调度工具→天然起不了),这里是全系统唯一审计面。

跨模块共享:`fire_schedule` 复用 routes.py 里 roundtable/persona 那组共享 helper
(`_persona_for_role_addr` / `_model_for_role` / `_rk_model` / `drive_in_tui`)—— **在调用点从
`routes` 取**(不复制),这样测试对 `routes.drive_in_tui` 的 monkeypatch 仍然穿得过来。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _scheduler_store(app):
    st = getattr(app.state, "scheduler_store", None)
    if st is None:
        import pathlib
        from karvyloop.karvy.scheduler import SchedulerStore
        cfgp = getattr(app.state, "config_path", "") or ""
        path = (pathlib.Path(cfgp).parent / "schedules.json") if cfgp else None
        st = SchedulerStore(path)
        app.state.scheduler_store = st
    return st


def _schedule_parser(app):
    """NL→cron 解析器(gateway 派生;无 gateway→None)。缓存到 app.state。"""
    if getattr(app.state, "_schedule_parser_cached", "MISS") == "MISS":
        from karvyloop.karvy.schedule_parser import make_schedule_parser
        rk = getattr(app.state, "runtime_kwargs", None) or {}
        app.state._schedule_parser_cached = make_schedule_parser(rk.get("gateway"), rk.get("model_ref", ""))
    return app.state._schedule_parser_cached


def _resolve_schedule_target(app, role_name: str):
    """把角色名解析成 (domain_id, role, agent_id, display);解析不到 → 全空(=小卡自己干)。"""
    if not (role_name or "").strip():
        return "", "", "", ""
    reg = getattr(app.state, "domain_registry", None)
    if reg is None:
        return "", "", "", ""
    try:
        for d in reg.list_active():
            for addr in reg.resolve_members(d.id):
                if addr.role == "user":
                    continue
                if role_name in (addr.agent_id or "") or role_name in (addr.role or ""):
                    return d.id, addr.role, (addr.agent_id or ""), f"{d.name} / {addr.agent_id or addr.role}"
    except Exception:
        pass
    return "", "", "", ""


def _schedule_to_dict(app, t) -> dict[str, Any]:
    from karvyloop.karvy.scheduler import next_run_after
    import time as _t
    tgt = ""
    if t.target_role:
        _, _, _, disp = _resolve_schedule_target(app, t.target_agent_id or t.target_role)
        tgt = disp or t.target_role
    return {
        "id": t.id, "cron": t.cron, "intent": t.intent, "title": t.title,
        "enabled": t.enabled, "target": tgt,
        "next_run": next_run_after(t.cron, max(_t.time(), t.last_run)) if t.enabled else None,
        "last_run": t.last_run or None, "last_status": t.last_status, "last_error": t.last_error,
    }


@router.get("/schedules")
def api_schedules(request: Request) -> dict[str, Any]:
    """列所有定时任务(全系统唯一审计面)。"""
    st = _scheduler_store(request.app)
    return {"schedules": [_schedule_to_dict(request.app, t) for t in st.all()]}


class ScheduleParseRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)


@router.post("/schedule/parse")
def api_schedule_parse(req: ScheduleParseRequest, request: Request) -> dict[str, Any]:
    """NL→cron 预览(小卡解析,不创建):你说一句话 → 出 cron+intent+委派,确认后再 create。"""
    parser = _schedule_parser(request.app)
    if parser is None:
        return {"ok": False, "reason": "no_llm"}
    from karvyloop.karvy.schedule_parser import local_now_str
    now_str = local_now_str()   # ISO8601 带显式时区 offset("每天下午3点"/"明早"按此时区推算)
    parsed = parser(req.description, now_str)
    if parsed is None:
        return {"ok": False, "reason": "not_understood"}   # 没听懂明确时间 → 让用户换种说法
    return {"ok": True, **parsed}


class ScheduleCreateRequest(BaseModel):
    cron: str = Field(..., min_length=1, max_length=120)
    intent: str = Field(..., min_length=1, max_length=2000)
    title: str = Field(default="", max_length=60)
    target_role: str = Field(default="", max_length=64)   # 角色名;空=小卡自己干


@router.post("/schedule/create")
def api_schedule_create(req: ScheduleCreateRequest, request: Request) -> dict[str, Any]:
    """新建定时任务。创建权 = Karvy/控制台这一面(角色没有调度工具,天然起不了)。"""
    st = _scheduler_store(request.app)
    did, role, aid, _ = _resolve_schedule_target(request.app, req.target_role)
    t = st.add(req.cron, req.intent, title=req.title,
               target_domain=did, target_role=role, target_agent_id=aid)
    if t is None:
        return {"ok": False, "reason": "bad_cron_or_intent"}
    return {"ok": True, "schedule": _schedule_to_dict(request.app, t)}


class ScheduleIdRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=32)
    enabled: bool = True


@router.post("/schedule/toggle")
def api_schedule_toggle(req: ScheduleIdRequest, request: Request) -> dict[str, Any]:
    ok = _scheduler_store(request.app).set_enabled(req.id, req.enabled)
    return {"ok": ok}


@router.post("/schedule/delete")
def api_schedule_delete(req: ScheduleIdRequest, request: Request) -> dict[str, Any]:
    return {"ok": _scheduler_store(request.app).remove(req.id)}


@router.post("/schedule/run_now")
async def api_schedule_run_now(req: ScheduleIdRequest, request: Request) -> dict[str, Any]:
    """手动跑一次(看板上的"▶ 跑一次")。"""
    st = _scheduler_store(request.app)
    t = st.get(req.id)
    if t is None:
        return {"ok": False, "reason": "not_found"}
    await fire_schedule(request.app, t)
    return {"ok": True}


async def raise_schedule_catchup_cards(app: Any) -> int:
    """跨天离线追赶(持久 loop 二环收尾):console 开机时调一次 —— 扫水位算出关机期间
    每条定时任务错过的场次,**每个 schedule 聚合弹一张**「要补跑一次吗」H2A 卡。

    H2A 纪律:
    - 绝不 auto-execute:卡只是问;ACCEPT 才由既有 run_task handler 真跑**一次**
      (骑 run_task,payload 带 schedule_id/missed_count;绝不逐场重放)。
    - 水位在 catchup_scan 里已推进到 now:REJECT/无人拍 = 不补,下次开机不再弹同一批。
    - 防重弹:proposal_id 按 schedule id 稳定;同 schedule 的补跑卡还挂着 → 跳过。
    返回本次弹出的卡数;单条失败跳过不阻断。
    """
    import time as _t
    st = _scheduler_store(app)
    missed = st.catchup_scan(now=_t.time())
    if not missed:
        return 0
    from karvyloop.console.proposals import broadcast_proposal
    from karvyloop.karvy.proactive import catchup_proposal_for
    reg = getattr(app.state, "proposal_registry", None)
    raised = 0
    for m in missed:
        t = m["task"]
        if reg is not None and reg.get(f"schedule_catchup-{t.id}") is not None:
            continue   # 同 schedule 已有待决补跑卡挂着,不重弹
        card = catchup_proposal_for(t, m["missed_count"], m.get("latest_missed"),
                                    capped=bool(m.get("capped")), now=_t.time())
        if card is None:
            continue
        try:
            await broadcast_proposal(app, card)
            raised += 1
        except Exception as e:
            logger.warning(f"[schedule] 补跑确认卡广播失败(跳过 {t.id}): {e}")
    return raised


async def fire_schedule(app: Any, t) -> None:
    """到点(或手动)执行一条定时任务:灌进 drive 管线;有委派目标就以那个角色人格跑,否则小卡自己跑。

    结果记成一个首页任务(看得见跑过)。走 §13(动态任务每次重跑、不回放 stale)。失败 fail-loud 记 last_error。
    """
    # roundtable/persona 共享 helper + drive_in_tui 从 routes 取(单一真源 + 保 monkeypatch 穿透)。
    from . import routes as _routes
    import time as _t
    mgr = getattr(app.state, "conversation_manager", None)
    main_loop = getattr(app.state, "main_loop", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    st = _scheduler_store(app)
    if main_loop is None or rk.get("gateway") is None:
        st.mark_run(t.id, "error", error="未接 LLM(--no-llm?)")
        return
    ws = rk.get("workspace_root", "/")
    persona = None
    eff_rk = rk
    who = "⏰ 小卡"
    if t.target_domain and t.target_role:
        try:
            from karvyloop.domain.registry import Address
            dom_reg = getattr(app.state, "domain_registry", None)
            addr = Address(domain_id=t.target_domain, role=t.target_role, agent_id=t.target_agent_id or None)
            dom = dom_reg.get(t.target_domain) if dom_reg is not None else None
            persona, speaker = _routes._persona_for_role_addr(app, addr, dom, ws)
            who = f"⏰ {speaker}"
            eff_rk = _routes._rk_model(rk, _routes._model_for_role(app, t.target_agent_id or t.target_role))
        except Exception:
            persona = None
    task_reg = getattr(app.state, "task_registry", None)
    task_id = task_reg.start(who=who, domain_id=(t.target_domain or "l0"),
                             role=(t.target_role or ""), intent=f"⏰ {t.intent[:120]}",
                             kind="schedule") if task_reg else None   # docs/90 刀3a
    try:
        scope = "domain" if t.target_domain and t.target_role else None
        # Step 0(a):你的决策标准在**定时任务**触发时也生效(到点替你做事,标准照管)。
        from karvyloop.console.decision_wire import assemble_governance
        _sched_gov = assemble_governance(app, intent=t.intent, domain=(t.target_domain or ""),
                                         role=(t.target_role or ""))
        # docs/90 刀3a:定时触发 run 也登 running-run 注册表 → /api/task/cancel 可停。
        from karvyloop.atoms.abort import abort_scope as _abort_scope
        with _abort_scope(task_id or ""):
            outcome = await _routes.drive_in_tui(t.intent, main_loop, governance=_sched_gov,
                                                 persona=persona, scope=scope, **eff_rk)
        err = getattr(outcome, "error", "") or ""
        if task_reg and task_id:
            task_reg.finish(task_id, result=(outcome.text or ""), error=err)
        st.mark_run(t.id, "error" if err else "ok", ts=_t.time(), error=err)
    except Exception as e:
        logger.exception(f"[schedule] 执行异常 {t.id}: {e}")
        if task_reg and task_id:
            task_reg.finish(task_id, error=str(e))
        st.mark_run(t.id, "error", ts=_t.time(), error=str(e))
