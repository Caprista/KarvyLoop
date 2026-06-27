"""routes — /api/* REST 端点(M3+ 批 8.5-C)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-C。

K 边界:
- K4 **强校验**:
  - 0 `domain.apply_*` 调用(grep 锁)
  - 只读 WorkbenchObserver / MainLoop / WidgetSnapshot
  - **不**写 `ml.store` / `ml.verify` / `ml.skill_index`
- K5 **强校验**:
  - H2A 决策**只**经 `decision_to_envelope` 工厂(import 是唯一构造路径)
  - 0 `Envelope(` 偷构(grep 锁)

借:Q5 — 借 WorkbenchObserver.snapshot / MainLoop.drive / decision_to_envelope;
       **自造**仅 FastAPI route 装饰器。
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from karvyloop.llm.token_ledger import token_source as _token_src

from karvyloop.cli.main_loop import MainLoop
from karvyloop.karvy.h2a import (
    H2A_ACCEPT,
    H2A_DEFER,
    H2A_REJECT,
    H2ADecision,
    decision_to_envelope,
    h2a_decide,
)
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.workbench.snapshot import snapshot_for_widgets
from karvyloop.workbench.main_loop_bridge import drive_in_tui

from .serializers import (
    drive_outcome_to_dict,
    drive_result_to_dict,
    envelope_to_dict,
    widget_snapshot,
)

logger = logging.getLogger(__name__)

# H2A REJECT 占位 reason:UI 不逼用户填(Hardy),留空时补它 —— 守协议 A8(REJECT 必带
# 非空 reason)+ 审计链有据,又不挡用户。诚实标注"用户未说明",不假造理由。
DEFAULT_REJECT_REASON = "(用户未说明)"

router = APIRouter(prefix="/api")


# ---- /api/snapshot ----

@router.get("/snapshot")
def api_snapshot(request: Request) -> dict[str, Any]:
    """当前 WidgetSnapshot JSON 视图(K4 只读)。"""
    workbench: WorkbenchObserver = request.app.state.workbench
    main_loop: Optional[MainLoop] = request.app.state.main_loop
    # 优先取 TUI 维护的 snapshot;若 App 不在,走 observer-only 路径
    if main_loop is not None and hasattr(main_loop, "_build_snapshot"):
        # 拍 8.5-A:WorkbenchApp 有 _build_snapshot(本拍暂不注入 App;走 fresh path)
        snap = snapshot_for_widgets(workbench)
    else:
        snap = snapshot_for_widgets(workbench)
    return widget_snapshot(snap)


# ---- /api/stats ----

@router.get("/stats")
def api_stats(request: Request) -> dict[str, Any]:
    """MainLoop 北极星指标(K4 只读 — 读 ml.stats,**不**写)。"""
    main_loop: Optional[MainLoop] = request.app.state.main_loop
    if main_loop is None:
        return {
            "main_loop_present": False,
            "drive_calls": 0,
            "fast_brain_hits": 0,
            "slow_brain_runs": 0,
            "crystallizations": 0,
            "auto_restores": 0,
            "fast_brain_hit_rate": 0.0,
        }
    s = main_loop.stats
    return {
        "main_loop_present": True,
        "drive_calls": s.drive_calls,
        "fast_brain_hits": s.fast_brain_hits,
        "slow_brain_runs": s.slow_brain_runs,
        "crystallizations": s.crystallizations,
        "auto_restores": s.auto_restores,
        "fast_brain_hit_rate": s.fast_brain_hit_rate,
    }


# ---- /api/chat_history ----

@router.get("/chat_history")
def api_chat_history(request: Request) -> list[dict[str, Any]]:
    """进程级聊天历史(8.5-A 暴露的 ring buffer,K4 只读)。"""
    workbench_app = request.app.state.workbench_app
    if workbench_app is None:
        return []
    return workbench_app.get_chat_history()


# ---- /api/intent ----

def scope_for_peer(mgr) -> str:
    """本轮场作用域(brick3+):私聊小卡(l0)→ "user"(个人技能);业务域 → "domain"。
    让结晶/召回跟随场 —— 业务域技能不再污染私聊小卡(brick3 只防住"对话被结晶",
    这里把真用工具的业务域技能也隔离开)。"""
    try:
        from karvyloop.karvy.capability import is_karvy_peer
        peer = mgr.current_peer() if mgr is not None else None
        domain_id = getattr(peer, "domain_id", "l0") if peer is not None else "l0"
        return "user" if is_karvy_peer(domain_id) else "domain"
    except Exception:
        return "user"


def speaker_display(app, mgr) -> str:
    """当前对话里"回复方"的显示名(身份模型 brick2 + brick4)。

    私聊小卡(l0)→ 返回 ""(前端映射成本地化的"小卡/Karvy");
    业务域 → 该角色的**花名(职务)**(brick4,profile.json);没花名则 agent_id/role。
    """
    try:
        from karvyloop.karvy.capability import is_karvy_peer
        peer = mgr.current_peer() if mgr is not None else None
        if peer is None or is_karvy_peer(getattr(peer, "domain_id", "l0")):
            return ""  # 小卡:前端本地化
        if getattr(peer, "role", "") == "group":
            return ""  # 群场:小卡当协调者发言
        rid = (peer.agent_id if (peer.role == "agent" and peer.agent_id) else peer.role) or ""
        role_reg = getattr(app.state, "role_registry", None) if app is not None else None
        if role_reg is not None and rid:
            try:
                rv = role_reg.get(rid)
            except Exception:
                rv = None
            if rv is not None:
                return rv.display_name()   # 花名(职务) / 花名 / id
        return rid
    except Exception:
        return ""


def _persona_for_current_peer(app, mgr, workspace_root: str, *, intent: str = ""):
    """按当前 peer 算 system prompt。

    - 私聊小卡(l0)→ 小卡人格(卡皮巴拉守护者)。
    - 业务域角色:**优先**用 paradigm 编译器(角色灵魂 7 文件 + 域 value.md/deontic → per-role
      system prompt,9.5 loop-step1);角色不在角色库 / 编译失败 → 回退轻量角色人格(0 回归)。
    任何异常 → 返 None(退回默认 coding 提示)。
    """
    try:
        from karvyloop.karvy.capability import is_karvy_peer
        from karvyloop.coding.persona import (
            build_karvy_persona_prompt, build_role_persona_prompt,
        )
        peer = mgr.current_peer() if mgr is not None else None
        domain_id = peer.domain_id if peer is not None else "l0"
        # ch4 群场:role=="group" → 小卡当协调者(看清群成员,帮分派,不冒充成员)
        if peer is not None and getattr(peer, "role", "") == "group":
            from karvyloop.coding.persona import build_group_coordinator_prompt
            dom_reg = getattr(app.state, "domain_registry", None)
            gname, members = "karvy world", []
            if not is_karvy_peer(domain_id) and dom_reg is not None:
                try:
                    dom = dom_reg.get(domain_id)
                    gname = getattr(dom, "name", domain_id)
                    members = [a.agent_id or a.role for a in dom_reg.resolve_members(domain_id)
                               if a.role != "user"]
                except Exception:
                    pass
            return build_group_coordinator_prompt(gname, members, cwd=workspace_root)
        if is_karvy_peer(domain_id):
            return build_karvy_persona_prompt(cwd=workspace_root)

        dom_reg = getattr(app.state, "domain_registry", None)
        role_reg = getattr(app.state, "role_registry", None)
        domain = None
        if dom_reg is not None:
            try:
                domain = dom_reg.get(domain_id)
            except Exception:
                domain = None
        # 角色 id:member_query `agent:designer` → role="agent"/agent_id="designer";否则用 role
        role_disp = getattr(peer, "role", None) or ""
        agent_id = getattr(peer, "agent_id", None) or ""
        candidates = [c for c in (agent_id, role_disp) if c]

        # 9.5 loop-step1:优先走 value.md→per-role 编译器(角色须在角色库里有 materialized 目录)
        if role_reg is not None:
            from karvyloop.coding.paradigm_prompt import build_role_paradigm_prompt
            for rid in candidates:
                try:
                    rv = role_reg.get(rid)
                except Exception:
                    rv = None
                if rv is not None:
                    cp = build_role_paradigm_prompt(rv, domain, intent=intent, cwd=workspace_root)
                    if cp is not None:
                        return cp  # 编译成功 → per-role 治理 system prompt

        # 回退:轻量角色人格(角色不在库里 / 编译失败)
        domain_name = getattr(domain, "name", None)
        role = role_disp or agent_id or "角色"
        return build_role_persona_prompt(role, domain_name=domain_name, cwd=workspace_root)
    except Exception:
        return None


class IntentRequest(BaseModel):
    intent: str = Field(..., min_length=1, max_length=4000)
    mention: str = Field(default="", max_length=64)          # ch4 #1:群里 @ 的角色 agent_id
    mention_domain: str = Field(default="", max_length=64)   # 该角色所属业务域(大群里同名消歧)
    images: list = Field(default_factory=list, max_length=6)  # 多模态:[{data_url, media_type, name}]
    attachments: dict = Field(default_factory=dict)  # 展示清单 {q, items:[{kind,name,thumb?}]} → 落历史给人回看


def _normalize_images(images) -> list:
    """前端 [{data_url, media_type}] → forge 要的 [{data: base64, media_type}](剥 data URI 前缀)。"""
    out = []
    for im in (images or [])[:6]:
        if not isinstance(im, dict):
            continue
        du = im.get("data_url") or ""
        mt = im.get("media_type") or ""
        data = ""
        if "," in du:
            head, data = du.split(",", 1)
            if not mt and head.startswith("data:") and ";" in head:
                mt = head[5:head.index(";")]
        if data:
            out.append({"data": data, "media_type": mt or "image/png"})
    return out


def _resolve_mention(app, mgr, mention: str, workspace_root: str, *, domain: str = "", intent: str = ""):
    """群里 @ 角色(Hardy):把 mention(agent_id[+domain])解析成 (persona, speaker, scope)。

    只在**群场**生效;@ 中的角色照它自己的人格/域回话(不再是协调者小卡)。**大群里两个业务域
    可能同名(都叫设计师)** → 带 domain 精准匹配 (domain_id, agent_id),并在署名上挂域名消歧
    (设计师（哟吼）)。找不到 → (None, "", None) 退回协调者。
    """
    if not (mention or "").strip():
        return None, "", None
    peer = mgr.current_peer() if mgr is not None else None
    if peer is None or getattr(peer, "role", "") != "group":
        return None, "", None
    from karvyloop.karvy.capability import is_karvy_peer
    dom_reg = getattr(app.state, "domain_registry", None)
    mid = mention.strip()
    did = (domain or "").strip()
    for a in _roundtable_roster(app, peer):
        if a.agent_id == mid and (not did or a.domain_id == did):
            dom = dom_reg.get(a.domain_id) if dom_reg is not None else None
            persona, speaker = _persona_for_role_addr(app, a, dom, workspace_root)
            # 大群(跨域)里给署名挂上业务域,免得两个"设计师"分不清
            dname = getattr(dom, "name", "") if dom is not None else ""
            if is_karvy_peer(peer.domain_id) and dname and dname not in speaker:
                speaker = f"{speaker}（{dname}）"
            return persona, speaker, "domain"
    return None, "", None


def group_no_mention_nudge(app, mgr, mention: str) -> dict | None:
    """群里**不 @ 任何人** → 系统不知道发给谁 → 不跑模型,只让小卡轻提醒一句(Hardy 定的群语义)。

    规则:@1 → 那个角色回;@2+ → workflow(走 /workflow/plan);**@0 → 没人回 + 小卡提醒**。
    只在群场(role=="group",含 Karvy World)生效;私聊小卡不受影响(走 route_to_role)。
    返回 drive_done payload(带 no_mention_nudge 标志,文案前端按语言本地化);否则 None。
    """
    if (mention or "").strip():
        return None
    peer = mgr.current_peer() if mgr is not None else None
    if getattr(peer, "role", "") != "group":
        return None
    # 例外:圆桌线虽挂在群 peer 下,但**追问圆桌 = 继续这场圆桌**(不是对群发话),不该 nudge → 放行正常 drive。
    cur = mgr.current() if mgr is not None else None
    if cur is not None and (getattr(cur, "title", "") or "").startswith("🎡"):
        return None
    return {"intent": "", "brain": "SLOW", "text": "", "speaker": "小卡",
            "skill_name": "", "fast_brain_hit": False, "crystallized": False,
            "no_mention_nudge": True}


# 注:旧 `/api/mention/fanout`(@多人 平行各回一句)已被 workflow 模式取代(@多人 → /workflow/plan),
# 端点移除。旧记录里的 `data.mention_fanout` 仍由前端 renderMentionReplies 重开渲染(向后兼容)。


# ---- 群内协作 workflow 模式(Hardy):@多人→小卡按目标+岗位职责设计 DAG→你拍板→执行(上游喂下游)----

def _extract_json_obj(text: str) -> str:
    from karvyloop.karvy.fastbrain.trace_habit import _strip_code_fences
    s = _strip_code_fences(text or "")
    i, j = s.find("{"), s.rfind("}")
    return s[i:j + 1] if (i >= 0 and j > i) else s


def _workflow_roles_from_mentions(app, peer, mentions):
    """把 @ 的 mentions 解析成角色 [{role_id, display, agent_id, domain_id, domain_name}](去重保序)。"""
    from karvyloop.karvy.capability import is_karvy_peer
    dom_reg = getattr(app.state, "domain_registry", None)
    roster = _roundtable_roster(app, peer)
    is_world = is_karvy_peer(peer.domain_id)
    out, seen = [], set()
    for m in (mentions or []):
        aid = (m.get("agent_id") or "").strip()
        did = (m.get("domain_id") or "").strip()
        for a in roster:
            if a.agent_id == aid and (not did or a.domain_id == did):
                key = (a.domain_id, a.agent_id)
                if key in seen:
                    break
                seen.add(key)
                dom = dom_reg.get(a.domain_id) if dom_reg is not None else None
                dname = getattr(dom, "name", "") if dom is not None else ""
                disp = _member_display(app, a)
                out.append({"role_id": f"r{len(out)}", "display": disp,
                            "agent_id": a.agent_id, "domain_id": a.domain_id,
                            "domain_name": dname if is_world else ""})
                break
    return out


async def _workflow_plan_llm(gw, model_ref, intent, roles) -> dict:
    """小卡设计 workflow DAG(显式任务用之,隐式按岗位职责+目标推)。返 {goal, steps:[...]}。"""
    import json as _json
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    roster_txt = "\n".join(
        f"- {r['role_id']}: {r['display']}" + (f"（{r['domain_name']}）" if r.get("domain_name") else "")
        for r in roles)
    sysp = (
        "你是群内协作的工作流编排者小卡。用户 @ 了下面这些角色协作完成一件事。设计一张 "
        "**workflow DAG**:给每个角色派一个步骤(必要时多个),标清**依赖**(下游依赖上游的产出)、"
        "能并行的就并行。用户给了显式任务就用它;没给就按该角色**职务/岗位职责 + 目标**推。"
        "**只输出 JSON**,别的不要:\n"
        '{"goal":"<一句话目标>","steps":[{"id":"s1","role_id":"<给定角色id>","task":"<这一步做什么>","depends_on":[]}]}\n'
        "规则:id 形如 s1/s2;role_id 必须是给定角色之一;depends_on 是前置 step 的 id 列表(无前置=[]);"
        "别造环;步骤别太碎,一个角色一步为主。")
    usr = f"角色:\n{roster_txt}\n\n用户消息:{intent}"
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gw.complete([{"role": "user", "content": usr}], [], ref,
                                    system=SystemPrompt(static=[sysp])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[workflow] 规划失败: {e}")
    try:
        plan = _json.loads(_extract_json_obj(out))
        if isinstance(plan, dict) and isinstance(plan.get("steps"), list):
            return plan
    except Exception as e:
        logger.warning(f"[workflow] 规划 JSON 解析失败: {e}")
    # 兜底:线性流水线(按 @ 顺序,各自一步)
    steps = [{"id": f"s{i+1}", "role_id": r["role_id"], "task": "完成你这部分",
              "depends_on": ([f"s{i}"] if i > 0 else [])} for i, r in enumerate(roles)]
    return {"goal": intent[:80], "steps": steps}


async def _refine_run_title(gw, model_ref, text: str, *, max_keep: int = 24) -> str:
    """2b:主题太长 → LLM 精炼成一个**极短主题名**(给工作流/圆桌卡当标题)。

    短的(≤max_keep)直接用,不烧 token;长的让 LLM 压成标签。任何失败 → 兜底截断(宁朴素勿崩)。
    """
    s = (text or "").strip()
    if not gw or len(s) <= max_keep:
        return s[:max_keep]
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    sysp = ("把用户这段意图压成一个**极短主题名**(≤12 字 / ≤6 词),像给一次协作起标签。"
            "只输出主题名本身,不要引号、标点、解释、前后缀。")
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gw.complete([{"role": "user", "content": s[:500]}], [], ref,
                                    system=SystemPrompt(static=[sysp])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[title] 主题精炼失败,兜底截断: {e}")
    out = (out or "").strip().strip("\"'《》「」 ")
    out = out.splitlines()[0].strip() if out else ""
    return out[:max_keep] if out else s[:max_keep]


def _enrich_plan(plan, roles) -> dict:
    """给 plan 的每步补上角色身份(display/agent_id/domain_id),丢弃指向未知角色的步骤。"""
    by_rid = {r["role_id"]: r for r in roles}
    steps = []
    valid_ids = set()
    for s in plan.get("steps", []):
        r = by_rid.get(s.get("role_id"))
        sid = s.get("id")
        if r is None or not sid:
            continue
        valid_ids.add(sid)
        steps.append({"id": sid, "role_id": r["role_id"], "display": r["display"],
                      "agent_id": r["agent_id"], "domain_id": r["domain_id"],
                      "task": (s.get("task") or "").strip() or "完成你这部分",
                      "depends_on": [d for d in (s.get("depends_on") or []) if isinstance(d, str)]})
    # 清掉指向不存在 step 的依赖
    for s in steps:
        s["depends_on"] = [d for d in s["depends_on"] if d in valid_ids and d != s["id"]]
    return {"goal": (plan.get("goal") or "").strip(), "steps": steps}


def _workflow_store(app):
    st = getattr(app.state, "workflow_store", None)
    if st is None:
        import pathlib
        from karvyloop.karvy.workflow_store import WorkflowStore
        cfgp = getattr(app.state, "config_path", "") or ""
        base = pathlib.Path(cfgp).parent if cfgp else (pathlib.Path.home() / ".karvyloop")
        st = WorkflowStore(base / "workflows.json")
        app.state.workflow_store = st
    return st


def _repoint_template(tpl, roles):
    """把结晶模板(按 role_key=agent_id 参数化)重指到当前 @ 的角色;角色没 @ 全 → None(不能复用)。"""
    by_key = {r["agent_id"]: r for r in roles}
    steps, valid = [], set()
    for s in tpl.get("steps", []):
        r = by_key.get(s.get("role_key"))
        if r is None or not s.get("id"):
            return None
        valid.add(s["id"])
        steps.append({"id": s["id"], "display": r["display"], "agent_id": r["agent_id"],
                      "domain_id": r["domain_id"], "task": s.get("task", ""),
                      "depends_on": list(s.get("depends_on", []))})
    for s in steps:
        s["depends_on"] = [d for d in s["depends_on"] if d in valid and d != s["id"]]
    return {"goal": tpl.get("goal", ""), "steps": steps, "from_template": tpl["id"]}


class WorkflowPlanRequest(BaseModel):
    intent: str = Field(..., min_length=1, max_length=4000)
    mentions: list[dict] = Field(default_factory=list, max_length=64)  # 50+ 步工作流压测放开到 64
    force_fresh: bool = False   # True = 跳过快脑匹配,重新设计


@router.post("/workflow/plan")
async def api_workflow_plan(req: WorkflowPlanRequest, request: Request) -> dict[str, Any]:
    """@多人 → 先**快脑匹配**结晶过的 workflow;命中提议复用,否则小卡现设计 DAG(都给你拍板)。"""
    app = request.app
    mgr = getattr(app.state, "conversation_manager", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    peer = mgr.current_peer() if mgr is not None else None
    if peer is None or getattr(peer, "role", "") != "group":
        return {"ok": False, "reason": "在群场里 @ 角色协作"}
    if gw is None or getattr(app.state, "main_loop", None) is None:
        return {"ok": False, "reason": "未接 LLM(--no-llm?)"}
    roles = _workflow_roles_from_mentions(app, peer, req.mentions)
    if len(roles) < 2:
        return {"ok": False, "reason": "workflow 需要 @ 两个及以上角色"}
    # 新 workflow **默认现设计**(针对这次的新意图)—— 不再把上次结晶的模板当默认计划塞回来
    # (Hardy:开新 workflow 却"沿用上一轮甚至上上轮内容" = 旧默认行为的 bug)。
    plan = await _workflow_plan_llm(gw, rk.get("model_ref", ""), req.intent, roles)
    result: dict[str, Any] = {"ok": True, "plan": _enrich_plan(plan, roles), "intent": req.intent}
    # 快脑匹配上 → 只**附带**一个"套用上次模板"的可选项(你显式点才用),且目标改成新意图,绝不默认套。
    if not req.force_fresh:
        tpl = _workflow_store(app).match(req.intent, [r["agent_id"] for r in roles])
        if tpl is not None:
            repointed = _repoint_template(tpl, roles)
            if repointed is not None:
                repointed["goal"] = req.intent   # 复用结构,目标用**新**意图(不沿用旧目标)
                result["matched"] = {"id": tpl["id"], "name": tpl.get("name", ""),
                                     "use_count": tpl.get("use_count", 0), "plan": repointed}
    return result


class WorkflowCrystallizeRequest(BaseModel):
    plan: dict = Field(...)
    name: str = Field(default="", max_length=40)


@router.post("/workflow/crystallize")
def api_workflow_crystallize(req: WorkflowCrystallizeRequest, request: Request) -> dict[str, Any]:
    """你确认后,把跑通的 workflow 结晶成可复用模板(按角色类型参数化,跨域可复用)。"""
    plan = req.plan or {}
    steps = plan.get("steps") or []
    if not steps:
        return {"ok": False, "reason": "空 workflow"}
    role_keys = list(dict.fromkeys(s.get("agent_id") for s in steps if s.get("agent_id")))
    tpl_steps = [{"id": s["id"], "role_key": s.get("agent_id"), "task": s.get("task", ""),
                  "depends_on": list(s.get("depends_on", []))}
                 for s in steps if s.get("id") and s.get("agent_id")]
    tpl = _workflow_store(request.app).save(goal=plan.get("goal", ""), role_keys=role_keys,
                                            steps=tpl_steps, name=req.name)
    return {"ok": True, "template": {"id": tpl["id"], "name": tpl["name"]}}


class WorkflowRunRequest(BaseModel):
    intent: str = Field(default="", max_length=4000)
    plan: dict = Field(...)
    edits: list[str] = Field(default_factory=list)   # §11 P2:你对小卡所提 DAG 的改动(决策信号)


# ---- 角色行为 evals(#39 ⑤:改了角色还干对吗,一键验)----

def _role_eval_store(app):
    st = getattr(app.state, "role_eval_store", None)
    if st is None:
        import pathlib
        from karvyloop.karvy.role_evals import RoleEvalStore
        cfgp = getattr(app.state, "config_path", "") or ""
        path = (pathlib.Path(cfgp).parent / "role_evals.json") if cfgp else None
        st = RoleEvalStore(path)
        app.state.role_eval_store = st
    return st


@router.get("/role/evals")
def api_role_evals(request: Request, role_id: str = "") -> dict[str, Any]:
    return {"evals": _role_eval_store(request.app).list(role_id)}


class RoleEvalAddRequest(BaseModel):
    role_id: str = Field(..., min_length=1, max_length=64)
    prompt: str = Field(..., min_length=1, max_length=2000)
    contains: list = Field(default_factory=list)
    absent: list = Field(default_factory=list)


@router.post("/role/eval/add")
def api_role_eval_add(req: RoleEvalAddRequest, request: Request) -> dict[str, Any]:
    ev = _role_eval_store(request.app).add(req.role_id, req.prompt,
                                           contains=req.contains, absent=req.absent)
    return {"ok": ev is not None, "eval": ev}


class RoleEvalDelRequest(BaseModel):
    role_id: str = Field(..., min_length=1, max_length=64)
    eval_id: str = Field(..., min_length=1, max_length=32)


@router.post("/role/eval/delete")
def api_role_eval_delete(req: RoleEvalDelRequest, request: Request) -> dict[str, Any]:
    return {"ok": _role_eval_store(request.app).delete(req.role_id, req.eval_id)}


class RoleEvalRunRequest(BaseModel):
    role_id: str = Field(..., min_length=1, max_length=64)
    eval_id: str = Field(default="", max_length=32)   # 空=跑该角色全部


@router.post("/role/eval/run")
async def api_role_eval_run(req: RoleEvalRunRequest, request: Request) -> dict[str, Any]:
    """跑角色的 eval(s):用该角色人格 drive 测试 prompt → 判定回复满不满足断言。fresh(不结晶)。"""
    from karvyloop.coding.persona import build_role_persona_prompt
    from karvyloop.karvy.role_evals import judge_eval
    app = request.app
    main_loop = getattr(app.state, "main_loop", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    if main_loop is None or rk.get("gateway") is None:
        return {"ok": False, "reason": "no_llm"}
    store = _role_eval_store(app)
    evs = [e for e in store.list(req.role_id) if not req.eval_id or e.get("id") == req.eval_id]
    if not evs:
        return {"ok": False, "reason": "no_evals"}
    ws = rk.get("workspace_root", "/")
    persona = build_role_persona_prompt(req.role_id, cwd=ws)   # 角色基线人格(改了它就该重验)
    results = []
    for ev in evs:
        try:
            outcome = await drive_in_tui(ev["prompt"], main_loop, persona=persona,
                                         fresh=True, **_rk_model(rk, _model_for_role(app, req.role_id)))
            reply = (getattr(outcome, "text", "") or "")
            err = getattr(outcome, "error", "")
            verdict = judge_eval(reply, ev)
            results.append({"id": ev["id"], "prompt": ev["prompt"], "reply": reply[:600],
                            "error": err or "", **verdict, "passed": verdict["passed"] and not err})
        except Exception as e:
            results.append({"id": ev["id"], "prompt": ev["prompt"], "reply": "", "error": str(e)[:200],
                            "passed": False, "missing": [], "present_forbidden": []})
    return {"ok": True, "results": results,
            "passed": sum(1 for r in results if r["passed"]), "total": len(results)}


def _workflow_run_store(app):
    st = getattr(app.state, "workflow_run_store", None)
    if st is None:
        import pathlib
        from karvyloop.karvy.workflow_runs import WorkflowRunStore
        cfgp = getattr(app.state, "config_path", "") or ""
        path = (pathlib.Path(cfgp).parent / "workflow_runs.json") if cfgp else None
        st = WorkflowRunStore(path)
        app.state.workflow_run_store = st
    return st


async def execute_workflow_durable(app, *, run_id: str, goal: str, steps: list,
                                   governance: str = "", task_id=None) -> dict:
    """#39 ①:持久化执行 workflow —— 每步产出 memoize 落盘,重启后 replay 时已完成步秒命中、只续剩余。

    run_step:① 已缓存(重启续)→ 直接返回不重跑;② 否则按角色人格 drive,**成功才落盘**(失败不存→
    重启会重试)。两条路(首跑/重启续)共用这一个 run_step,所以 replay 天然续上。
    """
    from karvyloop.domain import Address
    from karvyloop.karvy.workflow import run_workflow
    main_loop = getattr(app.state, "main_loop", None)
    dom_reg = getattr(app.state, "domain_registry", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    ws = rk.get("workspace_root", "/")
    store = _workflow_run_store(app)
    disp_by_id = {s["id"]: s.get("display", s.get("agent_id", "?")) for s in steps}

    async def run_step(step, upstream):
        sid = step.get("id", "")
        cached = store.step_output(run_id, sid)
        if cached is not None:        # 重启续:已完成步秒命中缓存,绝不重烧 token
            return {"output": cached}
        addr = Address(domain_id=step.get("domain_id", ""), role="agent",
                       agent_id=step.get("agent_id", ""))
        dom = dom_reg.get(addr.domain_id) if dom_reg is not None else None
        persona, _speaker = _persona_for_role_addr(app, addr, dom, ws)
        up_txt = "\n\n".join(f"【{disp_by_id.get(dep, dep)} 的产出】\n{out}"
                             for dep, out in upstream.items() if out)
        intent = (f"工作流目标:{goal}\n\n你的任务:{step.get('task', '')}\n\n"
                  + (f"上游产出(基于它继续):\n{up_txt}\n\n" if up_txt else "")
                  + "请完成你这一步,产出要能交给下游。简洁、聚焦你的职责。")
        outcome = await drive_in_tui(intent, main_loop, governance=governance,
                                     persona=persona, scope="domain", fresh=True,
                                     **_rk_model(rk, _model_for_role(app, step.get("agent_id", ""))))
        err = getattr(outcome, "error", "")
        await _push_step(app, task_id, sid,
                         disp_by_id.get(sid, step.get("agent_id", "?")),
                         "failed" if err else "done", err)
        if err:
            return None                # 不落盘 → 重启会重试这步(失败可能是瞬时)
        out = (outcome.text or "").strip()
        store.set_step(run_id, sid, out)   # memoize:成功才存,这就是 durable 的家
        return {"output": out}

    return await run_workflow({"goal": goal, "steps": steps}, run_step=run_step)


async def resume_workflows(app) -> int:
    """启动时 replay 被中断的 workflow(console 重启/崩溃后续上)。返回续跑的运行数。"""
    main_loop = getattr(app.state, "main_loop", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    if main_loop is None or rk.get("gateway") is None:
        return 0
    store = _workflow_run_store(app)
    mgr = getattr(app.state, "conversation_manager", None)
    n = 0
    for run in list(store.running()):
        try:
            rid = run["run_id"]
            result = await execute_workflow_durable(
                app, run_id=rid, goal=run.get("goal", ""), steps=run.get("steps", []),
                governance="")
            # 续完 → 落一条工作流线(和首跑一致,2a),标完成
            if mgr is not None and result.get("ok"):
                _record_workflow_line(app, run.get("domain_id", "l0"), run.get("goal", ""), result)
            store.finish(rid)
            n += 1
        except Exception as e:
            logger.warning(f"[workflow] 续跑 {run.get('run_id')} 失败: {e}")
    if n:
        logger.info(f"[karvyloop console] 续跑了 {n} 个被中断的 workflow")
    return n


def _record_workflow_line(app, domain_id: str, goal: str, result: dict) -> None:
    """把一次 workflow 结果落成独立工作流会话线(2a 的可复用版;resume 也用)。"""
    from karvyloop.domain import Address
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
    import uuid as _uuid
    mgr = getattr(app.state, "conversation_manager", None)
    dom_reg = getattr(app.state, "domain_registry", None)
    if mgr is None:
        return
    doc = _workflow_result_doc(result)
    run_id = _uuid.uuid4().hex[:16]
    run_peer = Address(domain_id=domain_id, role="workflow", agent_id=run_id)
    _dom = dom_reg.get(domain_id) if dom_reg is not None else None
    origin = (getattr(_dom, "name", "") or
              ("Karvy World" if domain_id == KARVY_WORLD_DOMAIN else domain_id))
    title = (goal[:60] or "工作流").strip()
    try:
        mgr.create_record(run_peer, title=title, user_intent=f"⚙ 工作流:{goal}",
                          agent_response=doc, brain="slow",
                          data={"workflow": result, "kind": "workflow", "origin_group": origin})
    except Exception as e:
        logger.warning(f"[workflow] 落工作流线失败: {e}")


@router.post("/workflow/run")
async def api_workflow_run(req: WorkflowRunRequest, request: Request) -> dict[str, Any]:
    """执行(你拍板/编辑后的)workflow DAG:依赖满足并发跑、上游产出喂下游。结果记进对话+同步首页。"""
    from karvyloop.domain import Address
    app = request.app
    mgr = getattr(app.state, "conversation_manager", None)
    main_loop = getattr(app.state, "main_loop", None)
    dom_reg = getattr(app.state, "domain_registry", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    peer = mgr.current_peer() if mgr is not None else None
    if peer is None or getattr(peer, "role", "") != "group":
        return {"ok": False, "reason": "在群场里执行 workflow"}
    if main_loop is None or dom_reg is None or rk.get("gateway") is None:
        return {"ok": False, "reason": "未接 LLM(--no-llm?)"}
    plan = req.plan or {}
    steps = plan.get("steps") or []
    if not steps:
        return {"ok": False, "reason": "空 workflow"}
    # Step 0(a):你的决策标准在**工作流执行**时也生效(query=goal),不只 l0 聊天。
    from karvyloop.console.decision_wire import assemble_governance
    governance = assemble_governance(app, intent=(plan.get("goal") or req.intent or ""),
                                     domain=(peer.domain_id or ""), base=(mgr.governance_text() or ""))
    ws = rk.get("workspace_root", "/")
    goal = (plan.get("goal") or req.intent or "").strip()
    # §11 P2:你对小卡所提 DAG 的改动 = 决策信号("你想要怎么做")→ 攒进决策结晶(走双关门;
    # 只在真改了才有,不浪费 token)。圆桌 goal 改写已由聊天蒸馏(P1b)覆盖,故只接 workflow 编辑。
    if req.edits:
        try:
            import time as _t
            from karvyloop.console.decision_wire import (
                observe_decision, schedule_decision_crystallize,
            )
            from karvyloop.crystallize.decision_pref import DecisionSample
            for e in req.edits[:10]:
                if str(e).strip():
                    observe_decision(app, DecisionSample(
                        decision="EDIT", context=str(e)[:500], reason="workflow 计划编辑",
                        domain=peer.domain_id or "", role="group", ts=_t.time()))
            schedule_decision_crystallize(app)
        except Exception:
            pass

    task_reg = getattr(app.state, "task_registry", None)
    task_id = (task_reg.start(who="⚙ 工作流", domain_id=peer.domain_id, role="group",
                              intent=f"⚙ {goal[:120]}") if task_reg is not None else None)
    # #39 ①:持久化执行 —— 登记运行(每步落盘),console 中途崩/重启能 replay 续上
    import uuid as _uuid
    run_id = _uuid.uuid4().hex[:16]
    _workflow_run_store(app).create(run_id, goal=goal, steps=steps, domain_id=peer.domain_id)
    try:
        result = await execute_workflow_durable(app, run_id=run_id, goal=goal, steps=steps,
                                                governance=governance, task_id=task_id)
    except Exception as e:
        if task_reg is not None and task_id is not None:
            task_reg.finish(task_id, error=str(e))
        logger.exception(f"[workflow] 执行异常: {e}")
        return {"ok": False, "reason": f"workflow 执行失败: {e}"}
    doc = _workflow_result_doc(result)
    if task_reg is not None and task_id is not None:
        task_reg.finish(task_id, result=doc)
    conv_id = ""
    run_line = None
    if mgr is not None:
        try:
            # 群线里留个 breadcrumb(群里看得到"跑过这个工作流");全文 + 结构进**专属工作流线**
            mgr.record_turn(f"⚙ 工作流:{goal}", doc, brain="slow", task_id=task_id or "",
                            data={"workflow": result})
            # 2a:把这次运行落成一条**独立「工作流」会话线**(role=workflow,左栏出卡 + 可重开追问)。
            # 此前 workflow 只 record_turn 进群线 → 没有专属可重开记录(Hardy:"工作流没历史")。
            from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
            import uuid as _uuid
            line_run_id = task_id or _uuid.uuid4().hex[:16]   # 工作流线的 agent_id(别和 durable run_id 撞)
            run_peer = Address(domain_id=peer.domain_id, role="workflow", agent_id=line_run_id)
            _dom = dom_reg.get(peer.domain_id) if dom_reg is not None else None
            origin_group = (getattr(_dom, "name", "") or
                            ("Karvy World" if peer.domain_id == KARVY_WORLD_DOMAIN else peer.domain_id))
            # 2b:主题太长 → LLM 精炼成短标题(短的原样,不烧 token)
            title = (await _refine_run_title(rk.get("gateway"), rk.get("model_ref", ""), goal)
                     or "工作流")
            run_conv = mgr.create_record(
                run_peer, title=title, user_intent=f"⚙ 工作流:{goal}", agent_response=doc,
                brain="slow", task_id=task_id or "",
                data={"workflow": result, "kind": "workflow", "origin_group": origin_group})
            conv_id = run_conv.id   # 追问 → 这条工作流线(上下文齐),不再是群线
            run_line = {"domain_id": run_peer.domain_id, "role": "workflow", "agent_id": line_run_id,
                        "conversation_id": run_conv.id, "title": title,
                        "origin_group": origin_group, "kind": "workflow"}
            if task_reg is not None and task_id is not None and conv_id:
                task_reg.set_conversation(task_id, conv_id)
        except Exception as e:
            logger.warning(f"[workflow] 记录失败: {e}")
    # 沉淀(Hardy):复用的 → bump 使用次数;现设计的且跑稳了 → 提议结晶(前端问你)。
    from_tpl = plan.get("from_template")
    if from_tpl:
        try:
            _workflow_store(app).bump_use(from_tpl)
        except Exception:
            pass
    _workflow_run_store(app).finish(run_id)   # #39 ①:跑完 → 标 done(不再被重启 replay)
    stable = result.get("ok") and all(s.get("status") == "done" for s in result.get("steps", []))
    crystallizable = bool(stable and not from_tpl)
    return {"ok": True, "workflow": result, "conversation_id": conv_id,
            "run_line": run_line,   # 2a:专属工作流会话线(左栏出卡 + 追问跳它)
            "crystallizable": crystallizable,
            "plan": {"goal": goal, "steps": steps} if crystallizable else None}


async def _push_step(app: Any, task_id: Optional[str], step_id: str, display: str,
                     status: str, error: str = "") -> None:
    """§0.7 P2:把一步的完成/失败推给 UI(实时进度,不等整体跑完)。失败不阻塞。"""
    if not task_id:
        return
    try:
        from karvyloop.console.task_events import broadcast_task_step
        await broadcast_task_step(app, {
            "task_id": task_id, "step_id": step_id, "display": display,
            "status": status, "error": (error or "")[:280],
        })
    except Exception:
        pass


def _workflow_result_doc(result: dict) -> str:
    parts = [f"⚙ 工作流:{result.get('goal', '')}"]
    for s in result.get("steps", []):
        mark = "✓" if s.get("status") == "done" else "✗"
        parts.append(f"\n\n**{mark} {s.get('display', '?')} · {s.get('task', '')}**\n{(s.get('output') or '').strip()}")
    return "".join(parts)


def _recall_domain(mgr) -> str:
    """§2.6 召回用域:在某业务域群 → 该域 id(召共享层 + 本域私有层);私聊/l0 大群 → ""(只召共享)。"""
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
    peer = mgr.current_peer() if mgr is not None else None
    d = getattr(peer, "domain_id", "") if peer is not None else ""
    return "" if d == KARVY_WORLD_DOMAIN else (d or "")


@router.post("/intent")
async def api_intent(req: IntentRequest, request: Request) -> dict[str, Any]:
    """提交 intent → 调 MainLoop.drive(K4 写 ml.drive 副作用,不算 K 违规:用户主动提交)。"""
    main_loop: Optional[MainLoop] = request.app.state.main_loop
    runtime_kwargs: dict = request.app.state.runtime_kwargs or {}
    workbench_app = request.app.state.workbench_app

    # 推到 chat history(8.5-A 复用)
    if workbench_app is not None:
        try:
            workbench_app.push_chat_log_line("user", req.intent)
        except Exception:
            pass

    if main_loop is None:
        # 修 silent-fail:返 200 + error dict,**不** 500
        return drive_outcome_to_dict(_stub_no_main_loop(req.intent))

    # 9.1d:取当前对话上下文(CV-8),喂 drive(上下文依赖门 + 慢脑消解多轮)
    # 9.2b:业务域线注入 value.md(CV-14)
    mgr = getattr(request.app.state, "conversation_manager", None)
    ctx = mgr.context_view() if mgr is not None else None
    governance = mgr.governance_text() if mgr is not None else ""

    # loop step4b 地基:个人知识库召回注入(同 ws._handle_intent_ws,封顶 8 条)
    # §2.6:在某业务域里 drive → 召回共享层 + 本域私有层(域专属认知不跨域漏)。
    mem = getattr(request.app.state, "memory", None)
    if mem is not None:
        try:
            block = mem.recall_block(req.intent, scope="personal", limit=8,
                                     domain=_recall_domain(mgr))
            if block:
                governance = (block + "\n\n" + governance).strip()
        except Exception:
            pass

    # §11 决策接口结晶:提案/drive 前注入"你的决策偏好"做预对齐(只偏置不执行,仍你拍板)。
    try:
        from karvyloop.console.decision_wire import prealign_governance
        _peer = mgr.current_peer() if mgr is not None else None
        _pa = prealign_governance(request.app, mem, query=req.intent,
                                  domain=(getattr(_peer, "domain_id", "") or ""),
                                  role=(getattr(_peer, "role", "") or ""))
        if _pa:
            governance = (_pa + "\n\n" + governance).strip()
    except Exception:
        pass

    # ch4 #1:群里 @ 角色 → 定向给它(它照自己人格/域回话);@ 命中则跳过路由 PROPOSE(你已点名)。
    ws_root = runtime_kwargs.get("workspace_root", "/")
    m_persona, m_speaker, m_scope = _resolve_mention(request.app, mgr, req.mention, ws_root,
                                                     domain=req.mention_domain, intent=req.intent)

    # 群里不 @ 任何人 → 没人回,小卡只轻提醒一句(不跑模型,不进历史)
    _nudge = group_no_mention_nudge(request.app, mgr, req.mention)
    if _nudge is not None:
        return _nudge

    if m_persona is None:
        # 9.4-门2:私聊小卡 + 业务委派意图 → 出 route_to_role PROPOSE(小卡是调度者不越进业务域)
        routed = await maybe_route_to_role(request.app, mgr, req.intent)
        if routed is not None:
            if workbench_app is not None:
                try:
                    workbench_app.push_chat_log_line("system", routed["text"])
                except Exception:
                    pass
            # 修上下文串台 bug:提议委派也要 record_turn —— 否则这句意图从不进对话记忆,
            # 紧接着的追问("就在X分析")会 self-drive 撞上**旧的无关 ctx**(如更早一张图),
            # 答非所问。记下这轮 → 追问承接的是真正的上一句。
            if mgr is not None:
                try:
                    mgr.record_turn(req.intent, routed["text"], brain="slow")
                except Exception:
                    pass
            return routed

    # 9.4e 方案 A:按当前 peer 算人格;@ 命中 → 用被 @ 角色的人格/域 scope(定向协作)。
    if m_persona is not None:
        persona, eff_scope = m_persona, (m_scope or "domain")
    else:
        persona = _persona_for_current_peer(request.app, mgr, ws_root, intent=req.intent)
        eff_scope = scope_for_peer(mgr)

    # 9.5 P2:任务看板 —— 把本次 drive 登记成一个任务(running),完成/出错再 finish。
    task_reg = getattr(request.app.state, "task_registry", None)
    task_id = None
    if task_reg is not None:
        _peer = mgr.current_peer() if mgr is not None else None
        _did = (_peer.domain_id if _peer is not None else "l0") or "l0"
        _role = (getattr(_peer, "role", "") or "") if _peer is not None else ""
        _who = m_speaker or ("小卡" if _did == "l0" else (_role or "角色"))   # @ 命中 → 是那个角色在忙
        task_id = task_reg.start(who=_who, domain_id=_did, role=_role, intent=req.intent)

    # 走 drive_in_tui(asyncio.to_thread 包装,防 R3-async 嵌套)
    try:
        # @ 命中 → 用被 @ 角色配置的模型(空=默认);否则全局 default。
        eff_rk = _rk_model(runtime_kwargs, _model_for_role(request.app, req.mention)) if m_persona is not None else runtime_kwargs
        outcome = await drive_in_tui(req.intent, main_loop, ctx=ctx, governance=governance,
                                     persona=persona, scope=eff_scope,
                                     images=_normalize_images(req.images), **eff_rk)
    except Exception as e:
        logger.exception(f"api_intent drive 异常: {e}")
        if task_reg is not None and task_id is not None:
            task_reg.finish(task_id, error=str(e))
        return {"intent": req.intent, "error": str(e), "brain": "SLOW", "text": ""}

    if task_reg is not None and task_id is not None:
        task_reg.finish(task_id, result=(outcome.text or ""), error=(outcome.error or ""))

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
            mgr.record_turn(
                req.intent, outcome.text or "",
                brain=outcome.brain.value, task_id=outcome.task_id,
                data=({"attachments": req.attachments} if req.attachments else None),  # 多模态:落历史给人回看
            )
            # 料→去聊天定位:把本任务挂到刚写入的对话 + 回填 trace_id(= turn.task_id)。
            # l0 私聊任务此前 conversation_id 一直空 → "去聊天"只能切场不能定位;且 registry id
            # ≠ turn 的 drive trace id,两个 id 空间对不上 → 定位永空。这里两样都补上。
            if task_reg is not None and task_id is not None:
                try:
                    task_reg.set_conversation(task_id, mgr.current().id, trace_id=outcome.task_id or "")
                except Exception:
                    pass
        except Exception:
            pass
        # loop step4b:轮后自动蒸馏(攒够 N 轮→批量编译进知识库;fire-and-forget 不阻塞)
        schedule_auto_distill(request.app, mgr)

    payload = drive_outcome_to_dict(outcome)
    payload["speaker"] = m_speaker or speaker_display(request.app, mgr)  # @ 命中 → 被 @ 角色署名
    return payload


def _match_role_for_intent(app, intent: str):
    """9.4-门2:小卡资源匹配 —— 在 active 业务域里找一个**角色名/agent_id 出现在 intent** 的业务 role。

    0.1.0 简单匹配(docs/29 §5:0.1.0 用 resolve_members 简单匹配,语义/能力匹配 P1)。
    跳过 user / observer(小卡自己,K1 不路由给自己)。返回 dict 或 None。
    """
    reg = getattr(app.state, "domain_registry", None)
    if reg is None:
        return None
    try:
        domains = list(reg.list_all())
    except Exception:
        return None
    for domain in domains:
        if getattr(domain, "lifecycle", "active") != "active":
            continue
        try:
            members = reg.resolve_members(domain.id)
        except Exception:
            continue
        for m in members:
            if m.role in ("user", "observer"):
                continue  # 不路由给用户 / 小卡(K1)
            hit = (m.role and m.role in intent) or (m.agent_id and m.agent_id in intent)
            if hit:
                # 显示名:member_query `agent:设计师` → role="agent"/agent_id="设计师",
                # 此时用 agent_id 当有意义的角色名;否则用 role。
                display = m.agent_id if (m.role == "agent" and m.agent_id) else m.role
                return {
                    "domain_id": domain.id,
                    "role": display,
                    "agent_id": m.agent_id or "",
                    "domain_name": getattr(domain, "name", domain.id),
                }
    return None


# 圆桌/多人协作信号:出现这些词 = 想让"几个人坐一起讨论"(圆桌),不是把活交给一个人(委派)。
_ROUNDTABLE_KW = (
    "圆桌", "round table", "roundtable", "开个会", "开会", "一起讨论", "一起分析",
    "一起聊", "大家讨论", "都来", "几个人", "多人", "讨论一下", "讨论下",
    "discuss together", "brainstorm", "panel",
)


def _resolve_roundtable_from_intent(app, intent: str):
    """私聊小卡 + 编排意图 → 解析出"在哪个群、拉哪些角色、议什么"。返回 dict 或 None。

    圆桌 vs 单点委派的区分:出现圆桌关键词 **或** 一句话里点到 ≥2 个角色 → 圆桌。
    群定位:句子里点到 "Karvy World/大群" 或匹配角色横跨 >1 个域 → l0 大群(跨域桌);
    否则匹配角色都在同一个域 → 那个域的群。议题 = 原 intent。
    """
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN

    reg = getattr(app.state, "domain_registry", None)
    if reg is None:
        return None
    low = (intent or "").lower()
    has_kw = any(k in intent or k in low for k in _ROUNDTABLE_KW)
    # 跨域扫所有 active 域的成员,收**全部**名字出现在 intent 里的业务角色(去重)。
    matched: list[dict] = []
    seen = set()
    try:
        for domain in reg.list_all():
            if getattr(domain, "lifecycle", "active") != "active":
                continue
            for m in reg.resolve_members(domain.id):
                if m.role in ("user", "observer"):
                    continue
                name = m.agent_id if (m.role == "agent" and m.agent_id) else m.role
                hit = (m.role and m.role in intent) or (m.agent_id and m.agent_id in intent)
                if not hit:
                    continue
                key = (domain.id, m.agent_id or m.role)
                if key in seen:
                    continue
                seen.add(key)
                matched.append({"domain_id": domain.id, "agent_id": m.agent_id or "",
                                "name": name, "domain_name": getattr(domain, "name", domain.id)})
    except Exception:
        return None
    if not matched:
        return None
    # 圆桌判定:有圆桌词 + ≥1 角色,或点到 ≥2 个**不同**角色。
    # (同一角色名跨多个域 ≠ 多人:"让分析师出周报" 命中两个域的"分析师"也只是单点委派,
    #  别误升圆桌 —— 真模型压测台逮到的 bug。)
    distinct_names = {m["name"] for m in matched}
    if not ((has_kw and matched) or len(distinct_names) >= 2):
        return None
    # 群定位:显式点到大群,或角色跨域 → l0 大群;否则同域群。
    wants_world = any(k in low for k in ("karvy world", "karvyworld")) or ("大群" in intent)
    domains_hit = {m["domain_id"] for m in matched}
    if wants_world or len(domains_hit) > 1:
        group_domain_id = KARVY_WORLD_DOMAIN
        group_name = "Karvy World"
    else:
        only = matched[0]
        group_domain_id = only["domain_id"]
        group_name = only["domain_name"]
    return {
        "group_domain_id": group_domain_id,
        "group_name": group_name,
        "participants": [m["agent_id"] for m in matched],
        "participant_names": [m["name"] for m in matched],
        "topic": intent,
    }


async def maybe_route_to_role(app, mgr, intent: str):
    """私聊小卡 + 业务委派意图 → 出 route_to_role PROPOSE(不自己干);否则返 None 走正常 drive。

    docs/29 KC-3/KC-4:小卡是调度者不是业务参与者 —— 业务活匹配 role + 提议委派,
    用户拍板(K5)后才由该 role 执行。匹配不到 role → 退回小卡自己执行(0 回归)。

    编排升级(Hardy 2026-06-25 bug):"让几个角色开圆桌讨论X" = **多人圆桌**,不是单点委派 ——
    先试圆桌解析,命中 → 出 roundtable PROPOSE;否则退回单角色 route_to_role(0 回归)。
    """
    from karvyloop.karvy.capability import dispatch_for_peer, is_karvy_peer

    peer = mgr.current_peer() if mgr is not None else None
    domain_id = peer.domain_id if peer is not None else "l0"  # 默认私聊小卡
    if not is_karvy_peer(domain_id):
        return None  # 私聊业务 role → 该 role 自己执行(照常 drive)
    # 运维意图(诊断/排查/运维)→ ops 诊断 H2A。ops 不是"委派给某角色",故不经 should_route 分类
    # (独立对抗验收点名:"帮我诊断系统" 会被 should_route 判 execute → 永远到不了 ops 路由)。
    if _looks_like_ops(intent):
        routed = await _fuzzy_ops_proposal(app, intent)
        if routed is not None:
            return routed
    if not dispatch_for_peer(domain_id, intent).should_route:
        return None  # execute / courier → 小卡自己处理(照常)
    registry = getattr(app.state, "proposal_registry", None)
    if registry is None:
        return None

    # ① 先试圆桌(多人协作)—— 命中则出 roundtable PROPOSE,不降级成单点委派。
    rt = _resolve_roundtable_from_intent(app, intent)
    if rt is not None:
        import time as _t
        from karvyloop.console.proposals import broadcast_proposal
        from karvyloop.karvy.proposal_registry import proposal_for_roundtable

        proposal = proposal_for_roundtable(ts=_t.time(), **rt)
        registry.register(proposal)
        try:
            await broadcast_proposal(app, proposal)
        except Exception:
            pass
        who = "、".join(rt["participant_names"]) if rt["participant_names"] else "群里的角色"
        return {
            "intent": intent, "brain": "SLOW", "fast_brain_hit": False,
            "crystallized": False, "skill_name": "", "routed": True,
            "text": (f"想让 {who} 一起讨论 —— 这是开**圆桌**(几个人坐一起),不是交给一个人。"
                     f"要在「{rt['group_name']}」开桌讨论「{rt['topic']}」吗?(到 🤝 H2A 处置)"),
        }

    # ② 退回单角色委派(原逻辑,确定性子串匹配)。
    match = _match_role_for_intent(app, intent)
    if match is not None:
        import time as _t
        from karvyloop.console.proposals import broadcast_proposal
        from karvyloop.karvy.proposal_registry import proposal_for_route

        proposal = proposal_for_route(ts=_t.time(), requirement=intent, **match)
        registry.register(proposal)
        try:
            await broadcast_proposal(app, proposal)  # 推到 H2A 列
        except Exception:
            pass
        return {
            "intent": intent, "brain": "SLOW", "fast_brain_hit": False,
            "crystallized": False, "skill_name": "", "routed": True,
            "text": (f"这件事属于业务域「{match['domain_name']}」 — "
                     f"要不要转给「{match['role']}」去做?(到 🤝 H2A 处置)"),
        }

    # ③ 模糊指令 LLM 拆解兜底(确定性规则没命中编排时):"去X域找几个人分析Y" 这类
    #    没点名、没说"圆桌"的模糊话 → LLM 拆出 域+人+方式 → 落到既有 H2A 提案。降级=小卡自己干。
    routed = await _maybe_fuzzy_dispatch(app, intent)
    if routed is not None:
        return routed
    return None  # 匹配不到 + 拆不出编排 → 小卡自己干(不强行路由)


_OPS_KW = (
    "诊断", "运维", "排查", "自检", "健康检查", "系统问题", "哪里有问题", "哪儿有问题",
    "哪有问题", "修一下系统", "系统出错", "系统报错", "self-heal", "diagnose", "health check",
)


def _looks_like_ops(intent: str) -> bool:
    """像不像"诊断/排查系统"这类运维意图(确定性,便宜,让 ops 能从自然语言路由)。"""
    low = (intent or "").lower()
    return any(k in intent or k in low for k in _OPS_KW)


async def _maybe_fuzzy_dispatch(app, intent: str):
    """模糊指令 → LLM 拆解 → roundtable/delegate/ops 的 H2A 提案;self/降级 → None。"""
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return None
    import time as _t

    from karvyloop.console.proposals import broadcast_proposal
    from karvyloop.karvy.fuzzy_dispatch import build_roster, decompose_dispatch

    roster = build_roster(app)
    try:
        with _token_src("fuzzy_dispatch"):
            plan = await decompose_dispatch(intent, roster=roster, gateway=gw,
                                            model_ref=rk.get("model_ref", ""))
    except Exception as e:  # noqa: BLE001 — 拆解任何异常都降级,不让 drive 崩
        logger.warning(f"[fuzzy_dispatch] 拆解失败,降级小卡自己干: {e}")
        return None
    if plan is None or not plan.is_actionable():
        return None
    registry = getattr(app.state, "proposal_registry", None)
    if registry is None:
        return None

    if plan.action == "roundtable":
        from karvyloop.karvy.proposal_registry import proposal_for_roundtable
        proposal = proposal_for_roundtable(
            ts=_t.time(), group_domain_id=plan.domain_id, group_name=plan.domain_name,
            participants=list(plan.participants), participant_names=list(plan.participant_names),
            topic=plan.topic or intent)
        who = "、".join(plan.participant_names)
        text = (f"我把你这句拆开了:想在「{plan.domain_name}」找 {who} 一起讨论「{plan.topic or intent}」"
                f"—— 这是开**圆桌**。要开吗?(到 🤝 H2A 处置)")
    elif plan.action == "delegate":
        from karvyloop.karvy.proposal_registry import proposal_for_route
        proposal = proposal_for_route(
            ts=_t.time(), domain_id=plan.domain_id, role=plan.participant_names[0],
            agent_id=plan.participants[0], domain_name=plan.domain_name,
            requirement=plan.topic or intent)
        text = (f"我理解你想把「{plan.topic or intent}」交给「{plan.domain_name}」的"
                f"「{plan.participant_names[0]}」。要转过去吗?(到 🤝 H2A 处置)")
    elif plan.action == "ops":
        return await _fuzzy_ops_proposal(app, intent)
    else:
        return None

    registry.register(proposal)
    try:
        await broadcast_proposal(app, proposal)
    except Exception:
        pass
    return {"intent": intent, "brain": "SLOW", "fast_brain_hit": False,
            "crystallized": False, "skill_name": "", "routed": True, "text": text}


async def _fuzzy_ops_proposal(app, intent: str):
    """模糊"帮我诊断/排查系统" → 跑 ops 诊断 → ops_fix H2A 提案(承既有 ops 路径)。"""
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    try:
        import time as _t

        from karvyloop.console.proposals import broadcast_proposal
        from karvyloop.karvy.proposal_registry import proposal_for_ops_fix
        from karvyloop.ops_agent import diagnose
        with _token_src("ops_diagnose"):
            d = await diagnose(intent, gateway=gw, model_ref=rk.get("model_ref", ""))
        codes = [f.get("code", "") for f in (d.to_dict().get("findings", []) or [])]
        prop = proposal_for_ops_fix(diagnosis=d.to_dict(), finding_codes=codes, ts=_t.time())
        registry = getattr(app.state, "proposal_registry", None)
        if registry is None:
            return None
        registry.register(prop)
        try:
            await broadcast_proposal(app, prop)
        except Exception:
            pass
        return {"intent": intent, "brain": "SLOW", "fast_brain_hit": False, "crystallized": False,
                "skill_name": "", "routed": True,
                "text": "我把这当成运维诊断跑了一轮,结论放到 🤝 H2A 处置(诊断是未核验建议,你拍板)。"}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[fuzzy_dispatch] ops 诊断失败,降级: {e}")
        return None


def _stub_no_main_loop(intent: str):
    """main_loop=None 时返 DriveOutcome stub(error,不 500)。"""
    from karvyloop.cli.main_loop import Brain
    from karvyloop.workbench.main_loop_bridge import DriveOutcome
    return DriveOutcome(
        intent=intent,
        brain=Brain.SLOW,
        text="",
        skill_name="",
        fast_brain_hit=False,
        crystallized=False,
        error="MainLoop 未注入 — 请先 karvyloop init",
    )


# ---- /api/tokens (9.3a:token 看板数据 — 按 source/model/day,K4 只读) ----

@router.get("/tokens")
def api_tokens(request: Request) -> dict[str, Any]:
    """token 用量看板:总量 + 按来源(功能)/ 模型 / 天 / **小时(时段)** + 最近调用时间线。无账本 → 空。"""
    led = getattr(request.app.state, "token_ledger", None)
    if led is None:
        return {"totals": {}, "by_source": [], "by_model": [], "by_day": [],
                "by_hour": [], "recent": []}
    return {
        "totals": led.totals(),
        "by_source": led.by_source(),   # ⭐ KarvyLoop 专属:看清 token 花在哪个功能
        "by_model": led.by_model(),
        "by_day": led.by_day(),
        "by_hour": led.buckets(interval_sec=3600, limit=48),  # ⭐ 时段:近 48 小时,看何时烧的
        "recent": led.recent(limit=50),                       # ⭐ 时间线:最近 50 次调用
    }


@router.get("/tokens/buckets")
def api_token_buckets(request: Request, interval: int = 3600,
                      limit: int = 200, since: float | None = None) -> dict[str, Any]:
    """任意粒度的 token 时间序列(压测看分钟级:`?interval=60`)。回答"token 什么时候烧的"。"""
    led = getattr(request.app.state, "token_ledger", None)
    if led is None:
        return {"interval": interval, "buckets": []}
    iv = max(1, min(int(interval), 86400))   # 1 秒 ~ 1 天,挡掉荒谬值
    lim = max(1, min(int(limit), 5000))
    return {"interval": iv, "buckets": led.buckets(interval_sec=iv, since=since, limit=lim)}


# ---- /api/domain/create (9.2c:建业务域 — 让 picker 真有业务域可选) ----

class DomainCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    value_md: str = Field(default="", max_length=8000)          # 9.4d:value.md 可选(空=暂无价值观)
    agent: str = Field(default="", max_length=64)               # 9.5 P4:入职角色**可选**(先想干啥再定角色)
    created_by_user: str = Field(default="ch", max_length=64)
    parent_id: str = Field(default="", max_length=64)           # §2.5:空=顶级域;非空=在该父域下建**子域**(继承 value/deontic)


@router.post("/domain/create")
def api_domain_create(req: DomainCreateRequest, request: Request) -> dict[str, Any]:
    """创建业务域(0.1.0 进程内 registry;域定义持久化 = P1)。

    value_md(9.4d 起**可选**):空 = 域暂无价值观原则(以后可补),deontic 强护栏照常治理;
    非空则自动补 `# 价值观` 前缀满足约定。member_query 自动建为 `user:<u> AND agent:<agent>`。
    """
    from karvyloop.domain.deontic import Deontic

    reg = getattr(request.app.state, "domain_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 domain_registry(console 未启用业务域)"}
    # 建域查重:已有同名 active 域 → 拒绝并提示(防注册表被同名域灌满,左栏/组织架构树变脏)
    try:
        _nm = (req.name or "").strip().lower()
        _dup = next((d for d in reg.list_active()
                     if (getattr(d, "name", "") or "").strip().lower() == _nm), None)
        if _dup is not None:
            return {"ok": False, "reason": f"已有同名业务域「{req.name.strip()}」"
                    f"(id {getattr(_dup, 'id', '?')});换个名字,或先归档旧的那个。"}
    except Exception:
        pass   # 查重失败不挡建域(降级)
    raw_value = (req.value_md or "").strip()
    if not raw_value:
        value_md = ""  # 空灵魂(合法)
    elif raw_value.startswith("# 价值观"):
        value_md = raw_value
    else:
        value_md = f"# 价值观\n\n{raw_value}"
    # 9.5 P4:角色可空 —— 空则 member_query 只含用户(域先建着,角色以后再入职)
    agent = (req.agent or "").strip()
    member_query = (
        f"user:{req.created_by_user} AND agent:{agent}" if agent
        else f"user:{req.created_by_user}"
    )
    try:
        if (req.parent_id or "").strip():
            # §2.5:在父域下建**子域**——继承父域 value.md + deontic(create_child,只能加不能删)
            domain = reg.create_child(
                parent_id=req.parent_id.strip(),
                name=req.name,
                created_by=f"user:{req.created_by_user}",
                deontic_override=Deontic(),
                member_query=member_query,
            )
        else:
            domain = reg.create(
                name=req.name,
                created_by=f"user:{req.created_by_user}",
                value_md_raw=value_md,
                deontic=Deontic(),
                member_query=member_query,
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"建域失败:{e}")
    # 9.5 loop-step1:把入职的角色**物化进角色库**(若还没有)——否则 peer 聊天时角色库查不到,
    # value.md→per-role 编译器永远 fall back(checker 发现的 CRITICAL 缺口)。
    if agent:
        role_reg = getattr(request.app.state, "role_registry", None)
        if role_reg is not None:
            try:
                if role_reg.get(agent) is None:
                    role_reg.create(agent, identity=f"业务域「{domain.name}」里的「{agent}」", atom_ids=[])
            except Exception as e:
                logger.warning(f"入职角色物化进角色库失败(不影响建域): {e}")

    # 9.2c-持久化:建完即存盘(域是用户数据,默认持久;domain_id 稳定→旧对话对得上)
    store = getattr(request.app.state, "domain_store", None)
    if store is not None:
        try:
            store.save_all(reg.list_all())
        except Exception as e:
            logger.warning(f"业务域存盘失败(本会话仍可用): {e}")

    # 门2(D4 live):role 入职新域 = docs/31 触发② → 查该 role 的全局技能 × 新域治理冲突,
    # 发现即出 resolve_conflict PROPOSE(注册进待决议表 + 回执;不拦运行时,SC-1/SC-5)。
    conflicts: list[dict[str, Any]] = []
    try:
        conflicts = _detect_domain_skill_conflicts(request.app, domain, req.agent)
    except Exception as e:
        logger.warning(f"技能×域冲突检测失败(不影响建域): {e}")

    return {
        "ok": True, "id": domain.id, "name": domain.name,
        "agent": req.agent, "conflicts": conflicts,
    }


class DomainArchiveRequest(BaseModel):
    domain_id: str = Field(..., min_length=1, max_length=64)


@router.post("/domain/archive")
def api_domain_archive(req: DomainArchiveRequest, request: Request) -> dict[str, Any]:
    """§2.6 ⑤:归档业务域(软删,可恢复)。**该域私有认知层随域清掉**(purge_domain);
    角色是公共库资产 → 回公共库(镜像还在,只脱离该域),通用/共享认知留着。"""
    app = request.app
    reg = getattr(app.state, "domain_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 domain_registry"}
    try:
        reg.archive(req.domain_id)
    except Exception as e:
        return {"ok": False, "reason": f"归档失败:{e}"}
    # 清该域私有认知层(Hardy 拍:域私有认知随域删)
    purged = 0
    mem = getattr(app.state, "memory", None)
    if mem is not None:
        try:
            purged = mem.purge_domain(req.domain_id)
        except Exception as e:
            logger.warning(f"[domain] 清域私有认知失败: {e}")
    store = getattr(app.state, "domain_store", None)
    if store is not None:
        try:
            store.save_all(reg.list_all())
        except Exception as e:
            logger.warning(f"[domain] 归档存盘失败: {e}")
    return {"ok": True, "purged_cognition": purged}


def _save_domains(app) -> None:
    store = getattr(app.state, "domain_store", None)
    reg = getattr(app.state, "domain_registry", None)
    if store is not None and reg is not None:
        try:
            store.save_all(reg.list_all())
        except Exception as e:
            logger.warning(f"[domain] 存盘失败: {e}")


class DomainUpdateRequest(BaseModel):
    domain_id: str = Field(..., min_length=1, max_length=64)
    value_md: Optional[str] = Field(default=None, max_length=8000)       # None=不改
    member_query: Optional[str] = Field(default=None, max_length=512)    # None=不改


@router.post("/domain/update")
def api_domain_update(req: DomainUpdateRequest, request: Request) -> dict[str, Any]:
    """编辑业务域(P0 审计:此前建错只能删重建)。改价值观/成员;archived 域拒改(先恢复)。"""
    reg = getattr(request.app.state, "domain_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 domain_registry"}
    raw = req.value_md
    if raw is not None:
        raw = raw.strip()
        if raw and not raw.startswith("# 价值观"):
            raw = f"# 价值观\n\n{raw}"
    try:
        d = reg.update(req.domain_id, value_md_raw=raw, member_query=req.member_query)
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    _save_domains(request.app)
    return {"ok": True, "id": d.id, "name": d.name}


class DomainRestoreRequest(BaseModel):
    domain_id: str = Field(..., min_length=1, max_length=64)


@router.post("/domain/restore")
def api_domain_restore(req: DomainRestoreRequest, request: Request) -> dict[str, Any]:
    """恢复已归档业务域 → active(P0 审计:registry 有 unarchive 没接)。"""
    reg = getattr(request.app.state, "domain_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 domain_registry"}
    try:
        reg.unarchive(req.domain_id)
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    _save_domains(request.app)
    return {"ok": True}


@router.get("/skills")
def api_skills(request: Request) -> dict[str, Any]:
    """列已结晶技能库(L0)——楔子的家。name/触发/描述/用量/是否归档 + SKILL.md 正文(封顶)。"""
    import pathlib as _pl
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
            "when_to_use": getattr(e, "when_to_use", ""),
            "description": getattr(e, "description", ""),
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
            "body": body,
        })
    out.sort(key=lambda s: (s["archived"], -s["recall_count"], -s["usage_count"]))
    return {"skills": out}


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


# ---- loop step4b:个人知识库(摄入编译 + 列表)----

class MemoryIngestRequest(BaseModel):
    material: str = Field(..., min_length=1, max_length=20000)
    agent_id: str = Field(default="user", max_length=64)


@router.post("/memory/ingest")
async def api_memory_ingest(req: MemoryIngestRequest, request: Request) -> dict[str, Any]:
    """摄入一段材料 → 编译成结构化 Belief 写进个人知识库(loop step4b-1 + 地基)。"""
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"ok": False, "reason": "memory 未接(--no-llm?)"}
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return {"ok": False, "reason": "无 gateway,无法编译(--no-llm?)"}
    from karvyloop.cognition.ingest import ingest_material
    try:
        res = await ingest_material(req.material, gateway=gw, mem=mem,
                                    model_ref=rk.get("model_ref", ""), agent_id=req.agent_id)
    except Exception as e:
        logger.warning(f"[memory/ingest] 摄入失败: {e}")
        return {"ok": False, "reason": f"摄入失败: {e}"}
    return {"ok": True, "written": res.written, "skipped": res.skipped,
            "beliefs": [b.content for b in res.beliefs],
            "skip_reasons": res.skip_reasons[:5]}


# ---- 认知库沉淀工作流(Hardy):喂料→抓取分析→知识自生长框架结构化→交流→你拍板沉淀/拒绝 ----
# 一次一条、持久化(重启续),不结束不开下一条。用 LLM Wiki/知识自生长框架结构化(others/卡帕西)。

def _distill_store(app):
    """单条待办沉淀的持久化存储(lazy)。"""
    st = getattr(app.state, "distill_store", None)
    if st is None:
        import pathlib
        from karvyloop.cognition.distill_session import DistillSessionStore
        cfgp = getattr(app.state, "config_path", "") or ""
        base = pathlib.Path(cfgp).parent if cfgp else (pathlib.Path.home() / ".karvyloop")
        st = DistillSessionStore(base / "pending_distill.json")
        app.state.distill_store = st
    return st


def _distill_public(s):
    """给前端的视图:去掉抓来的大段正文(只留摘要/来源/交流/阶段)。"""
    if not s:
        return None
    return {"id": s.get("id"), "summary": s.get("summary", ""),
            "source_url": s.get("source_url", ""), "material": (s.get("material") or "")[:300],
            "transcript": s.get("transcript", []), "phase": s.get("phase", "awaiting")}


def _extract_url(material: str) -> str:
    import re
    m = re.search(r"https?://\S+", material or "")
    return m.group(0).rstrip(").,。)】>\"'") if m else ""


async def _fetch_url(url: str, *, timeout: float = 12.0, max_chars: int = 16000) -> str:
    """抓链接正文(极简 HTML→text)。本地优先 + 用户主动分享的链接;失败返空。"""
    import re
    try:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout,
                                     headers={"User-Agent": "Mozilla/5.0 KarvyLoop"}) as c:
            r = await c.get(url)
            r.raise_for_status()
            txt = r.text
        txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", txt)
        txt = re.sub(r"(?is)<[^>]+>", " ", txt)
        txt = re.sub(r"&[a-z]+;", " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt[:max_chars]
    except Exception as e:
        logger.warning(f"[distill] 抓链接失败 {url}: {e}")
        return ""


_DISTILL_FRAMEWORK = (
    "你是小卡。用户分享了一份材料给个人知识库。用「知识自生长 / LLM Wiki」框架**分析并结构化**它,"
    "总结给用户看(**还没沉淀**,等用户确认)。按这个结构,清晰小标题 + bullet,简洁抓重点别堆字:\n"
    "1. **这是什么** —— 核心主题 / 来源类型;\n"
    "2. **核心概念 / 实体** —— 像 wiki 的概念页/实体页,抽 3-6 个要点;\n"
    "3. **关系** —— 它们之间怎么关联(谁支撑谁 / 谁对比谁);\n"
    "4. **值得沉淀的要点** —— 将来能复用的(关于这个主题、或关于用户);\n"
    "5. **建议沉淀吗** —— 一句话给建议 + 为什么。\n"
    "若材料里有用户自己的背景,优先结合。"
)


async def _distill_analyze(gw, model_ref, content, user_ctx="") -> str:
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    usr = (f"[关于用户的已知背景]\n{user_ctx}\n\n" if user_ctx else "") + f"[分享的材料]\n{content}"
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gw.complete([{"role": "user", "content": usr}], [], ref,
                                    system=SystemPrompt(static=[_DISTILL_FRAMEWORK])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[distill] 分析失败: {e}")
    return out.strip() or "(分析失败,稍后重试)"


async def _distill_chat_reply(gw, model_ref, session, message) -> str:
    """沉淀前的交流:你对这份料追问/补充,小卡围绕材料+当前总结回应。"""
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    sysp = ("你是小卡,正在和用户讨论一份待沉淀进知识库的材料。基于下面的材料和你的结构化总结,"
            "回应用户的追问/补充,帮他判断要不要沉淀。简洁、对话式。")
    convo = "\n".join(f"{x['who']}: {x['text']}" for x in session.get("transcript", []))
    usr = (f"[材料]\n{(session.get('fetched') or session.get('material') or '')[:6000]}\n\n"
           f"[你的结构化总结]\n{session.get('summary', '')}\n\n"
           f"[此前交流]\n{convo}\n\n[用户最新一句]\n{message}")
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gw.complete([{"role": "user", "content": usr}], [], ref,
                                    system=SystemPrompt(static=[sysp])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[distill] 交流失败: {e}")
    return out.strip() or "(没接上,再说一次?)"


class MemoryFeedRequest(BaseModel):
    material: str = Field(..., min_length=1, max_length=20000)


@router.get("/memory/distill")
def api_memory_distill(request: Request) -> dict[str, Any]:
    """当前待沉淀的那一条(没有→null)。前端开知识库先查这个 —— "下次打开继续聊"。"""
    return {"pending": _distill_public(_distill_store(request.app).current())}


@router.post("/memory/feed")
async def api_memory_feed(req: MemoryFeedRequest, request: Request) -> dict[str, Any]:
    """喂料(第1步):抓链接正文 → 知识自生长框架分析结构化 → 给你看(进"待沟通"态)。

    一次一条:已有待办未结束 → 拒绝,让你先把当前这条聊完(确认沉淀或拒绝)。
    """
    app = request.app
    mem = getattr(app.state, "memory", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if mem is None or gw is None:
        return {"ok": False, "reason": "memory/gateway 未接(--no-llm?)"}
    store = _distill_store(app)
    if store.current() is not None:
        return {"ok": False, "reason": "还有一条料在沉淀流程里没结束 —— 先把它聊完(确认沉淀或拒绝)再喂下一条。",
                "pending": _distill_public(store.current())}
    material = (req.material or "").strip()
    url = _extract_url(material)
    fetched = material
    if url:
        body = await _fetch_url(url)
        if body:
            fetched = f"[链接 {url} 的内容]\n{body}"
    user_ctx = ""
    try:
        user_ctx = mem.recall_block(material, scope="personal", limit=5) or ""
    except Exception:
        pass
    summary = await _distill_analyze(gw, rk.get("model_ref", ""), fetched, user_ctx)
    s = store.open(material=material, fetched=fetched[:16000], summary=summary, source_url=url or "")
    return {"ok": True, "session": _distill_public(s), "fetched_url": url or ""}


class DistillChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


@router.post("/memory/distill/chat")
async def api_memory_distill_chat(req: DistillChatRequest, request: Request) -> dict[str, Any]:
    """沉淀前交流(第3步前半):你对这条料追问/补充,小卡回应,记进 transcript。"""
    app = request.app
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    store = _distill_store(app)
    s = store.current()
    if s is None:
        return {"ok": False, "reason": "没有待沉淀的料"}
    if gw is None:
        return {"ok": False, "reason": "无 gateway(--no-llm?)"}
    reply = await _distill_chat_reply(gw, rk.get("model_ref", ""), s, req.message.strip())
    store.append_turn(who="you", text=req.message.strip())
    store.append_turn(who="karvy", text=reply)
    return {"ok": True, "reply": reply}


class DistillDecideRequest(BaseModel):
    decision: str = Field(..., pattern="^(persist|reject)$")


@router.post("/memory/distill/decide")
async def api_memory_distill_decide(req: DistillDecideRequest, request: Request) -> dict[str, Any]:
    """你拍板(第3步):persist → 沉淀进认知库(编译成 Belief);reject → 丢弃。都结束这条、可开下一条。"""
    app = request.app
    store = _distill_store(app)
    s = store.current()
    if s is None:
        return {"ok": False, "reason": "没有待沉淀的料"}
    if req.decision == "reject":
        store.close()
        return {"ok": True, "decision": "reject"}
    # persist:把抓来的正文编译进 Belief(复用 ingest;失败不丢待办,让你重试)
    mem = getattr(app.state, "memory", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if mem is None or gw is None:
        return {"ok": False, "reason": "memory/gateway 未接,沉淀失败(待办保留,可重试)"}
    from karvyloop.cognition.ingest import ingest_material
    try:
        res = await ingest_material(s.get("fetched") or s.get("material") or "", gateway=gw,
                                    mem=mem, model_ref=rk.get("model_ref", ""), source="fed")
    except Exception as e:
        logger.warning(f"[distill] 沉淀失败: {e}")
        return {"ok": False, "reason": f"沉淀失败(待办保留,可重试): {e}"}
    store.close()
    return {"ok": True, "decision": "persist", "written": res.written}


@router.get("/memory")
def api_memory_list(request: Request) -> dict[str, Any]:
    """列个人知识库当前 Belief(管理面 / 验证用)。决策偏好走自己的面,这里排除(免双显)。"""
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"beliefs": []}
    from karvyloop.crystallize.decision_pref import is_decision_pref
    return {"beliefs": [
        {"content": b.content, "kind": b.provenance.get("kind", "?"),
         "source": b.provenance.get("source", "?"), "freshness_ts": b.freshness_ts}
        for b in mem.index.all("personal") if not is_decision_pref(b)
    ]}


class DecisionPrefOpRequest(BaseModel):
    op: str = Field(..., pattern="^(delete|confirm|edit)$")   # 删 / 确认 / 编辑
    content: str = Field(..., min_length=1, max_length=2000)  # 按内容定位(Belief 无 id)
    new_content: str = Field(default="", max_length=2000)     # edit 用


@router.get("/decision_prefs")
def api_decision_prefs(request: Request) -> dict[str, Any]:
    """列你的决策偏好(可见 = 你掌舵的前提)。docs/02 §11 P1 可编辑面。"""
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"prefs": []}
    from karvyloop.crystallize.decision_pref import is_decision_pref
    prefs: list = []
    for sc in ("personal", "domain"):
        for b in mem.index.all(sc):
            if not is_decision_pref(b):
                continue
            p = b.provenance
            prefs.append({
                "content": b.content, "kind": p.get("kind", "taste"),
                "strength": p.get("strength", 0.0), "status": p.get("status", "provisional"),
                "applies": p.get("applies", {}), "evidence_n": len(p.get("evidence", [])),
                "freshness_ts": b.freshness_ts,
            })
    prefs.sort(key=lambda x: x["strength"], reverse=True)
    return {"prefs": prefs}


@router.post("/decision_prefs/op")
def api_decision_pref_op(req: DecisionPrefOpRequest, request: Request) -> dict[str, Any]:
    """对一条决策偏好:删除 / 确认(升 confirmed)/ 编辑内容。你随时能改 = 不固化你 + H2A。"""
    mem = getattr(request.app.state, "memory", None)
    if mem is None:
        return {"ok": False, "reason": "未接认知库"}
    from karvyloop.crystallize.decision_pref import (
        confirm_pref, find_decision_pref, rename_pref,
    )
    beliefs = [b for sc in ("personal", "domain") for b in mem.index.all(sc)]
    target = find_decision_pref(beliefs, req.content)
    if target is None:
        return {"ok": False, "reason": "偏好不存在(可能已被你删/改)"}
    try:
        if req.op == "delete":
            mem.archive(target)
        elif req.op == "confirm":
            mem.archive(target)
            mem.write(confirm_pref(target))
        elif req.op == "edit":
            nc = (req.new_content or "").strip()
            if not nc:
                return {"ok": False, "reason": "新内容不能为空"}
            mem.archive(target)
            mem.write(rename_pref(target, nc))
        return {"ok": True}
    except Exception as e:
        logger.warning(f"[decision_prefs] {req.op} 失败: {e}")
        return {"ok": False, "reason": str(e)}


@router.get("/decision_prefs/stats")
def api_decision_pref_stats(request: Request) -> dict[str, Any]:
    """复利信号(docs/02 §11 MVP):教会几条偏好 + 提案接受率趋势(越用越懂你的可测证据)。"""
    app = request.app
    mem = getattr(app.state, "memory", None)
    total = confirmed = 0
    by_kind: dict[str, int] = {}
    if mem is not None:
        from karvyloop.crystallize.decision_pref import is_decision_pref
        for sc in ("personal", "domain"):
            for b in mem.index.all(sc):
                if not is_decision_pref(b):
                    continue
                total += 1
                if b.provenance.get("status") == "confirmed":
                    confirmed += 1
                k = b.provenance.get("kind", "taste")
                by_kind[k] = by_kind.get(k, 0) + 1
    stats = getattr(app.state, "decision_stats", None)
    outcome = stats.summary() if stats is not None else {
        "decisions_total": 0, "accept_rate": None, "recent_accept_rate": None,
        "trend": None, "enough_for_trend": False,
    }
    return {"prefs_total": total, "confirmed": confirmed, "by_kind": by_kind, **outcome}


def _persona_for_role_addr(app, addr, domain, workspace_root: str):
    """给圆桌一个成员(Address)算人格:优先 per-role 编译,回退轻量角色人格。"""
    from karvyloop.coding.persona import build_role_persona_prompt
    from karvyloop.coding.paradigm_prompt import build_role_paradigm_prompt
    role_reg = getattr(app.state, "role_registry", None)
    rid = (addr.agent_id or addr.role) or ""
    if role_reg is not None and rid:
        try:
            rv = role_reg.get(rid)
        except Exception:
            rv = None
        if rv is not None:
            cp = build_role_paradigm_prompt(rv, domain, cwd=workspace_root)
            if cp is not None:
                return cp, (rv.display_name() if hasattr(rv, "display_name") else rid)
    return (build_role_persona_prompt(addr.role or rid or "角色",
                                      domain_name=getattr(domain, "name", None),
                                      cwd=workspace_root), rid or addr.role)


def _model_for_role(app, agent_id: str) -> str:
    """角色级模型引用(空=层叠到全局 default;#1 §3.1 软默认层叠)。role 是 agent 特例,适用。"""
    role_reg = getattr(app.state, "role_registry", None)
    aid = (agent_id or "").strip()
    if role_reg is not None and aid:
        try:
            rv = role_reg.get(aid)
            if rv is not None:
                return (getattr(rv, "model", "") or "").strip()
        except Exception:
            pass
    return ""


def _rk_model(rk: dict, model: str) -> dict:
    """覆盖 runtime_kwargs 的 model_ref(角色配了模型就用它,否则原样=全局 default)。"""
    if not model:
        return rk
    out = dict(rk)
    out["model_ref"] = model
    return out


class RoundtableStartRequest(BaseModel):
    intent: str = Field(..., min_length=1, max_length=8000)             # 圆桌主题
    participants: list[str] = Field(default_factory=list, max_length=64)  # 选的 agent_id;空=全上桌(50+ 大桌压测放开到 64)


class RoundtableDiscussRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=64)


