"""routes_roles — 角色库 + 公共模型列表 + 范式编辑 + 外部 Agent 导入端点(P2-② 纯搬移)。

覆盖:/api/models、/roles、/roles/presence、/role/create、/role/remove、/role/update、
/role/paradigm(读/改/gaps)、/role/in_domain、/agent/import。自带 APIRouter,由 app.py
include_router;符号在 routes.py re-export 保既有 import 可达。

从 routes.py 逐字搬移,零逻辑改动。自带本地 helper(_role_to_dict / _domains_referencing_role /
_detect_domain_skill_conflicts);后者被 routes.py 的 /domain/create 复用 → routes.py re-export 它。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from karvyloop import i18n
from karvyloop.llm.token_ledger import token_source as _token_src

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


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
    tags: list = Field(default_factory=list)                # #3b:语义标签(双语 dict 或旧英文串;缺则默认打来源标签)


def _role_to_dict(v) -> dict[str, Any]:
    return {"id": v.id, "identity": v.identity, "atom_ids": list(v.atom_ids),
            "skill_ids": list(getattr(v, "skill_ids", [])),
            "nickname": getattr(v, "nickname", ""), "title": getattr(v, "title", ""),
            "model": getattr(v, "model", ""),
            "tags": [dict(tg) for tg in getattr(v, "tags", [])],   # #3b:双语语义标签(筛选/显示)
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


@router.get("/roles/presence")
def api_roles_presence(request: Request) -> dict[str, Any]:
    """工位区聚合快照(P1.5 灵魂缺口①,纯只读)。契约形状冻结(前端并行开发):
    {"roles":[{"role_id","display","domain_id","status":"busy|idle","running",
    "last_activity_ts","last_task":{"id","intent"}|null}]}。

    角色库全角色 + 小卡(l0,role_id="karvy")各一行(没任务 = idle,工位常驻在场);
    running/最近活动从 task registry 折叠(与 WS `role_presence` 增量同一套纯函数,一个口径)。
    """
    from karvyloop.console.task_events import roles_for_presence
    from karvyloop.console.tasks import aggregate_presence
    app = request.app
    task_reg = getattr(app.state, "task_registry", None)
    try:
        tasks = task_reg.list() if task_reg is not None else []
    except Exception:
        tasks = []
    return {"roles": aggregate_presence(roles_for_presence(app), tasks)}


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
                       nickname=req.nickname, title=req.title, model=req.model,
                       tags=list(req.tags))
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
    atom_ids: Optional[list[str]] = Field(default=None)             # None=不改可用原子(全范式编辑器)


@router.post("/role/update")
def api_role_update(req: RoleUpdateRequest, request: Request) -> dict[str, Any]:
    """编辑角色(P0 审计:此前写错只能删重建)。改 identity(人格)/ model / 随身技能 / 可用原子。"""
    reg = getattr(request.app.state, "role_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 role_registry"}
    try:
        rv = reg.update(req.role_id, identity=req.identity, model=req.model,
                        skill_ids=req.skill_ids, atom_ids=req.atom_ids)
    except Exception as e:  # UnknownSkillError 等 → 422(引了不存在的技能)
        raise HTTPException(status_code=422, detail=f"改角色失败:{e}")
    if rv is None:
        return {"ok": False, "reason": f"角色「{req.role_id}」不存在"}
    return {"ok": True, "role_id": req.role_id}


# ---- 范式可见可编(docs/00 §2.4):让编辑页看见+能改完整七层范式,不再只有 identity/atoms/skills ----

@router.get("/role/paradigm")
def api_role_paradigm(role_id: str, request: Request) -> dict[str, Any]:
    """读一个角色的**完整七层范式**(IDENTITY/SOUL/USER/MEMORY/COMMITMENT/VERIFY + atoms/skills)。

    解决"用户不知道范式是什么":GET /api/roles 只返 identity/atoms/skills,看不到灵魂层;这里全暴露。
    `editable_slots` 告诉前端哪些层可改(MEMORY=运行时只读、COMPOSITION 走 atom/skill 编辑)。
    """
    reg = getattr(request.app.state, "role_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 role_registry"}
    pm = reg.read_paradigm((role_id or "").strip())
    if pm is None:
        return {"ok": False, "reason": f"角色「{role_id}」不存在"}
    return {"ok": True, "paradigm": pm}


@router.get("/role/in_domain")
def api_role_in_domain(role_id: str, domain_id: str, request: Request) -> dict[str, Any]:
    """看一个角色**在某业务域里**的合并样子(docs/00 §2.4/§5):
    ① 角色**原生范式**(自己的 7 文件,不可在此改 —— 去角色库编辑)+ ② 本域**继承来的行为准则**
    (value.md 灵魂 + deontic 硬护栏,域级可改)。解决"域内角色看不到它受哪份 value.md 治理"的缺口。"""
    rreg = getattr(request.app.state, "role_registry", None)
    dreg = getattr(request.app.state, "domain_registry", None)
    if rreg is None or dreg is None:
        return {"ok": False, "reason": "未接 role/domain registry"}
    pm = rreg.read_paradigm((role_id or "").strip())
    if pm is None:
        return {"ok": False, "reason": f"角色「{role_id}」不存在"}
    d = dreg.get((domain_id or "").strip())
    if d is None:
        return {"ok": False, "reason": f"业务域「{domain_id}」不存在"}
    de = d.deontic
    return {"ok": True, "role_id": role_id, "domain_id": domain_id, "domain_name": d.name,
            "paradigm": pm,
            "value_md": (d.value_md.text if getattr(d, "value_md", None) else ""),
            "deontic": {"forbid": list(de.forbid), "oblige": list(de.oblige), "permit": list(de.permit)}}


class RoleSoulUpdateRequest(BaseModel):
    role_id: str = Field(..., min_length=1, max_length=64)
    slot: str = Field(..., max_length=16)          # IDENTITY/SOUL/USER/COMMITMENT/VERIFY
    text: str = Field(default="", max_length=8000)


@router.post("/role/paradigm/update")
def api_role_paradigm_update(req: RoleSoulUpdateRequest, request: Request) -> dict[str, Any]:
    """编辑一个**可编辑灵魂层**(SOUL/USER/COMMITMENT/VERIFY/IDENTITY)——不再 write-once(原先只创建时能填)。

    MEMORY(运行时)/COMPOSITION(走 atom/skill 编辑)不在此;非法 slot / 角色不存在 → ok=False。
    """
    reg = getattr(request.app.state, "role_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 role_registry"}
    ok = reg.update_soul((req.role_id or "").strip(), req.slot, req.text)
    if not ok:
        return {"ok": False, "reason": f"改不了:slot「{req.slot}」不可编辑 或 角色「{req.role_id}」不存在"}
    return {"ok": True, "role_id": req.role_id, "slot": req.slot.strip().upper()}


@router.get("/role/paradigm/gaps")
async def api_role_paradigm_gaps(role_id: str, request: Request) -> dict[str, Any]:
    """范式**对话式补全引擎**(docs/02 §14 ③):检测角色缺哪几层 + LLM 为缺层**起草建议**。

    被动塑形是死的——这里 LLM 理解已有层、为缺的 SOUL/VERIFY/... 起草草稿,前端展示成"问答补全"(用户
    确认/改后走 POST /role/paradigm/update 落)。`complete=false` = 范式还没齐(不补不落库的信号)。
    三入口(import/自建/对话生成)统一:任何来源的角色都能跑这个。
    """
    reg = getattr(request.app.state, "role_registry", None)
    if reg is None:
        return {"ok": False, "reason": "未接 role_registry"}
    pm = reg.read_paradigm((role_id or "").strip())
    if pm is None:
        return {"ok": False, "reason": f"角色「{role_id}」不存在"}
    from karvyloop.roles.completion import detect_paradigm_gaps, suggest_paradigm_completion
    gaps = detect_paradigm_gaps(pm)
    suggestions: dict[str, Any] = {}
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gaps and gw is not None:
        try:
            with _token_src("paradigm_complete"):
                suggestions = await suggest_paradigm_completion(pm, gaps, gateway=gw,
                                                               model_ref=rk.get("model_ref", ""))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[paradigm completion] 起草失败: {e}")
    return {"ok": True, "role_id": role_id, "gaps": gaps, "suggestions": suggestions,
            "complete": len(gaps) == 0}


# ---- 9.5:外部 Agent 导入(按 KarvyLoop 范式改造 → 落角色库)----

class AgentImportRequest(BaseModel):
    role_id: str = Field(..., min_length=1, max_length=64)          # 落库后的角色名
    source_type: str = Field(default="generic-json", max_length=32)  # claude/codex/agent-bundle/generic-json
    system_prompt: str = Field(default="", max_length=16000)
    tools: list[str] = Field(default_factory=list)


@router.post("/agent/import")
async def api_agent_import(req: AgentImportRequest, request: Request) -> dict[str, Any]:
    """把外部 agent 导入成 KarvyLoop 资产。

    **M3 LLM 拆解(docs/14 §10,Hardy 2026-06-26 拍)**:有 LLM 时,先跑一次拆解
    (agent → 真人设 role + 公共原子库里的可复用 atom + 识别内含 skill),**耗 token**;
    tools 不再是 COMPOSITION 里的死字符串,而是落成原子(任何角色都能复用)。
    **按型分流(docs/84 #2,判据=宪法"担不担你的责",判型折进同一次拆解)**:
    - decision → 建 role(atoms 可 0:纯人设顾问合法);
    - hybrid → 现路径(落原子 + 建带原子引用的 role);
    - executor → **只落公共原子库,不建 role**(把不担责的纯执行体安进决策席才是错),
      role_id 参数降级为原子 provenance(origin="agent-import:<rid>");
    - skill → 不落 role/atom(零写盘),如实标 import_kind="skill_like" + 指路技能库导入。
    **降级链不变**:无 LLM(--no-llm)/ 拆解失败(宁空勿毒返 None)/ is_valid 按型不过
    → 回退 v0 确定性 adapter(五段 Source→Map→Plan→Apply→Validate,套模板写 7 文件、
    0 原子、0 token),如实标 decomposed=False。
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
            kind = getattr(decomp, "agent_kind", "hybrid")
            # ---- skill 型:一段流程剧本不是"谁" → 不落 role/atom(零写盘),指路技能库导入 ----
            # 不静默强改写成角色;is_valid 已保证 skills≥1,前端拿着 skills_recognized 去 skill import。
            if kind == "skill":
                return {
                    "ok": True, "role_id": rid, "decomposed": True,
                    "agent_kind": "skill", "import_kind": "skill_like",
                    "note": i18n.t("agent_import.note.skill_like"),
                    "atoms": [], "atoms_created": [],
                    "atoms_advisory": [], "atoms_executable": [],
                    "skills_recognized": list(decomp.skills), "skills_bound": [],
                    "identity": decomp.identity,
                }
            # ---- 落原子(decision / hybrid / executor 共用;origin = 导入 provenance)----
            atoms_created: list[str] = []
            for ap in decomp.atoms:
                if atom_reg.get(ap.id) is not None:
                    continue                     # 复用已有(甲:用不拥有)
                try:
                    # 导入原子刻意**不 provisional**(与 self_create/merge 相反)。理由(J2-次审计核过):
                    # executor 型不建 role → 原子落库即 0 引用孤儿;若标 provisional,daily
                    # review_provisional 会把 0 引用孤儿当"没人用"删掉(provisional.py:43),会在用户
                    # 建 role 绑定前就摧毁"任何角色可组合"的承诺。decision/hybrid 型这些原子当场绑进
                    # 新建 role(≥1 引用)本也不需要 provisional。故统一永久 + origin 留 provenance;
                    # 真要 GC 走用户显式删,不靠 provisional 巡检(否则=按型该留的被静默删)。
                    atom_reg.create(ap.id, ap.kind, ap.purpose, tools=list(ap.tools),
                                    tags=list(getattr(ap, "tags", ()) or ()),
                                    origin=f"agent-import:{rid}")
                    atoms_created.append(ap.id)
                except Exception as e:  # noqa: BLE001 — 单个原子建失败不阻断,跳过
                    logger.warning(f"[agent/import] 原子 {ap.id} 建失败: {e}")
            atom_ids = [ap.id for ap in decomp.atoms if atom_reg.get(ap.id) is not None]
            # 诚实披露(docs/14 §11.1):哪些原子是顾问型(工具没接真实注册表,只靠人设推理)
            advisory = [aid for aid in atom_ids
                        if (atom_reg.get(aid) is not None and not atom_reg.get(aid).executable)]
            executable = [aid for aid in atom_ids if aid not in advisory]
            # ---- executor 型:纯执行体不担你的责 → 只落公共原子库,**不建 role**(docs/84 #2)----
            # role_id 参数在此路降级为原子 provenance(上面 origin);要决策席请自建 role 绑这些原子。
            if kind == "executor":
                return {
                    "ok": True, "role_id": rid, "decomposed": True,
                    "agent_kind": "executor", "import_kind": "pure_executor",
                    "note": i18n.t("agent_import.note.pure_executor", n=len(atom_ids)),
                    "atoms": atom_ids, "atoms_created": atoms_created,
                    "atoms_advisory": advisory, "atoms_executable": executable,
                    "skills_recognized": list(decomp.skills), "skills_bound": [],
                    "identity": decomp.identity,
                }
            # ---- decision / hybrid:现路径 —— 建带原子引用的 role(decision 的 atoms 可 0)----
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
            # agent-vs-skill 识别(docs/14 §11.3):若**一个可执行原子都没有**,这更像一段顾问人设/技能,
            # 不是会干活的工具型 agent —— **不静默当 agent 强改写**,如实标 import_kind + 建议走 skill import。
            import_kind = "tool_agent" if executable else "advisory_persona"
            # 顾问角色 = 暂无可立即执行的能力。**补能力的正路是给它一个 skill**(不是"缺工具"——
            # 我们没有也不需要"工具编辑":skill 最终落到 run_command 写代码 / MCP 连外部系统)。
            note = "" if executable else i18n.t("agent_import.note.advisory_persona")
            return {
                "ok": True, "role_id": rid, "decomposed": True,
                "agent_kind": kind,
                "import_kind": import_kind, "note": note,
                "atoms": atom_ids, "atoms_created": atoms_created,
                "atoms_advisory": advisory,        # 工具是合成名、对不上真工具 → 只能顾问推理
                "atoms_executable": executable,
                "skills_recognized": list(decomp.skills), "skills_bound": bind_skills,
                "identity": decomp.identity,
            }
        # decomp 为 None/无效(is_valid 按型不过)→ 落到 v0 降级

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
        "note": i18n.t("agent_import.note.v0_fallback"),
        "written": list(getattr(result, "written", [])),
        "valid": bool(getattr(validation, "is_valid", True)),
    }


