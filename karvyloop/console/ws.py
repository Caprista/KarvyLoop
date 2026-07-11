"""ws — WebSocket /ws 端点(M3+ 批 8.5-C)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-C。

K 边界:同 routes.py(K4 只读 + K5 factory-only)。
WS 协议:
  - server emit `snapshot` / `drive_done` / `h2a_envelope` / `h2a_proposal`(9.0d 真 emit)/ `error` / `pong`
    + §0.7 fail-loud:`task_status`(任务 running/done/error 即时推)/ `task_step`(workflow/圆桌步级)
      / `system_error`(后台 fire-and-forget 失败)—— 见 console/task_events.py
    + P4 逐字流式:`drive_event`(drive 进行中的增量 render 事件 text_delta/tool_call,worker 线程经
      run_coroutine_threadsafe 桥回 loop 推;终态 `drive_done` 清草稿渲染权威版)
    + ⑤c 环境感知召回:`ambient_recall`(intent 到达时并行算,相关技能/知识主动浮出;
      纯本地重叠打分零 LLM,fire-and-forget 不挡 drive;低分/冷却中静默不推)
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

from karvyloop.runtime.main_loop import MainLoop
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.workbench.main_loop_bridge import drive_in_tui

from .routes import _stub_no_main_loop
from .serializers import drive_outcome_to_dict

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """主 WebSocket 端点。"""
    app = websocket.app
    from karvyloop.console import access as _acc
    # 同源门(**C1 修复,始终生效**):浏览器对 WS 握手必带 Origin。恶意网页 `new WebSocket("ws://127.0.0.1:8766/ws")`
    # 带的是 evil.com 的 Origin ≠ 本机 Host → 拒握手,堵住跨站 WebSocket 劫持(CSWSH=本机 RCE+数据外泄)。
    # loopback 对 token 免密**但不对同源门免密** —— 这正是之前"把 localhost 当无条件可信"的盲区。
    if not _acc.origin_ok(websocket.headers.get("origin", ""),
                          websocket.headers.get("sec-fetch-site", ""),
                          websocket.headers.get("host", "")):
        await websocket.close(code=1008)   # policy violation(跨源)
        return
    # 访问令牌门(HTTP 中间件不管 WS scope,这里单独查):本机免密;非本机需 cookie/query token,否则拒握手。
    _token = getattr(app.state, "access_token", None)
    if _token:
        _client = websocket.client.host if websocket.client else ""
        if not _acc.is_loopback(_client):
            _supplied = websocket.cookies.get(_acc.COOKIE) or websocket.query_params.get("token") or ""
            if not _acc.token_ok(_supplied, _token):
                await websocket.close(code=1008)   # policy violation
                return
    await websocket.accept()
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


def _direct_chat_role_domain(app, mgr, *, mention: str, mention_domain: str) -> tuple[str, str]:
    """直聊路径下,这轮 drive 归属的(域, 角色)—— 供角色经验沉淀(W1 补直聊漏)。

    - @ 命中(群里点名一个业务角色)→ 用被 @ 角色的(域, 角色);跨域同名靠 mention_domain 消歧。
    - 否则用当前 peer:业务域私聊 → (peer.domain_id, peer.role/agent_id);私聊小卡(l0)/群协调
      场 → ("", "")(sediment 内部保守门会拒 l0/无域,这里给空即可)。
    只返回**能归属到某业务域某角色**的情形;拿不到 → ("", "")(触发点交给内部保守门兜底)。
    """
    try:
        from karvyloop.karvy.capability import is_karvy_peer
        peer = mgr.current_peer() if mgr is not None else None
    except Exception:
        peer = None
    if peer is None:
        return "", ""
    # @ 命中:在群场点名了一个业务角色 → 归属被 @ 角色(它自己的域)
    m = (mention or "").strip()
    if m and getattr(peer, "role", "") == "group":
        try:
            from .roundtable_engine import _roundtable_roster
            did = (mention_domain or "").strip()
            for a in _roundtable_roster(app, peer):
                if a.agent_id == m and (not did or a.domain_id == did):
                    a_did = getattr(a, "domain_id", "") or ""
                    if a_did and not is_karvy_peer(a_did):
                        a_role = (a.agent_id if (getattr(a, "role", "") == "agent" and a.agent_id)
                                  else getattr(a, "role", "")) or ""
                        return a_did, a_role
        except Exception:
            return "", ""
        return "", ""
    # 非 @:当前 peer 直聊。私聊小卡(l0)/群场 → 空(内部保守门会拒)
    did = getattr(peer, "domain_id", "l0") or "l0"
    try:
        if is_karvy_peer(did) or getattr(peer, "role", "") == "group":
            return "", ""
    except Exception:
        return "", ""
    role = (peer.agent_id if (getattr(peer, "role", "") == "agent" and getattr(peer, "agent_id", ""))
            else getattr(peer, "role", "")) or ""
    return did, role


async def _handle_intent_ws(websocket: WebSocket, app, payload: dict) -> None:
    """WS 路径的 intent 处理(同 routes.api_intent 的核心逻辑)。"""
    intent = (payload.get("intent") or "").strip()
    if not intent:
        await websocket.send_json({"type": "error", "payload": "empty intent"})
        return

    # ⑤c 环境感知召回:相关技能/知识主动浮出(工作台"料")。fire-and-forget:
    # 纯本地重叠打分(零 LLM,毫秒级),结果走 WS 广播,不 await 在 drive 前面挡路。
    _schedule_ambient_recall(app, intent)

    main_loop: Optional[MainLoop] = app.state.main_loop
    runtime_kwargs: dict = app.state.runtime_kwargs or {}
    workbench_app = app.state.workbench_app

    if workbench_app is not None:
        try:
            workbench_app.push_chat_log_line("user", intent)
        except Exception:
            pass

    # 9.1d:取当前对话上下文喂 drive(CV-8 上下文依赖门 + 慢脑消解多轮)
    # 9.2b:业务域线注入 value.md(CV-14)
    mgr = getattr(app.state, "conversation_manager", None)

    # 共创模式(docs/47 ④ 会话粘性):当前对话已在共创态 → 整轮进状态机,**不再依赖
    # 逐轮关键词命中**(修"第二轮换说法就掉线"脆点);"就这样吧/退出"由状态机清态。
    # 未激活 / 状态机让路(换话题)→ None → 走正常 drive(0 回归)。
    # 放在 main_loop 检查**之前**:状态机不依赖 main_loop(无 LLM 也有确定性兜底)。
    try:
        from karvyloop.karvy.cocreation import cocreation_take_turn
        _coc_reply = await cocreation_take_turn(
            app, mgr, intent,
            gateway=runtime_kwargs.get("gateway"),
            model_ref=runtime_kwargs.get("model_ref", ""))
    except Exception:
        logger.warning("[ws] cocreation 轮处理失败,降级正常 drive", exc_info=True)
        _coc_reply = None
    if _coc_reply is not None:
        if workbench_app is not None:
            try:
                workbench_app.push_chat_log_line("agent", _coc_reply)
            except Exception:
                pass
        # 共创轮必 record_turn(早返回不记 = ctx 串台,2026-06-25 世界杯 bug 病根)
        if mgr is not None:
            try:
                mgr.record_turn(intent, _coc_reply, brain="slow")
            except Exception:
                pass
        await websocket.send_json({"type": "drive_done", "payload": {
            "intent": intent, "brain": "SLOW", "fast_brain_hit": False,
            "crystallized": False, "skill_name": "", "routed": False,
            "cocreation": True, "text": _coc_reply}})
        return

    if main_loop is None:
        outcome = _stub_no_main_loop(intent)
        await websocket.send_json({
            "type": "drive_done",
            "payload": drive_outcome_to_dict(outcome),
        })
        return

    ctx = mgr.context_view() if mgr is not None else None
    governance = mgr.governance_text() if mgr is not None else ""
    _domain_gov = governance   # 域治理块(value.md+deontic);persona 已编入时在下方去重

    # loop step4b 地基:从个人知识库召回相关 Belief,注入上下文最前(token 纪律:封顶 8 条)。
    # 让"关于你"的长期记忆真的喂进模型 —— 否则摄入/蒸馏写进的库没人读。
    mem = getattr(app.state, "memory", None)
    _recall_used: list = []   # Q1 召回解释:这轮垫了哪几条记忆(空列表=没垫),挂进 drive_done
    # docs/69 Q4:过去认知问句("你当时/上个月怎么理解的")→ 按那个时点召回(确定性正则,零 LLM)。
    from .routes import _resolve_recall_as_of
    _recall_as_of = _resolve_recall_as_of(intent)
    if mem is not None:
        try:
            from .routes import _recall_domain
            block = mem.recall_block(intent, scope="personal", limit=8,
                                     domain=_recall_domain(mgr),   # §2.6 域隔离
                                     as_of=_recall_as_of,
                                     explain_sink=_recall_used)
            if block:
                governance = (block + "\n\n" + governance).strip()
        except Exception:
            _recall_used = []   # 召回失败没垫成 → 不留半截解释

    # §11 决策接口结晶:提案/drive 前注入"你的决策偏好"做预对齐(只偏置不执行,仍你拍板)。
    try:
        from karvyloop.console.decision_wire import prealign_governance
        _peer = mgr.current_peer() if mgr is not None else None
        _pa = prealign_governance(app, mem, query=intent,
                                  domain=(getattr(_peer, "domain_id", "") or ""),
                                  role=(getattr(_peer, "role", "") or ""))
        if _pa:
            governance = (_pa + "\n\n" + governance).strip()
    except Exception:
        pass

    # docs/66 §F:知识线 → 馆员人设进最前(其他线零侵入);与 routes.api_intent 同款接缝
    try:
        from karvyloop.cognition.knowledge_chat import knowledge_governance
        governance = knowledge_governance(mgr.current_peer() if mgr is not None else None, governance)
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

    # 去重(对抗验收):paradigm 编译的 persona 已把域治理(value.md+deontic)编进 system prompt,
    # governance 里再带一份 = 双注入白烧 token。域块是 governance 的**尾段**(召回/预对齐都往前贴),
    # 精准剥掉尾段,保留召回 + 决策偏好预对齐。与委派路径 proposal_handlers 的 _base="" 同一策略。
    if getattr(persona, "covers_domain_governance", False) and _domain_gov and \
            governance.endswith(_domain_gov):
        governance = governance[: -len(_domain_gov)].strip()

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
        from .routes import _normalize_images, self_create_role_id
        outcome = await drive_in_tui(intent, main_loop, ctx=ctx, governance=governance,
                                     persona=persona, scope=eff_scope, on_event=_on_event,
                                     images=_normalize_images(payload.get("images")),
                                     # §15.5:直接聊天也挂 create_atom(角色标配,Hardy)+ 归属当前角色
                                     atom_registry=getattr(app.state, "atom_registry", None),
                                     role_registry=getattr(app.state, "role_registry", None),
                                     self_create_role=self_create_role_id(mgr),
                                     # 小卡自我认知落地:建 agent 意图 → 挂 instantiate_domain_template
                                     domain_registry=getattr(app.state, "domain_registry", None),
                                     domain_store=getattr(app.state, "domain_store", None),
                                     # 跨 runtime 协作(docs/71 M1):小卡人格 + 接了 citizen_registry →
                                     # 挂 external_agent/attach/list/revoke(WS 聊天里也能接入/派活外部 runtime)。
                                     # drive_in_tui 内再门一道(persona.karvy_self);业务角色不挂(0 回归)。
                                     citizen_registry=getattr(app.state, "citizen_registry", None),
                                     external_bridge_factory=getattr(app.state, "external_bridge_factory", None),
                                     external_token_recorder=getattr(app.state, "external_token_recorder", None),
                                     **runtime_kwargs)
    except Exception as e:
        await websocket.send_json({
            "type": "drive_done",
            "payload": {"intent": intent, "error": str(e), "brain": "SLOW", "text": "",
                        "recall_used": _recall_used},
        })
        return

    # 共创递口(docs/47 §3.1):建 agent 意图命中(L0 关键词 / L1 LLM build 分类)→
    # 本轮回复末尾主动递"一起共创"的口,并挂 OFFERED 会话态(下一轮应答不再依赖关键词)。
    # 递口零副作用(只写会话态);失败静默 = 旧行为。
    if not outcome.error:
        try:
            from karvyloop.karvy.cocreation import maybe_offer_cocreation
            _offer = await maybe_offer_cocreation(
                app, mgr, intent,
                gateway=runtime_kwargs.get("gateway"),
                model_ref=runtime_kwargs.get("model_ref", ""))
            if _offer:
                outcome.text = ((outcome.text or "").rstrip() + "\n\n" + _offer).strip()
        except Exception:
            logger.debug("[ws] 共创递口失败(静默)", exc_info=True)

    # fs_grants:这轮 drive 里碰壁的工作区外路径 → 升授权卡(去重;敏感路径永不出卡)
    try:
        from karvyloop.console.proposals import raise_fs_access_cards
        await raise_fs_access_cards(app)
    except Exception:
        pass
    from .routes import speaker_display
    _turn_speaker = m_speaker or speaker_display(app, mgr)   # @ 命中=角色花名,否则当前场署名
    if workbench_app is not None and not outcome.error:
        try:
            workbench_app.push_chat_log_line("agent", outcome.text or "(empty result)",
                                             events=getattr(outcome, "events", None),
                                             speaker=_turn_speaker)   # per-turn 署名(历史重渲不再错标小卡)
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
        # W1(docs/56 审计 HIGH):角色经验沉淀补**直聊路径**(此前只在委派 ACCEPT 沉,
        # 直接跟业务角色私聊/群里 @ 它做完域内活儿角色学不到 → 飞轮半瘫)。
        # 只对**能归属到某业务域某角色**的成功轮触发;保守门(无域/l0/纯失败)在
        # sediment_experience/should_distill 内部兜底。直聊无独立 checker → 干净完成(无 error)
        # 即本路径可得的最强成功信号,当 verified。fire-and-forget、fail-soft,绝不阻断响应。
        try:
            _exp_domain, _exp_role = _direct_chat_role_domain(
                app, mgr, mention=mention, mention_domain=mention_domain)
            if _exp_domain and _exp_role:
                from .proposal_handlers import _schedule_role_experience
                _schedule_role_experience(
                    app, role=_exp_role, domain=_exp_domain, requirement=intent,
                    result=(outcome.text or ""), success=True, verified=True)
        except Exception:
            logger.debug("[ws] 直聊角色经验沉淀触发失败(静默,不阻断)", exc_info=True)

    _payload = drive_outcome_to_dict(outcome)
    _payload["speaker"] = _turn_speaker   # @ 命中 → 被 @ 角色署名(与历史 push 同一值)
    _payload["recall_used"] = _recall_used   # Q1 召回解释:垫了哪几条记忆(空=没垫)
    if _recall_as_of is not None:
        _payload["recall_as_of"] = _recall_as_of   # docs/69 Q4:按此时点召回(chip 标"按 X 时点的记忆")
    await websocket.send_json({"type": "drive_done", "payload": _payload})


# ---- ⑤c 环境感知召回(ambient recall):工作台"料"的主动浮出 ----

WS_TYPE_AMBIENT_RECALL = "ambient_recall"


def _ambient_cooldown(app):
    """冷却表懒挂在 app.state(进程级,跨连接共享 —— 同一 intent 换个标签页也不重复推)。"""
    from karvyloop.karvy.ambient import AmbientCooldown
    cd = getattr(app.state, "ambient_cooldown", None)
    if cd is None:
        cd = AmbientCooldown()
        app.state.ambient_cooldown = cd
    return cd


def _schedule_ambient_recall(app, intent: str) -> None:
    """fire-and-forget 触发(镜像 task_events._schedule 模式):无 loop → 静默跳过。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_broadcast_ambient_recall(app, intent))