def _roundtable_state(app) -> dict:
    """待办圆桌态(conv_id → {topic, participants, phase})。持久化(配了 config_path 时)→ 重启续
    "开始讨论"。测试无 config_path → 纯内存(不污染真实 home)。"""
    st = getattr(app.state, "roundtables", None)
    if st is None:
        cfgp = getattr(app.state, "config_path", "") or ""
        if cfgp:
            import json
            import pathlib
            path = pathlib.Path(cfgp).parent / "roundtables.json"
            app.state._roundtables_path = path
            try:
                st = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            except Exception:
                st = {}
            if not isinstance(st, dict):
                st = {}
        else:
            st = {}   # 无 config(测试)→ 纯内存
        app.state.roundtables = st
    return st


def _persist_roundtable_state(app) -> None:
    """待办圆桌态落盘(配了路径才落;原子写)。"""
    path = getattr(app.state, "_roundtables_path", None)
    if path is None:
        return
    try:
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(app.state.roundtables, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.warning(f"[roundtable] 待办态持久化失败: {e}")


# ---- 定时任务(Hardy 2026-06-25:只有 Karvy 能起;角色无调度工具→天然起不了)----

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
    import time as _t
    now_str = _t.strftime("%Y-%m-%d %H:%M %A", _t.localtime())
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


async def fire_schedule(app: Any, t) -> None:
    """到点(或手动)执行一条定时任务:灌进 drive 管线;有委派目标就以那个角色人格跑,否则小卡自己跑。

    结果记成一个首页任务(看得见跑过)。走 §13(动态任务每次重跑、不回放 stale)。失败 fail-loud 记 last_error。
    """
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
            persona, speaker = _persona_for_role_addr(app, addr, dom, ws)
            who = f"⏰ {speaker}"
            eff_rk = _rk_model(rk, _model_for_role(app, t.target_agent_id or t.target_role))
        except Exception:
            persona = None
    task_reg = getattr(app.state, "task_registry", None)
    task_id = task_reg.start(who=who, domain_id=(t.target_domain or "l0"),
                             role=(t.target_role or ""), intent=f"⏰ {t.intent[:120]}") if task_reg else None
    try:
        scope = "domain" if t.target_domain and t.target_role else None
        # Step 0(a):你的决策标准在**定时任务**触发时也生效(到点替你做事,标准照管)。
        from karvyloop.console.decision_wire import assemble_governance
        _sched_gov = assemble_governance(app, intent=t.intent, domain=(t.target_domain or ""),
                                         role=(t.target_role or ""))
        outcome = await drive_in_tui(t.intent, main_loop, governance=_sched_gov, persona=persona,
                                     scope=scope, **eff_rk)
        err = getattr(outcome, "error", "") or ""
        if task_reg and task_id:
            task_reg.finish(task_id, result=(outcome.text or ""), error=err)
        st.mark_run(t.id, "error" if err else "ok", ts=_t.time(), error=err)
    except Exception as e:
        logger.exception(f"[schedule] 执行异常 {t.id}: {e}")
        if task_reg and task_id:
            task_reg.finish(task_id, error=str(e))
        st.mark_run(t.id, "error", ts=_t.time(), error=str(e))


# ---- 2c:左栏"X 掉" = 隐藏不删 ----
# Hardy 语义:X 只是从左栏隐藏这条会话线,**内容不删**;还能从流入的料点"追问"重开(重开即重新显示)。
# 持久化一组被隐藏的 line key("域|角色|agent");私聊小卡(l0/observer/karvy)永不可隐藏。

def _line_key(domain_id: str, role: str, agent_id: str = "") -> str:
    return f"{domain_id or ''}|{role or ''}|{agent_id or ''}"


def _is_karvy_private_line(domain_id: str, role: str, agent_id: str) -> bool:
    """私聊小卡(永远置顶、不可 X)。"""
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
    return domain_id == KARVY_WORLD_DOMAIN and role == "observer" and (agent_id or "") in ("karvy", "")


def _hidden_lines(app) -> set:
    """被隐藏的 line key 集合。持久化(有 config_path 时)→ 重启仍隐藏;测试纯内存。"""
    st = getattr(app.state, "hidden_lines", None)
    if st is None:
        cfgp = getattr(app.state, "config_path", "") or ""
        st = set()
        if cfgp:
            import json
            import pathlib
            path = pathlib.Path(cfgp).parent / "hidden_lines.json"
            app.state._hidden_lines_path = path
            try:
                if path.exists():
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        st = set(str(x) for x in raw)
            except Exception:
                st = set()
        app.state.hidden_lines = st
    return st


def _persist_hidden_lines(app) -> None:
    path = getattr(app.state, "_hidden_lines_path", None)
    if path is None:
        return
    try:
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(_hidden_lines(app)), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.warning(f"[hidden] 隐藏态落盘失败: {e}")


def _is_line_hidden(app, domain_id: str, role: str, agent_id: str = "") -> bool:
    return _line_key(domain_id, role, agent_id) in _hidden_lines(app)


def _set_line_hidden(app, domain_id: str, role: str, agent_id: str, hidden: bool) -> bool:
    """隐藏/恢复一条线。私聊小卡不可隐藏。返回是否生效。"""
    if hidden and _is_karvy_private_line(domain_id, role, agent_id):
        return False
    s = _hidden_lines(app)
    key = _line_key(domain_id, role, agent_id)
    if hidden:
        s.add(key)
    else:
        s.discard(key)
    _persist_hidden_lines(app)
    return True


class LineHideRequest(BaseModel):
    domain_id: str = Field(..., min_length=1, max_length=64)
    role: str = Field(..., min_length=1, max_length=64)
    agent_id: str = Field(default="", max_length=64)
    hidden: bool = Field(default=True)   # True=X 掉隐藏;False=恢复显示


@router.post("/line/hide")
def api_line_hide(req: LineHideRequest, request: Request) -> dict[str, Any]:
    """2c:X 掉(隐藏)/恢复一条左栏会话线 —— 只动显示,**不删内容**。私聊小卡不可隐藏。"""
    ok = _set_line_hidden(request.app, req.domain_id, req.role, req.agent_id, req.hidden)
    if not ok:
        return {"ok": False, "reason": "pinned"}   # 小卡置顶,X 不动它
    return {"ok": True, "hidden": req.hidden}


def _line_origin_name(app, did: str) -> str:
    """发起群名(卡片副标题)。Karvy World 用品牌名;域用域名;兜底 id。"""
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
    if did == KARVY_WORLD_DOMAIN:
        return "Karvy World"
    reg = getattr(app.state, "domain_registry", None)
    d = reg.get(did) if reg is not None else None
    return getattr(d, "name", "") or did


@router.get("/lines")
def api_lines(request: Request) -> dict[str, Any]:
    """2d:左栏「工作流」「圆桌」两区的卡 —— 各自跑出来的独立会话线(主题 + 发起群)。已隐藏的过滤掉。

    工作流线:role=="workflow"(每次运行一条,peer 独立)。
    圆桌线:挂在群 peer 下、标题以 🎡 开头(按 conv_id 区分;隐藏键用 role="roundtable"+conv_id)。
    """
    app = request.app
    out: dict[str, Any] = {"workflows": [], "roundtables": []}
    mgr = getattr(app.state, "conversation_manager", None)
    if mgr is None:
        return out
    try:
        for m in mgr.all_conversation_metas():
            role = getattr(m.peer, "role", "")
            did = getattr(m.peer, "domain_id", "")
            aid = getattr(m.peer, "agent_id", "") or ""
            title = (m.title or "").strip()
            if role == "workflow":
                if _is_line_hidden(app, did, "workflow", aid):
                    continue
                out["workflows"].append({
                    "domain_id": did, "role": "workflow", "agent_id": aid,
                    "conversation_id": m.id, "title": title or "工作流",
                    "origin_group": _line_origin_name(app, did), "last_active_at": m.last_active_at})
            elif role == "group" and title.startswith("🎡"):
                if _is_line_hidden(app, did, "roundtable", m.id):
                    continue
                out["roundtables"].append({
                    "domain_id": did, "role": "roundtable", "agent_id": m.id,
                    "conversation_id": m.id, "title": title.lstrip("🎡 ").strip() or "圆桌",
                    "origin_group": _line_origin_name(app, did), "last_active_at": m.last_active_at})
    except Exception as e:
        logger.warning(f"[lines] 列工作流/圆桌线失败: {e}")
    out["workflows"].sort(key=lambda x: -(x.get("last_active_at") or 0))
    out["roundtables"].sort(key=lambda x: -(x.get("last_active_at") or 0))
    return out


class LineOpenRequest(BaseModel):
    role: str = Field(..., min_length=1, max_length=32)          # workflow / roundtable
    domain_id: str = Field(..., min_length=1, max_length=64)
    agent_id: str = Field(default="", max_length=64)
    conversation_id: str = Field(default="", max_length=64)


@router.post("/line/open")
def api_line_open(req: LineOpenRequest, request: Request) -> dict[str, Any]:
    """2e:打开一条工作流/圆桌线(点卡片 / 料里点追问都走这)。重开 = 切到该线 + 自动恢复显示。"""
    from karvyloop.domain.registry import Address
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"ok": False, "reason": "未接对话编排器"}
    if req.role == "workflow":
        peer = Address(domain_id=req.domain_id, role="workflow", agent_id=req.agent_id)
        conv = mgr.set_peer(peer)
        _set_line_hidden(request.app, req.domain_id, "workflow", req.agent_id, False)
        ret_peer = {"domain_id": req.domain_id, "role": "workflow", "agent_id": req.agent_id}
    elif req.role == "roundtable":
        # 圆桌线在群 peer 下:切到群 + resume 那条 conv(继续这场圆桌,追问免 @ —— nudge 已放行 🎡 线)
        gpeer = Address(domain_id=req.domain_id, role="group", agent_id="")
        mgr.set_peer(gpeer)
        conv = mgr.resume(gpeer, req.conversation_id) or mgr.current()
        _set_line_hidden(request.app, req.domain_id, "roundtable", req.conversation_id, False)
        ret_peer = {"domain_id": req.domain_id, "role": "group", "agent_id": ""}
    else:
        return {"ok": False, "reason": "bad role"}
    if conv is None:
        return {"ok": False, "reason": "not_found"}
    return {
        "ok": True, **ret_peer, "is_group": req.role == "roundtable",
        "conversation_id": conv.id, "turn_count": conv.turn_count,
        "turns": [{"user_intent": t.user_intent, "agent_response": t.agent_response,
                   "brain": t.brain, "task_id": t.task_id, "data": t.data} for t in conv.turns],
    }


class ConvOpenRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=64)


@router.post("/line/open_by_conv")
def api_line_open_by_conv(req: ConvOpenRequest, request: Request) -> dict[str, Any]:
    """2e:按 conversation_id 定位并打开它**真正所在的线**(料里点追问走这)。

    比"切群 + resume"稳:工作流线挂在 role=workflow 的独立 peer 下,在群 peer 上 resume 找不到
    (这就是"追问没上下文"的根)。这里按 id 在所有 peer 的 metas 里找到它的真 peer 再开。
    """
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"ok": False, "reason": "未接对话编排器"}
    target = None
    try:
        for m in mgr.all_conversation_metas():
            if m.id == req.conversation_id:
                target = m.peer
                break
    except Exception as e:
        logger.warning(f"[line] 定位对话失败: {e}")
    if target is None:
        return {"ok": False, "reason": "not_found"}
    mgr.set_peer(target)
    conv = mgr.resume(target, req.conversation_id) or mgr.current()
    if conv is None:
        return {"ok": False, "reason": "not_found"}
    role = getattr(target, "role", "")
    title = (conv.title or "")
    is_round = role == "group" and title.startswith("🎡")
    # 重开 → 自动恢复显示(把它从隐藏集移除)
    if role == "workflow":
        _set_line_hidden(request.app, target.domain_id, "workflow", target.agent_id or "", False)
    elif is_round:
        _set_line_hidden(request.app, target.domain_id, "roundtable", conv.id, False)
    return {
        "ok": True, "domain_id": target.domain_id, "role": role,
        "agent_id": target.agent_id or "", "is_group": role == "group",
        "is_run_line": role == "workflow" or is_round,
        "kind": "workflow" if role == "workflow" else ("roundtable" if is_round else ""),
        "title": title.lstrip("⚙🎡 ").strip(), "origin_group": _line_origin_name(request.app, target.domain_id),
        "conversation_id": conv.id, "turn_count": conv.turn_count,
        "turns": [{"user_intent": t.user_intent, "agent_response": t.agent_response,
                   "brain": t.brain, "task_id": t.task_id, "data": t.data} for t in conv.turns],
    }