# ---- docs/84 #3:多 agent 系统导入(两阶段 H2A:plan 零写盘 → 人拍板 → apply 确定性落地)----

class SystemImportPlanRequest(BaseModel):
    bundle: dict = Field(...)                                    # agents[] + topology(源格式原样)
    domain_name: str = Field(default="", max_length=64)          # 可选:覆盖落地域名


@router.post("/agent/import_system/plan")
async def api_agent_import_system_plan(req: SystemImportPlanRequest, request: Request) -> dict[str, Any]:
    """读一个多 agent 系统 bundle → ImportPlan + 降级报告(**零写盘**,IR 不持久化)。

    一次 LLM(SYSTEM_TRIAGE,token_source=agent_import)读懂拓扑 → 确定性翻译成 plan
    (系统→域/嵌套→子域/流水线+条件+失败策略→workflow 模板/群聊→圆桌种子/supervisor→路由权上移;
    动态路由/循环/汇报链/黑板/定时 → degradations 逐条如实报,人拍板)。
    宁空勿毒:TRIAGE 出不来合法 IR → mode="per_agent"(拓扑丢失如实报,各 agent 走单 agent 导入,
    那条路每个会各跑一次拆解);无 LLM 同理。
    """
    from karvyloop.adapter.source import ManifestError, parse_system_bundle
    from karvyloop.adapter.system_import import system_triage, translate_to_plan
    try:
        bundle = parse_system_bundle(req.bundle or {}, source_path="<console-import>")
    except ManifestError as e:
        raise HTTPException(status_code=422, detail=f"不是可用的多 agent bundle:{e}")

    app = request.app
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    atom_reg = getattr(app.state, "atom_registry", None)

    def _per_agent_fallback(note_key: str) -> dict[str, Any]:
        # 拓扑读不出/无 LLM:如实报,指路"逐个当单 agent 导"(/api/agent/import),零写盘。
        return {
            "ok": True, "mode": "per_agent", "triaged": False,
            "note": i18n.t(note_key),
            "agents": [{"name": m.agent_name, "preview": m.system_prompt[:200]}
                       for m in bundle.agents],
            "agents_total": bundle.agents_total,
            "agents_dropped": list(bundle.agents_dropped),
            "degradations": [{
                "element": "topology",
                "why": i18n.t("system_import.degrade.topology.why"),
                "fallback": i18n.t("system_import.degrade.topology.fallback"),
            }],
        }

    if gw is None:
        return _per_agent_fallback("system_import.note.no_llm")
    ir = None
    try:
        existing = [a.id for a in atom_reg.list_all()] if atom_reg is not None else []
        with _token_src("agent_import"):        # 导入读谱的 token 入账本、归 agent_import 源
            ir = await system_triage(bundle, existing_atom_ids=existing,
                                     gateway=gw, model_ref=rk.get("model_ref", ""))
    except Exception as e:  # noqa: BLE001 — 读谱任何异常都降级,不让导入崩
        logger.warning(f"[import_system/plan] TRIAGE 失败,降级逐个导:{e}")
    if ir is None:
        return _per_agent_fallback("system_import.note.triage_failed")

    plan, degradations = translate_to_plan(
        ir, bundle_name=(req.domain_name or bundle.name).strip())
    if (req.domain_name or "").strip():
        plan["domain"]["name"] = req.domain_name.strip()
    return {
        "ok": True, "mode": "system", "triaged": True,
        "plan": plan, "degradations": degradations,
        "agents_total": bundle.agents_total,
        "agents_dropped": list(bundle.agents_dropped),
    }


