"""ws — WebSocket /ws 端点(M3+ 批 8.5-C)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-C。

K 边界:同 routes.py(K4 只读 + K5 factory-only)。
WS 协议:
  - server emit `snapshot` / `drive_done` / `h2a_envelope` / `h2a_proposal`(9.0d 真 emit)/ `error` / `pong`
    + §0.7 fail-loud:`task_status`(任务 running/done/error 即时推)/ `task_step`(workflow/圆桌步级)
      / `system_error`(后台 fire-and-forget 失败)—— 见 console/task_events.py
    + P4 逐字流式:`drive_event`(drive 进行中的增量 render 事件 text_delta/tool_call,worker 线程经
      run_coroutine_threadsafe 桥回 loop 推;终态 `drive_done` 清草稿渲染权威版)
  - client send `intent` / `h2a_decision` / `propose`(9.0d:触发 IntentAnalyst boot)/ `ping`

9.0d:`h2a_proposal` 此前只在协议注释里,从未真 emit;本拍接 ProposalPump 真路径
(client 发 `propose` → server 触发 analyst boot → 有 Proposal 推 `h2a_proposal`)。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from karvyloop.cli.main_loop import MainLoop
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.workbench.main_loop_bridge import drive_in_tui

from .routes import _stub_no_main_loop
from .serializers import drive_outcome_to_dict

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """主 WebSocket 端点。"""
    await websocket.accept()
    app = websocket.app
    # 注册 client
    clients: set = app.state.ws_clients
    clients.add(websocket)
    try:
        # 1. 推初始 snapshot
        await _send_snapshot(websocket, app)
        # 2. 收 client 消息
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "payload": "invalid json"})
                continue
            mtype = msg.get("type", "")
            payload = msg.get("payload", {})
            if mtype == "intent":
                await _handle_intent_ws(websocket, app, payload)
            elif mtype == "h2a_decision":
                await _handle_h2a_decision_ws(websocket, app, payload)
            elif mtype == "propose":
                await _handle_propose_ws(websocket, app, payload)
            elif mtype == "ping":
                await websocket.send_json({"type": "pong"})
            else:
                await websocket.send_json({"type": "error", "payload": f"unknown type: {mtype}"})
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(websocket)


async def _send_snapshot(websocket: WebSocket, app) -> None:
    """推当前 snapshot(简化:用 routes.api_snapshot 同款逻辑)。"""
    workbench: WorkbenchObserver = app.state.workbench
    from karvyloop.workbench.snapshot import snapshot_for_widgets
    from .serializers import widget_snapshot
    snap = snapshot_for_widgets(workbench)
    await websocket.send_json({"type": "snapshot", "payload": widget_snapshot(snap)})


async def _handle_intent_ws(websocket: WebSocket, app, payload: dict) -> None:
    """WS 路径的 intent 处理(同 routes.api_intent 的核心逻辑)。"""
    intent = (payload.get("intent") or "").strip()
    if not intent:
        await websocket.send_json({"type": "error", "payload": "empty intent"})
        return
    main_loop: Optional[MainLoop] = app.state.main_loop
    runtime_kwargs: dict = app.state.runtime_kwargs or {}
    workbench_app = app.state.workbench_app

    if workbench_app is not None:
        try:
            workbench_app.push_chat_log_line("user", intent)
        except Exception:
            pass

    if main_loop is None:
        outcome = _stub_no_main_loop(intent)
        await websocket.send_json({
            "type": "drive_done",
            "payload": drive_outcome_to_dict(outcome),
        })
        return

    # 9.1d:取当前对话上下文喂 drive(CV-8 上下文依赖门 + 慢脑消解多轮)
    # 9.2b:业务域线注入 value.md(CV-14)
    mgr = getattr(app.state, "conversation_manager", None)
    ctx = mgr.context_view() if mgr is not None else None
    governance = mgr.governance_text() if mgr is not None else ""

    # loop step4b 地基:从个人知识库召回相关 Belief,注入上下文最前(token 纪律:封顶 8 条)。
    # 让"关于你"的长期记忆真的喂进模型 —— 否则摄入/蒸馏写进的库没人读。
    mem = getattr(app.state, "memory", None)
    if mem is not None:
        try:
            from .routes import _recall_domain
            block = mem.recall_block(intent, scope="personal", limit=8,
                                     domain=_recall_domain(mgr))   # §2.6 域隔离
            if block:
                governance = (block + "\n\n" + governance).strip()
        except Exception:
            pass

    # §11 决策接口结晶:提案/drive 前注入"你的决策偏好"做预对齐(只偏置不执行,仍你拍板)。
    try:
        from karvyloop.console.decision_wire import prealign_governance
        _peer = mgr.current_peer() if mgr is not None else None
        _pa = prealign_governance(app, mem, domain=(getattr(_peer, "domain_id", "") or ""),
                                  role=(getattr(_peer, "role", "") or ""))
        if _pa:
            governance = (_pa + "\n\n" + governance).strip()
    except Exception:
        pass

    # ch4 #1:群里 @ 角色 → 定向给它;@ 命中跳过路由 PROPOSE(你已点名)。
    from .routes import _resolve_mention, _persona_for_current_peer, scope_for_peer
    ws_root = runtime_kwargs.get("workspace_root", "/")
    mention = (payload.get("mention") or "").strip()
    mention_domain = (payload.get("mention_domain") or "").strip()
    m_persona, m_speaker, m_scope = _resolve_mention(app, mgr, mention, ws_root,
                                                     domain=mention_domain, intent=intent)

    # 群里不 @ 任何人 → 没人回,小卡只轻提醒一句(不跑模型)
    from .routes import group_no_mention_nudge
    _nudge = group_no_mention_nudge(app, mgr, mention)
    if _nudge is not None:
        await websocket.send_json({"type": "drive_done", "payload": _nudge})
        return

    if m_persona is None:
        # 9.4-门2:私聊小卡 + 业务委派 → route_to_role PROPOSE(同 REST api_intent)
        from .routes import maybe_route_to_role
        routed = await maybe_route_to_role(app, mgr, intent)
        if routed is not None:
            if workbench_app is not None:
                try:
                    workbench_app.push_chat_log_line("system", routed["text"])
                except Exception:
                    pass
            # 修上下文串台 bug(同 REST api_intent):提议委派也 record_turn,否则追问撞旧 ctx。
            if mgr is not None:
                try:
                    mgr.record_turn(intent, routed["text"], brain="slow")
                except Exception:
                    pass
            await websocket.send_json({"type": "drive_done", "payload": routed})
            return

    # 9.4e/step5:私聊→小卡人格,业务域→per-role;@ 命中 → 被 @ 角色人格 + domain scope。
    if m_persona is not None:
        persona, eff_scope = m_persona, (m_scope or "domain")
    else:
        persona = _persona_for_current_peer(app, mgr, ws_root, intent=intent)
        eff_scope = scope_for_peer(mgr)

    # P4 逐字流式:drive 在 worker 线程跑,每个 render 事件经 run_coroutine_threadsafe 桥回本 loop
    # 推 `drive_event`(loop 不被 to_thread 阻塞,可即时广播)→ 前端逐字追加。失败不拖垮 drive。
    _loop = asyncio.get_running_loop()

    def _on_event(ev):
        try:
            from karvyloop.console.task_events import broadcast_drive_event
            asyncio.run_coroutine_threadsafe(broadcast_drive_event(app, ev), _loop)
        except Exception:
            pass

    try:
        from .routes import _normalize_images
        outcome = await drive_in_tui(intent, main_loop, ctx=ctx, governance=governance,
                                     persona=persona, scope=eff_scope, on_event=_on_event,
                                     images=_normalize_images(payload.get("images")),
                                     **runtime_kwargs)
    except Exception as e:
        await websocket.send_json({
            "type": "drive_done",
            "payload": {"intent": intent, "error": str(e), "brain": "SLOW", "text": ""},
        })
        return

    if workbench_app is not None and not outcome.error:
        try:
            workbench_app.push_chat_log_line("agent", outcome.text or "(empty result)",
                                             events=getattr(outcome, "events", None))
            if outcome.crystallized and outcome.skill_name:
                workbench_app.push_chat_log_line("system", f"🔔 已结晶: {outcome.skill_name}")
        except Exception:
            pass

    # 9.1d:这一轮入当前对话(CV-10,带 brain 标记)
    if mgr is not None and not outcome.error:
        try:
            _att = payload.get("attachments")
            mgr.record_turn(
                intent, outcome.text or "",
                brain=outcome.brain.value, task_id=outcome.task_id,
                data=({"attachments": _att} if _att else None),  # 多模态:落历史给人回看
            )
        except Exception:
            pass
        # loop step4b:轮后自动蒸馏(fire-and-forget,不阻塞 WS 响应)
        from .routes import schedule_auto_distill
        schedule_auto_distill(app, mgr)

    from .routes import speaker_display
    _payload = drive_outcome_to_dict(outcome)
    _payload["speaker"] = m_speaker or speaker_display(app, mgr)   # @ 命中 → 被 @ 角色署名
    await websocket.send_json({"type": "drive_done", "payload": _payload})


async def _handle_h2a_decision_ws(websocket: WebSocket, app, payload: dict) -> None:
    """WS 路径的 H2A 决策(K5 经 decision_to_envelope 工厂)。"""
    from fastapi import HTTPException
    # 复用 routes 的 HTTPException 路径
    from .routes import H2ADecideRequest, api_h2a_decide
    try:
        req = H2ADecideRequest(**payload)
        # api_h2a_decide 是 sync def + Request 形参;直接构造 Request 不易,改成手写一份 K5 校验
        from datetime import datetime, timezone
        from karvyloop.domain import Address
        from karvyloop.karvy.h2a import (
            H2A_DEFER, H2A_REJECT, H2ADecision, decision_to_envelope,
        )
        from .routes import DEFAULT_REJECT_REASON
        from .serializers import envelope_to_dict

        user_addr = Address(
            domain_id=req.user_address_domain_id,
            role=req.user_address_role,
            agent_id=req.user_address_agent_id,
        )
        to_addr = Address(
            domain_id=req.to_address_domain_id,
            role=req.to_address_role,
            agent_id=req.to_address_agent_id,
        )
        # 不逼用户填(Hardy)+ 守协议 A8:REJECT 留空 → 补诚实占位(与 REST 同语义)。
        eff_reason = req.reason
        if req.decision == H2A_REJECT and not req.reason.strip():
            eff_reason = DEFAULT_REJECT_REASON
        decision_obj = H2ADecision(
            decision=req.decision,
            reason=eff_reason,
            proposal_id=req.proposal_id,
            user_address=user_addr,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        # §11 决策接口结晶:把这次拍板记成样本(信号源),攒够批量后结晶成"决策偏好"。
        # 观察决策**绝不**打断决策流(H2A 是命脉)→ 整段裹 try。
        try:
            import time as _time
            from karvyloop.console.decision_wire import (
                observe_decision, schedule_decision_crystallize,
            )
            from karvyloop.crystallize.decision_pref import DecisionSample
            _ctx = ""
            _kind = ""
            _skip = False
            _reg = getattr(app.state, "proposal_registry", None)
            if _reg is not None:
                try:
                    _p = _reg.get(req.proposal_id)
                    _ctx = getattr(_p, "summary", "") or ""
                    _kind = getattr(_p, "kind", "") or ""
                    # 确认"决策偏好"本身不是工作决策 → 别观察(否则结晶元循环:确认偏好又生样本)
                    if _kind == "confirm_decision_pref":
                        _skip = True
                except Exception:
                    pass
            if not _skip:
                observe_decision(app, DecisionSample(
                    decision=req.decision, context=(_ctx or req.proposal_id),
                    reason=eff_reason, scope="personal",
                    domain=req.to_address_domain_id or "", role=req.to_address_role or "",
                    ts=_time.time()))
                schedule_decision_crystallize(app)
                # §11 MVP 复利信号:记真实提案决策结果(confirm 类已被 _skip 排除,不计入)
                _stats = getattr(app.state, "decision_stats", None)
                if _stats is not None:
                    _stats.record(req.decision)
                # 最近拍板流水(只读回看):拍完会从待决列消失,但人能回看拍过什么
                _log = getattr(app.state, "decision_log", None)
                if _log is not None:
                    _log.record(decision=req.decision, summary=_ctx, proposal_id=req.proposal_id,
                                reason=eff_reason, kind=_kind,
                                domain=req.to_address_domain_id or "", role=req.to_address_role or "")
        except Exception:
            pass
        # D5(docs/30):按 kind 兑现(若接了 registry)— 与 REST /api/h2a_decide 同语义。
        # 9.4-门2:route_to_role handler 会同步 drive(一次 LLM)→ 用 to_thread 包,
        # 不阻塞 WS 事件循环(REST 路径是 sync def,FastAPI 已自动线程池化)。
        import asyncio

        async def _dispatch():
            registry = getattr(app.state, "proposal_registry", None)
            if registry is None:
                return None
            handlers = getattr(app.state, "proposal_handlers", None) or {}
            res = await asyncio.to_thread(
                registry.decide, req.proposal_id, req.decision, handlers=handlers
            )
            return res.to_dict() if res is not None else None

        if req.decision == H2A_DEFER:
            await websocket.send_json({"type": "h2a_envelope", "payload": {"envelope": None, "decision": req.decision, "dispatch": await _dispatch()}})
            return
        # REJECT 不强制 reason(Hardy):reason 空也照拒;K5(人拍板/by=[])由工厂保证,与 reason 无关。
        env = decision_to_envelope(decision_obj, to_addr)
        _disp = await _dispatch()   # 兑现(handler 内会 stash 执行后回报卡)
        from karvyloop.console.proposal_handlers import pop_report_card
        await websocket.send_json({
            "type": "h2a_envelope",
            "payload": {"envelope": envelope_to_dict(env), "decision": req.decision,
                        "dispatch": _disp, "report_card": pop_report_card(app, req.proposal_id)},
        })
    except Exception as e:
        await websocket.send_json({"type": "h2a_envelope", "payload": {"envelope": None, "error": str(e)}})


async def _handle_propose_ws(websocket: WebSocket, app, payload: dict) -> None:
    """WS 路径触发 IntentAnalyst boot 一次(9.0d)。

    pump 通过 broadcast_proposal 推给**所有** ws_clients(含本 client);
    若 pump=None 或 analyst 沉默,只回本 client 一条 status 提示(不广播)。

    K5:本端点**只推建议** — 决策仍走 client `h2a_decision` → decision_to_envelope 工厂。
    """
    from karvyloop.console.proposals import proactive_from_state

    pump = getattr(app.state, "proposal_pump", None)
    proposal = None
    sent = 0
    if pump is not None:
        recent_n = int(payload.get("recent_n", 20)) if isinstance(payload, dict) else 20
        proposal, sent = await pump.boot(recent_n=recent_n)
    # loop-step2b:pump 未接 / 沉默 → 确定性兜底(观察任务看板:失败任务 → 提议重试)。
    # proactive_from_state 内部已 broadcast 给所有 client(含本 client)。
    if proposal is None:
        proposal, sent = await proactive_from_state(app)
    if proposal is None:
        # 真的没啥可提的:只回本 client 一条空提示(不广播)
        await websocket.send_json({"type": "h2a_proposal", "payload": None, "sent": 0})
        return
    # 有 proposal:已 broadcast 给所有 client(含本 client),此处仅 log
    logger.debug(f"[ws] propose 触发 → 推 {sent} client(s)")


@router.get("/ws/_health")
def ws_health(request: Request) -> dict[str, Any]:
    """WS 端点健康检查(GET,供 e2e/loadbalancer 探活)。"""
    return {"ws_clients": len(request.app.state.ws_clients)}


__all__ = ["router"]