def _roundtable_pending(app, conv_id: str):
    """这条对话若是"待讨论圆桌"(阶段0未点开始)→ 返 {conversation_id, participants} 供前端亮横幅。"""
    st = _roundtable_state(app).get(conv_id or "")
    if st and st.get("phase") == "aligning":
        names = []
        dom_reg = getattr(app.state, "domain_registry", None)
        peer = None
        mgr = getattr(app.state, "conversation_manager", None)
        if mgr is not None:
            peer = mgr.current_peer()
        roster = _roundtable_roster(app, peer) if peer is not None else []
        by_id = {a.agent_id: a for a in roster}
        for aid in st.get("participants", []):
            a = by_id.get(aid)
            names.append(_member_display(app, a) if a is not None else aid)
        return {"conversation_id": conv_id, "participants": names}
    return None


def _member_display(app, addr) -> str:
    """轻量取一个成员的展示名(花名/职务),不构造人格 prompt(给名册列表用)。"""
    role_reg = getattr(app.state, "role_registry", None)
    rid = (addr.agent_id or addr.role) or ""
    if role_reg is not None and rid:
        try:
            rv = role_reg.get(rid)
            if rv is not None and hasattr(rv, "display_name"):
                return rv.display_name()
        except Exception:
            pass
    return rid or addr.role or "角色"