class SystemImportApplyRequest(BaseModel):
    plan: dict = Field(...)                                      # 人审过的 ImportPlan(判型可改/模板可编)
    created_by_user: str = Field(default="ch", max_length=64)


@router.post("/agent/import_system/apply")
async def api_agent_import_system_apply(req: SystemImportApplyRequest, request: Request) -> dict[str, Any]:
    """把人拍过板的 ImportPlan **确定性落地**(零 LLM):原子→角色(自动 seed 尽责契约)→
    域+子域→WorkflowStore 模板(provenance:import)→圆桌种子(H2A 卡,人拍了才开桌)。

    失败回滚不留孤儿;同名活跃域拒。善后:落完若接了 LLM,顺手跑一次原子库合并**建议**
    (dry-run,只报簇不动库 —— 合并本身另走 H2A)。
    """
    from karvyloop.adapter.system_import import SystemApplyError, apply_system_plan
    app = request.app
    if (req.plan or {}).get("mode") == "per_agent":
        raise HTTPException(status_code=422,
                            detail=i18n.t("system_import.apply.per_agent_mode"))
    try:
        report = apply_system_plan(
            req.plan or {},
            atom_registry=getattr(app.state, "atom_registry", None),
            role_registry=getattr(app.state, "role_registry", None),
            domain_registry=getattr(app.state, "domain_registry", None),
            domain_store=getattr(app.state, "domain_store", None),
            workflow_store=getattr(app.state, "workflow_store", None),
            proposal_registry=getattr(app.state, "proposal_registry", None),
            created_by_user=req.created_by_user or "ch")
    except SystemApplyError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # 善后①:原子库合并建议(批量导入后近重复最多的时刻;dry-run,H2A 才真并)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    atom_reg = getattr(app.state, "atom_registry", None)
    report["consolidation_suggestions"] = []
    if gw is not None and atom_reg is not None and report.get("atoms_created") and len(atom_reg) >= 2:
        try:
            from karvyloop.atoms.consolidate import suggest_consolidation
            with _token_src("agent_import"):
                report["consolidation_suggestions"] = await suggest_consolidation(
                    atom_reg.list_all(), gateway=gw, model_ref=rk.get("model_ref", ""))
        except Exception as e:  # noqa: BLE001 — 建议失败不影响已落地资产
            logger.warning(f"[import_system/apply] 合并建议失败(资产已落):{e}")
    # 善后②:识别出的内含技能 → 指路技能库导入(不静默改写,人自己拍)
    if report.get("skills_recognized"):
        report["note"] = i18n.t("system_import.note.skills_to_import",
                                skills=", ".join(report["skills_recognized"][:8]))
    return report


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
