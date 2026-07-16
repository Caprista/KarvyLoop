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

from karvyloop.runtime.main_loop import MainLoop
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
from .workflow_engine import (  # P2-e:workflow 引擎已下沉(纯搬移);re-export 保端点与既有 import/monkeypatch 可达
    _mark_task_cancelled,
    _push_step,
    _workflow_plan_llm,
    _workflow_result_doc,
    _workflow_roles_from_mentions,
    _workflow_run_store,
    _workflow_store,
    execute_workflow_durable,
)
from .distill_engine import (  # P2-e:沉淀引擎已下沉(纯搬移);同上 re-export
    _distill_analyze,
    _distill_chat_reply,
    _distill_public,
    _distill_store,
)
from .roundtable_engine import (  # P2-e:圆桌引擎已下沉(纯搬移);同上 re-export
    _execute_roundtable_discussion,
    _member_display,
    _persist_roundtable_state,
    _resolve_roundtable_from_intent,
    _roundtable_clarify_opening,
    _roundtable_clarify_turn,
    _roundtable_external_roster,
    _roundtable_members,
    _roundtable_pending,
    _roundtable_result_doc,
    _roundtable_roster,
    _roundtable_state,
)

logger = logging.getLogger(__name__)

# H2A REJECT 占位 reason:UI 不逼用户填(Hardy),留空时补它 —— 守协议 A8(REJECT 必带
# 非空 reason)+ 审计链有据,又不挡用户。诚实标注"用户未说明",不假造理由。

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


def self_create_role_id(mgr) -> str:
    """当前私聊角色的 id —— 给 create_atom 自造原子归属(沉淀进该 role 的 composition)。
    私聊小卡(l0)或无 peer → 空(原子进公共池 provisional,不归属业务角色)。§15.5。"""
    try:
        peer = mgr.current_peer() if mgr is not None else None
    except Exception:
        peer = None
    if peer is None or (getattr(peer, "domain_id", "l0") or "l0") == "l0":
        return ""
    role = getattr(peer, "role", "") or ""
    aid = getattr(peer, "agent_id", "") or ""
    return aid if (role == "agent" and aid) else role


def speaker_display(app, mgr) -> str:
    """当前对话里"回复方"的显示名(身份模型 brick2 + brick4)。

    私聊小卡(l0)→ 返回 ""(前端映射成本地化的"小卡/Karvy");
    业务域 → 该角色的**花名(职务)**(brick4,profile.json);没花名则 agent_id/role。
    """
    try:
        from karvyloop.karvy.capability import is_direct_role_peer, is_karvy_peer
        peer = mgr.current_peer() if mgr is not None else None
        # Hardy:l0 直聊某角色 → 回复方是**那个角色**(不是小卡);其余 l0 才是小卡。走下方共用尾段。
        _direct = is_direct_role_peer(peer)
        if not _direct and (peer is None or is_karvy_peer(getattr(peer, "domain_id", "l0"))):
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
        from karvyloop.karvy.capability import is_direct_role_peer, is_karvy_peer
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
        # Hardy:角色面板点角色卡即聊(不必先加进业务域)。l0 直聊某角色 → **该角色**人格,走
        # l0/personal scope(domain=None → 不挂业务域 value.md/deontic)。须在 is_karvy_peer(l0) 前判。
        if is_direct_role_peer(peer):
            role_reg = getattr(app.state, "role_registry", None)
            rid = (getattr(peer, "agent_id", "") or getattr(peer, "role", "")) or ""
            if role_reg is not None and rid:
                from karvyloop.coding.paradigm_prompt import build_role_paradigm_prompt
                try:
                    rv = role_reg.get(rid)
                except Exception:
                    rv = None
                if rv is not None:
                    cp = build_role_paradigm_prompt(rv, None, intent=intent, cwd=workspace_root)
                    if cp is not None:
                        return cp   # per-role 人格(无域治理)
            return build_role_persona_prompt(rid or "角色", domain_name=None, cwd=workspace_root)
        if is_karvy_peer(domain_id):
            # intent 透传:建 agent 类意图命中 → 注入小卡的系统自我认知(架构 101 + 方法论)
            return build_karvy_persona_prompt(cwd=workspace_root, intent=intent)

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
# P2-e:引擎已下沉 workflow_engine.py(纯搬移,行为零变化);此处 re-export 供端点/外部 import 用。


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
        with _token_src("topic_name"):   # 主题名压缩:此前无标 → 记 unknown(P0-9 长尾覆盖)
            async for ev in gw.complete([{"role": "user", "content": s[:500]}], [], ref,
                                        system=SystemPrompt(static=[sysp])):
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[title] 主题精炼失败,兜底截断: {e}")
    out = (out or "").strip().strip("\"'《》「」 ")
    out = out.splitlines()[0].strip() if out else ""
    return out[:max_keep] if out else s[:max_keep]


def _sanitize_when(w, valid_ids: set, self_id: str):
    """条件分支净化:只认 {step∈valid, status|contains|equals}。坏的 → None(无条件=恒跑)。"""
    if not isinstance(w, dict):
        return None
    ref = w.get("step")
    if not isinstance(ref, str) or ref not in valid_ids or ref == self_id:
        return None
    if "status" in w and w.get("status") in ("done", "failed", "skipped"):
        return {"step": ref, "status": w["status"]}
    if isinstance(w.get("contains"), str) and w["contains"]:
        return {"step": ref, "contains": w["contains"][:200]}
    if isinstance(w.get("equals"), str):
        return {"step": ref, "equals": w["equals"][:200]}
    return None