def _roundtable_roster(app, peer) -> list:
    """这个群场能拉谁上桌(返回 [Address]):
    - 业务域群 → 本域的 agent(排除 user);
    - karvy world 大群(l0)→ 跨所有活跃域的 agent(去重)—— Hardy:大群里也能起圆桌。
    """
    from karvyloop.karvy.capability import is_karvy_peer
    dom_reg = getattr(app.state, "domain_registry", None)
    if dom_reg is None or peer is None:
        return []
    out, seen = [], set()
    try:
        if is_karvy_peer(peer.domain_id):
            for d in dom_reg.list_active():
                for a in dom_reg.resolve_members(d.id):
                    if a.role == "user":
                        continue
                    k = (a.domain_id, a.agent_id)
                    if k not in seen:
                        seen.add(k); out.append(a)
        else:
            for a in dom_reg.resolve_members(peer.domain_id):
                if a.role != "user":
                    out.append(a)
    except Exception as e:
        logger.warning(f"[roundtable] 取名册失败: {e}")
    return out


@router.get("/roundtable/roster")
def api_roundtable_roster(request: Request) -> dict[str, Any]:
    """圆桌可选参与者名册(随当前群场:大群=跨域全员,域群=本域)。前端勾选谁上桌。"""
    app = request.app
    mgr = getattr(app.state, "conversation_manager", None)
    dom_reg = getattr(app.state, "domain_registry", None)
    peer = mgr.current_peer() if mgr is not None else None
    if peer is None or getattr(peer, "role", "") != "group":
        return {"ok": False, "reason": "圆桌在群场里开(先切到大群或某个域群)", "members": []}
    members = []
    for a in _roundtable_roster(app, peer):
        dom = dom_reg.get(a.domain_id) if dom_reg is not None else None
        members.append({
            "agent_id": a.agent_id, "role": a.role, "domain_id": a.domain_id,
            "domain_name": getattr(dom, "name", "") if dom is not None else "",
            "display": _member_display(app, a),
        })
    return {"ok": True, "members": members}


def _roundtable_result_doc(result: dict) -> str:
    """把圆桌产出拼成"结果文档":结论为主 + 内部讨论附在后面。

    这份文档进 task_registry → 同步到工作台首页【流进来的料】卡;点卡看这份(结论+讨论),
    再"打开聊天"跳回群场追问小卡(Hardy:圆桌结果要回流首页,点击去聊天查看+追问)。
    """
    concl = (result.get("conclusion") or "").strip()
    parts = [concl or "(小卡未给出结论)"]
    tr = result.get("transcript") or []
    if tr:
        parts.append(f"\n\n---\n\n**内部讨论**({result.get('rounds', 0)} 轮):")
        for x in tr:
            parts.append(f"\n- **R{x.get('round')} · {x.get('speaker', '?')}**:{(x.get('text') or '').strip()}")
    return "".join(parts)


