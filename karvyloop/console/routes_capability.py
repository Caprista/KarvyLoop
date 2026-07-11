"""routes_capability — 能力/技能/授权面端点(P2-② routes god-module 拆分,纯搬移)。

覆盖:/api/skills、/skill_lifecycle、/desk/memento、/coding/capability、/capability/overview、
/fs_grants*、/silence/*、/coding/config、/mcp/*、/skill/*、/domain/templates*、/task_cost_estimate、
/domains。自带 APIRouter,由 app.py include_router;符号在 routes.py re-export 保既有 import 可达。

从 routes.py 逐字搬移,零逻辑改动。这些端点只读/写 request.app.state,自带全部本地 helper
(_skill_status / _skill_net_granted / _skill_sources_store),不依赖 routes.py 的核心 helper。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api")


@router.get("/skills")
def api_skills(request: Request) -> dict[str, Any]:
    """列已结晶技能库(L0)——楔子的家。name/触发/描述/用量/是否归档 + SKILL.md 正文(封顶)。"""
    import pathlib as _pl
    from karvyloop.crystallize.crystallize import read_crystallized_ts as _read_cts
    ml = getattr(request.app.state, "main_loop", None)
    idx = getattr(ml, "skill_index", None) if ml is not None else None
    store = getattr(ml, "store", None) if ml is not None else None
    if idx is None:
        return {"skills": [], "no_llm": True}   # --no-llm:无 main_loop
    out = []
    for e in idx.all():
        st = store.get(e.sig) if store is not None else None
        body = ""
        scripts: list[str] = []
        try:
            p = _pl.Path(e.path)
            if p.exists():
                body = p.read_text(encoding="utf-8")[:6000]
                sdir = p.parent / "scripts"
                if sdir.is_dir():
                    scripts = sorted(f"scripts/{q.name}" for q in sdir.glob("*")
                                     if q.is_file())[:50]
        except Exception:
            pass
        out.append({
            "name": e.name, "sig": e.sig, "scope": getattr(e, "scope", ""),
            # 来源:system(包内 bundled 只读模板,reset 动不到)vs user(用户结晶/手写,清数据时清)
            "source": getattr(e, "source", "user"),
            "when_to_use": getattr(e, "when_to_use", ""),
            "description": getattr(e, "description", ""),
            # #3b:语义标签(skill_tags_tick 打的,可能是 "en|zh" 双语编码 / 旧英文串)——面板筛选 + 双语显示
            "tags": list(getattr(e, "tags", ()) or ()),
            "usage_count": getattr(st, "usage_count", 0) if st else 0,
            "success_count": getattr(st, "success_count", 0) if st else 0,
            "recall_count": getattr(st, "recall_count", 0) if st else 0,
            "archived": store.is_archived(e.sig) if store is not None else False,
            # 第三方导入的技能:标来源 + 是否带脚本(执行需沙箱)——让 Hardy 一眼分辨自家 vs 外来
            "third_party": "source: third-party" in body,
            "untrusted": "trust: untrusted" in body,
            "scripts": scripts,   # 携带的脚本(可在沙箱里试跑;P0-c)
            "net_granted": _skill_net_granted(request.app, e.name),  # 用户是否已授网(P1)
            "status": _skill_status(body),   # 待沉淀 / 待验证 / 已沉淀(btw-1)
            # P1.5 缺口③:结晶时刻(frontmatter `crystallized_ts:`;老技能无标 → null,加性不伪造)
            "crystallized_ts": _read_cts(body),
            "body": body,
        })
    out.sort(key=lambda s: (s["archived"], -s["recall_count"], -s["usage_count"]))
    return {"skills": out}


@router.get("/skill_lifecycle")
def api_skill_lifecycle(request: Request) -> dict[str, Any]:
    """每技能事件时间线(给前端时间线视图供数;K4 只读,全部从 Trace 聚合)。

    契约(别改形状):{"skills": [{"name", "sig", "events": [{"ts", "type", "detail",
    "trace_ref"}]}]}。type ∈ crystallized(kind=crystallize)/ revised(kind=skill_revision,
    29112e9)/ rerun(eval_fact 带 skill_rerun 标)。improved:improve.py 写回目前不留
    Trace 痕 → 数据不可得,该 type 诚实不出现(不编)。无 main_loop / 无 Trace → 空表。
    """
    ml = getattr(request.app.state, "main_loop", None)
    trace = getattr(ml, "trace", None) if ml is not None else None
    if trace is None:
        return {"skills": []}
    from karvyloop.crystallize import EVAL_FACT_KIND, REVISION_KIND
    by_sig: dict[str, dict[str, Any]] = {}

    def _slot(sig: str, name: str) -> dict[str, Any]:
        s = by_sig.setdefault(sig, {"name": "", "sig": sig, "events": []})
        if name and not s["name"]:
            s["name"] = name
        return s

    try:
        task_ids = trace.all_tasks()
    except Exception:
        return {"skills": []}
    for tid in task_ids:
        for e in trace.query(tid):
            p = e.payload or {}
            sig = str(p.get("sig", "") or "")
            if not sig:
                continue   # 没 sig 归不了属,跳过(不猜)
            if e.kind == "crystallize":
                ev_type, name = "crystallized", str(p.get("name", "") or "")
                detail = str(p.get("when_to_use", "") or "")
                trace_ref = str(p.get("trace_ref", "") or "") or f"{e.task_id}:{e.seq}"
            elif e.kind == REVISION_KIND:
                ev_type, name = "revised", str(p.get("skill_name", "") or "")
                note = str(p.get("note", "") or "")
                mode = str(p.get("mode", "") or "")
                detail = f"{mode}: {note}" if note else mode
                trace_ref = f"{e.task_id}:{e.seq}"   # skill_revision 本身就是审计事件
            elif e.kind == EVAL_FACT_KIND and p.get("skill_rerun"):
                ev_type, name = "rerun", str(p.get("skill_name", "") or "")
                detail = "success" if p.get("success") else "failure"
                trace_ref = str(p.get("trace_ref", "") or "") or f"{e.task_id}:{e.seq}"
            else:
                continue
            _slot(sig, name)["events"].append({
                "ts": e.ts, "type": ev_type, "detail": detail, "trace_ref": trace_ref,
            })
    skills = list(by_sig.values())
    for s in skills:
        s["events"].sort(key=lambda ev: ev["ts"])
    # 最近有动静的技能在前(时间线视图默认关注活跃技能)
    skills.sort(key=lambda s: -(s["events"][-1]["ts"] if s["events"] else 0.0))
    return {"skills": skills}


@router.get("/skills/curve")
def api_skills_curve(request: Request, sig: str = "") -> dict[str, Any]:
    """结晶裸分曲线(docs/57 P1 护城河可感知):每技能 day 粒度分数时间序列
    (usage_score / success_rate / promote_progress)+ 全库成长曲线(技能数/晋级数/
    平均成功率/复用命中率随时间)——"越用越像你"的可见增长线。

    契约(别改形状):{"bucket": "day", "promote_score", "min_success_rate",
    "skills": [{"sig", "name", "crystallized_ts", "points": [{"day", "ts",
    "usage_count", "success_count", "usage_score", "success_rate",
    "promote_progress", "reruns", "crystallized"}]}], "growth": {"points": [{"day",
    "ts", "skills_total", "promotions", "revisions", "runs_total",
    "avg_success_rate", "hit_rate"}]}}。

    K4 只读:全部从 Trace 回放推导(eval_fact / crystallize / skill_revision),
    不改记账、不在执行热路径另算(铁律:Trace 是所有评价的唯一数据源)。
    ``?sig=`` 只取一个技能的序列(growth 仍全库)。无 main_loop / 无 Trace → 优雅空。
    """
    from karvyloop.crystallize.crystallize import MIN_SUCCESS_RATE, PROMOTE_SCORE
    from karvyloop.crystallize.curve import build_curves
    ml = getattr(request.app.state, "main_loop", None)
    trace = getattr(ml, "trace", None) if ml is not None else None
    if trace is None:
        return build_curves(None)
    th = getattr(ml, "thresholds", None)
    idx = getattr(ml, "skill_index", None)
    resolver = (lambda s: idx.name_for_sig(s) or "") if idx is not None else None
    from karvyloop.crystallize.store import USAGE_DEBOUNCE_SEC
    return build_curves(
        trace, sig=(sig or "")[:128],
        promote_score=float(getattr(th, "promote_score", PROMOTE_SCORE)),
        min_success_rate=float(getattr(th, "min_success_rate", MIN_SUCCESS_RATE)),
        debounce_sec=float(getattr(th, "usage_debounce_sec", USAGE_DEBOUNCE_SEC)),
        name_resolver=resolver)


@router.get("/desk/memento")
def api_desk_memento(request: Request) -> dict[str, Any]:
    """周五纪念物(P1.5 灵魂缺口③,轻读口)。契约形状冻结:
    {"week_label","tasks_done","skills_new","decisions","tokens_total"}。

    有 digest 水位(周报卡还挂着)→ 直接读现成结构化 digest,不重算;
    读不到 → build_weekly_digest 确定性重建一次(零 LLM,纯 Trace/账本投影)。纯只读。
    """
    from karvyloop.cognition.weekly_digest import (
        build_weekly_digest, load_memento, memento_from_digest,
    )
    app = request.app
    reg = getattr(app.state, "proposal_registry", None)
    m = load_memento(registry=reg)
    if m is not None:
        return {**m, "source": "digest"}
    ml = getattr(app.state, "main_loop", None)
    trace = getattr(ml, "trace", None) if ml is not None else None
    if trace is None:
        trace = getattr(app.state, "trace", None)   # 无 main_loop 时的备选源(同 weekly tick)
    import time as _time
    digest = build_weekly_digest(
        trace, getattr(app.state, "token_ledger", None),
        getattr(app.state, "taste_predictions", None), reg, _time.time(),
        decision_log=getattr(app.state, "decision_log", None))
    return {**memento_from_digest(digest), "source": "computed"}


@router.get("/coding/capability")
def api_coding_capability(request: Request) -> dict[str, Any]:
    """内建「Coding」技能(#1 v1.0):把编码能力当一个**可在技能库里看见**的技能露出。

    不是装饰卡——`tools` 直接反映**真实**装上的工具(内建 read/write/edit/run + web,
    加运行时注入的 MCP 工具),`executor` 是当前执行器(forge,内建沙箱)。
    pluggable 执行器(外接 Claude Code CLI 等)是后续聚焦项:此处如实标 configurable,
    不假装已通——诚实优先(不当 yes-man)。
    """
    from karvyloop.coding.tools.read import ReadTool
    from karvyloop.coding.tools.write import WriteTool
    from karvyloop.coding.tools.edit import EditTool
    from karvyloop.coding.tools.bash import BashTool
    from karvyloop.coding.tools.web import WebFetchTool, WebSearchTool

    # 类级 name/description——无需实例化(实例化要 sandbox/token,这里只列能力)
    builtin = [ReadTool, WriteTool, EditTool, BashTool, WebSearchTool, WebFetchTool]
    tools = [{"name": c.name, "description": (getattr(c, "description", "") or "").strip(),
              "kind": "builtin"} for c in builtin]
    # 运行时注入的 MCP 工具(启动时接入;真实露出,不命中知识库时也能搜/调外部能力)。
    # connect_mcp_agent_tools 返回 {name: McpAgentTool} 字典 —— 取 .values() 拿工具对象,
    # 不是 keys(否则只剩名字串、描述空)。兼容 list 形态。
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    _mcp = rk.get("mcp_tools") or []
    _mcp_objs = list(_mcp.values()) if isinstance(_mcp, dict) else list(_mcp)
    for mt in _mcp_objs:
        tools.append({"name": getattr(mt, "name", "mcp_tool"),
                      "description": (getattr(mt, "description", "") or "").strip(),
                      "kind": "mcp"})
    # 外接编码工具(#3,可编辑):如实反映保存的偏好。诚实——v1.0 不接入执行(external_active=False),
    # 所以**实际跑的永远是 Forge**(executor/sandboxed 恒 forge/True),不假装外接已生效。
    from karvyloop.coding.coding_config import get_coding_config_public
    cc = get_coding_config_public()
    return {
        "name": "coding",
        "executor": "forge",             # 当前实际执行器 = 内建 Forge(外接执行接入待后续)
        "sandboxed": True,               # Forge 走沙箱 + 能力令牌
        "external_executor": cc["external_executor"],   # 用户保存的外接 coder 命令(可编辑);None=没配
        "external_active": cc["external_active"],        # False:已存未接入(诚实)
        "tools": tools,
    }


@router.get("/capability/overview")
def api_capability_overview(request: Request) -> dict[str, Any]:
    """能力合一清单(P3-d):**一张表**审计全系统能力面。

    此前两套能力系统各说各话 —— 工具走 capability 决策链(模式下限/规则),技能走 grants
    (信任级/联网授权/完整性锁),"谁能干什么"要拼两处才知道。本端点合一:
    - tools:真实装上的工具(内建+MCP,复用 /coding/capability)+ 每个的**模式下限**
      (required_mode;不在表里=FULL 最严,HR-1 fail-closed)。
    - skills:每个技能的信任级(自家 trusted / 第三方 untrusted)+ 是否带脚本 + 联网授权
      + **完整性锁状态**(untrusted 才有:ok/unlocked/mismatch)。
    """
    from pathlib import Path as _P
    from karvyloop.capability.policy import required_mode
    cap = api_coding_capability(request)
    tools = [{**t_, "required_mode": required_mode(t_.get("name", "")).name.lower()}
             for t_ in cap.get("tools", [])]
    skills_out: list[dict[str, Any]] = []
    sk = api_skills(request)
    skills_dir = _P.home() / ".karvyloop" / "skills"
    for s in sk.get("skills", []):
        entry = {
            "name": s["name"], "source": s.get("source", "user"),
            "trust": "untrusted" if s.get("untrusted") else "trusted",
            "net_granted": bool(s.get("net_granted")),
            "has_scripts": bool(s.get("scripts")),
            "status": s.get("status", ""),
            "lock": "",   # 只有 untrusted 有锁语义
        }
        if s.get("untrusted"):
            try:
                from karvyloop.registry.skill_lock import verify_lock
                st, _detail = verify_lock(skills_dir, s["name"])
                entry["lock"] = st
            except Exception:
                entry["lock"] = "unknown"
        skills_out.append(entry)
    st = getattr(request.app.state, "fs_grants", None)
    # 挣来的静音:已授权静音处理的类别(桶)—— 能力总览可见可撤(docs/49 机制2)。
    # 只列活跃(未吊销未过期)的授权;读不到台账/无 LLM → 空(不猜)。
    silence_grants: list[dict[str, Any]] = []
    try:
        from karvyloop.karvy.silence import get_store
        active = get_store(request.app).active_grants()
        for bucket, g in active.items():
            silence_grants.append({
                "bucket": bucket, "kind": g.get("kind", ""),
                "domain": g.get("domain", ""),
                "granted_at": g.get("granted_at", 0.0),
                "expires_at": g.get("expires_at", 0.0),
            })
    except Exception:
        silence_grants = []
    return {"tools": tools, "skills": skills_out,
            "fs_grants": st.list() if st is not None else [],
            "silence_grants": silence_grants,
            "executor": cap.get("executor", ""), "sandboxed": cap.get("sandboxed", False),
            "no_llm": bool(sk.get("no_llm"))}


@router.get("/capability/unlocks")
def api_capability_unlocks(request: Request) -> dict[str, Any]:
    """「能力解锁」清单(Hardy 2026-07-04:不配置就降级的功能,给用户引导和选择)。

    确定性探测(零 LLM):每项可选能力当前 已就绪(on)/ 未配置(off)/ 缺依赖
    (missing_dep)+ 安装命令 + 非机密事实。价值文案 / 怎么做 / 生态链接全在前端
    i18n(en+zh);**绝不读、绝不回显任何密钥值**(detail 只有个数/包名级事实)。
    """
    from karvyloop.console.unlocks import list_unlocks
    cfgp = getattr(request.app.state, "config_path", "") or ""
    return {"unlocks": list_unlocks(cfgp)}


class CapabilityEnableRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=32)


@router.post("/capability/enable")
def api_capability_enable(req: CapabilityEnableRequest, request: Request) -> dict[str, Any]:
    """一键启用某可选能力(Hardy 2026-07-09:app 替用户装,不用敲命令、不让人自己找门)。

    后台 `pip install <底层包>` → 装完可选件懒加载即生效(**不重启** console)。安全同一键升级:
    CSRF 头(X-Karvyloop-Upgrade)+ 本机/私网来源门 —— 装东西是控自己机器的事,挡公网/恶意跨源。
    只装 INSTALLABLE 白名单里的固定包(id 之外无任何用户输入进 pip),无任意包注入面。
    """
    from karvyloop.console.capability_install import start_install
    from karvyloop.console.routes_ops import _is_trusted_upgrade_origin
    if (request.headers.get("x-karvyloop-upgrade") or "") != "1":
        return {"ok": False, "reason": "缺启用标记(防 CSRF);请从控制台界面点启用"}
    client = (request.client.host if request.client else "") or ""
    if not _is_trusted_upgrade_origin(client):
        return {"ok": False, "reason": f"启用只能从本机或同局域网触发(你的来源 {client} 不在可信网内)"}
    return start_install(req.id)


@router.get("/capability/enable_status")
def api_capability_enable_status(request: Request, id: str = "") -> dict[str, Any]:
    """轮询某能力的一键启用进度/结果(state ∈ running|done|failed;无 → 空)。只读,不触发安装。"""
    from karvyloop.console.capability_install import read_status
    st = read_status(id)
    return st if st else {"state": "", "id": id}


class FsGrantRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024)
    ops: list = Field(default_factory=lambda: ["read"])


class FsGrantRevokeRequest(BaseModel):
    grant_id: str = Field(..., min_length=1, max_length=64)


@router.get("/domain/templates")
def api_domain_templates(request: Request) -> dict[str, Any]:
    """开箱域模板清单(「一键开公司」;docs/42 优化④,Lindy 验证的冷启动)。"""
    from karvyloop.domain.templates import list_templates
    return {"templates": list_templates()}


class DomainTemplateRequest(BaseModel):
    template_id: str = Field(..., min_length=1, max_length=64)


@router.post("/domain/templates/instantiate")
def api_domain_template_instantiate(req: DomainTemplateRequest, request: Request) -> dict[str, Any]:
    """一键开公司:建角色(带尽责下属契约 seed)+ 建域(value.md+deontic+成员)+ 持久化。"""
    from karvyloop.domain.templates import instantiate_template
    app = request.app
    return instantiate_template(
        req.template_id,
        domain_registry=getattr(app.state, "domain_registry", None),
        role_registry=getattr(app.state, "role_registry", None),
        domain_store=getattr(app.state, "domain_store", None))


@router.get("/task_cost_estimate")
def api_task_cost_estimate(request: Request) -> dict[str, Any]:
    """#42 打计费黑箱:"花钱之前告诉你" —— 最近 n 个有归因任务的 token 消耗分布。

    诚实边界:只统计接了 per-task 归因之后的任务(老账不猜);样本 <3 前端不显示数字。"""
    led = getattr(request.app.state, "token_ledger", None)
    if led is None:
        return {"n": 0, "mean": 0, "min": 0, "max": 0}
    try:
        return led.estimate_task_cost(n=10)
    except Exception:
        return {"n": 0, "mean": 0, "min": 0, "max": 0}


@router.get("/fs_grants")
def api_fs_grants(request: Request) -> dict[str, Any]:
    """授权台账:所有工作区外路径授权(能力总览的数据源;敏感地板清单一并给,UI 可解释)。"""
    from karvyloop.capability.fs_grants import SENSITIVE_MARKERS
    st = getattr(request.app.state, "fs_grants", None)
    return {"grants": st.list() if st is not None else [],
            "sensitive_markers": list(SENSITIVE_MARKERS)}


@router.post("/fs_grants")
def api_fs_grants_add(req: FsGrantRequest, request: Request) -> dict[str, Any]:
    """手动放行一条路径(能力总览里的"授权新路径")。敏感路径 → 硬地板拒。"""
    st = getattr(request.app.state, "fs_grants", None)
    if st is None:
        return {"ok": False, "reason": "授权台账未接"}
    g = st.record(req.path, list(req.ops or ["read"]), origin="manual")
    if g is None:
        return {"ok": False, "reason": "敏感路径(密钥/凭据类),永不放行"}
    return {"ok": True, "grant": g}


@router.post("/fs_grants/revoke")
def api_fs_grants_revoke(req: FsGrantRevokeRequest, request: Request) -> dict[str, Any]:
    st = getattr(request.app.state, "fs_grants", None)
    if st is None:
        return {"ok": False, "reason": "授权台账未接"}
    return {"ok": st.revoke(req.grant_id)}


# ---- 挣来的静音:授权撤销 / 翻案(docs/49 机制2;能力总览可见可撤)----

class SilenceRevokeRequest(BaseModel):
    bucket: str = Field(..., min_length=1, max_length=128)


class SilenceOverturnRequest(BaseModel):
    proposal_id: str = Field(..., min_length=1, max_length=128)


@router.post("/silence/revoke")
def api_silence_revoke(req: SilenceRevokeRequest, request: Request) -> dict[str, Any]:
    """撤销某桶的静音授权(能力总览里一键撤;调 silence.revoke_grant)。

    没有可撤的授权 → ok=False(可能已过期/已吊销)。撤销后该类卡恢复逐张问你。"""
    from karvyloop.karvy.silence import revoke_grant
    ok = revoke_grant(request.app, req.bucket, reason="user")
    return {"ok": bool(ok)}


@router.post("/silence/overturn")
def api_silence_overturn(req: SilenceOverturnRequest, request: Request) -> dict[str, Any]:
    """翻案:推翻一条已静音处理的决定(最强负信号)→ 台账标记 + 吊销该桶授权 + 出告知卡。

    找不到该条 / 已翻过 → ok=False。翻案会连坐吊销整桶授权(押错一次不容忍)。"""
    from karvyloop.karvy.silence import overturn_silenced
    entry = overturn_silenced(request.app, req.proposal_id)
    return {"ok": entry is not None, "entry": entry}


@router.post("/coding/config")
async def api_coding_config(request: Request) -> dict[str, Any]:
    """#3:保存/清除外接编码工具命令(高级用户想用自己的 coder,如 Claude Code CLI)。
    命令落 ~/.karvyloop/coding.json(仓外,不进 repo)。v1.0 只存不接入执行(诚实)。"""
    from karvyloop.coding.coding_config import set_external_executor
    try:
        body = await request.json()
    except Exception:
        body = {}
    cmd = (body or {}).get("external_executor", "")
    pub = set_external_executor(cmd if isinstance(cmd, str) else "")
    return {"ok": True, **pub}


# ---- #42 优化:MCP 渠道预设(拧开就有水)—— 一键把知名 MCP server 写进 config.yaml ----

class McpPresetApplyRequest(BaseModel):
    preset_id: str = Field(..., min_length=1, max_length=64)
    params: dict[str, str] = Field(default_factory=dict)   # 如 {folder}/{token};绝不回显


@router.get("/mcp/presets")
def api_mcp_presets(request: Request) -> dict[str, Any]:
    """渠道预设目录 + 哪些已在 config.yaml 配好(只比对名字,**绝不回显 env/密钥**)。
    另带已配置的 remote(http)server 列表(只有 name / 去 query 的 url / 有没有凭证
    这个 bool,**绝不含 token/headers 值**)。
    诚实:MCP server 只在 console 启动时连(app.py lifespan),无热加载 → requires_restart。"""
    from karvyloop.console.mcp_presets import (
        configured_names, configured_remote_servers, list_presets)
    cfgp = getattr(request.app.state, "config_path", "") or ""
    ws = None
    try:
        from karvyloop.config_workspace import resolve_workspace
        ws = resolve_workspace(cfgp or None, ensure=False)
    except Exception:
        pass
    names = configured_names(cfgp)
    return {"presets": [{**p, "configured": p["id"] in names} for p in list_presets(ws)],
            "remote_servers": configured_remote_servers(cfgp),
            "requires_restart": True}


@router.post("/mcp/preset/apply")
def api_mcp_preset_apply(req: McpPresetApplyRequest, request: Request) -> dict[str, Any]:
    """把一个预设 upsert 进 config.yaml 的 mcp.servers。密钥只落 config.yaml(它本来就是
    密钥之家,仓外);响应**绝不回显 params/token**。工具只在启动时连接(无热加载)→
    如实返回 requires_restart=True,不假装已生效。"""
    from karvyloop.console.mcp_presets import apply_preset
    cfgp = getattr(request.app.state, "config_path", "") or ""
    if not cfgp:
        return {"ok": False, "reason": "未接 config(--no-llm?)"}
    ok, reason = apply_preset(req.preset_id, dict(req.params or {}), cfgp)
    if not ok:
        return {"ok": False, "reason": reason}
    return {"ok": True, "requires_restart": True, "preset_id": req.preset_id}


class McpRemoteAddRequest(BaseModel):
    """贴 URL 加 remote MCP server(streamable HTTP)。token 可选(bearer);
    请求体里的 token 只落 config.yaml,**绝不回显、绝不 log**。"""
    url: str = Field(..., min_length=8, max_length=2048)
    name: str = Field(default="", max_length=64)     # 不填 → 从 host 推导(mcp.notion.com → notion)
    token: str = Field(default="", max_length=4096)  # 可选 bearer token / API key


@router.post("/mcp/server/add")
def api_mcp_server_add(req: McpRemoteAddRequest, request: Request) -> dict[str, Any]:
    """"贴个 URL + 可选 token"加 remote MCP server(vendor 托管,streamable HTTP)。
    upsert 进 config.yaml 的 mcp.servers(`{name, url, transport: http, [token]}`)。
    凭证只落 config.yaml(仓外,export 排除);响应**绝不回显 token**;
    校验失败的 reason 只含参数名/URL host,不含 token 值。无热加载 → requires_restart。"""
    from karvyloop.console.mcp_presets import add_remote_server
    cfgp = getattr(request.app.state, "config_path", "") or ""
    if not cfgp:
        return {"ok": False, "reason": "未接 config(--no-llm?)"}
    ok, reason, name = add_remote_server(req.url, req.name, req.token, cfgp)
    if not ok:
        return {"ok": False, "reason": reason}
    return {"ok": True, "requires_restart": True, "name": name}


def _skill_status(body: str) -> str:
    """技能生命周期状态(btw-1,Hardy 定义):
      - crystallized(已沉淀):过了我方结晶门(有 verify_proof)或外部技能成功跑通一次(verified_at)。
      - unverified(待验证):外部导入(third-party),还没在本机跑通。
      - pending(待沉淀):自己写的/一次性,还没结晶。
    用 frontmatter 文本标记判定(body 含完整 SKILL.md;frontmatter 在头部,封顶内必含)。
    """
    if "verify_proof" in body or "verified_at" in body:
        return "crystallized"
    if "source: third-party" in body or "trust: untrusted" in body:
        return "unverified"
    return "pending"


def _skill_net_granted(app, name: str) -> bool:
    """读用户对某技能的联网授权(P1:第三方按需授网;默认拒)。"""
    ml = getattr(app.state, "main_loop", None)
    skills_dir = getattr(ml, "skills_dir", None) if ml is not None else None
    if skills_dir is None:
        return False
    try:
        from karvyloop.registry.skill_user_grants import load_user_grants
        g = load_user_grants(skills_dir)
        return bool(g and g.net_granted(name))
    except Exception:
        return False


class SkillGrantRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    net: bool = False    # 授予/收回联网


@router.post("/skill/grant")
def api_skill_grant(req: SkillGrantRequest, request: Request) -> dict[str, Any]:
    """用户对某技能显式授权联网(P1)。默认拒;这里是人主动放开(凌驾默认收口)。"""
    ml = getattr(request.app.state, "main_loop", None)
    skills_dir = getattr(ml, "skills_dir", None) if ml is not None else None
    if skills_dir is None:
        return {"ok": False, "reason": "未接技能库(--no-llm?)"}
    from karvyloop.registry.skill_user_grants import load_user_grants
    from karvyloop.registry.skill_import import safe_skill_name
    g = load_user_grants(skills_dir)
    if g is None:
        return {"ok": False, "reason": "无法访问授权存储"}
    g.set_net(safe_skill_name(req.name) or req.name, req.net)
    return {"ok": True, "name": req.name, "net": req.net}


def _skill_sources_store(app):
    ml = getattr(app.state, "main_loop", None)
    skills_dir = getattr(ml, "skills_dir", None) if ml is not None else None
    if skills_dir is None:
        return None
    from karvyloop.registry.skill_sources import load_skill_sources
    return load_skill_sources(skills_dir)


@router.get("/skill/catalog")
def api_skill_catalog(request: Request, q: str = "", source: str = "all") -> dict[str, Any]:
    """浏览可导入的技能目录(P1-b):走**用户配置的检索源**(默认官方 + 市场)。

    每条带 `source`(可直接喂 /api/skill/import 一键导)。网络失败 → 空列表(不崩)。
    """
    from karvyloop.registry.skill_catalog import search_catalog
    store = _skill_sources_store(request.app)
    srcs = store.enabled() if store is not None else None   # 只走已启用的源(关掉的不拖慢)
    try:
        entries = search_catalog((q or "")[:128], source=source, sources=srcs)
    except Exception as e:
        return {"entries": [], "reason": f"目录获取失败:{e}"}
    return {"entries": [e.to_dict() for e in entries]}


@router.get("/skill/sources")
def api_skill_sources(request: Request) -> dict[str, Any]:
    """列检索源(含开关状态)——管理面用。"""
    store = _skill_sources_store(request.app)
    if store is None:
        return {"sources": [], "no_llm": True}
    return {"sources": store.list()}


class SkillSourcesSaveRequest(BaseModel):
    sources: list[dict] = Field(default_factory=list)


@router.post("/skill/sources")
def api_skill_sources_save(req: SkillSourcesSaveRequest, request: Request) -> dict[str, Any]:
    """整表保存检索源(增删改 + 开关)。校验:≥1 个 enabled、id 不重、格式合法,否则不落盘。"""
    store = _skill_sources_store(request.app)
    if store is None:
        return {"ok": False, "reason": "未接技能库(--no-llm?)"}
    ok, reason = store.save(list(req.sources))
    return {"ok": ok, "reason": reason, "sources": store.list()}


class SkillImportRequest(BaseModel):
    source: str = Field(..., min_length=1, max_length=512)   # github spec / url / 本地路径 / zip
    kind: str = Field(default="auto", max_length=16)         # auto|github|zip|local
    overwrite: bool = False


@router.post("/skill/import")
def api_skill_import(req: SkillImportRequest, request: Request) -> dict[str, Any]:
    """导入第三方/外部技能(Agent Skills 开放标准)进本地技能库 —— 加入大家都在用的生态。

    校验门 + 安全护栏(路径穿越/zip bomb)在 skill_import 里;成功后重建索引,新技能即刻
    可召回 + 可被角色绑定。第三方默认 untrusted(无 verify_proof),带脚本的执行走 P0-c 沙箱。
    """
    ml = getattr(request.app.state, "main_loop", None)
    skills_dir = getattr(ml, "skills_dir", None) if ml is not None else None
    if skills_dir is None:
        return {"ok": False, "reason": "未接技能库(--no-llm?)"}
    from karvyloop.registry.skill_import import import_skill
    res = import_skill(req.source, skills_dir=skills_dir, kind=req.kind, overwrite=req.overwrite)
    if res.ok:
        try:
            ml.skill_index.rebuild_from_disk(skills_dir)   # 新技能进索引 → recall/绑定看得见
        except Exception:
            pass
    return {"ok": res.ok, "name": res.name, "reason": res.reason,
            "has_scripts": res.has_scripts, "untrusted": res.untrusted, "origin": res.origin}


class SkillRunRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)        # 技能名(skills_dir 下的目录)
    script: str = Field(..., min_length=1, max_length=256)     # 技能内相对脚本路径
    args: list[str] = Field(default_factory=list)


@router.post("/skill/run")
async def api_skill_run(req: SkillRunRequest, request: Request) -> dict[str, Any]:
    """在沙箱里试跑一个技能携带的脚本(P0-c)——让 Hardy 真看到第三方脚本被关进笼子里跑。

    脚本路径限定在技能目录内;token 由信任级 + allowed-tools 派生(第三方=最小授予,无网络)。
    沙箱不可用(非 Linux / 无 bwrap)→ 明确报错,绝不退化成无隔离直接跑。
    """
    import pathlib as _pl
    import tempfile as _tf
    ml = getattr(request.app.state, "main_loop", None)
    skills_dir = getattr(ml, "skills_dir", None) if ml is not None else None
    if skills_dir is None:
        return {"ok": False, "reason": "未接技能库(--no-llm?)"}
    from karvyloop.registry.skill_import import safe_skill_name
    name = safe_skill_name(req.name)
    skill_dir = _pl.Path(skills_dir) / name
    if not (skill_dir / "SKILL.md").is_file():
        return {"ok": False, "reason": f"技能「{req.name}」不存在"}
    from karvyloop.sandbox.selector import default_sandbox
    sb = default_sandbox()
    if not getattr(sb, "available", lambda: True)():
        return {"ok": False, "reason": "本机沙箱不可用(需 Linux + bubblewrap);拒绝无隔离执行"}
    ws = _tf.mkdtemp(prefix="karvyskill-ws-")
    net = _skill_net_granted(request.app, name)   # 用户授网了吗(默认否)
    from karvyloop.registry.skill_exec import run_skill_script
    try:
        res = await run_skill_script(str(skill_dir), req.script, list(req.args),
                                     sandbox=sb, workspace=ws, timeout_s=60.0, net=net)
    except Exception as e:
        return {"ok": False, "reason": f"执行失败:{e}"}
    promoted = False
    if res.exit_code == 0:
        # btw-1:外部技能完整成功跑通一次 → 标 verified_at(待验证 → 已沉淀)
        from karvyloop.registry.skill_exec import mark_skill_verified
        try:
            promoted = mark_skill_verified(str(skill_dir))
            if promoted:
                ml.skill_index.rebuild_from_disk(skills_dir)  # 刷新索引让状态翻新
        except Exception:
            pass
    return {
        "ok": res.exit_code == 0,
        "exit_code": res.exit_code,
        "stdout": res.stdout.decode("utf-8", "replace")[:8000],
        "stderr": res.stderr.decode("utf-8", "replace")[:4000],
        "timed_out": res.timed_out, "truncated": res.truncated,
        "promoted": promoted,   # 本次跑通把它从"待验证"升成"已沉淀"了吗
    }


class SkillRestoreRequest(BaseModel):
    sig: str = Field(..., min_length=1, max_length=128)


@router.post("/skill/restore")
def api_skill_restore(req: SkillRestoreRequest, request: Request) -> dict[str, Any]:
    """恢复被淘汰(归档)的技能(P0 审计:evict.restore() 没接端点)。快脑下次又能命中。"""
    ml = getattr(request.app.state, "main_loop", None)
    store = getattr(ml, "store", None) if ml is not None else None
    if store is None:
        return {"ok": False, "reason": "未接技能库(--no-llm?)"}
    try:
        store.restore(req.sig)
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    return {"ok": True}


@router.get("/domains")
def api_domains_list(request: Request) -> dict[str, Any]:
    """列所有业务域(含已归档,带 lifecycle/value_md/member_query)——管理面编辑/恢复用(P0 审计)。"""
    reg = getattr(request.app.state, "domain_registry", None)
    if reg is None:
        return {"domains": []}
    out = []
    for d in reg.list_all():
        out.append({
            "id": d.id, "name": d.name, "lifecycle": d.lifecycle,
            "value_md": getattr(d.value_md, "text", "") if d.value_md else "",
            "member_query": d.member_query, "parent_id": d.parent_id,
        })
    return {"domains": out}