def _enrich_plan(plan, roles) -> dict:
    """给 plan 的每步补上角色身份(display/agent_id/domain_id),丢弃指向未知角色的步骤;
    净化 IR 进阶字段(inputs/when/on_fail),引用必须指向有效 step、自指剔除。"""
    by_rid = {r["role_id"]: r for r in roles}
    raw_steps = []
    valid_ids = set()
    for s in plan.get("steps", []):
        r = by_rid.get(s.get("role_id"))
        sid = s.get("id")
        if r is None or not sid:
            continue
        valid_ids.add(sid)
        raw_steps.append((s, r, sid))
    steps = []
    for s, r, sid in raw_steps:
        deps = [d for d in (s.get("depends_on") or []) if isinstance(d, str)
                and d in valid_ids and d != sid]
        inputs = [d for d in (s.get("inputs") or []) if isinstance(d, str) and d in deps]
        pol = s.get("on_fail")
        step = {"id": sid, "role_id": r["role_id"], "display": r["display"],
                "agent_id": r["agent_id"], "domain_id": r["domain_id"],
                "task": (s.get("task") or "").strip() or "完成你这部分",
                "depends_on": deps}
        # M2(#71 §7.2):外部公民 step 标 is_external(前端 🔌 徽标;执行走 bridge、产出 untrusted)。
        if r.get("is_external"):
            step["is_external"] = True
        if inputs:
            step["inputs"] = inputs
        when = _sanitize_when(s.get("when"), valid_ids, sid)
        if when is not None:
            step["when"] = when
            # 条件门引用的上游**必须是依赖**:否则会在它跑完前就判(fail-open 漏判,分支误触发)。
            # 强制入 depends_on(deps 即 step["depends_on"],原地补);若因此成环 → _topo_ok 兜底拒。
            if when["step"] not in deps:
                deps.append(when["step"])
        if pol in ("skip", "retry", "abort"):
            step["on_fail"] = pol
            if pol == "retry":
                try:
                    step["max_retries"] = max(1, min(5, int(s.get("max_retries", 2))))
                except (TypeError, ValueError):
                    step["max_retries"] = 2
        steps.append(step)
    return {"goal": (plan.get("goal") or "").strip(), "steps": steps}


def _repoint_template(tpl, roles):
    """把结晶模板(按 role_key=agent_id 参数化)重指到当前 @ 的角色;角色没 @ 全 → None(不能复用)。"""
    by_key = {r["agent_id"]: r for r in roles}
    steps, valid = [], set()
    for s in tpl.get("steps", []):
        r = by_key.get(s.get("role_key"))
        if r is None or not s.get("id"):
            return None
        valid.add(s["id"])
        step = {"id": s["id"], "display": r["display"], "agent_id": r["agent_id"],
                "domain_id": r["domain_id"], "task": s.get("task", ""),
                "depends_on": list(s.get("depends_on", []))}
        # 保留 IR 进阶字段,否则结晶复用会把分支/容错丢回最朴素的线性 DAG
        for k in ("inputs", "when", "on_fail", "max_retries"):
            if k in s:
                step[k] = s[k]
        steps.append(step)
    for s in steps:
        s["depends_on"] = [d for d in s["depends_on"] if d in valid and d != s["id"]]
        if isinstance(s.get("inputs"), list):
            s["inputs"] = [d for d in s["inputs"] if d in valid and d != s["id"]]
        w = s.get("when")
        if isinstance(w, dict) and w.get("step") not in valid:
            s.pop("when", None)   # when 指向被裁掉的 step → 丢条件(恒跑,别静默剪枝)
    return {"goal": tpl.get("goal", ""), "steps": steps, "from_template": tpl["id"]}


class WorkflowPlanRequest(BaseModel):
    intent: str = Field(..., min_length=1, max_length=4000)
    # 标准形状 [{agent_id, domain_id?}];也接纯字符串 ["role-a", ...](API 直调最自然的写法,
    # 之前直接 422 无提示——caller-injected 形状要尊重,能明确解释的就别拒)
    mentions: list[dict | str] = Field(default_factory=list, max_length=64)  # 50+ 步工作流压测放开到 64
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
    # docs/84 载体补强:结晶不再丢 when/inputs/on_fail/max_retries —— 否则带分支/汇聚/容错的
    # workflow 一结晶就退化成最朴素的线性 DAG,复用(_repoint_template 本就透传)拿不回这些字段。
    tpl_steps = []
    for s in steps:
        if not (s.get("id") and s.get("agent_id")):
            continue
        st = {"id": s["id"], "role_key": s.get("agent_id"), "task": s.get("task", ""),
              "depends_on": list(s.get("depends_on", []))}
        for k in ("inputs", "when", "on_fail", "max_retries"):
            if s.get(k) is not None:
                st[k] = s[k]
        tpl_steps.append(st)
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
    # task_id 存进 run 记录 → 前端"中止"按钮只握 task_id 也能定位到这条 run(cancel 用)。
    _workflow_run_store(app).create(run_id, goal=goal, steps=steps, domain_id=peer.domain_id,
                                    task_id=task_id or "")
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
    _clear_task_cancelled_safe(app, task_id or "")   # 中止旗用完即清(下次同 id 复用别误判)
    return {"ok": True, "workflow": result, "conversation_id": conv_id,
            "run_line": run_line,   # 2a:专属工作流会话线(左栏出卡 + 追问跳它)
            "crystallizable": crystallizable,
            "plan": {"goal": goal, "steps": steps} if crystallizable else None}