async def _broadcast_ambient_recall(app, intent: str) -> None:
    """算命中 → 广播 `ambient_recall` 给所有 WS client(复用 task_events 的
    ws_clients 广播/剔死连接模式)。零 LLM(ambient_recall 纯词面重叠);任何失败
    只 debug log,绝不冒泡到 drive 路径。"""
    try:
        from karvyloop.karvy.ambient import ambient_recall

        from .routes import _recall_domain, scope_for_peer
        ml = getattr(app.state, "main_loop", None)
        mgr = getattr(app.state, "conversation_manager", None)
        hits = ambient_recall(
            intent,
            skill_index=getattr(ml, "skill_index", None),
            skills_dir=getattr(ml, "skills_dir", None),
            memory=getattr(app.state, "memory", None),
            skill_scope=scope_for_peer(mgr),      # 场作用域:私聊=user 技能,业务域=domain 技能
            domain=_recall_domain(mgr),           # §2.6:域私有认知只在本域浮出
            cooldown=_ambient_cooldown(app),
        )
        if not hits:
            return   # 宁静默勿噪音:低分/冷却中/无候选都不推
        from .task_events import _broadcast
        await _broadcast(app, {
            "type": WS_TYPE_AMBIENT_RECALL,
            "payload": {"hits": [h.to_dict() for h in hits], "for_intent": intent},
        })
    except Exception as e:
        logger.debug(f"[ws] ambient_recall 失败(静默,不影响 drive): {e}")


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
        # 单一接缝 record_decision_signals(样本→结晶 / stats / decision_log;REST 路径同调,
        # P3-a 对齐)。绝不打断决策流(内部自吞)。
        from karvyloop.console.decision_wire import record_decision_signals
        record_decision_signals(app, decision=req.decision, proposal_id=req.proposal_id,
                                reason=eff_reason,
                                domain=req.to_address_domain_id or "",
                                role=req.to_address_role or "",
                                edits=(req.edits or None))
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
                lambda: registry.decide(req.proposal_id, req.decision, handlers=handlers,
                                        edits=(req.edits or None))
            )
            # 委派兑现同步 drive 后:被委派 role 碰壁工作区外路径攒的「想要」→ 这一轮升 H2A
            # 授权卡(与顶层 drive 收尾同待遇;REST 端点同调)。已在事件循环里 → 直接 await。
            try:
                from karvyloop.console.proposals import raise_fs_access_cards
                await raise_fs_access_cards(app)
            except Exception:
                logger.debug("[ws] 委派收尾升 fs_access 卡失败(不阻断)", exc_info=True)
            return res.to_dict() if res is not None else None

        if req.decision == H2A_DEFER:
            await websocket.send_json({"type": "h2a_envelope", "payload": {"envelope": None, "proposal_id": req.proposal_id, "decision": req.decision, "dispatch": await _dispatch()}})
            return
        # REJECT 不强制 reason(Hardy):reason 空也照拒;K5(人拍板/by=[])由工厂保证,与 reason 无关。
        env = decision_to_envelope(decision_obj, to_addr)
        _disp = await _dispatch()   # 兑现(handler 内会 stash 执行后回报卡)
        from karvyloop.console.proposal_handlers import pop_report_card
        await websocket.send_json({
            "type": "h2a_envelope",
            "payload": {"envelope": envelope_to_dict(env), "proposal_id": req.proposal_id, "decision": req.decision,
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
