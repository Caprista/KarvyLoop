"""routes_system — 系统/审计/杂项只读端点 + 任务看板 + 语言(P2-② 纯搬移)。

覆盖:/api/tasks、/task/{id}、/task/{id}/trace、/decisions/recent、/decisions/audit、
/proposals/pending、/setup_status、/health、/lang(读/写)、/decision_card(读/judge)、/propose。
自带 APIRouter,由 app.py include_router;符号在 routes.py re-export 保既有 import 可达。

从 routes.py 逐字搬移,零逻辑改动。均只读/写 request.app.state,不依赖 routes.py 核心 helper。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api")


# ---- 9.5 P2:任务看板 ----

@router.get("/tasks")
def api_tasks(request: Request) -> dict[str, Any]:
    """列最近任务(谁在忙/状态/结果摘要/关联 peer)。"""
    reg = getattr(request.app.state, "task_registry", None)
    if reg is None:
        return {"tasks": []}
    return {"tasks": reg.list()}


@router.get("/task/{task_id}")
def api_task_detail(task_id: str, request: Request) -> dict[str, Any]:
    """一个任务的结果文档(完整结果)。"""
    reg = getattr(request.app.state, "task_registry", None)
    d = reg.get(task_id) if reg is not None else None
    if d is None:
        return {"ok": False, "reason": "not found"}
    return {"ok": True, "task": d}


@router.get("/task/{task_id}/trace")
def api_task_trace(task_id: str, request: Request) -> dict[str, Any]:
    """#42 优化③「时间线→Trace 下钻」:任务详情里展开**底层真实动作**(工具调用/事件)。

    把"信我"的叙述变成可检视的证据(与决策卡"已核验区"同一哲学)——Trace 本来就记全了,
    这里只是把 registry 任务 id 翻成 drive trace id、取那条切片、投影成人能读的行。
    """
    reg = getattr(request.app.state, "task_registry", None)
    d = reg.get(task_id) if reg is not None else None
    if d is None:
        return {"ok": False, "reason": "not found", "entries": []}
    trace = getattr(getattr(request.app.state, "main_loop", None), "trace", None)
    if trace is None:
        return {"ok": False, "reason": "未接认知库", "entries": []}
    tid = (d.get("trace_id") or "").strip() or task_id   # l0 任务回填过 drive trace id;没有则试 registry id
    out: list[dict[str, Any]] = []
    try:
        for e in trace.query(tid):
            p = getattr(e, "payload", {}) or {}
            row: dict[str, Any] = {"seq": getattr(e, "seq", 0), "kind": getattr(e, "kind", ""),
                                   "ts": getattr(e, "ts", 0.0)}
            if e.kind == "atom_run":
                calls = p.get("tool_calls") or []
                row["tools"] = [{"name": c.get("name", ""),
                                 "input": str(c.get("input", ""))[:400]} for c in calls[:40]]
                row["success"] = bool(p.get("success"))
                row["gist"] = str(p.get("output") or "")[:280]
            else:
                # 其余 kind(user_turn/task_run/fast_brain_hit/satisfaction…):给个诚实的摘要行
                row["gist"] = str(p.get("intent") or p.get("text") or p.get("result")
                                  or p.get("summary") or "")[:280]
            out.append(row)
    except Exception as ex:
        return {"ok": False, "reason": f"trace 读取失败:{ex}", "entries": []}
    return {"ok": True, "entries": out, "trace_id": tid}


class LangRequest(BaseModel):
    lang: str = Field(..., pattern="^(en|zh)$")


@router.get("/decisions/recent")
def api_decisions_recent(request: Request, limit: int = 10) -> dict[str, Any]:
    """最近拍板流水(只读回看):拍完从待决列消失,但人能回看自己拍过什么。newest-first。"""
    log = getattr(request.app.state, "decision_log", None)
    if log is None:
        return {"decisions": []}
    limit = max(1, min(int(limit or 10), 50))
    return {"decisions": log.recent(limit)}


@router.get("/decisions/audit")
def api_decisions_audit(request: Request, since: float = 0.0, until: float = 0.0,
                        decision: str = "", limit: int = 1000) -> dict[str, Any]:
    """**决策审计/合规查询**:按时间窗 + 决定类型查可审计的决策流水(newest-first)。

    外部用决策历史做审计/合规走这个端点(区别于 /decisions/recent 的 UI 回看窗):
    - `since` / `until`:Unix 时间戳过滤(0=不限);`decision`:ACCEPT|REJECT|DEFER|REVOKE(空=全部);
    - `limit`:返回上限(≤5000,= 留存上限)。每条含 ts/decision/summary/reason/kind/domain/role/proposal_id。
    - `total`:当前留存条数(审计完整性指示)。落盘于 ~/.karvyloop/decision_log.json。
    """
    log = getattr(request.app.state, "decision_log", None)
    if log is None or not hasattr(log, "query"):
        return {"ok": False, "reason": "未接决策流水", "decisions": [], "total": 0}
    rows = log.query(since=(since or None), until=(until or None),
                     decision=decision, limit=max(1, min(int(limit or 1000), 5000)))
    return {"ok": True, "decisions": rows, "returned": len(rows),
            "total": log.count(), "filters": {"since": since, "until": until, "decision": decision}}


@router.get("/proposals/pending")
async def api_proposals_pending(request: Request) -> dict[str, Any]:
    """开机拉取待决提案 —— 让"待你拍的板"跨刷新/切语言存活。

    决策 loop 红线:待决提案只靠 WS 实时推、没有开机拉取 → 一刷新就消失,人被迫问"怎么样了"。
    DEFER 的提案仍挂在 registry(D5),靠本接口下次进来再呈现;ACCEPT/REJECT 已 remove,不返。
    """
    registry = getattr(request.app.state, "proposal_registry", None)
    if registry is None:
        return {"proposals": []}
    # 原住民引荐(docs/60 空屋子解法):开机拉取是前端必经的第一口 → 在此做一次幂等检查
    # (角色库空 + 从没引荐过才出卡;REJECT 后由状态文件保证永不纠缠;卡直接出现在本次
    # 响应里,不吃 WS 时序、不用等 boot_poll 延迟)。fail-soft:引荐失败绝不影响待决列表。
    try:
        from karvyloop.karvy.residents import residents_referral_tick
        await residents_referral_tick(request.app)
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(f"[residents] 引荐检查失败(待决列表照常返回): {e}")
    out: list[dict[str, Any]] = []
    for p in registry.pending():
        try:
            out.append(p.to_dict())
        except Exception:
            pass
    return {"proposals": out}


@router.get("/residents")
def api_residents(request: Request) -> dict[str, Any]:
    """全部随包原住民 + 是否已入住(Hardy 2026-07-09:引荐卡一生只出一次 → 之后加的原住民
    再没门可进、也没处浏览。这是**随时可浏览/请进来**的常驻门,补掉那个发现性黑洞)。"""
    from karvyloop.i18n import get_locale
    from karvyloop.karvy.residents import list_residents, resident_display_name
    loc = get_locale()

    def _pick(d: dict) -> str:  # 双语字段取当前 locale > en > 任意非空(与 residents._loc 同语义)
        return str((d or {}).get(loc) or (d or {}).get("en")
                   or next((v for v in (d or {}).values() if v), "")).strip()

    st = request.app.state
    role_reg = getattr(st, "role_registry", None)
    out: list[dict[str, Any]] = []
    for res in list_residents(getattr(st, "residents_dir", None)):
        rid = res["id"]
        out.append({
            "id": rid,
            "name": resident_display_name(res, loc),     # emoji + 花名
            "pitch": _pick(res.get("pitch") or {}),
            "first_task": _pick(res.get("first_task") or {}),
            "instantiated": role_reg is not None and role_reg.get(rid) is not None,
        })
    return {"residents": out}


class ResidentInviteRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)


@router.post("/residents/invite")
def api_residents_invite(req: ResidentInviteRequest, request: Request) -> dict[str, Any]:
    """把一个原住民请进来(实例化成角色)。在线注册表直接生效,**不用重启**;幂等(已入住复用)。

    与空屋子引荐卡同一条 instantiate_resident(契约 seed + VERIFY/MEMORY 种子 + fs 白名单),
    只是入口从"一生一次的卡"变成"随时可点的门"。
    """
    from karvyloop.karvy.residents import instantiate_resident, load_resident
    st = request.app.state
    role_reg = getattr(st, "role_registry", None)
    if role_reg is None:
        return {"ok": False, "reason": "角色注册表不可用"}
    res = load_resident(req.id, getattr(st, "residents_dir", None))
    if res is None:
        return {"ok": False, "reason": f"原住民镜像不在(打包丢了?):{req.id}"}
    try:
        out = instantiate_resident(res, role_registry=role_reg,
                                   fs_grants=getattr(st, "fs_grants", None),
                                   home=getattr(st, "residents_home", None))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"入住失败:{type(e).__name__}: {e}"}
    return {"ok": True, **out}


@router.get("/setup_status")
def api_setup_status(request: Request) -> dict[str, Any]:
    """无 Key 强制引导:进系统后判断有没有可用模型(网页 + TUI 一致)。

    must_setup=True → 前端弹**强制**录入模型(不可关,直到配好);没 Key 用不了。
    覆盖:首次安装从没配 + Key 后续被删/env 没设。用户显式 --no-llm 不强制。
    """
    from karvyloop.gateway.readiness import setup_status
    return setup_status(request.app)


@router.get("/health")
def api_health(request: Request, online: bool = True) -> dict[str, Any]:
    """系统健康摘要(doctor 环的 REST 面)—— overall + 逐条 finding(level/code/params/fixable)。

    区别于 /healthz(简单存活探针,app.py:645):这条给控制台「系统健康」卡用,前端自己走 i18n 渲染。
    online 默认 True(带活性探测:模型端点/本地服务/磁盘可写/沙箱);?online=false 跳过网络探测快速返回。
    诚实边界:health_summary 永不抛、不含 key(活性探测只连通性,不读/打印凭证);config_path 取自 app.state。
    """
    from karvyloop.cli.doctor_cmd import health_summary
    cfg_path = getattr(request.app.state, "config_path", "") or None
    return health_summary(online=online, config_path=cfg_path)


@router.get("/lang")
def api_lang_get() -> dict[str, Any]:
    """当前生效语言。"""
    from karvyloop.i18n import get_locale
    return {"lang": get_locale()}


@router.post("/lang")
def api_lang_set(req: LangRequest, request: Request) -> dict[str, Any]:
    """设置语言并**持久到 config.yaml**(GUI 切换器调)→ 下次启动自动生效,不必每次 --lang。

    同时 set_locale(本进程立即生效)。config_path 来自 app.state(console 启动时存)。
    """
    from karvyloop.config_lang import write_lang
    from karvyloop.i18n import set_locale
    set_locale(req.lang)
    cfg_path = getattr(request.app.state, "config_path", "") or None
    persisted = write_lang(req.lang, cfg_path)
    return {"ok": True, "lang": req.lang, "persisted": persisted}


# ---- /api/decision_card(决策卡:翻译提案 + 记录拍板)----

@router.get("/decision_card")
def api_decision_card(request: Request, proposal_id: str = "") -> dict[str, Any]:
    """把一条待决提案翻成决策卡(接地于验证门,无则老实 unverifiable)。决策 loop 界面。"""
    if not proposal_id:
        return {"ok": False, "reason": "缺 proposal_id"}
    from karvyloop.console.decision_card_wire import build_card_for_proposal
    card = build_card_for_proposal(request.app, proposal_id)
    if card is None:
        return {"ok": False, "reason": "提案不存在或未接 registry"}
    return {"ok": True, "card": card}


class DecisionCardJudgeRequest(BaseModel):
    proposal_id: str = Field(..., min_length=1, max_length=128)
    decision: str = Field(..., max_length=16)        # ACCEPT|REJECT|DEFER
    engaged: bool = False                            # 是否改/删过判定依据(=真判断,非 rubber-stamp)
    edited_criteria: list[dict] = Field(default_factory=list)
    basis: str = Field(default="", max_length=2000)  # 你在卡上陈述的判断依据(尤救 unverifiable 卡;STATE 显式信号)


@router.post("/decision_card/judge")
def api_decision_card_judge(req: DecisionCardJudgeRequest, request: Request) -> dict[str, Any]:
    """记录对决策卡的判断:反投降计数 + engaged 回喂结晶。返回 needs_recheck(防认知投降)。"""
    from karvyloop.console.decision_card_wire import judge_card
    return judge_card(request.app, proposal_id=req.proposal_id, decision=req.decision,
                      engaged=req.engaged, edited_criteria=list(req.edited_criteria),
                      basis=req.basis)


class DecisionCardAskRequest(BaseModel):
    proposal_id: str = Field(..., min_length=1, max_length=128)
    question: str = Field(..., min_length=1, max_length=1000)
    transcript: list[dict] = Field(default_factory=list)   # [{who,text}] 此前追问(客户端维护)


@router.post("/decision_card/ask")
async def api_decision_card_ask(req: DecisionCardAskRequest, request: Request) -> dict[str, Any]:
    """就一张决策卡追问(docs/77 可追问决策卡):答案锚卡证据、中立不推 ACCEPT、不碰拍板(问责单点)。"""
    from karvyloop.console.decision_card_wire import decision_card_ask
    return await decision_card_ask(request.app, proposal_id=req.proposal_id,
                                   question=req.question, transcript=list(req.transcript))


# ---- /api/propose (9.0d:IntentAnalyst boot 触发 + 推 h2a_proposal) ----

@router.post("/propose")
async def api_propose(request: Request) -> dict[str, Any]:
    """触发 IntentAnalyst boot 一次 → 有 Proposal 就推 WS clients(K5:只建议不决策)。

    契约:
    - proposal_pump=None(未接 analyst)→ 200 + {"proposal": null, "reason": "..."}
    - analyst 沉默(强度不够)→ 200 + {"proposal": null, "sent": 0}
    - analyst 出 Proposal → 200 + {"proposal": <dict>, "sent": N}

    K5:本端点**只推建议** — 用户拍板仍走 /api/h2a_decide → decision_to_envelope 工厂。
    """
    from karvyloop.console.proposals import proactive_from_state

    pump = getattr(request.app.state, "proposal_pump", None)
    proposal = None
    sent = 0
    if pump is not None:
        proposal, sent = await pump.boot()
    # loop-step2b:pump 未接 / 沉默 → 用确定性的状态观察兜底(任务看板:失败任务 → 提议重试)
    if proposal is None:
        proposal, sent = await proactive_from_state(request.app)
    if proposal is None:
        return {"proposal": None, "sent": 0}
    return {"proposal": proposal.to_dict(), "sent": sent}