def _clear_task_cancelled_safe(app, task_id: str) -> None:
    try:
        from .workflow_engine import _clear_task_cancelled
        _clear_task_cancelled(app, task_id)
    except Exception:
        pass


# ---- #54 逃生门:跑起来也能踩刹车(cancel)+ 重启不无条件复活(resume/discard 拍板)----

class WorkflowCancelRequest(BaseModel):
    # 前端"中止"按钮只握 task_id(看板卡的 tk.id);也接 run_id(durable run 直连)。给一个即可。
    task_id: str = Field(default="", max_length=64)
    run_id: str = Field(default="", max_length=64)


@router.post("/workflow/cancel")
async def api_workflow_cancel(req: WorkflowCancelRequest, request: Request) -> dict[str, Any]:
    """中止一条正在跑的 workflow(§0.7 逃生门):置该 run 为 cancelled → 跑中的步循环不再起新步、
    剩余步标 skipped。**协作式**:已在跑的步尽力跑完(不硬杀线程),但绝不再烧下一步 token。"""
    app = request.app
    store = _workflow_run_store(app)
    run = None
    if req.run_id:
        run = store.get(req.run_id)
    if run is None and req.task_id:
        run = store.find_by_task(req.task_id)
    rid = (run or {}).get("run_id", "") if run else req.run_id
    ok = store.cancel(rid) if rid else False
    # 双保险:按 task_id 也记中止旗(圆桌无 run store,workflow 借它兜底 durable run 未及时找到的窗口)。
    if req.task_id:
        _mark_task_cancelled(app, req.task_id)
    return {"ok": bool(ok or req.task_id), "run_id": rid, "cancelled": bool(ok)}


@router.post("/roundtable/cancel")
async def api_roundtable_cancel(req: WorkflowCancelRequest, request: Request) -> dict[str, Any]:
    """中止一场正在进行的圆桌(§0.7 逃生门):按 task_id 记中止旗 → 圆桌每轮开始前查它 →
    不再烧下一轮 token,拿已有发言收敛返回。"""
    app = request.app
    if not req.task_id:
        return {"ok": False, "reason": "need_task_id"}
    _mark_task_cancelled(app, req.task_id)
    return {"ok": True, "task_id": req.task_id}


# workflow 逃生门端点(pending_resume / resume / discard)已 carve 到 routes_workflow.py
# (2026-07-11 激活外部 runtime 把 routes.py 顶破 2000 红线 → 拆自包含端点给头寸,路径不变)。


def _recall_domain(mgr) -> str:
    """§2.6 召回用域:在某业务域群 → 该域 id(召共享层 + 本域私有层);私聊/l0 大群 → ""(只召共享)。"""
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
    peer = mgr.current_peer() if mgr is not None else None
    d = getattr(peer, "domain_id", "") if peer is not None else ""
    return "" if d == KARVY_WORLD_DOMAIN else (d or "")


