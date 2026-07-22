"""routes_system — 系统/审计/杂项只读端点 + 任务看板 + 语言(P2-② 纯搬移)。

覆盖:/api/tasks、/task/{id}、/task/{id}/trace、/decisions/recent、/decisions/audit、
/proposals/pending、/setup_status、/health、/lang(读/写)、/decision_card(读/judge)、/propose。
自带 APIRouter,由 app.py include_router;符号在 routes.py re-export 保既有 import 可达。

从 routes.py 逐字搬移,零逻辑改动。均只读/写 request.app.state,不依赖 routes.py 核心 helper。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

import logging

logger = logging.getLogger(__name__)

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
        from karvyloop.console.proposals import OVERFLOW_DRAWER_N
        return {"proposals": [], "drawer_n": OVERFLOW_DRAWER_N}   # docs/92 刀2:阈值口径一致
    # 原住民引荐(docs/60 空屋子解法):开机拉取是前端必经的第一口 → 在此做一次幂等检查
    # (角色库空 + 从没引荐过才出卡;REJECT 后由状态文件保证永不纠缠;卡直接出现在本次
    # 响应里,不吃 WS 时序、不用等 boot_poll 延迟)。fail-soft:引荐失败绝不影响待决列表。
    try:
        from karvyloop.karvy.residents import residents_referral_tick
        await residents_referral_tick(request.app)
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(f"[residents] 引荐检查失败(待决列表照常返回): {e}")
    # B-5 #7(docs/81):DEFER 老化重浮的必经口在此 —— 首次熬过 48h 的卡落 defer_aged_out
    # 埋点(registry 打标保幂等;fail-soft,待决列表照常返回)。
    try:
        from karvyloop.console.proposals import trace_aged_defers
        trace_aged_defers(request.app)
    except Exception:
        pass
    out: list[dict[str, Any]] = []
    from karvyloop.console.proposals import OVERFLOW_DRAWER_N, proposal_wire_payload
    for p in registry.pending():
        try:
            # docs/92 刀1:与 WS 推送同一出口口径(chain_intent/high_risk 派生字段)
            out.append(proposal_wire_payload(registry, p))
        except Exception:
            pass
    # docs/92 刀2:积压抽屉阈值 N(后端唯一配置源)随开机拉取带给前端 —— boot 配置,
    # 不做每卡字段、不做 UI 设置项;老前端/移动页/接入页不认识此键 = 自然忽略(纯增量)。
    return {"proposals": out, "drawer_n": OVERFLOW_DRAWER_N}


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
async def api_setup_status(request: Request, live: bool = False) -> dict[str, Any]:
    """无 Key 强制引导:进系统后判断有没有可用模型(网页 + TUI 一致)。

    must_setup=True → 前端弹**强制**录入模型(不可关,直到配好);没 Key 用不了。
    覆盖:首次安装从没配 + Key 后续被删/env 没设。用户显式 --no-llm 不强制。

    ?live=1(CFG-05 内测):配置级就绪(key 在)≠ 真能用 —— 此前重启后 key 被手改坏,
    gate 直接放行进主界面。live=1 时对默认 chat 模型再做一次**与首配"保存并验证"同一套**
    的最小真调用(validate_default_model,不造第二套),结果放 live_* 字段:前端据
    error_class 区分 key 坏/地址错(回 setup gate)vs 网络不通(给"离线继续"出口,
    别把离线用户锁死在门外)。默认 live=0 零成本(不发真请求),既有调用方不受影响。
    """
    from karvyloop.gateway.readiness import setup_status
    out = setup_status(request.app)
    out["live_checked"] = False
    # 配置级没就绪(本来就要强制引导)或显式 --no-llm(用户主动选只读)→ 不发真请求
    if live and out.get("ready") and not out.get("no_llm_mode"):
        from karvyloop.console.routes_models import validate_default_model
        v = await validate_default_model(request.app)
        out["live_checked"] = True
        out["live_ok"] = bool(v.get("ok"))
        out["live_model"] = str(v.get("model", "") or "")
        if not v.get("ok"):
            out["live_reason"] = str(v.get("reason", "") or "")        # 已脱敏(不泄 key)
            out["live_error_class"] = str(v.get("error_class", "") or "")
    return out


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


# ---- /api/decision/{pid}/lifeline(决策的生命线,docs/85 Part B)----

# Trace kind → 时间线站位(与前端 _DLIFE_STATIONS 对齐;缺站前端显诚实空位)
_DLIFE_KIND_TO_TYPE = {
    "decision_point": "born",           # T1 💡诞生(登记/广播咽喉)
    "decision_judged": "judged",        # T2 ✍️你的判断(judge_card 埋;card_seen 时兼喂 aligned 站)
    "decision_made": "decided",         # T3 ⚖你拍板
    "decision_dispatched": "dispatched",  # T4 🚚兑现
    "silenced_decision": "dispatched",  # 静音自动兑现(auto 标出;拍板站诚实留空)
}

# ♻ 回流站(docs/85 三刀):偏好校准事件记在共用 task 桶(PREF_TRACE_TASK),payload 只有
# content[:80]+strength —— **没有 proposal 键**(结晶按批攒样本一起喂,且与 distill 路径共用),
# 逐条归因不可诚实推得 → 只做**批次级**:取这次拍板 ts 之后的**第一簇**偏好事件
# (同一次结晶 run 的事件间隔为秒级;簇间隔 > _LEARNED_CLUSTER_GAP_S = 另一批,不算)。
_LEARNED_KINDS = {"decision_pref_reinforced": "reinforced",
                  "decision_pref_weakened": "weakened",
                  "pref_auto_revoked": "revoked"}
_LEARNED_CLUSTER_GAP_S = 600.0   # 首簇聚类间隔(一次结晶 run 内事件相邻为秒级,600s 很宽裕)
_LEARNED_CAP = 8                 # 回流站最多摆几条(其余只报总数)


def _learned_events(trace: Any, decided_ts: float) -> list[dict[str, Any]]:
    """这次拍板之后的第一批偏好结晶事件(批次级归因,绝不编逐条对应)。

    诚实边界:事件本身(何时/哪条偏好/强度变化)是 Trace 事实;「与这次拍板的关联」只是
    时间就近 + 结晶按批的机制推论 —— 行内 attribution="batch" 标出,前端必须带免责句。
    无拍板锚(decided_ts<=0)→ 不聚合(返回 [])。"""
    if trace is None or decided_ts <= 0:
        return []
    from karvyloop.console.decision_wire import PREF_TRACE_TASK
    rows: list[tuple[float, str, dict, str]] = []
    try:
        for kind, label in _LEARNED_KINDS.items():
            for e in trace.query(PREF_TRACE_TASK, kind=kind):
                if e.ts >= decided_ts:
                    rows.append((e.ts, label, getattr(e, "payload", {}) or {},
                                 f"{e.task_id}:{getattr(e, 'seq', 0)}"))
    except Exception:
        return []
    if not rows:
        return []
    rows.sort(key=lambda r: r[0])
    burst: list[tuple[float, str, dict, str]] = []
    last_ts = None
    for r in rows:                      # 首簇:从最早一条起,间隔 ≤ gap 连成一批
        if last_ts is not None and (r[0] - last_ts) > _LEARNED_CLUSTER_GAP_S:
            break
        burst.append(r)
        last_ts = r[0]
    out: list[dict[str, Any]] = []
    for ts, label, p, tref in burst[:_LEARNED_CAP]:
        row: dict[str, Any] = {"ts": ts, "type": "learned", "pref_event": label,
                               "detail": str(p.get("content", "") or ""),
                               "attribution": "batch", "trace_ref": tref}
        for k in ("strength_before", "strength_after"):
            if isinstance(p.get(k), (int, float)):
                row[k] = p[k]
        out.append(row)
    if out:
        out[0]["learned_total"] = len(burst)   # 首簇总条数(超 cap 时前端报「共 N 条」)
    return out


@router.get("/decision/{proposal_id}/lifeline")
def api_decision_lifeline(proposal_id: str, request: Request) -> dict[str, Any]:
    """一次决策的生命线(docs/85):与 /api/skill_lifecycle 同心智同数据纪律 ——
    **K4 只读,全部从 Trace 聚合**(decision_log 兜底拍板存根 + 任务态 + query_run 工具步
    投影 + token_ledger.task_total),不在执行路径另算。

    契约(同构 skill_lifecycle,别改形状):
    {"ok", "proposal_id", "stub",
     "events": [{"ts","type","detail","trace_ref", ...extras}],
       # type ∈ born/aligned/judged/decided/dispatched/learned
       # aligned = T2 卡缓存命中时的建卡事实投影;learned = ♻ 批次级回流(attribution="batch",
       # 首簇 + learned_total;逐条归因不可诚实推得,绝不编)
     "steps":  [{"ts","name","gist","input",("ok","err")}],   # 兑现 run 的真实工具步(run_id 投影;
       # ok/err = docs/82 slice C 成败事实,老格式条目键缺省)
     "tokens": int|null, "task": {...}|null}
    缺哪站诚实缺(不编);埋点前的老决策只有 decision_log 存根 → stub=true。
    """
    pid = (proposal_id or "").strip()[:512]
    if not pid:
        return {"ok": False, "reason": "缺 proposal_id", "events": [], "steps": [],
                "tokens": None, "task": None, "stub": False}
    st = request.app.state
    trace = getattr(getattr(st, "main_loop", None), "trace", None)
    if trace is None:
        trace = getattr(st, "trace", None)   # 无 main_loop 时的备选源(同 weekly tick)

    events: list[dict[str, Any]] = []
    run_id = ""
    if trace is not None:
        try:
            for e in trace.query(pid):
                ev_type = _DLIFE_KIND_TO_TYPE.get(e.kind)
                if ev_type is None:
                    continue
                p = getattr(e, "payload", {}) or {}
                row: dict[str, Any] = {"ts": e.ts, "type": ev_type,
                                       "trace_ref": f"{e.task_id}:{e.seq}"}
                if e.kind == "decision_point":
                    row["detail"] = str(p.get("basis") or p.get("summary") or "")
                    row["summary"] = str(p.get("summary", "") or "")
                    row["strength"] = p.get("strength")
                    row["kind"] = str(p.get("kind", "") or "")
                elif e.kind == "decision_made":
                    row["detail"] = str(p.get("reason", "") or "")
                    row["decision"] = str(p.get("decision", "") or "")
                    row["edited"] = list(p.get("edited") or [])
                elif e.kind == "decision_dispatched":
                    row["detail"] = str(p.get("detail", "") or "")
                    row["ok"] = bool(p.get("ok"))
                    row["verdict"] = str(p.get("verdict", "") or "")
                    if not run_id:
                        run_id = str(p.get("run_id", "") or "")
                elif e.kind == "silenced_decision":
                    row["detail"] = str(p.get("detail", "") or p.get("summary", "") or "")
                    row["ok"] = bool(p.get("ok"))
                    row["auto"] = True   # 静音自动兑现(非你拍板)—— 前端如实标
                else:   # decision_judged(T2,docs/85 二刀)
                    row["detail"] = str(p.get("basis") or p.get("detail") or "")
                    row["engaged"] = bool(p.get("engaged"))
                    row["card_seen"] = bool(p.get("card_seen"))
                    if p.get("edits_n"):
                        row["edits_n"] = int(p.get("edits_n") or 0)
                        row["edited"] = str(p.get("edited", "") or "")
                    # 建卡事实(卡缓存命中才有)→ 兼喂 🧭 aligned 站(缺省=站留诚实空位)
                    if "aligned" in p:
                        row["aligned"] = int(p.get("aligned") or 0)
                        row["violations"] = int(p.get("violations") or 0)
                        events.append({"ts": e.ts, "type": "aligned",
                                       "aligned": int(p.get("aligned") or 0),
                                       "aligned_omitted": int(p.get("aligned_omitted") or 0),
                                       "violations": int(p.get("violations") or 0),
                                       "trace_ref": f"{e.task_id}:{e.seq}"})
                events.append(row)
        except Exception as ex:
            return {"ok": False, "reason": f"trace 读取失败:{ex}", "events": [],
                    "steps": [], "tokens": None, "task": None, "stub": False}
    events.sort(key=lambda r: r["ts"])

    # ⚖拍板兜底:埋点前的老决策 Trace 无痕,但 decision_log 落过流水 → 给"拍板存根"
    stub = False
    if not any(r["type"] == "decided" for r in events):
        try:
            log = getattr(st, "decision_log", None)
            hit = None
            if log is not None and hasattr(log, "query"):
                hit = next((r for r in log.query(limit=5000)
                            if str(r.get("proposal_id") or "") == pid), None)
            if hit is not None:
                stub = not events   # Trace 全空、只有流水 = 埋点前老决策(前端标一句实话)
                events.append({"ts": float(hit.get("ts") or 0.0), "type": "decided",
                               "detail": str(hit.get("reason", "") or ""),
                               "decision": str(hit.get("decision", "") or ""),
                               "summary": str(hit.get("summary", "") or ""),
                               "trace_ref": ""})
                events.sort(key=lambda r: r["ts"])
        except Exception:
            pass

    # ♻ 回流站(三刀):拍板锚之后的第一批偏好结晶(批次级归因;无锚/无事件 → 站留诚实空位)
    try:
        decided_ts = max((r["ts"] for r in events if r["type"] == "decided"), default=0.0)
        learned = _learned_events(trace, float(decided_ts or 0.0))
        if learned:
            events.extend(learned)
            events.sort(key=lambda r: r["ts"])
    except Exception:
        pass   # 回流聚合失败不拖垮整条生命线(其余站照常返回)

    # 任务态(run_task 兑现登记的任务,Task.proposal_id 回链;老任务无此字段 → null)
    task: Optional[dict] = None
    tid = ""
    try:
        reg = getattr(st, "task_registry", None)
        if reg is not None:
            for tk in reg.list():
                if tk.get("proposal_id") == pid:
                    tid = str(tk.get("id") or "")
                    task = {"id": tid, "status": tk.get("status", ""),
                            "who": tk.get("who", ""), "result": tk.get("result", ""),
                            "started": tk.get("started"), "finished": tk.get("finished")}
                    break
    except Exception:
        task = None

    # 🔧执行工具步:T4 记下的 run_id → trace.query_run 投影(用户原话的 "each agent's
    # reasoning steps");无 run_id / 无记录 → 空列表(前端显诚实空位)。
    # 下钻(二刀):每步带 input 全一点的摘要 + ok/error_reason 成败事实(docs/82 slice C
    # 执行器已回填;老格式条目无 ok 字段 → 键缺省诚实,前端不标 ✓/✗)。
    def _step(ts: float, c: dict) -> dict[str, Any]:
        s: dict[str, Any] = {"ts": ts, "name": str(c.get("name", "") or "?"),
                             "gist": str(c.get("input", ""))[:160],
                             "input": str(c.get("input", ""))[:400]}
        if "ok" in c:
            s["ok"] = bool(c.get("ok"))
            if c.get("error_reason"):
                s["err"] = str(c.get("error_reason", ""))[:200]
        return s

    steps: list[dict[str, Any]] = []
    if trace is not None and run_id:
        try:
            for e in trace.query_run(run_id):
                p = getattr(e, "payload", {}) or {}
                if e.kind == "atom_run":
                    for c in (p.get("tool_calls") or [])[:40]:
                        steps.append(_step(e.ts, c))
                elif e.kind == "tool_call":
                    steps.append(_step(e.ts, p))
                if len(steps) >= 40:
                    break
        except Exception:
            steps = []

    # 💰token:per-task 归因账本(route_to_role 记在 proposal_id 名下,run_task 记在任务 id)
    tokens: Optional[int] = None
    try:
        ledger = getattr(st, "token_ledger", None)
        if ledger is not None and hasattr(ledger, "task_total"):
            tokens = int(ledger.task_total(pid)) or (int(ledger.task_total(tid)) if tid else 0)
    except Exception:
        tokens = None

    if not events and task is None and not steps:
        return {"ok": False, "reason": "这条决策没有任何记录", "proposal_id": pid,
                "events": [], "steps": [], "tokens": None, "task": None, "stub": False}
    return {"ok": True, "proposal_id": pid, "stub": stub, "events": events,
            "steps": steps, "tokens": tokens, "task": task}


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


# ---- /api/h2a_decide(P2-② 纯搬移自 routes.py;K5 强校验)----
from karvyloop.karvy.h2a import (  # noqa: E402
    H2A_DEFER as _H2A_DEFER,
    H2A_REJECT as _H2A_REJECT,
    H2ADecision as _H2ADecision,
    decision_to_envelope as _decision_to_envelope,
)
from .serializers import envelope_to_dict as _envelope_to_dict  # noqa: E402

DEFAULT_REJECT_REASON = "(用户未说明)"

class H2ADecideRequest(BaseModel):
    proposal_id: str = Field(..., min_length=1, max_length=512)
    decision: str = Field(..., pattern="^(ACCEPT|REJECT|DEFER)$")
    reason: str = Field(default="", max_length=2000)
    # #42 优化①「改了再批」:就地改过的 payload 字段(白名单覆盖在 registry.decide 做;
    # 只许覆盖已有 str 键)。修改是楔子最富的偏好信号,记录在 record_decision_signals。
    edits: dict = Field(default_factory=dict)
    user_address_domain_id: str = Field(default="dom-1")
    user_address_role: str = Field(default="user")
    user_address_agent_id: str = Field(default="console-user")
    to_address_domain_id: str = Field(default="dom-1")
    to_address_role: str = Field(default="agent")
    to_address_agent_id: str = Field(default="karvy")


@router.post("/h2a_decide")
def api_h2a_decide(req: H2ADecideRequest, request: Request) -> dict[str, Any]:
    """H2A 决策 → 经 `decision_to_envelope` 工厂(K5 唯一路径)+ D5 按 kind 兑现。

    错误码契约:
    - DEFER → 200 + {"envelope": null} (K5:DEFER 不发 envelope;D5:挂起留 registry)
    - 其他 → 200 + envelope dict(`by=[]` 是 K5 不变量)

    REJECT-reason 的取舍(Hardy「不强制 reason」× 协议不变量 A8 的调和):
    - A8(docs/19 §A8)是 A2A **协议级**不变量——`REJECT` envelope 必带非空 `reason`;
      它有专属错误类 `RejectMissingReasonError`,且 docs/22(T1 路由)/docs/23(L0)都
      显式承诺**冻结 A1–A8**。所以**不能**在协议层把它拆了。
    - Hardy 要的是**不强制用户打字**("不想说为什么就能拒"),这是 **UX** 诉求。
    - 调和:UI 边界**永不逼用户填**;REJECT 留空时,这里补一个**诚实占位** reason
      `(用户未说明)`,既不挡用户、又让协议 A8 + 审计链(reject 有据可查)完好。
    - K5(docs/20)= 人拍板 / envelope `by=[]`,由 `decision_to_envelope` 保证,与 reason 无关。

    D5(docs/30):接 `app.state.proposal_registry` —— ACCEPT 凭 proposal_id 查回
    原 Proposal 按 kind 兑现(`dispatch` 字段回显结果);无 registry / 未登记 → 静默兼容。
    K5/PR-4:兑现只在用户已 ACCEPT 后跑;dispatch 不构 Envelope、不替决策。
    """
    from karvyloop.domain import Address
    from datetime import datetime, timezone

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

    # 不逼用户填(Hardy)+ 守协议 A8:REJECT 留空 → 补诚实占位,其余原样。
    eff_reason = req.reason
    if req.decision == _H2A_REJECT and not req.reason.strip():
        eff_reason = DEFAULT_REJECT_REASON

    decision_obj = _H2ADecision(
        decision=req.decision,
        reason=eff_reason,
        proposal_id=req.proposal_id,
        user_address=user_addr,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # §11 决策信号(P3-a 对齐):REST 拍板与 WS 同喂 样本→结晶 / stats / decision_log。
    # 此前只有 WS 接了 —— 走 REST 拍的板从不进偏好结晶回路(决策 loop 白拍)。
    # 必须在 _dispatch()(会把提案移出 registry)之前记,才能取到 summary/kind。
    from karvyloop.console.decision_wire import record_decision_signals
    record_decision_signals(request.app, decision=req.decision, proposal_id=req.proposal_id,
                            reason=eff_reason,
                            domain=req.to_address_domain_id or "",
                            role=req.to_address_role or "",
                            edits=(req.edits or None))

    # D5:按 kind 兑现(若接了 registry)。reason 可选,不拦 REJECT。
    def _dispatch() -> dict[str, Any] | None:
        registry = getattr(request.app.state, "proposal_registry", None)
        if registry is None:
            return None
        handlers = getattr(request.app.state, "proposal_handlers", None) or {}
        # T4(docs/85):与 WS 同走 dispatch_decision 咽喉(run_scope 串工具步 +
        # decision_dispatched 埋点;fail-soft,行为与直接 registry.decide 一字不变)。
        from karvyloop.console.decision_wire import dispatch_decision
        res = dispatch_decision(request.app, proposal_id=req.proposal_id,
                                decision=req.decision, handlers=handlers,
                                edits=(req.edits or None))
        # 委派兑现(route_to_role / run_task 等)会同步 drive → 被委派 role 可能碰壁工作区外
        # 路径(note_denied 攒「想要」)。与顶层 drive 收尾同待遇:这一轮就把「想要」升成 H2A
        # 授权卡,否则委派活的授权卡永远不出(缺口)。sync 端点在 FastAPI 线程池(无运行
        # loop)→ asyncio.run 安全;失败不阻断决策回执。
        import asyncio
        from karvyloop.console.proposals import raise_fs_access_cards
        try:
            asyncio.run(raise_fs_access_cards(request.app))
        except Exception:
            logger.debug("[h2a_decide] 委派收尾升 fs_access 卡失败(不阻断)", exc_info=True)
        return res.to_dict() if res is not None else None

    if req.decision == _H2A_DEFER:
        # K5:DEFER 不发 envelope,返 null;D5:挂起(留 registry,下次再呈现)
        return {"envelope": None, "decision": req.decision, "dispatch": _dispatch()}

    # K5 唯一 Envelope 构造路径(REJECT 的空 reason 已在上面补成占位,A8 不破)
    env = _decision_to_envelope(decision_obj, to_addr)
    from karvyloop.console.proposal_handlers import pop_report_card
    return {
        "envelope": _envelope_to_dict(env),
        "decision": req.decision,
        "dispatch": _dispatch(),  # D5:ACCEPT 兑现结果 / REJECT 丢弃回执(handler 内会 stash 回报卡)
        # 执行后回报卡:兑现跑了独立验收 → 把"它到底验过没"翻成卡(grounded ✓ 的自然产地)
        "report_card": pop_report_card(request.app, req.proposal_id),
    }