async def _host_moderate_call(gw, model_ref, topic, transcript, *, final):
    """小卡兼主持:防跑偏/防冷场(决定 continue/converge)+ 收敛产出。一次 gateway 调用。"""
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    convo = "\n".join(f"[{x['speaker']}] {x['text']}" for x in transcript) or "(还没人发言)"
    if final:
        sysp = ("你是圆桌主持人小卡。把下面这场围绕主题的讨论**收敛成一份简洁结论**"
                "(给老板看、并写进认知库)。抓住共识与关键分歧,给可用的产出。只输出结论本身。")
    else:
        sysp = ("你是圆桌主持人小卡,管三件事:明确主题、防跑偏、防冷场。看这场讨论:"
                "**够不够得出结论了**?够了只回一个词 CONVERGE;还值得再聊一轮回 CONTINUE。"
                "只回这一个词,别的不说。")
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gw.complete([{"role": "user", "content": f"主题:{topic}\n\n讨论:\n{convo}"}],
                                    [], ref, system=SystemPrompt(static=[sysp])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception:
        out = ""
    if final:
        return {"text": out.strip()}
    return {"action": "converge" if "CONVERGE" in out.upper() else "continue"}


async def _roundtable_clarify_opening(gw, model_ref, topic, member_names) -> str:
    """阶段0:小卡作主持,开讨论**前**先跟用户对齐目标(需求分析)——复述理解 + 2-3 个澄清问题。"""
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    sysp = ("你是圆桌主持人小卡。用户刚发起一个圆桌。开讨论**之前**,你的第一件事是**跟用户"
            "对齐目标(需求分析)**:用一两句复述你对主题的理解,然后问 2-3 个最关键的澄清问题"
            "(要分析什么、想要的产出/目标是什么、范围或约束)。**别开始讨论、别替成员发言**,只对齐。"
            "亲切、简洁、像主持人开场。")
    usr = f"圆桌主题:{topic}\n准备上桌的成员:{'、'.join(member_names) or '(待定)'}"
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gw.complete([{"role": "user", "content": usr}], [], ref,
                                    system=SystemPrompt(static=[sysp])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[roundtable] 对齐开场失败: {e}")
    return out.strip() or (f"我们先对齐一下「{topic}」:你最想分析的核心是什么?期望的产出/目标是?"
                           "有没有范围或约束?对齐清楚我就开始组织讨论。")


async def _roundtable_goal_summary(gw, model_ref, topic, align_text) -> str:
    """阶段0→1:把"小卡↔用户"的对齐对话收敛成一句**目标**,喂给即将上桌的成员。"""
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    sysp = ("你是圆桌主持人小卡。根据你和用户刚才对齐目标的对话,把这次圆桌**要解决的目标**"
            "收敛成一句清晰具体的话(给即将上桌的成员看,让他们围绕它讨论)。只输出这一句目标。")
    usr = f"主题:{topic}\n\n对齐对话:\n{align_text or '(用户未补充,按主题字面理解)'}"
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gw.complete([{"role": "user", "content": usr}], [], ref,
                                    system=SystemPrompt(static=[sysp])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[roundtable] 目标收敛失败: {e}")
    return out.strip() or topic


def _roundtable_members(app, peer, participants):
    """按勾选的成员从群名册取;空 → 全上桌。返回 [Address]。

    §2.6:寻址用 **(域, agent_id) 复合键**(`域::agent_id`)—— 同名角色跨域才能独立选中。
    兼容旧数据:也认裸 agent_id。
    """
    roster = _roundtable_roster(app, peer)
    if participants:
        chosen = set(participants)
        return [a for a in roster
                if f"{a.domain_id}::{a.agent_id}" in chosen or a.agent_id in chosen]
    return list(roster)


@router.post("/roundtable/start")
async def api_roundtable_start(req: RoundtableStartRequest, request: Request) -> dict[str, Any]:
    """圆桌阶段0(Hardy):**先对齐目标再讨论**。建圆桌对话 + 切进去 + 小卡发需求分析开场。

    不立刻拉成员讨论 —— 主持人(小卡)先跟你把目标聊清楚(goal);你对齐够了再点「开始讨论」
    (→ /roundtable/discuss)。圆桌是目标驱动的会,goal 里的信息拿到了才算结束。
    """
    app = request.app
    mgr = getattr(app.state, "conversation_manager", None)
    main_loop = getattr(app.state, "main_loop", None)
    dom_reg = getattr(app.state, "domain_registry", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    peer = mgr.current_peer() if mgr is not None else None
    if peer is None or getattr(peer, "role", "") != "group":
        return {"ok": False, "reason": "圆桌在群场里开(先切到大群或某个域群)"}
    if main_loop is None or dom_reg is None or gw is None or mgr is None:
        return {"ok": False, "reason": "未接 LLM(--no-llm?)"}
    members = _roundtable_members(app, peer, req.participants)
    if not members:
        return {"ok": False, "reason": "这个群里没有可上桌的角色(先去业务域入职 agent)"}
    member_names = [_member_display(app, a) for a in members]
    model_ref = rk.get("model_ref", "")
    # 建圆桌对话并切进去(你就落在圆桌窗里跟小卡对齐)。2b:主题太长 → LLM 精炼成短标题。
    _rt_title = await _refine_run_title(gw, model_ref, req.intent)
    conv = mgr.new_conversation(title=f"🎡 {_rt_title}")
    opening = await _roundtable_clarify_opening(gw, model_ref, req.intent, member_names)
    mgr.record_turn(f"🎡 发起圆桌:{req.intent}", opening, brain="slow")
    _roundtable_state(app)[conv.id] = {
        "topic": req.intent, "participants": [a.agent_id for a in members],
        "domain_id": peer.domain_id, "phase": "aligning",
    }
    _persist_roundtable_state(app)   # 落盘 → 重启续"开始讨论"
    return {"ok": True, "conversation_id": conv.id, "opening": opening,
            "participants": member_names, "topic": req.intent}


async def _roundtable_clarify_turn(gw, model_ref, topic, align_history, user_msg):
    """阶段0 对话式(Hardy:少按钮)—— 小卡看对齐对话 + 用户最新一句,判断够不够开始讨论了。

    返 (reply, ready)。ready=True → 目标已清楚(或用户说可以开始),小卡这就自己组织讨论;
    False → 还需澄清,继续问。小卡在末尾单独一行写 READY/ASK,这里解析后剥掉。
    """
    import re
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    sysp = ("你是圆桌主持人小卡,正在和用户**对齐圆桌目标**(还没开始讨论)。看已有对齐对话 + 用户最新一句,"
            "判断:目标是否**已清楚到可以组织成员讨论了**(或用户已明确表示可以开始)。"
            "够了 → 回一句简短的「好,我这就组织大家讨论」,并在**最后单独一行**只写 READY;"
            "还需澄清 → 继续问最关键的 1-2 点(必要时直接问「这样我可以开始了吗?」),最后单独一行只写 ASK。")
    usr = f"圆桌主题:{topic}\n\n对齐对话:\n{align_history or '(刚开始)'}\n\n用户最新一句:{user_msg}"
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gw.complete([{"role": "user", "content": usr}], [], ref,
                                    system=SystemPrompt(static=[sysp])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[roundtable] 对齐轮失败: {e}")
    text = out.strip()
    m = re.search(r"\n\s*(READY|ASK)\s*$", text)
    if m:
        ready = (m.group(1) == "READY")
        text = text[:m.start()].strip()
    else:
        ready = ("READY" in text) and ("ASK" not in text)
        text = re.sub(r"\b(READY|ASK)\b", "", text).strip()
    return (text or "（我再想想怎么帮你对齐）"), ready


async def _execute_roundtable_discussion(app, conversation_id: str) -> dict[str, Any]:
    """圆桌阶段1 执行核心(被 /discuss 和 对话式自动开始 复用):goal→成员群聊→收敛→产出→记录。"""
    mgr = getattr(app.state, "conversation_manager", None)
    main_loop = getattr(app.state, "main_loop", None)
    dom_reg = getattr(app.state, "domain_registry", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    st = _roundtable_state(app).get(conversation_id)
    if not st:
        return {"ok": False, "reason": "没有待讨论的圆桌"}
    peer = mgr.current_peer() if mgr is not None else None
    if peer is None or getattr(peer, "role", "") != "group":
        return {"ok": False, "reason": "请在圆桌窗里开始"}
    mgr.resume(peer, conversation_id)   # 确保结果追加进这条圆桌对话
    members = _roundtable_members(app, peer, st["participants"])
    if not members:
        return {"ok": False, "reason": "圆桌成员不在了(域里角色变动?)"}
    governance = mgr.governance_text() or ""
    ws = rk.get("workspace_root", "/")
    model_ref = rk.get("model_ref", "")
    topic = st["topic"]

    # 把"小卡↔你"的对齐对话收敛成一句 goal,喂给成员
    ctx = mgr.context_view() or ()
    align = "\n".join(
        ((f"你:{tn.user_intent}" if tn.user_intent else "")
         + (f"\n小卡:{tn.agent_response}" if tn.agent_response else "")).strip()
        for tn in ctx
    ).strip()
    goal = await _roundtable_goal_summary(gw, model_ref, topic, align)
    # Step 0(a):你的决策标准在**圆桌**里也生效(成员发言按你的标准对齐;fresh 只跳执行记忆,
    # 不跳你的标准 —— governance 显式传仍生效)。query=goal → 按相关性召回。
    from karvyloop.console.decision_wire import assemble_governance
    governance = assemble_governance(app, intent=goal, domain=(peer.domain_id or ""), base=governance)

    async def member_reply(addr, _topic, transcript):
        dom = dom_reg.get(addr.domain_id)
        persona, speaker = _persona_for_role_addr(app, addr, dom, ws)
        convo = "\n".join(f"[{x['speaker']}] {x['text']}" for x in transcript)
        intent = (f"圆桌目标:{goal}\n(原始主题:{topic})\n\n"
                  + (f"已有讨论:\n{convo}\n\n" if convo else "")
                  + "请你围绕**目标**,从你的职务/视角给出看法(简洁、有观点,别复述别人)。")
        outcome = await drive_in_tui(intent, main_loop, governance=governance,
                                     persona=persona, scope="domain", fresh=True,
                                     **_rk_model(rk, _model_for_role(app, addr.agent_id)))
        err = getattr(outcome, "error", "")
        # §0.7 P2:圆桌成员发言/缺席即时推送(实时看谁说了、谁没回应)
        await _push_step(app, task_id, addr.agent_id, speaker,
                         "failed" if err else "done", err)
        if err:
            return None
        return {"speaker": speaker, "text": (outcome.text or "").strip()}

    async def host_moderate(_topic, transcript, *, final):
        return await _host_moderate_call(gw, model_ref, goal, transcript, final=final)

    task_reg = getattr(app.state, "task_registry", None)
    task_id = (task_reg.start(who="🎡 圆桌", domain_id=peer.domain_id, role="group",
                              intent=f"🎡 {topic[:120]}") if task_reg is not None else None)
    from karvyloop.karvy.roundtable import run_roundtable_session
    # 50+ 大桌:全员上桌(封顶 64,防真·失控),但**并发只 6 路**——别 50 路同时打一把 key 截断。
    _seats = min(len(members), 64)
    try:
        result = await run_roundtable_session(goal, members, member_reply=member_reply,
                                              host_moderate=host_moderate, max_rounds=3,
                                              max_seats=_seats, concurrency=6)
    except Exception as e:
        if task_reg is not None and task_id is not None:
            task_reg.finish(task_id, error=str(e))
        logger.exception(f"[roundtable] 讨论异常: {e}")
        return {"ok": False, "reason": f"圆桌讨论失败: {e}"}
    result["topic"] = topic
    result["goal"] = goal
    result_doc = _roundtable_result_doc(result)
    if task_reg is not None and task_id is not None:
        task_reg.finish(task_id, result=result_doc)
        task_reg.set_conversation(task_id, conversation_id)   # 卡 → 跳回这条圆桌

    # 讨论产出追加进圆桌对话(带结构化负载 → 群聊串渲染)
    try:
        mgr.record_turn("🎡 圆桌讨论", result_doc, brain="slow", task_id=task_id or "",
                        data={"roundtable": {
                            "topic": topic, "goal": goal,
                            "transcript": result.get("transcript", []),
                            "conclusion": result.get("conclusion", ""),
                            "rounds": result.get("rounds", 0),
                            "converged": result.get("converged", False),
                        }})
    except Exception as e:
        logger.warning(f"[roundtable] 追加讨论记录失败: {e}")

    # 产出 → 认知库
    mem = getattr(app.state, "memory", None)
    if result.get("conclusion") and mem is not None:
        try:
            import time as _t
            from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
            from karvyloop.schemas.cognition import Belief
            # §2.6 认知两层:域群圆桌 → 域专属(私有)认知(applies.domain,只在本域召回);
            # l0 大群(跨域)圆桌 → 通用/共享层(无 applies)。
            _dom = getattr(peer, "domain_id", "") if peer is not None else ""
            applies = ({"domain": _dom, "role": "group"}
                       if _dom and _dom != KARVY_WORLD_DOMAIN else {})
            mem.write(Belief(
                content=f"圆桌「{topic[:40]}」结论:{result['conclusion'][:600]}",
                provenance={"source": "roundtable", "kind": "fact",
                            "topic": topic[:80], "applies": applies},
                freshness_ts=_t.time(), scope="personal"))
        except Exception as e:
            logger.warning(f"[roundtable] 结论写认知失败: {e}")

    st["phase"] = "done"
    _persist_roundtable_state(app)
    return {"ok": True, "conversation_id": conversation_id, **result}


@router.post("/roundtable/discuss")
async def api_roundtable_discuss(req: RoundtableDiscussRequest, request: Request) -> dict[str, Any]:
    """圆桌阶段1(手动备用入口):直接开始讨论。常态走对话式 /align 自动开始。"""
    app = request.app
    st = _roundtable_state(app).get(req.conversation_id)
    if not st or st.get("phase") == "done":
        return {"ok": False, "reason": "没有待讨论的圆桌(可能已开过,或服务重启丢了待办)"}
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    if rk.get("gateway") is None or getattr(app.state, "main_loop", None) is None:
        return {"ok": False, "reason": "未接 LLM(--no-llm?)"}
    return await _execute_roundtable_discussion(app, req.conversation_id)


class RoundtableAlignRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=64)
    message: str = Field(..., min_length=1, max_length=4000)


@router.post("/roundtable/align")
async def api_roundtable_align(req: RoundtableAlignRequest, request: Request) -> dict[str, Any]:
    """圆桌阶段0 对话式(Hardy:少按钮)—— 你跟小卡聊;小卡判断聊清了就**自己开始讨论**(或直接问你)。

    你的每句对齐消息走这里(不走通用 /intent)。小卡 ready → 内联跑讨论、连结果一起返(前端渲群聊串)。
    """
    app = request.app
    mgr = getattr(app.state, "conversation_manager", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    st = _roundtable_state(app).get(req.conversation_id)
    if not st or st.get("phase") == "done":
        return {"ok": False, "reason": "这条圆桌不在对齐中(可能已开始或结束)"}
    if gw is None or mgr is None or getattr(app.state, "main_loop", None) is None:
        return {"ok": False, "reason": "未接 LLM(--no-llm?)"}
    peer = mgr.current_peer()
    if peer is None or getattr(peer, "role", "") != "group":
        return {"ok": False, "reason": "请在圆桌窗里对齐"}
    mgr.resume(peer, req.conversation_id)
    ctx = mgr.context_view() or ()
    align = "\n".join(
        ((f"你:{tn.user_intent}" if tn.user_intent else "")
         + (f"\n小卡:{tn.agent_response}" if tn.agent_response else "")).strip()
        for tn in ctx).strip()
    reply, ready = await _roundtable_clarify_turn(gw, rk.get("model_ref", ""), st["topic"], align, req.message)
    try:
        mgr.record_turn(req.message, reply, brain="slow")   # 这轮对齐进圆桌对话
    except Exception:
        pass
    if not ready:
        return {"ok": True, "reply": reply, "started": False}
    # 小卡判定可以开始 → 内联跑讨论,连结果返回(前端先显 reply,再渲群聊串)
    result = await _execute_roundtable_discussion(app, req.conversation_id)
    return {"ok": True, "reply": reply, "started": True, "result": result}


def _concept_cache(app):
    cc = getattr(app.state, "concept_cache", None)
    if cc is None:
        import pathlib
        from karvyloop.cognition.concepts import ConceptCache
        cfgp = getattr(app.state, "config_path", "") or ""
        base = pathlib.Path(cfgp).parent if cfgp else (pathlib.Path.home() / ".karvyloop")
        cc = ConceptCache(base / "concept_cache.json")
        app.state.concept_cache = cc
    return cc


@router.get("/memory/graph")
async def api_memory_graph(request: Request) -> dict[str, Any]:
    """认知图谱(ch4 pillar 3):**语义边** —— LLM 抽每条的概念(沉淀/查图时抽,缓存),共享概念=边。

    Hardy 选 B(LLM wiki 式互链,非 embedding)。抽过的看图零 LLM;没概念的 belief 回退词面边。
    """
    app = request.app
    mem = getattr(app.state, "memory", None)
    if mem is None:
        return {"nodes": [], "edges": []}
    from karvyloop.cognition.graph import concept_graph
    beliefs = mem.index.all("personal")
    contents = [getattr(b, "content", "") for b in beliefs]
    cache = _concept_cache(app)
    concepts, missing = cache.resolve(contents)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if missing and gw is not None:
        from karvyloop.cognition.concepts import extract_concepts_batch
        try:
            extracted = await extract_concepts_batch([contents[i] for i in missing],
                                                     gateway=gw, model_ref=rk.get("model_ref", ""))
            for k, i in enumerate(missing):
                cs = extracted[k] if k < len(extracted) else []
                concepts[i] = cs
                cache.put(contents[i], cs)   # 编译一次,下次零 LLM
        except Exception as e:
            logger.warning(f"[graph] 概念抽取失败,回退词面: {e}")
    concepts = [c or [] for c in concepts]
    return concept_graph(beliefs, concepts)


async def maybe_auto_distill(app: Any, mgr: Any) -> Optional[dict]:
    """轮后自动蒸馏(loop step4b):攒够 N 轮未蒸馏 → 把新轮编译成 Belief 写进长期库。

    复用 4b-1 编译器(经 auto_distill.distill_turns)。fire-and-forget 调,**异步晚跑**,故须防:
    - **并发重复蒸**(每轮都 schedule 一个 task):per-conv in-flight 闸 + watermark 在 await 前
      **乐观推进**(单调,`max`)→ 第二个 task 看到已推进/在飞 → 跳过。
    - **TOCTOU**:slice 端点 `end` 只读一次,watermark 推进到 end(不回读 len)。
    - **失败 hammer**:推进后**不回退**(失败该批跳过 + 记日志),否则坏 gateway 每轮重试烧钱。
    - **隐私/隔离**:只蒸**私聊(l0)**进 personal;业务域对话不混进个人库(personal/domain
      路径隔离硬规则)。
    无 memory/gateway/对话 → 跳过。返回 {"written":N};无动作返 None。
    """
    try:
        mem = getattr(app.state, "memory", None)
        if mem is None or mgr is None:
            return None
        rk = getattr(app.state, "runtime_kwargs", None) or {}
        gw = rk.get("gateway")
        if gw is None:
            return None
        conv = mgr.current() if hasattr(mgr, "current") else None
        if conv is None or not getattr(conv, "turns", None):
            return None
        # 只蒸私聊(l0)→ personal;业务域对话不混进个人库
        from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
        peer = getattr(conv, "peer", None)
        if peer is not None and getattr(peer, "domain_id", KARVY_WORLD_DOMAIN) != KARVY_WORLD_DOMAIN:
            return None
        from karvyloop.cognition.auto_distill import should_distill, distill_turns_with_decisions
        marks = getattr(app.state, "distill_watermarks", None)
        if marks is None:
            marks = app.state.distill_watermarks = {}
        inflight = getattr(app.state, "_distill_inflight", None)
        if inflight is None:
            inflight = app.state._distill_inflight = set()
        n = len(conv.turns)
        wm = marks.get(conv.id, 0)
        if not should_distill(n, wm) or conv.id in inflight:
            return None
        end = n                                  # slice 端点只读一次(防 TOCTOU)
        new_turns = list(conv.turns[wm:end])
        inflight.add(conv.id)
        marks[conv.id] = max(wm, end)            # await 前乐观推进(单调;防并发重复蒸)
    except Exception as e:
        logger.warning(f"[auto_distill] 准备阶段异常(跳过本轮): {e}")  # 不静默吞,留诊断信号
        return None
    try:
        # §11 P1b:同一次 LLM 调用 piggyback —— 抽 facts(写记忆)+ decisions(显式陈述源)。
        res, decisions = await distill_turns_with_decisions(
            new_turns, gateway=gw, mem=mem, model_ref=rk.get("model_ref", ""))
        if decisions:
            try:
                from karvyloop.console.decision_wire import crystallize_candidates
                # 聊天来源 = 私聊小卡 → 全局(ctx 空);走双关门(显式 1 次/隐式跨批复现)。
                await crystallize_candidates(app, decisions)
            except Exception as e:
                logger.debug(f"[auto_distill] 决策偏好结晶失败(不影响蒸馏): {e}")
        return {"written": res.written}
    except Exception as e:
        # 已推进 watermark,不回退 → 失败只跳过该批,不每轮重试 hammer LLM
        logger.warning(f"[auto_distill] 蒸馏失败(该批跳过): {e}")
        # §0.7 fail-loud:后台蒸馏失败不再只 log 静默死,主动 push 给 UI(灭死角)
        try:
            from karvyloop.console.task_events import schedule_system_error
            schedule_system_error(app, "auto_distill", str(e))
        except Exception:
            pass
        return None
    finally:
        inflight.discard(conv.id)


def schedule_auto_distill(app: Any, mgr: Any) -> None:
    """fire-and-forget 调度轮后自动蒸馏(不阻塞对话响应)。保 task 引用防被 GC。"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # 无事件循环(同步上下文)→ 跳过
    tasks = getattr(app.state, "_distill_tasks", None)
    if tasks is None:
        tasks = app.state._distill_tasks = set()
    task = loop.create_task(maybe_auto_distill(app, mgr))
    tasks.add(task)

    def _on_done(t: Any) -> None:
        tasks.discard(t)
        # §0.7 fail-loud:防 maybe_auto_distill 之外逃逸的异常静默死(防御性兜底)
        try:
            exc = t.exception()
        except Exception:
            return  # cancelled / 取结果失败 → 不处理
        if exc is not None:
            logger.error(f"[auto_distill] 后台任务逃逸异常: {exc}")
            try:
                from karvyloop.console.task_events import schedule_system_error
                schedule_system_error(app, "auto_distill", str(exc))
            except Exception:
                pass

    task.add_done_callback(_on_done)


# ---- 9.5 #3-P1:公共原子库 + 角色库管理面 API ----

class AtomCreateRequest(BaseModel):
    atom_id: str = Field(..., min_length=1, max_length=64)   # 名字(COMPOSITION-safe)
    kind: str = Field(default="task")                        # task / daemon
    prompt: str = Field(default="", max_length=8000)
    tools: list[str] = Field(default_factory=list)
    model: Optional[str] = Field(default=None, max_length=128)


def _atom_to_dict(a) -> dict[str, Any]:
    return {"id": a.id, "kind": a.kind, "prompt": a.prompt,
            "tools": list(a.tools), "model": a.model,
            "is_read_only": a.is_read_only}


@router.get("/atoms")
def api_atoms(request: Request) -> dict[str, Any]:
    """列公共原子库。"""
    reg = getattr(request.app.state, "atom_registry", None)
    if reg is None:
        return {"atoms": []}
    return {"atoms": [_atom_to_dict(a) for a in reg.list_all()]}


@router.post("/atom/create")
def api_atom_create(req: AtomCreateRequest, request: Request) -> dict[str, Any]:
    """建一个原子入公共库(就地买糖也走这个)。"""
    reg = getattr(request.app.state, "atom_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 atom_registry"}
    try:
        a = reg.create(req.atom_id, req.kind, req.prompt,
                       tools=list(req.tools), model=req.model)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"建原子失败:{e}")
    return {"ok": True, "atom": _atom_to_dict(a)}


class AtomRemoveRequest(BaseModel):
    atom_id: str = Field(..., min_length=1, max_length=64)


@router.post("/atom/remove")
def api_atom_remove(req: AtomRemoveRequest, request: Request) -> dict[str, Any]:
    reg = getattr(request.app.state, "atom_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 atom_registry"}
    return {"ok": reg.remove(req.atom_id)}


class RoleCreateRequest(BaseModel):
    role_id: str = Field(..., min_length=1, max_length=64)
    identity: str = Field(default="", max_length=8000)
    soul: str = Field(default="", max_length=8000)          # 9.5:性格/原则(SOUL.md)
    user_desc: str = Field(default="", max_length=8000)     # 9.5:服务对象(USER.md)
    atom_ids: list[str] = Field(default_factory=list)       # 从公共原子库挑(甲:用不拥有)
    skill_ids: list[str] = Field(default_factory=list)      # 从技能库引用(随身技能;绑定即生效)
    nickname: str = Field(default="", max_length=64)        # brick4:花名(进某域的人名)
    title: str = Field(default="", max_length=64)           # brick4:职务
    model: str = Field(default="", max_length=64)           # 角色级模型(空=默认;软默认层叠)


def _role_to_dict(v) -> dict[str, Any]:
    return {"id": v.id, "identity": v.identity, "atom_ids": list(v.atom_ids),
            "skill_ids": list(getattr(v, "skill_ids", [])),
            "nickname": getattr(v, "nickname", ""), "title": getattr(v, "title", ""),
            "model": getattr(v, "model", ""),
            "display_name": v.display_name() if hasattr(v, "display_name") else v.id}


@router.get("/models")
def api_models(request: Request) -> dict[str, Any]:
    """全局可用模型列表(给 role/agent 选模型用)。default = agents.defaults.model。"""
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    reg = getattr(gw, "reg", None)
    if reg is None:
        return {"models": [], "default": ""}
    try:
        models = [{"id": mid, "name": getattr(md, "name", mid)} for mid, md in reg.models.items()]
        return {"models": models, "default": getattr(reg, "default_chat", "")}
    except Exception:
        return {"models": [], "default": ""}


@router.get("/roles")
def api_roles(request: Request) -> dict[str, Any]:
    """列角色库。"""
    reg = getattr(request.app.state, "role_registry", None)
    if reg is None:
        return {"roles": []}
    return {"roles": [_role_to_dict(v) for v in reg.list_all()]}


@router.post("/role/create")
def api_role_create(req: RoleCreateRequest, request: Request) -> dict[str, Any]:
    """建一个角色镜像(物化 7 文件 + COMPOSITION;引的原子须在公共库)。"""
    reg = getattr(request.app.state, "role_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 role_registry"}
    try:
        v = reg.create(req.role_id, identity=req.identity, soul=req.soul,
                       user_desc=req.user_desc, atom_ids=list(req.atom_ids),
                       skill_ids=list(req.skill_ids),
                       nickname=req.nickname, title=req.title, model=req.model)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"建角色失败:{e}")
    return {"ok": True, "role": _role_to_dict(v)}


class RoleRemoveRequest(BaseModel):
    role_id: str = Field(..., min_length=1, max_length=64)
    force: bool = False   # §2.6:被业务域引用时,force=true 才真删(确认过)


def _domains_referencing_role(app, role_id: str) -> list:
    """§2.6 引用守护:哪些业务域的 member_query 引用了这个角色(agent:X / role:X)。"""
    reg = getattr(app.state, "domain_registry", None)
    if reg is None:
        return []
    out = []
    try:
        for d in reg.list_all():
            q = getattr(d, "member_query", "") or ""
            if f"agent:{role_id}" in q or f"role:{role_id}" in q:
                out.append({"id": d.id, "name": getattr(d, "name", d.id)})
    except Exception:
        pass
    return out


@router.post("/role/remove")
def api_role_remove(req: RoleRemoveRequest, request: Request) -> dict[str, Any]:
    reg = getattr(request.app.state, "role_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 role_registry"}
    # §2.6 引用守护:被某域引用 → 先拦+告诉是哪些域(别留悬空);force 才真删。
    refs = _domains_referencing_role(request.app, req.role_id)
    if refs and not req.force:
        return {"ok": False, "blocked": True, "referenced_by": refs,
                "reason": f"该角色被 {len(refs)} 个业务域引用,确认仍删?(删后这些域将引用不到它)"}
    return {"ok": reg.remove(req.role_id), "referenced_by": refs}


class RoleUpdateRequest(BaseModel):
    role_id: str = Field(..., min_length=1, max_length=64)
    identity: Optional[str] = Field(default=None, max_length=8000)   # None=不改人格
    model: Optional[str] = Field(default=None, max_length=128)       # None=不改模型
    skill_ids: Optional[list[str]] = Field(default=None)             # None=不改随身技能


@router.post("/role/update")
def api_role_update(req: RoleUpdateRequest, request: Request) -> dict[str, Any]:
    """编辑角色(P0 审计:此前写错只能删重建)。改 identity(人格)/ model / 随身技能。"""
    reg = getattr(request.app.state, "role_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 role_registry"}
    try:
        rv = reg.update(req.role_id, identity=req.identity, model=req.model,
                        skill_ids=req.skill_ids)
    except Exception as e:  # UnknownSkillError 等 → 422(引了不存在的技能)
        raise HTTPException(status_code=422, detail=f"改角色失败:{e}")
    if rv is None:
        return {"ok": False, "reason": f"角色「{req.role_id}」不存在"}
    return {"ok": True, "role_id": req.role_id}


# ---- 9.5:外部 Agent 导入(按 KarvyLoop 范式改造 → 落角色库)----

class AgentImportRequest(BaseModel):
    role_id: str = Field(..., min_length=1, max_length=64)          # 落库后的角色名
    source_type: str = Field(default="generic-json", max_length=32)  # claude/codex/openclaw-hermes/generic-json
    system_prompt: str = Field(default="", max_length=16000)
    tools: list[str] = Field(default_factory=list)


@router.post("/agent/import")
async def api_agent_import(req: AgentImportRequest, request: Request) -> dict[str, Any]:
    """把外部 agent 导入成 KarvyLoop 资产。

    **M3 LLM 拆解(docs/14 §10,Hardy 2026-06-26 拍)**:有 LLM 时,先跑一次拆解
    (agent → 真人设 role + 公共原子库里的可复用 atom + 识别内含 skill),**耗 token**;
    tools 不再是 COMPOSITION 里的死字符串,而是落成原子(任何角色都能复用)。
    **降级**:无 LLM(--no-llm)/ 拆解失败(宁空勿毒返 None)→ 回退 v0 确定性 adapter
    (五段 Source→Map→Plan→Apply→Validate,套模板写 7 文件、0 原子、0 token)。
    """
    app = request.app
    reg = getattr(app.state, "role_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 role_registry"}
    rid = (req.role_id or "").strip()
    if not rid:
        raise HTTPException(status_code=422, detail="role_id 不能为空")
    # 字符集前置校验(对齐 RoleRegistry._ROLE_ID_RE):否则 "a.b" 这类绕过下面的存在性检查,
    # 等拆解+建原子落盘后才在 reg.create 崩 → 原子孤儿留在公共池(独立对抗验收 Defect 2)。
    if not re.match(r"^[\w\-]+$", rid):
        raise HTTPException(status_code=422, detail="role_id 只能含字母/数字/下划线/连字符")
    if (reg.root / rid).exists():
        raise HTTPException(status_code=422, detail=f"角色「{rid}」已存在")

    from karvyloop.adapter import discover_manifest
    from karvyloop.adapter.source import ManifestError
    payload = {
        "system_prompt": req.system_prompt,
        "tools": [{"name": t} for t in req.tools if t],
        "agent_name": rid,
    }
    try:
        manifest = discover_manifest(req.source_type, payload, source_path="<console-import>")
    except ManifestError as e:                  # J1:缺 system_prompt/tools → 拒收(422 非 500)
        raise HTTPException(status_code=422,
                            detail=f"缺 system_prompt 或 tools(外部 agent 至少要有这两样):{e}")
    if not manifest.is_minimal():
        raise HTTPException(status_code=422,
                            detail="缺 system_prompt 或 tools(外部 agent 至少要有这两样)")

    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    atom_reg = getattr(app.state, "atom_registry", None)

    # ---- M3 路径:有 LLM + 原子库 → 拆解 → 落原子 → 建带原子引用的 role ----
    if gw is not None and atom_reg is not None:
        try:
            from karvyloop.adapter.bootstrap import bootstrap_decompose
            with _token_src("agent_import"):     # 让导入拆解的 token 进账本 + 归到 agent_import 源
                decomp = await bootstrap_decompose(
                    manifest, existing_atom_ids=[a.id for a in atom_reg.list_all()],
                    gateway=gw, model_ref=rk.get("model_ref", ""))
        except Exception as e:  # noqa: BLE001 — 拆解任何异常都降级,不让导入崩
            decomp = None
            logger.warning(f"[agent/import] LLM 拆解失败,降级 v0: {e}")
        if decomp is not None and decomp.is_valid():
            atoms_created: list[str] = []
            for ap in decomp.atoms:
                if atom_reg.get(ap.id) is not None:
                    continue                     # 复用已有(甲:用不拥有)
                try:
                    atom_reg.create(ap.id, ap.kind, ap.purpose, tools=list(ap.tools))
                    atoms_created.append(ap.id)
                except Exception as e:  # noqa: BLE001 — 单个原子建失败不阻断,跳过
                    logger.warning(f"[agent/import] 原子 {ap.id} 建失败: {e}")
            atom_ids = [ap.id for ap in decomp.atoms if atom_reg.get(ap.id) is not None]
            # 技能:只绑**确认在库**的(识别到但没导入的只报不绑 → 不谎称角色拥有它,也不触 UnknownSkillError)
            known = reg._known_skills() if hasattr(reg, "_known_skills") else None
            bind_skills = [s for s in decomp.skills if known is not None and s in known]
            try:
                reg.create(rid, identity=decomp.identity, soul=decomp.soul,
                           atom_ids=atom_ids, skill_ids=bind_skills)
            except Exception as e:  # noqa: BLE001
                # 回滚本次新建的原子(不留孤儿在公共池);复用的已有原子不动(Defect 2 防御纵深)
                for aid in atoms_created:
                    try:
                        atom_reg.remove(aid)
                    except Exception:  # noqa: BLE001
                        pass
                raise HTTPException(status_code=422, detail=f"拆解出原子但建角色失败:{e}")
            return {
                "ok": True, "role_id": rid, "decomposed": True,
                "atoms": atom_ids, "atoms_created": atoms_created,
                "skills_recognized": list(decomp.skills), "skills_bound": bind_skills,
                "identity": decomp.identity,
            }
        # decomp 为 None/无效 → 落到 v0 降级

    # ---- v0 降级:确定性 adapter(无 LLM 或拆解失败)----
    try:
        from karvyloop.adapter import apply_plan, build_plan, validate_with_loader
        target = str(reg.root / rid)
        plan = build_plan(manifest, target)
        if not plan.can_apply:
            warns = [w for s in plan.slots for w in (s.warnings or [])]
            raise HTTPException(status_code=422, detail=f"需要你先 review(改造有疑点):{warns}")
        result = apply_plan(plan, manifest, target)
        validation = validate_with_loader(plan, target)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"导入失败:{e}")
    return {
        "ok": True, "role_id": rid, "decomposed": False,
        "note": "未接 LLM 或拆解未成 → 走确定性 adapter(tools 仅列名,未出原子)",
        "written": list(getattr(result, "written", [])),
        "valid": bool(getattr(validation, "is_valid", True)),
    }


def _detect_domain_skill_conflicts(app, domain, agent: str) -> list[dict[str, Any]]:
    """D4 live:检 (agent, 新域) 下全局技能 × 域治理的冲突,注册 resolve_conflict PROPOSE。

    返回 [{proposal_id, summary}, ...](回给前端展示)。任何缺件(无 main_loop /
    无技能 / 无规则)→ 返空,不报错。默认保守 judge(离线);LLM judge 后插。
    """
    import time as _time
    from karvyloop.domain.skill_conflict import (
        SkillDomainConflictDetector, SkillView, rules_from_domain,
    )
    from karvyloop.karvy.proposal_registry import proposal_from_conflict

    ml = getattr(app.state, "main_loop", None)
    idx = getattr(ml, "skill_index", None) if ml is not None else None
    if idx is None:
        return []
    skills = [
        SkillView(name=e.name, sig=e.sig, text=(e.when_to_use or e.name))
        for e in idx.all()
    ]
    if not skills:
        return []
    rules = rules_from_domain(domain.deontic, domain.value_md)
    if not rules:
        return []

    detector = SkillDomainConflictDetector()  # 默认保守 judge
    value_version = str(getattr(domain.value_md, "text", ""))[:32]
    found = detector.detect(
        role=agent, domain_id=domain.id, value_version=value_version,
        skills=skills, rules=rules,
    )
    registry = getattr(app.state, "proposal_registry", None)
    ts = _time.time()
    out: list[dict[str, Any]] = []
    for c in found:
        p = proposal_from_conflict(c, ts=ts)
        if registry is not None:
            registry.register(p)
        out.append({"proposal_id": p.proposal_id, "summary": c.summary()})
    return out


# ---- /api/peers (9.2b:可对话对象 — 私聊小卡 + 各业务域角色) ----

@router.get("/peers")
def api_peers(request: Request) -> dict[str, Any]:
    """列可对话对象(场+角色):私聊小卡(l0)+ 各 active 业务域 resolve_members 的角色。

    K4 只读(读 registry,不改)。无 registry → 仅私聊小卡。
    """
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN

    peers: list[dict[str, Any]] = [{
        "domain_id": KARVY_WORLD_DOMAIN, "domain_name": "karvy world(私聊)",
        "role": "observer", "agent_id": "karvy",
        "label": "🏠 私聊小卡", "is_private": True,
    }, {
        # ch4 KarvyChat:Karvy World 大群(小卡协调你所有 Agent)。is_world:前端用它出本地化标题。
        "domain_id": KARVY_WORLD_DOMAIN, "domain_name": "Karvy World",
        "role": "group", "agent_id": "",
        "label": "👥 Karvy World 大群", "is_group": True, "is_private": False, "is_world": True,
    }]
    reg = getattr(request.app.state, "domain_registry", None)
    if reg is not None:
        try:
            for d in reg.list_active():
                # 域群:小卡协调该域全体成员
                peers.append({
                    "domain_id": d.id, "domain_name": d.name,
                    "role": "group", "agent_id": "",
                    "label": f"👥 {d.name} 域群", "is_group": True, "is_private": False,
                })
                for addr in reg.resolve_members(d.id):
                    if addr.role == "user":
                        continue  # 用户自己不是"对话对象"
                    peers.append({
                        "domain_id": d.id, "domain_name": d.name,
                        "role": addr.role, "agent_id": addr.agent_id,
                        "label": f"🏢 {d.name} / {addr.role}"
                                 + (f"·{addr.agent_id}" if addr.agent_id else ""),
                        "is_private": False,
                    })
        except Exception as e:
            logger.warning(f"api_peers 列业务域成员失败(仅返私聊): {e}")
    # 每个对象标注"最近沟通时间"(供左栏:私聊/群聊各自按最近沟通排序;
    # 没私聊过的 agent 前端隐藏,没沟通过的群聊仍显示)。无对话编排器 → 全 None。
    last_active: dict[str, float] = {}
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is not None:
        try:
            # 跨**所有** peer 扫(不能只看当前场,否则别的 agent 永远无 last_active → 左栏不显)
            for m in mgr.all_conversation_metas():
                k = f"{m.peer.domain_id}|{m.peer.role}|{m.peer.agent_id or ''}"
                la = m.last_active_at or 0.0
                if la > last_active.get(k, 0.0):
                    last_active[k] = la
        except Exception as e:
            logger.warning(f"api_peers 标注最近沟通失败(降级无时序): {e}")
    for p in peers:
        k = f"{p['domain_id']}|{p['role']}|{p.get('agent_id') or ''}"
        p["last_active_at"] = last_active.get(k)   # None = 从没沟通过
    # 2f:X 掉的私聊从左栏隐藏(记录还在,重新切到它会自动恢复;小卡置顶不可隐藏)。
    # 群(结构性)不隐藏 —— UI 不给群 X,这里也不滤它们。
    peers = [p for p in peers
             if p.get("is_group") or not _is_line_hidden(request.app, p["domain_id"], p["role"],
                                                          p.get("agent_id") or "")]
    return {"peers": peers}


class PeerSwitchRequest(BaseModel):
    domain_id: str = Field(..., min_length=1, max_length=64)
    role: str = Field(..., min_length=1, max_length=64)
    agent_id: Optional[str] = Field(default=None, max_length=64)


@router.post("/peer/switch")
def api_peer_switch(req: PeerSwitchRequest, request: Request) -> dict[str, Any]:
    """切到某「场+角色」(CV-13:切场 = 独立上下文线)。返该线当前对话 + 历史轮。"""
    from karvyloop.domain.registry import Address

    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"ok": False, "reason": "未接对话编排器"}
    peer = Address(domain_id=req.domain_id, role=req.role, agent_id=req.agent_id)
    conv = mgr.set_peer(peer)
    # 2c:重开一条线(含从料里点追问)→ 自动恢复显示(把它从隐藏集移除),让卡重新回左栏
    _set_line_hidden(request.app, req.domain_id, req.role, req.agent_id or "", False)
    return {
        "ok": True,
        "domain_id": peer.domain_id, "role": peer.role, "agent_id": peer.agent_id,
        "conversation_id": conv.id, "turn_count": conv.turn_count,
        "turns": [
            {"user_intent": t.user_intent, "agent_response": t.agent_response,
             "brain": t.brain, "task_id": t.task_id, "data": t.data}
            for t in conv.turns
        ],
        "roundtable_pending": _roundtable_pending(request.app, conv.id),
    }


# ---- /api/conversation/* (9.1d:对话 — ➕新对话 / 🕘历史 / resume) ----

def _conv_meta_to_dict(m) -> dict[str, Any]:
    return {
        "id": m.id, "title": m.title, "created_at": m.created_at,
        "last_active_at": m.last_active_at, "turn_count": m.turn_count,
        "domain_id": m.peer.domain_id, "peer_role": m.peer.role,
        "peer_agent_id": m.peer.agent_id,
    }


@router.get("/conversations")
def api_conversations(request: Request) -> dict[str, Any]:
    """历史对话列表(0.1.0 刚需,按 last_active 倒序;K4 只读)。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"conversations": [], "current_id": None}
    metas = mgr.list_conversations()
    cur = mgr.current()
    return {
        "conversations": [_conv_meta_to_dict(m) for m in metas],
        "current_id": cur.id if cur is not None else None,
    }


@router.post("/conversation/new")
def api_conversation_new(request: Request) -> dict[str, Any]:
    """开新对话(CV-2 唯一边界;旧对话摘要喂 Trace CV-4)。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"id": None, "reason": "未接对话编排器"}
    conv = mgr.new_conversation()
    return {"id": conv.id, "title": conv.title, "turn_count": conv.turn_count}


class ResumeRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=64)


@router.post("/conversation/resume")
def api_conversation_resume(req: ResumeRequest, request: Request) -> dict[str, Any]:
    """从历史 resume 一段对话(0.1.0 刚需)。找不到 → 404。"""
    mgr = getattr(request.app.state, "conversation_manager", None)
    if mgr is None:
        return {"id": None, "reason": "未接对话编排器"}
    # 9.2a:resume 需 (peer, id);0.1.0 console 在当前 peer 内 resume(场切换留 9.2b)
    peer = mgr.current_peer()
    conv = mgr.resume(peer, req.conversation_id) if peer is not None else None
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {
        "id": conv.id, "title": conv.title, "turn_count": conv.turn_count,
        "turns": [
            {"user_intent": t.user_intent, "agent_response": t.agent_response,
             "brain": t.brain, "task_id": t.task_id, "data": t.data}
            for t in conv.turns
        ],
        "roundtable_pending": _roundtable_pending(request.app, conv.id),
    }


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


# ---- /api/h2a_decide (K5 强校验) ----

class H2ADecideRequest(BaseModel):
    proposal_id: str = Field(..., min_length=1, max_length=512)
    decision: str = Field(..., pattern="^(ACCEPT|REJECT|DEFER)$")
    reason: str = Field(default="", max_length=2000)
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
    if req.decision == H2A_REJECT and not req.reason.strip():
        eff_reason = DEFAULT_REJECT_REASON

    decision_obj = H2ADecision(
        decision=req.decision,
        reason=eff_reason,
        proposal_id=req.proposal_id,
        user_address=user_addr,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # D5:按 kind 兑现(若接了 registry)。reason 可选,不拦 REJECT。
    def _dispatch() -> dict[str, Any] | None:
        registry = getattr(request.app.state, "proposal_registry", None)
        if registry is None:
            return None
        handlers = getattr(request.app.state, "proposal_handlers", None) or {}
        res = registry.decide(req.proposal_id, req.decision, handlers=handlers)
        return res.to_dict() if res is not None else None

    if req.decision == H2A_DEFER:
        # K5:DEFER 不发 envelope,返 null;D5:挂起(留 registry,下次再呈现)
        return {"envelope": None, "decision": req.decision, "dispatch": _dispatch()}

    # K5 唯一 Envelope 构造路径(REJECT 的空 reason 已在上面补成占位,A8 不破)
    env = decision_to_envelope(decision_obj, to_addr)
    from karvyloop.console.proposal_handlers import pop_report_card
    return {
        "envelope": envelope_to_dict(env),
        "decision": req.decision,
        "dispatch": _dispatch(),  # D5:ACCEPT 兑现结果 / REJECT 丢弃回执(handler 内会 stash 回报卡)
        # 执行后回报卡:兑现跑了独立验收 → 把"它到底验过没"翻成卡(grounded ✓ 的自然产地)
        "report_card": pop_report_card(request.app, req.proposal_id),
    }


# ---- /api/lang (9.4:语言偏好读/写,持久到 config.yaml)----

class LangRequest(BaseModel):
    lang: str = Field(..., pattern="^(en|zh)$")


# ---- 全局模型配置增删改查(Hardy:模型是全局配置,要有管理入口)----

def _model_cfg_path(app):
    return getattr(app.state, "config_path", "") or None


def _reload_gateway_registry(app) -> tuple[bool, str]:
    """改完 config.yaml → 热替换内存里的 ModelRegistry(下次 LLM 调用即生效,不必重启)。"""
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    cfgp = _model_cfg_path(app)
    if gw is None or not cfgp:
        return False, "无 gateway 或无 config 路径(--no-llm?)"
    try:
        from karvyloop.gateway.registry import ModelRegistry
        gw.reg = ModelRegistry.load(cfgp)
        return True, ""
    except Exception as e:
        # 配置已落盘,但新配置过不了校验(如默认模型被删)→ 不热替换,提示重启/修正
        return False, f"配置已保存,但热加载失败(检查默认模型/必填项;重启也会校验):{e}"


@router.get("/model/config")
def api_model_config(request: Request) -> dict[str, Any]:
    """全局模型管理视图(密钥遮罩 + 默认标记 + provider 列表 + 合法 api 列表)。"""
    cfgp = _model_cfg_path(request.app)
    if not cfgp:
        return {"models": [], "no_llm": True}
    from karvyloop.gateway.config_models import list_models
    try:
        return list_models(cfgp)
    except Exception as e:
        return {"models": [], "reason": f"读配置失败:{e}"}


class ModelSaveRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    model_id: str = Field(..., min_length=1, max_length=128)
    model_name: str = Field(default="", max_length=128)
    api: str = Field(..., max_length=32)
    role: str = Field(default="chat", max_length=16)
    base_url: str = Field(default="", max_length=256)
    api_key: str = Field(default="", max_length=512)      # 留空/遮罩串=保留原值
    auth_header: str = Field(default="", max_length=32)
    messages_path: str = Field(default="", max_length=128)
    context_window: int = Field(default=200000, ge=0)
    max_tokens: int = Field(default=8192, ge=0)
    reasoning: bool = False


@router.post("/model/save")
def api_model_save(req: ModelSaveRequest, request: Request) -> dict[str, Any]:
    """新增/编辑全局模型(写 config.yaml + 热加载注册表)。密钥留空=保留原值。"""
    cfgp = _model_cfg_path(request.app)
    if not cfgp:
        return {"ok": False, "reason": "未接 config(--no-llm?)"}
    from karvyloop.gateway.config_models import upsert_model
    ok, reason = upsert_model(req.model_dump(), cfgp)
    if not ok:
        return {"ok": False, "reason": reason}
    reloaded, rmsg = _reload_gateway_registry(request.app)
    return {"ok": True, "reloaded": reloaded, "reload_note": rmsg}


@router.get("/providers/presets")
def api_providers_presets(request: Request) -> dict[str, Any]:
    """引导式 onboarding 的 provider 预设(选一个→预填技术字段,只需粘 key;含"去哪拿 key")。"""
    from karvyloop.gateway.presets import presets_public
    return {"presets": presets_public()}


def _scrub_secret(msg: str) -> str:
    """错误信息脱敏(CLAUDE.md:绝不外泄 key / Authorization)。保留 401/连不上等有用信号。"""
    import re
    s = str(msg or "")
    s = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "sk-***", s)
    s = re.sub(r"(?i)(bearer|x-api-key|authorization)[:=\s]+\S+", r"\1 ***", s)
    s = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", "***", s)   # 兜底:长 token 串一律打码
    return s[:200]


@router.post("/model/validate")
async def api_model_validate(request: Request) -> dict[str, Any]:
    """对当前默认 chat 模型做一次最小真调用,确认 key/端点真能用。

    zero-barrier:坏 key / 连不上 **当场抓**,而不是用户首次用才暴露。错误信息脱敏(不泄 key)。
    """
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return {"ok": False, "reason": "no_gateway"}
    try:
        ref = getattr(gw.reg, "default_chat", "") or ""
        if not ref:
            return {"ok": False, "reason": "no_default_model"}
        got = False
        async for _ev in gw.complete([{"role": "user", "content": "ping"}], [], ref):
            got = True
            break   # 收到第一个事件 = 端点+key 通了,够了
        return {"ok": True, "model": ref} if got else {"ok": False, "reason": "no_response"}
    except Exception as e:
        return {"ok": False, "reason": _scrub_secret(f"{type(e).__name__}: {e}")}


class ModelDeleteRequest(BaseModel):
    model_id: str = Field(..., min_length=1, max_length=128)


@router.post("/model/delete")
def api_model_delete(req: ModelDeleteRequest, request: Request) -> dict[str, Any]:
    cfgp = _model_cfg_path(request.app)
    if not cfgp:
        return {"ok": False, "reason": "未接 config(--no-llm?)"}
    from karvyloop.gateway.config_models import delete_model
    ok, reason = delete_model(req.model_id, cfgp)
    if not ok:
        return {"ok": False, "reason": reason}
    reloaded, rmsg = _reload_gateway_registry(request.app)
    return {"ok": True, "reloaded": reloaded, "reload_note": rmsg}


class ModelDefaultRequest(BaseModel):
    role: str = Field(..., max_length=16)      # chat | embedding
    model_id: str = Field(..., min_length=1, max_length=128)


@router.post("/model/set_default")
def api_model_set_default(req: ModelDefaultRequest, request: Request) -> dict[str, Any]:
    cfgp = _model_cfg_path(request.app)
    if not cfgp:
        return {"ok": False, "reason": "未接 config(--no-llm?)"}
    from karvyloop.gateway.config_models import set_default
    ok, reason = set_default(req.role, req.model_id, cfgp)
    if not ok:
        return {"ok": False, "reason": reason}
    reloaded, rmsg = _reload_gateway_registry(request.app)
    return {"ok": True, "reloaded": reloaded, "reload_note": rmsg}


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


@router.get("/update_status")
def api_update_status(request: Request) -> dict[str, Any]:
    """版本检测(只读,缓存一天,零遥测)。前端据此显**可关掉**的"有新版"横幅;**绝不自动升级**。

    升级铁律:detect → notify → 你按下。命令交给你执行(`git pull` / `pip -U`),系统不替你升。
    """
    try:
        from karvyloop.update import check_update
        return check_update()       # 缓存优先(不 force),网络不可达 → newer=False,不阻断 UI
    except Exception:
        from karvyloop.update import current_version
        return {"current": current_version(), "latest": None, "newer": False,
                "command": "", "url": "", "checked": False, "source": "error"}


@router.get("/ops/diagnose")
async def api_ops_diagnose(request: Request) -> dict[str, Any]:
    """自愈运维 agent(L1):用**活着的** gateway 诊断 doctor 当前发现的真问题,人话说+提修法。

    诚实边界:接地于 doctor 真 findings;LLM **只诊断+提议、绝不执行**;无 gateway(模型挂)→
    退回确定性 doctor(那时 LLM 也帮不上,L0 顶)。bootstrap 悖论:这条本就该在系统活着时用。
    """
    from karvyloop.doctor import FAIL, WARN, run_doctor
    findings = run_doctor(check_port=False)
    problems = [f for f in findings if f.level in (FAIL, WARN)]
    if not problems:
        return {"ok": True, "healthy": True, "diagnosis": None}
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return {"ok": True, "healthy": False, "diagnosis": None, "reason": "no_model"}
    from karvyloop.i18n import t as _t
    from karvyloop.ops_agent import diagnose
    signal = "\n".join("- " + _t("doctor.msg." + f.code, **f.params) for f in problems)
    d = await diagnose(signal, gateway=gw, model_ref=rk.get("model_ref", ""))
    return {"ok": True, "healthy": False,
            "diagnosis": d.to_dict() if d.ok else None,
            "reason": "" if d.ok else "diagnose_failed"}


@router.post("/ops/propose_fix")
async def api_ops_propose_fix(request: Request) -> dict[str, Any]:
    """L1 自愈 slice3:把运维诊断**升成正式 H2A 决策卡**(不只读着看 / 不只 system_error)。

    信号 = doctor 真 findings **+ 可选的真实运行时报错**(body `{error, source}`)——比固定自检更丰富。
    诚实边界(承 ops_agent / doctor):卡是 unverifiable 诊断;register+broadcast 进 H2A 列由你拍;
    ACCEPT 只跑确定性可逆修复(handler 内),**LLM 文本绝不被执行**。无问题→不造卡;无模型→退回确定性。
    """
    app = request.app
    try:
        body = await request.json()
    except Exception:
        body = {}
    error = str((body or {}).get("error", "") or "").strip()
    source = str((body or {}).get("source", "") or "").strip()

    from karvyloop.doctor import AUTO_FIXABLE, FAIL, WARN, run_doctor
    from karvyloop.i18n import t as _t
    problems = [f for f in run_doctor(check_port=False) if f.level in (FAIL, WARN)]
    parts = ["- " + _t("doctor.msg." + f.code, **f.params) for f in problems]
    if error:
        parts.append(f"- 运行时报错({source or '未知来源'}):{error}")
    if not parts:
        return {"ok": True, "healthy": True, "proposal_id": ""}   # 没问题不造卡

    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return {"ok": True, "healthy": False, "proposal_id": "", "reason": "no_model"}
    reg = getattr(app.state, "proposal_registry", None)
    if reg is None:
        return {"ok": False, "healthy": False, "proposal_id": "", "reason": "no_registry"}

    from karvyloop.ops_agent import diagnose
    d = await diagnose("\n".join(parts), gateway=gw, model_ref=rk.get("model_ref", ""))
    if not d.ok:
        return {"ok": True, "healthy": False, "proposal_id": "", "reason": "diagnose_failed"}

    import time as _time

    from karvyloop.console.proposals import broadcast_proposal
    from karvyloop.karvy.proposal_registry import proposal_for_ops_fix
    codes = [f.code for f in problems]
    auto_fixable = bool(codes) and any(c in AUTO_FIXABLE for c in codes) and d.risk == "reversible"
    # 幂等键:有 doctor 码用码集合;纯运行时报错用报错前缀(同错收敛成一张卡)
    key = ",".join(sorted(codes)) if codes else ("err:" + error[:120])
    prop = proposal_for_ops_fix(diagnosis=d.to_dict(), finding_codes=codes,
                                ts=_time.time(), auto_fixable=auto_fixable, key=key)
    reg.register(prop)
    try:
        await broadcast_proposal(app, prop)
    except Exception:
        pass
    return {"ok": True, "healthy": False, "proposal_id": prop.proposal_id,
            "auto_fixable": auto_fixable, "diagnosis": d.to_dict()}


# ---- /api/search/config(产品内配搜索:默认 keyless DuckDuckGo;可选填 Brave/Tavily key)----

class SearchConfigRequest(BaseModel):
    provider: str = Field(default="", max_length=32)   # "" / brave / tavily;空=清除回 keyless
    api_key: str = Field(default="", max_length=256)


@router.get("/search/config")
def api_search_config_get(request: Request) -> dict[str, Any]:
    """搜索配置公开态(不回传 key 明文):mode=keyless/keyed + provider + has_key + 可选 provider 列表。"""
    from karvyloop.coding.tools.web import get_search_config_public
    return {"ok": True, **get_search_config_public(), "providers": ["brave", "tavily"]}


@router.post("/search/config")
def api_search_config_set(req: SearchConfigRequest, request: Request) -> dict[str, Any]:
    """产品内保存搜索 key(写仓外 ~/.karvyloop/search.json,绝不进 repo)。
    provider/key 留空 = 清除 → 回 keyless。立即生效(清缓存)。"""
    from karvyloop.coding.tools.web import set_search_config
    return {"ok": True, **set_search_config(req.provider, req.api_key)}


# ---- /api/files/* (workspace 文件管理:列/看/下载)----
# 安全:**钉死在 workspace 根**(agent 产物在这);.. / 符号链接逃逸一律拒。
# config/凭证(~/.karvyloop)在 workspace 之外,天然不可达 → 不会泄密。
# LAN 提醒:console 绑 0.0.0.0 时局域网可访问这些文件,沿用"仅在受信网络开"的口径。

def _files_root(request: Request):
    from pathlib import Path
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    root = rk.get("workspace_root") or ""
    if not root:
        return None
    try:
        p = Path(root).resolve()
        return p if p.exists() else None
    except Exception:
        return None


def _files_safe(root, rel: str):
    """把相对路径解析进 root;越狱(../ 或符号链接逃出 root)→ None。"""
    if root is None:
        return None
    try:
        target = (root / (rel or "")).resolve()
    except Exception:
        return None
    return target if (target == root or root in target.parents) else None


@router.get("/files/list")
def api_files_list(request: Request, path: str = "") -> dict[str, Any]:
    """列 workspace 下某目录(钉死在 workspace 根)。无 workspace / 越狱 → ok:false。"""
    root = _files_root(request)
    if root is None:
        return {"ok": False, "reason": "no_workspace"}
    target = _files_safe(root, path)
    if target is None or not target.exists() or not target.is_dir():
        return {"ok": False, "reason": "bad_path"}
    entries: list[dict[str, Any]] = []
    try:
        for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                st = p.stat()
                entries.append({"name": p.name, "is_dir": p.is_dir(),
                                "size": (st.st_size if p.is_file() else 0), "mtime": st.st_mtime})
            except OSError:
                continue
    except OSError:
        return {"ok": False, "reason": "bad_path"}
    rel = "" if target == root else str(target.relative_to(root)).replace("\\", "/")
    return {"ok": True, "path": rel, "entries": entries, "workspace": str(root)}


@router.get("/files/view")
def api_files_view(request: Request, path: str) -> dict[str, Any]:
    """看文本文件(预览;封顶 100KB;非文本/过大 → 提示下载)。"""
    root = _files_root(request)
    target = _files_safe(root, path) if root else None
    if target is None or not target.is_file():
        return {"ok": False, "reason": "bad_path"}
    try:
        if target.stat().st_size > 100_000:
            return {"ok": True, "too_big": True}
        text = target.read_bytes().decode("utf-8")
        return {"ok": True, "text": text}
    except UnicodeDecodeError:
        return {"ok": True, "binary": True}
    except Exception as e:
        return {"ok": False, "reason": type(e).__name__}


@router.get("/files/download")
def api_files_download(request: Request, path: str):
    """下载 workspace 内的文件(路径越狱/非文件 → 404)。"""
    from fastapi.responses import FileResponse
    root = _files_root(request)
    target = _files_safe(root, path) if root else None
    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(target), filename=target.name)


@router.post("/files/upload")
async def api_files_upload(request: Request, dir: str = "", name: str = "") -> dict[str, Any]:
    """上传文件进 workspace 的某目录(裸 body=文件字节,name/dir 走 query;免 multipart 依赖)。

    安全:目标目录钉死在 workspace 根;name 只取 basename(防 `../` / 路径分隔逃逸);封顶 100MB。
    """
    import os
    root = _files_root(request)
    if root is None:
        return {"ok": False, "reason": "no_workspace"}
    safe = os.path.basename((name or "").strip())
    if not safe or safe in (".", ".."):
        return {"ok": False, "reason": "bad_name"}
    dir_target = _files_safe(root, dir)
    if dir_target is None or not dir_target.is_dir():
        return {"ok": False, "reason": "bad_path"}
    target = (dir_target / safe).resolve()
    if not (target == root or root in target.parents):   # 双保险:仍在 root 内
        return {"ok": False, "reason": "bad_path"}
    body = await request.body()
    if len(body) > 100 * 1024 * 1024:
        return {"ok": False, "reason": "too_big"}
    try:
        target.write_bytes(body)
    except OSError as e:
        return {"ok": False, "reason": type(e).__name__}
    return {"ok": True, "name": safe, "size": len(body)}


@router.post("/files/delete")
def api_files_delete(request: Request, path: str = "") -> dict[str, Any]:
    """删 workspace 内的文件 / **空**目录(不可逆 → 前端会先确认)。
    钉死在 workspace 根:越狱拒、删根拒;非空目录拒(让用户先清里面,避免误删一整棵树)。"""
    root = _files_root(request)
    if root is None:
        return {"ok": False, "reason": "no_workspace"}
    target = _files_safe(root, path)
    if target is None or target == root or not target.exists():
        return {"ok": False, "reason": "bad_path"}   # 越狱 / 删根 / 不存在 一律拒
    try:
        if target.is_dir():
            if any(target.iterdir()):
                return {"ok": False, "reason": "not_empty"}   # 非空目录不删
            target.rmdir()
        else:
            target.unlink()
    except OSError as e:
        return {"ok": False, "reason": type(e).__name__}
    return {"ok": True}


@router.get("/decisions/recent")
def api_decisions_recent(request: Request, limit: int = 10) -> dict[str, Any]:
    """最近拍板流水(只读回看):拍完从待决列消失,但人能回看自己拍过什么。newest-first。"""
    log = getattr(request.app.state, "decision_log", None)
    if log is None:
        return {"decisions": []}
    limit = max(1, min(int(limit or 10), 50))
    return {"decisions": log.recent(limit)}


@router.get("/proposals/pending")
def api_proposals_pending(request: Request) -> dict[str, Any]:
    """开机拉取待决提案 —— 让"待你拍的板"跨刷新/切语言存活。

    决策 loop 红线:待决提案只靠 WS 实时推、没有开机拉取 → 一刷新就消失,人被迫问"怎么样了"。
    DEFER 的提案仍挂在 registry(D5),靠本接口下次进来再呈现;ACCEPT/REJECT 已 remove,不返。
    """
    registry = getattr(request.app.state, "proposal_registry", None)
    if registry is None:
        return {"proposals": []}
    out: list[dict[str, Any]] = []
    for p in registry.pending():
        try:
            out.append(p.to_dict())
        except Exception:
            pass
    return {"proposals": out}


@router.get("/setup_status")
def api_setup_status(request: Request) -> dict[str, Any]:
    """无 Key 强制引导:进系统后判断有没有可用模型(网页 + TUI 一致)。

    must_setup=True → 前端弹**强制**录入模型(不可关,直到配好);没 Key 用不了。
    覆盖:首次安装从没配 + Key 后续被删/env 没设。用户显式 --no-llm 不强制。
    """
    from karvyloop.gateway.readiness import setup_status
    return setup_status(request.app)


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


__all__ = ["router"]