def _resolve_recall_as_of(intent: str) -> Optional[float]:
    """docs/69 Q4:问句是"你当时/上个月怎么理解的"这类过去认知问句 → 解析出那个时点 T(epoch 秒)。
    确定性正则识别(零 LLM,热路径零成本);不是过去认知问句 / 解析不出时刻 → None(照当下召回,
    绝不猜时间)。ws 与 routes 两条 drive 入口共用 cognition.past_recall.resolve_as_of。"""
    try:
        from karvyloop.cognition.past_recall import resolve_as_of
        return resolve_as_of(intent or "")
    except Exception:
        return None   # 识别是增益不是命脉:任何异常退回当下召回,不挡 drive


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

    # 斜杠命令(/status /doctor /url /reboot /version /help):私聊小卡时**确定性拦截、零 LLM**
    # (0 token、0 请求 —— 订阅计划按请求限流,ops 走快捷不吃配额)。放在 main_loop 检查之前,
    # 没配模型也能用 /doctor /url 自救。业务域里不拦(那的 "/" 可能是正文)。
    try:
        from karvyloop.karvy.slash import dispatch_slash, is_slash
        # 有图/附件 → 不当斜杠命令(用户要处理内容,不是跑 ops)—— 别丢了图
        if is_slash(req.intent) and not getattr(req, "images", None) and not getattr(req, "attachments", None):
            from karvyloop.karvy.capability import is_direct_role_peer, is_karvy_peer
            _mgr = getattr(request.app.state, "conversation_manager", None)
            _peer = _mgr.current_peer() if _mgr is not None else None
            # 私聊小卡才拦斜杠 ops;l0 直聊某角色时 "/" 交给角色(ops 不越到角色场)。
            if (_peer is None or is_karvy_peer(getattr(_peer, "domain_id", "l0"))) \
                    and not is_direct_role_peer(_peer):
                _sl = dispatch_slash(req.intent, request.app)
                if _sl is not None:
                    if workbench_app is not None:
                        try:
                            workbench_app.push_chat_log_line("agent", _sl["text"])
                        except Exception:
                            pass
                    if _mgr is not None:
                        try:
                            _mgr.record_turn(req.intent, _sl["text"], brain="slash")
                        except Exception:
                            pass
                    return {"intent": req.intent, "brain": "SLASH", "fast_brain_hit": True,
                            "crystallized": False, "skill_name": "", "routed": False,
                            "text": _sl["text"], "error": None, "slash": _sl.get("cmd", "")}
    except Exception:
        logger.warning("[api_intent] slash 处理失败,降级正常 drive", exc_info=True)

    if main_loop is None:
        # 修 silent-fail:返 200 + error dict,**不** 500
        return drive_outcome_to_dict(_stub_no_main_loop(req.intent))

    # 9.1d:取当前对话上下文(CV-8),喂 drive(上下文依赖门 + 慢脑消解多轮)
    # 9.2b:业务域线注入 value.md(CV-14)
    mgr = getattr(request.app.state, "conversation_manager", None)

    # 共创模式(docs/47 ④,镜像 ws._handle_intent_ws 同款接缝):对话已在共创态 →
    # 整轮进状态机,不再依赖逐轮关键词;早返回必 record_turn(防 ctx 串台)。
    try:
        from karvyloop.karvy.cocreation import cocreation_take_turn
        _coc_reply = await cocreation_take_turn(
            request.app, mgr, req.intent,
            gateway=runtime_kwargs.get("gateway"),
            model_ref=runtime_kwargs.get("model_ref", ""))
    except Exception:
        logger.warning("[api_intent] cocreation 轮处理失败,降级正常 drive", exc_info=True)
        _coc_reply = None
    if _coc_reply is not None:
        if workbench_app is not None:
            try:
                workbench_app.push_chat_log_line("agent", _coc_reply)
            except Exception:
                pass
        if mgr is not None:
            try:
                mgr.record_turn(req.intent, _coc_reply, brain="slow")
            except Exception:
                pass
        return {"intent": req.intent, "brain": "SLOW", "fast_brain_hit": False,
                "crystallized": False, "skill_name": "", "routed": False,
                "cocreation": True, "text": _coc_reply, "error": None}

    ctx = mgr.context_view() if mgr is not None else None
    governance = mgr.governance_text() if mgr is not None else ""
    _domain_gov = governance   # 域治理块(value.md+deontic);persona 已编入时在下方去重

    # loop step4b 地基:个人知识库召回注入(同 ws._handle_intent_ws,封顶 8 条)
    # §2.6:在某业务域里 drive → 召回共享层 + 本域私有层(域专属认知不跨域漏)。
    mem = getattr(request.app.state, "memory", None)
    _recall_used: list = []   # Q1 召回解释:这轮垫了哪几条记忆(空列表=没垫),挂进响应 payload
    # docs/69 Q4:问句是"你**当时/上个月**怎么理解的"这类**过去认知**问句 → 按那个时点召回
    # (确定性正则,零 LLM;宁漏勿误,解析不出时刻=None 照当下召回)。
    _recall_as_of = _resolve_recall_as_of(req.intent)
    if mem is not None:
        try:
            block = mem.recall_block(req.intent, scope="personal", limit=8,
                                     domain=_recall_domain(mgr),
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
        _pa = prealign_governance(request.app, mem, query=req.intent,
                                  domain=(getattr(_peer, "domain_id", "") or ""),
                                  role=(getattr(_peer, "role", "") or ""))
        if _pa:
            governance = (_pa + "\n\n" + governance).strip()
    except Exception:
        pass

    # docs/66 §F:知识线 → 馆员人设进最前(其他线零侵入);召回块已在上文注入=馆员手边有知识库
    try:
        from karvyloop.cognition.knowledge_chat import knowledge_governance
        governance = knowledge_governance(mgr.current_peer() if mgr is not None else None, governance)
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

    # 去重(对抗验收):paradigm 编译的 persona 已把域治理(value.md+deontic)编进 system prompt,
    # governance 再带一份 = 双注入白烧 token。域块是 governance 尾段(召回/预对齐都往前贴),
    # 剥尾段、保留召回 + 预对齐。与委派路径 proposal_handlers 的 _base="" 同一策略。
    if getattr(persona, "covers_domain_governance", False) and _domain_gov and \
            governance.endswith(_domain_gov):
        governance = governance[: -len(_domain_gov)].strip()

    # 9.5 P2:任务看板 —— 把本次 drive 登记成一个任务(running),完成/出错再 finish。
    task_reg = getattr(request.app.state, "task_registry", None)
    task_id = None
    if task_reg is not None:
        _peer = mgr.current_peer() if mgr is not None else None
        _did = (_peer.domain_id if _peer is not None else "l0") or "l0"
        _role = (getattr(_peer, "role", "") or "") if _peer is not None else ""
        # @ 命中/l0 直聊角色 → 是那个角色在忙(speaker_display 返角色名,私聊小卡/群返 "")
        _who = m_speaker or speaker_display(request.app, mgr) or ("小卡" if _did == "l0" else (_role or "角色"))
        task_id = task_reg.start(who=_who, domain_id=_did, role=_role, intent=req.intent)

    # 走 drive_in_tui(asyncio.to_thread 包装,防 R3-async 嵌套)
    try:
        # @ 命中 → 用被 @ 角色配置的模型(空=默认);否则全局 default。
        eff_rk = _rk_model(runtime_kwargs, _model_for_role(request.app, req.mention)) if m_persona is not None else runtime_kwargs
        # per-task token 归因(#42):这轮 drive 烧的每个 token 记到任务名下(成本预估样本)
        from karvyloop.llm.token_ledger import token_task as _token_task
        with _token_task(task_id or ""):
            outcome = await drive_in_tui(req.intent, main_loop, ctx=ctx, governance=governance,
                                         persona=persona, scope=eff_scope,
                                         images=_normalize_images(req.images),
                                         # §15.5:直接聊天也挂 create_atom(角色标配,Hardy)+ 归属当前角色
                                         atom_registry=getattr(request.app.state, "atom_registry", None),
                                         role_registry=getattr(request.app.state, "role_registry", None),
                                         self_create_role=self_create_role_id(mgr),
                                         # 小卡自我认知落地:建 agent 意图 → 挂 instantiate_domain_template
                                         domain_registry=getattr(request.app.state, "domain_registry", None),
                                         domain_store=getattr(request.app.state, "domain_store", None),
                                         # 小卡随聊能力(karvy/tools.py):定时任务(只小卡能起)+ 随聊记忆(写/召回)。
                                         # 只在小卡人格挂(persona.karvy_self);业务角色 persona 不挂(drive_in_tui 内再门一道)。
                                         scheduler_store=_scheduler_store(request.app),
                                         schedule_parser=_schedule_parser(request.app),
                                         schedule_target_resolver=(
                                             lambda rn: _resolve_schedule_target(request.app, rn)),
                                         memory=mem,
                                         # 跨 runtime 协作(docs/71 M1):小卡人格 + 接了 citizen_registry →
                                         # 挂 external_agent/attach/list/revoke(直接聊天里"接入/派活外部 runtime")。
                                         # drive_in_tui 内再门一道(persona.karvy_self);业务角色不挂(0 回归)。
                                         citizen_registry=getattr(request.app.state, "citizen_registry", None),
                                         external_bridge_factory=getattr(request.app.state, "external_bridge_factory", None),
                                         external_token_recorder=getattr(request.app.state, "external_token_recorder", None),
                                         **eff_rk)
    except Exception as e:
        logger.exception(f"api_intent drive 异常: {e}")
        if task_reg is not None and task_id is not None:
            task_reg.finish(task_id, error=str(e))
        return {"intent": req.intent, "error": str(e), "brain": "SLOW", "text": "",
                "recall_used": _recall_used}

    if task_reg is not None and task_id is not None:
        task_reg.finish(task_id, result=(outcome.text or ""), error=(outcome.error or ""))

    # 共创递口(docs/47 §3.1,镜像 ws 同款):建 agent 意图命中 → 回复末尾递"一起共创"口
    # 并挂 OFFERED 会话态;零副作用,失败静默=旧行为。
    if not outcome.error:
        try:
            from karvyloop.karvy.cocreation import maybe_offer_cocreation
            _offer = await maybe_offer_cocreation(
                request.app, mgr, req.intent,
                gateway=runtime_kwargs.get("gateway"),
                model_ref=runtime_kwargs.get("model_ref", ""))
            if _offer:
                outcome.text = ((outcome.text or "").rstrip() + "\n\n" + _offer).strip()
        except Exception:
            logger.debug("[api_intent] 共创递口失败(静默)", exc_info=True)

    # fs_grants:这轮 drive 里碰壁的工作区外路径 → 升授权卡(去重;敏感路径永不出卡)
    try:
        from karvyloop.console.proposals import raise_fs_access_cards
        await raise_fs_access_cards(request.app)
    except Exception:
        pass

    _turn_speaker = m_speaker or speaker_display(request.app, mgr)   # @ 命中=角色花名,否则当前场署名
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
        # W1(docs/56 审计 HIGH):角色经验沉淀补**直聊路径**(REST api_intent 同 ws 镜像)。
        # 只对能归属到某业务域某角色的成功轮触发;无域/l0/纯失败在内部保守门兜底。
        # 直聊无独立 checker → 干净完成(无 error)即最强成功信号,当 verified。fail-soft 不阻断。
        try:
            from .ws import _direct_chat_role_domain
            from .proposal_handlers import _schedule_role_experience
            _exp_domain, _exp_role = _direct_chat_role_domain(
                request.app, mgr, mention=req.mention, mention_domain=req.mention_domain)
            if _exp_domain and _exp_role:
                _schedule_role_experience(
                    request.app, role=_exp_role, domain=_exp_domain, requirement=req.intent,
                    result=(outcome.text or ""), success=True, verified=True)
        except Exception:
            logger.debug("[api_intent] 直聊角色经验沉淀触发失败(静默,不阻断)", exc_info=True)

    payload = drive_outcome_to_dict(outcome)
    payload["speaker"] = _turn_speaker  # @ 命中 → 被 @ 角色署名(与历史 push 同一值)
    payload["recall_used"] = _recall_used  # Q1 召回解释:垫了哪几条记忆(空=没垫)
    if _recall_as_of is not None:
        payload["recall_as_of"] = _recall_as_of  # docs/69 Q4:这轮按此时点召回(chip 标"按 X 时点的记忆")
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


async def maybe_route_to_role(app, mgr, intent: str):
    """私聊小卡 + 业务委派意图 → 出 route_to_role PROPOSE(不自己干);否则返 None 走正常 drive。

    docs/29 KC-3/KC-4:小卡是调度者不是业务参与者 —— 业务活匹配 role + 提议委派,
    用户拍板(K5)后才由该 role 执行。匹配不到 role → 退回小卡自己执行(0 回归)。

    编排升级(Hardy 2026-06-25 bug):"让几个角色开圆桌讨论X" = **多人圆桌**,不是单点委派 ——
    先试圆桌解析,命中 → 出 roundtable PROPOSE;否则退回单角色 route_to_role(0 回归)。
    """
    from karvyloop.karvy.capability import (
        INTENT_COURIER, dispatch_for_peer, is_direct_role_peer, is_karvy_peer,
    )

    peer = mgr.current_peer() if mgr is not None else None
    # docs/66 §F:知识线 = 馆员自己接(专职消化知识),不路由不提圆桌 —— 真机实拍:
    # "帮我消化一个说法"被截胡成"拉俩角色开圆桌讨论",馆员根本没答。知识线豁免整个路由层。
    from karvyloop.cognition.knowledge_chat import is_knowledge_peer
    if is_knowledge_peer(peer):
        return None
    # Hardy:l0 直聊某角色 → 那个角色自己答,不经小卡路由/委派/ops 截胡(别落进 is_karvy_peer 分支)。
    if is_direct_role_peer(peer):
        return None
    domain_id = peer.domain_id if peer is not None else "l0"  # 默认私聊小卡
    if not is_karvy_peer(domain_id):
        return None  # 私聊业务 role → 该 role 自己执行(照常 drive)
    # 运维意图(诊断/排查/运维)→ ops 诊断 H2A。ops 不是"委派给某角色",故不经 should_route 分类
    # (独立对抗验收点名:"帮我诊断系统" 会被 should_route 判 execute → 永远到不了 ops 路由)。
    if _looks_like_ops(intent):
        routed = await _fuzzy_ops_proposal(app, intent)
        if routed is not None:
            return routed
    dispatch = dispatch_for_peer(domain_id, intent)
    if dispatch.intent_class == INTENT_COURIER:
        return None  # 转达 → 小卡自己传话(照常),不进编排
    registry = getattr(app.state, "proposal_registry", None)

    # 第三镜修(2026-07-12):关键词门从"锁死 LLM 编排的硬 gate"降为 **fast-path 信号**。
    # 病根:LLM 拆解器 fuzzy_dispatch 早写好,却被前置关键词门(should_route)反锁——"帮我把
    # 竞品分析整理出来"没委派暗号 → 判 execute → return None → 小卡自己硬干,该编排的活漏判。
    # 修:明确委派词 → 先走确定性圆桌/单角色匹配(命中省一次 LLM);**没说暗号 → 一律交 fuzzy_dispatch
    # 兜底判断**,别让"没命中关键词"就等于"小卡自己干"。fuzzy_dispatch 内含 empty-roster 护栏(无角色
    # 可编排则不烧 LLM——这是"编排是否可能"的状态判断,不是关键词启发式)。
    if dispatch.should_route and registry is not None:
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

    # ③ LLM 拆解器兜底(**默认仲裁**,不再被关键词门锁):关键词没命中确定性编排时也给它机会。
    #    "帮我整理竞品分析" 这类没暗号但该编排的活在这里被 LLM 认出;拆不出/无角色 → None,小卡自己干。
    routed = await _maybe_fuzzy_dispatch(app, intent)
    if routed is not None:
        return routed
    return None  # 拆不出编排 → 小卡自己干(不强行路由)


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
    if not roster:
        return None  # 无可编排角色 → 别烧 LLM,小卡自己干(能力护栏,非关键词启发式)
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
    from karvyloop.runtime.main_loop import Brain
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


# ---- /api/tokens* 已下沉到 routes_tokens.py(P2-② 纯搬移)----
from .routes_tokens import (  # noqa: E402
    api_token_buckets,
    api_tokens,
    api_tokens_query,
)


# ---- /api/domain/* (建域/归档/编辑/恢复)已下沉到 routes_domain.py(P2-② 纯搬移)----
# 端点由 routes_domain.router 提供(app.py include_router);re-export 其符号保既有 import 可达。
from .routes_domain import (  # noqa: E402
    DomainArchiveRequest,
    DomainCreateRequest,
    DomainRestoreRequest,
    DomainUpdateRequest,
    _save_domains,
    api_domain_archive,
    api_domain_create,
    api_domain_restore,
    api_domain_update,
)


# ---- 能力/技能/授权面端点(/skills,/capability,/fs_grants,/silence,/mcp,/skill,/domain/templates,
# /task_cost_estimate,/domains 等)已下沉到 routes_capability.py(P2-② 纯搬移)----
# 端点由 routes_capability.router 提供(app.py include_router);re-export 其符号保既有 import 可达。
from .routes_capability import (  # noqa: E402
    CapabilityEnableRequest,
    DomainTemplateRequest,
    FsGrantRequest,
    FsGrantRevokeRequest,
    McpPresetApplyRequest,
    McpRemoteAddRequest,
    SilenceOverturnRequest,
    SilenceRevokeRequest,
    SkillGrantRequest,
    SkillImportRequest,
    SkillRestoreRequest,
    SkillRunRequest,
    SkillSourcesSaveRequest,
    _skill_net_granted,
    _skill_sources_store,
    _skill_status,
    api_capability_enable,
    api_capability_enable_status,
    api_capability_overview,
    api_coding_capability,
    api_coding_config,
    api_desk_memento,
    api_domain_template_instantiate,
    api_domain_templates,
    api_domains_list,
    api_fs_grants,
    api_fs_grants_add,
    api_fs_grants_revoke,
    api_mcp_preset_apply,
    api_mcp_presets,
    api_mcp_server_add,
    api_silence_overturn,
    api_silence_revoke,
    api_skill_catalog,
    api_skill_grant,
    api_skill_import,
    api_skill_lifecycle,
    api_skill_restore,
    api_skill_run,
    api_skills,
    api_skill_sources,
    api_skill_sources_save,
    api_task_cost_estimate,
)


# ---- 任务看板 /api/tasks,/task/{id}* 已下沉到 routes_system.py(P2-② 纯搬移;re-export 见下方)----


# ---- /api/memory* 已下沉到 routes_memory.py(P2-② 纯搬移)----
# 端点由 routes_memory.router 提供(app.py include_router);re-export 其符号保既有
# import 可达。注:被测试 monkeypatch 的 `_source_ref` 现家在 routes_memory —— 测试 patch
# 目标已改指 routes_memory(见 test_distill_workflow.py),生产端点在 routes_memory 里直接调它。
from .routes_memory import (  # noqa: E402
    ConsolidateApplyRequest,
    DistillChatRequest,
    DistillDecideRequest,
    MemoryFeedRequest,
    MemoryIngestRequest,
    MemoryRemoveRequest,
    _extract_url,
    _fetch_url,
    _source_ref,
    api_memory_consolidate_apply,
    api_memory_consolidate_suggest,
    api_memory_distill,
    api_memory_distill_chat,
    api_memory_distill_decide,
    api_memory_feed,
    api_memory_ingest,
    api_memory_list,
    api_memory_recall,
    api_memory_recent,
)


# ---- /api/decision_prefs* 已下沉到 routes_decision_prefs.py(P2-② 纯搬移)----
from .routes_decision_prefs import (  # noqa: E402
    DecisionPrefOpRequest,
    _clear_revocation,
    api_decision_pref_op,
    api_decision_pref_stats,
    api_decision_prefs,
)


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


# ---- 定时任务(/api/schedule*)已下沉到 routes_schedules.py(P2-② 纯搬移)----
# 端点由 routes_schedules.router 提供(app.py include_router);re-export 其符号保
# app.py 的 `from ...routes import _scheduler_store, fire_schedule`(现指向此处 re-export)
# 与既有测试 import 可达。fire_schedule 复用本模块的 persona/model/drive_in_tui(回取避免复制)。
from .routes_schedules import (  # noqa: E402
    ScheduleCreateRequest,
    ScheduleIdRequest,
    ScheduleParseRequest,
    _resolve_schedule_target,
    _schedule_parser,
    _schedule_to_dict,
    _scheduler_store,
    api_schedule_create,
    api_schedule_delete,
    api_schedule_parse,
    api_schedule_run_now,
    api_schedule_toggle,
    api_schedules,
    fire_schedule,
)


# ---- /api/line* + /api/lines(左栏会话线:隐藏/列出/打开)已下沉到 routes_lines.py(P2-② 纯搬移)----
# 端点由 routes_lines.router 提供(app.py include_router);re-export 其符号保既有 import 可达
# (_is_line_hidden 被本文件的 /peers 复用)。
from .routes_lines import (  # noqa: E402
    ConvOpenRequest,
    LineHideRequest,
    LineOpenRequest,
    _hidden_lines,
    _is_karvy_private_line,
    _is_line_hidden,
    _line_key,
    _line_origin_name,
    _persist_hidden_lines,
    _set_line_hidden,
    api_line_hide,
    api_line_open,
    api_line_open_by_conv,
    api_lines,
)


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
    # 圆桌客人席入口:能进这个场的外部公民也列进名册(逻辑在 roundtable_engine,routes 只调用)。
    members.extend(_roundtable_external_roster(app, peer))
    return {"ok": True, "members": members}


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


# ---- 轮后自动蒸馏已下沉到 routes_memory.py(god-module 拆分:蒸馏维护属 memory 域)----
from .routes_memory import (  # noqa: E402
    maybe_auto_distill,
    schedule_auto_distill,
)

# ---- /api/atoms* + /api/atom/* 已下沉到 routes_atoms.py(P2-② 纯搬移)----
from .routes_atoms import (  # noqa: E402
    AtomCreateRequest,
    AtomMergeRequest,
    AtomRemoveRequest,
    AtomUpdateRequest,
    _atom_to_dict,
    api_atom_create,
    api_atom_remove,
    api_atom_update,
    api_atoms,
    api_atoms_consolidate_apply,
    api_atoms_consolidate_suggest,
)


# ---- 角色库 / 模型列表 / 范式编辑 / Agent 导入端点已下沉到 routes_roles.py(P2-② 纯搬移)----
# 端点由 routes_roles.router 提供(app.py include_router);re-export 其符号保既有 import 可达。
# 注:_detect_domain_skill_conflicts 被本文件的 /domain/create 复用,故一并 re-export。
from .routes_roles import (  # noqa: E402
    AgentImportRequest,
    RoleCreateRequest,
    RoleRemoveRequest,
    RoleSoulUpdateRequest,
    RoleUpdateRequest,
    _detect_domain_skill_conflicts,
    _domains_referencing_role,
    _role_to_dict,
    api_agent_import,
    api_models,
    api_role_create,
    api_role_in_domain,
    api_role_paradigm,
    api_role_paradigm_gaps,
    api_role_paradigm_update,
    api_role_remove,
    api_role_update,
    api_roles,
    api_roles_presence,
)


# ---- /api/peers + /api/peer/switch 已下沉到 routes_peers.py(P2-② 纯搬移)----
from .routes_peers import (  # noqa: E402
    PeerSwitchRequest,
    api_peer_switch,
    api_peers,
)


# ---- /api/conversation* + /api/conversations 已下沉到 routes_conversations.py(P2-② 纯搬移)----
from .routes_conversations import (  # noqa: E402
    ResumeRequest,
    _conv_meta_to_dict,
    api_conversation_new,
    api_conversation_resume,
    api_conversations,
)


# ---- /api/propose 已下沉到 routes_system.py(P2-② 纯搬移)----
from .routes_system import api_propose  # noqa: E402


# ---- /api/h2a_decide 已下沉到 routes_system.py(P2-② 纯搬移,god-module 门 2009>2000 触发)----
from .routes_system import (  # noqa: E402
    DEFAULT_REJECT_REASON,
    H2ADecideRequest,
    api_h2a_decide,
)




# ---- /api/model/* + /api/providers/* 已下沉到 routes_models.py(P2-② 纯搬移)----
# 端点由 routes_models.router 提供(app.py include_router);re-export 其符号保既有
# import(_scrub_secret / _classify_model_error 等被测试 import)可达。
from .routes_models import (  # noqa: E402
    ModelDefaultRequest,
    ModelDeleteRequest,
    ModelReasoningRequest,
    ModelSaveRequest,
    _classify_model_error,
    _model_cfg_path,
    _reload_gateway_registry,
    _restart_required,
    _scrub_secret,
    api_detect_local_models,
    api_model_config,
    api_model_delete,
    api_model_save,
    api_model_set_default,
    api_model_set_reasoning,
    api_model_validate,
    api_providers_presets,
)


# ---- /api/decision_card* 已下沉到 routes_system.py(P2-② 纯搬移)----
from .routes_system import (  # noqa: E402
    DecisionCardJudgeRequest,
    api_decision_card,
    api_decision_card_judge,
)


# ---- /api/update* + /api/ops/* + /api/search/config + /api/doctor/fix 已下沉到 routes_ops.py(P2-② 纯搬移)----
# 端点由 routes_ops.router 提供(app.py include_router);re-export 其符号保既有 import 可达。
from .routes_ops import (  # noqa: E402
    DoctorFixRequest,
    SearchConfigRequest,
    _acquire_upgrade_lock,
    _is_trusted_upgrade_origin,
    _read_last_upgrade,
    api_doctor_fix,
    api_ops_diagnose,
    api_ops_propose_fix,
    api_search_config_get,
    api_search_config_set,
    api_update_apply,
    api_update_rollback,
    api_update_status,
)


# ---- /api/files/* (workspace 文件管理)已下沉到 routes_files.py(P2-② 纯搬移)----
# 端点由 routes_files.router 提供(app.py include_router);此处 re-export 其符号,
# 保既有 `from karvyloop.console.routes import _files_root` / monkeypatch 目标可达。
from .routes_files import (  # noqa: E402
    _files_root,
    _files_safe,
    api_files_delete,
    api_files_download,
    api_files_list,
    api_files_upload,
    api_files_view,
)


# ---- 系统/审计/杂项只读端点 + /lang 已下沉到 routes_system.py(P2-② 纯搬移)----
# 端点由 routes_system.router 提供(app.py include_router);re-export 其符号保既有 import 可达。
from .routes_system import (  # noqa: E402
    LangRequest,
    ResidentInviteRequest,
    api_decisions_audit,
    api_decisions_recent,
    api_health,
    api_lang_get,
    api_lang_set,
    api_proposals_pending,
    api_residents,
    api_residents_invite,
    api_setup_status,
    api_task_detail,
    api_task_trace,
    api_tasks,
)


# ============================================================================
# docs/56 audit ② MED — "后端有能力没 UI 入口" 补三个入口(隔离区,可整块被拆分工人搬走)
#   1. /api/budget       花费预算上限 UI(GET 当前用量/上限,POST 改上限)
#   2. /api/doctor/fix   doctor 确定性自愈的 UI 触发(auto 直接修,confirm 需二次确认)
#   3. workflow 续/丢    端点已在(resume|discard|pending_resume),此批把它们接进前端
# 三者都复用既有后端(config_budget / doctor_cmd / workflow_engine),不重写业务逻辑。
# ============================================================================


# ---- /api/budget 已下沉到 routes_budget.py(P2-② 纯搬移)----
# 端点由 routes_budget.router 提供(app.py include_router);re-export 其符号保既有 import 可达。
from .routes_budget import (  # noqa: E402
    BudgetSaveRequest,
    _budget_model_cost,
    api_budget,
    api_budget_save,
)


__all__ = ["router"]
