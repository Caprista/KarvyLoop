"""routes_domain — /api/domain/* 端点(建业务域 / 归档 / 编辑 / 恢复)(P2-② 纯搬移)。

覆盖:/api/domain/create、/domain/archive、/domain/update、/domain/restore。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import 可达。

从 routes.py 逐字搬移,零逻辑改动。跨模块共享:/domain/create 用到 _detect_domain_skill_conflicts
(家在 routes_roles)—— 在调用点从 routes 取(re-export),保单一真源。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ---- /api/domain/create (9.2c:建业务域 — 让 picker 真有业务域可选) ----

class DomainCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    value_md: str = Field(default="", max_length=8000)          # 9.4d:value.md 可选(空=暂无价值观)
    agent: str = Field(default="", max_length=64)               # 9.5 P4:入职角色**可选**;单角色(back-compat)
    agents: list[str] = Field(default_factory=list)             # Hardy:建域可入职**多个**角色(优先 agents)
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
    # 9.5 P4:角色可空 —— 空则 member_query 只含用户(域先建着,角色以后再入职)。
    # Hardy:支持入职**多个**角色 —— agents 优先,兼容旧的单 agent 字段;去空、去重保序。
    _raw_agents = list(req.agents or [])
    if req.agent:
        _raw_agents.append(req.agent)
    agent_list: list[str] = []
    for _a in _raw_agents:
        _a = (_a or "").strip()
        if _a and _a not in agent_list:
            agent_list.append(_a)
    # member_query:user 子句 + 每个角色一个 agent 子句(AND 串联,resolve_members 逐子句解析成成员)
    member_query = " AND ".join(
        [f"user:{req.created_by_user}"] + [f"agent:{_a}" for _a in agent_list]
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
    role_reg = getattr(request.app.state, "role_registry", None)
    if role_reg is not None:
        for _a in agent_list:
            try:
                if role_reg.get(_a) is None:
                    role_reg.create(_a, identity=f"业务域「{domain.name}」里的「{_a}」", atom_ids=[])
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
    # _detect_domain_skill_conflicts 家在 routes_roles → 从 routes(re-export)取,保单一真源。
    from . import routes as _routes
    conflicts: list[dict[str, Any]] = []
    try:
        for _a in agent_list:
            conflicts.extend(_routes._detect_domain_skill_conflicts(request.app, domain, _a))
    except Exception as e:
        logger.warning(f"技能×域冲突检测失败(不影响建域): {e}")

    return {
        "ok": True, "id": domain.id, "name": domain.name,
        "agent": agent_list[0] if agent_list else "",   # back-compat:首个角色
        "agents": agent_list, "conflicts": conflicts,
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
    member_query: Optional[str] = Field(default=None, max_length=512)    # None=不改(原始 DSL,back-compat)
    agents: Optional[list[str]] = Field(default=None)                    # None=不改成员;给了就**后端**重建 member_query(用户不手编 DSL,Hardy)


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
    # 成员:前端传**角色多选** agents → 后端重建 member_query,**保留原 user(域主)子句**;
    # 用户永远不该手编 member_query DSL(Hardy:编辑域那串 user:... AND agent:... 很奇怪)。
    member_query = req.member_query
    if req.agents is not None:
        from karvyloop.domain.registry import parse_member_query
        d0 = reg.get(req.domain_id)
        user_clause = "user:ch"
        if d0 is not None:
            found_user = next((cl for cl in parse_member_query(d0.member_query or "")
                               if cl.type == "user"), None)
            if found_user is not None:
                user_clause = f"user:{found_user.value}"
            else:
                cb = getattr(d0, "created_by", "") or ""
                if cb.startswith("user:"):
                    user_clause = cb
        seen: list[str] = []
        for _a in req.agents:
            _a = (_a or "").strip()
            if _a and _a not in seen:
                seen.append(_a)
        member_query = " AND ".join([user_clause] + [f"agent:{_a}" for _a in seen])
    try:
        d = reg.update(req.domain_id, value_md_raw=raw, member_query=member_query)
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
