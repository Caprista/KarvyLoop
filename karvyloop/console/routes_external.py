"""routes_external — 跨 runtime 协作的管理面 + 按需接入引导端点(新文件,别塞进 routes.py)。

routes.py 已逼近 2000 行红线 → 本文件自带 APIRouter,由 app.py include_router;所有新的
外部 runtime 管理端点(列/删/探活/引导)进这里。

**定位(#71 协作产品层 + #72 接入插件层)**:外部 runtime 是**第四类实体**——opaque、归属外部
主人的执行体,输出永远是 untrusted 数据。这一层只做**管理面**(看在线状态、删、看醒目外部标识)
和**按需接入引导**(没装给官方安装指引;我们绝不代托管/不 bundle/不 git clone 他家代码)。

**消费的契约(C1 实现;不改注册表,只消费)**:
    CitizenRegistry.list(domain=None) / .detach(domain, citizen_id)->bool / .liveness(citizen_id)->dict
    ExternalCitizen.tier ∈ {"guest","scoped"}
C1 若尚未 merge 该套命名:本模块**防御性 getattr** 退回当前已 merge 的等价面
(list_all() / remove() / .status),并在返回体标 _integration_pending;**绝不硬崩**。

只读端点走 READ_ONLY 语义、写端点(detach)走现有能力门约束(本机/私网来源,同一键升级 CSRF 规格)。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# 外部公民地址里固定的 role 段(与 external_runtime.citizen.EXTERNAL_ROLE 同值;
# 前端直聊 peer=(域, "external", citizen_id) 用它,不与原生 role 混脸)。
_EXTERNAL_ROLE = "external"

# tier 缺省:C1 未给 .tier 时,按最保守档("guest")呈现——不假装它有更宽的授权面。
_DEFAULT_TIER = "guest"


def _registry(app: Any):
    """取外部公民注册表(C1 接线点:app.state.citizen_registry)。未接 → None(降级空清单)。"""
    return getattr(app.state, "citizen_registry", None)


def _list_citizens(reg: Any, domain: Optional[str]) -> tuple[list[Any], bool]:
    """列公民,兼容两套注册表命名。返回 (citizens, integration_pending)。

    - 目标契约(C1):reg.list(domain=None)。
    - 当前已 merge:reg.list_all()(无 domain 过滤)/ list_active()。
    integration_pending=True 表示走的是回退面(C1 目标命名还没 merge)。
    """
    fn = getattr(reg, "list", None)
    if callable(fn):
        try:
            return list(fn(domain=domain) if domain is not None else fn()), False
        except TypeError:
            # 有的 .list 不吃 domain kw → 无参调用再按 domain 过滤
            try:
                items = list(fn())
            except Exception:
                items = []
            if domain is not None:
                items = [c for c in items if getattr(c, "domain_id", "") == domain]
            return items, False
        except Exception as e:
            logger.warning(f"citizen_registry.list 失败,降级空清单: {e}")
            return [], False
    # 回退:当前已 merge 的 list_all()
    fallback = getattr(reg, "list_all", None)
    if callable(fallback):
        try:
            items = list(fallback())
        except Exception:
            items = []
        if domain is not None:
            items = [c for c in items if getattr(c, "domain_id", "") == domain]
        return items, True
    return [], True


def _liveness_of(reg: Any, citizen: Any) -> dict[str, Any]:
    """取单个公民的活性,兼容两套命名。返回 {status: online|offline|unreachable}。

    - 目标契约(C1):reg.liveness(citizen_id) -> {status: ...}。
    - 回退:从 citizen.status 映射(active→online / unreachable→unreachable / 其余→offline)。
      **不主动探活**(那是 attach 向导/probe 的活),这里只如实反映注册表已知态。
    """
    cid = getattr(citizen, "citizen_id", "") or ""
    fn = getattr(reg, "liveness", None)
    if callable(fn) and cid:
        try:
            r = fn(cid)
            if isinstance(r, dict) and r.get("status"):
                return {"status": str(r.get("status"))}
        except Exception as e:
            logger.warning(f"citizen_registry.liveness 失败,回退 status 映射: {e}")
    # 回退:静态 status → 活性灯(不触网、不起子进程)
    st = (getattr(citizen, "status", "") or "").lower()
    mapped = {"active": "online", "unreachable": "unreachable"}.get(st, "offline")
    return {"status": mapped}


def _citizen_view(reg: Any, citizen: Any) -> dict[str, Any]:
    """一个公民的前端视图:身份 + tier + 活性 + 醒目外部标识所需字段。绝不含 key。"""
    cid = getattr(citizen, "citizen_id", "") or ""
    card = getattr(citizen, "capability_card", {}) or {}
    return {
        "citizen_id": cid,
        "domain_id": getattr(citizen, "domain_id", "") or "",
        "runtime_kind": getattr(citizen, "runtime_kind", "") or "",
        # tier:C1 给则用,没给按最保守 guest(不假装更宽授权)。
        "tier": (getattr(citizen, "tier", "") or _DEFAULT_TIER),
        "status": getattr(citizen, "status", "") or "",
        "liveness": _liveness_of(reg, citizen)["status"],
        # 醒目外部标识:恒 True —— 前端据此渲染 🔌 异色徽标(untrusted 外部执行体,不与原生角色混脸)。
        "is_external": True,
        # 直聊寻址(与 external_runtime.citizen_address 一致:role 段固定 "external")。
        "chat_peer": {"domain_id": getattr(citizen, "domain_id", "") or "",
                      "role": _EXTERNAL_ROLE, "agent_id": cid},
        # 能力卡里的非机密事实(版本/模型提示),供 UI 展示"这是个什么执行体"。
        "version": str(card.get("version") or ""),
    }


# ---- GET /api/external/citizens:列外部公民(带 tier + 活性)----

@router.get("/external/citizens")
def api_external_citizens(request: Request, domain: str = "") -> dict[str, Any]:
    """列已接入的外部公民(带 tier + 活性灯)。K4 只读(读注册表,不改)。

    domain 传空 = 全部挂载点;传具体域 = 只列该域(复合键 (域, citizen_id))。
    未接注册表(C1 未 merge app.state.citizen_registry)→ 返空清单 + _integration_pending。
    """
    reg = _registry(request.app)
    if reg is None:
        return {"citizens": [], "_integration_pending": "app.state.citizen_registry 未接线(C1)"}
    dom = domain if domain else None
    citizens, pending = _list_citizens(reg, dom)
    out = {"citizens": [_citizen_view(reg, c) for c in citizens]}
    if pending:
        out["_integration_pending"] = "registry.list/.detach/.liveness/.tier 目标命名未 merge(走回退面)"
    return out


# ---- GET /api/external/liveness:单个公民活性 ----

@router.get("/external/liveness")
def api_external_liveness(request: Request, citizen_id: str = "", domain: str = "") -> dict[str, Any]:
    """探单个外部公民的活性(online|offline|unreachable)。只读。

    先按 (域, citizen_id) 精确解析;解析不到就在全表里按 citizen_id 找任一挂载。
    """
    reg = _registry(request.app)
    if not citizen_id:
        return {"ok": False, "reason": "缺 citizen_id"}
    if reg is None:
        return {"ok": False, "reason": "未接注册表(C1 未 merge)", "status": "unreachable"}
    citizen = None
    resolve_in = getattr(reg, "resolve_in", None)
    if callable(resolve_in):
        try:
            citizen = resolve_in(domain or "", citizen_id)
        except Exception:
            citizen = None
    if citizen is None:
        # 全表兜底(私聊/无域 or 域未知):按 citizen_id 找任一挂载
        citizens, _ = _list_citizens(reg, None)
        for c in citizens:
            if (getattr(c, "citizen_id", "") or "") == citizen_id:
                citizen = c
                break
    if citizen is None:
        return {"ok": False, "reason": "公民不存在", "status": "unreachable"}
    return {"ok": True, "citizen_id": citizen_id, "status": _liveness_of(reg, citizen)["status"]}


# ---- POST /api/external/detach:删除(解绑)一个外部公民 ----

class ExternalDetachRequest(BaseModel):
    citizen_id: str = Field(..., min_length=1, max_length=64)
    domain_id: str = Field(default="", max_length=64)


def _is_trusted_origin(request: Request) -> bool:
    """写操作来源门:同一键升级/一键启用的规格 —— 只准本机/私网触发(挡公网恶意跨源)。"""
    try:
        from karvyloop.console.routes_ops import _is_trusted_upgrade_origin
        client = (request.client.host if request.client else "") or ""
        return _is_trusted_upgrade_origin(client)
    except Exception:
        # 取不到来源判定器 → 保守放行本机回环(测试/降级),不静默开放公网
        client = (request.client.host if request.client else "") or ""
        return client in ("127.0.0.1", "::1", "localhost", "testclient", "")


@router.post("/external/detach")
def api_external_detach(req: ExternalDetachRequest, request: Request) -> dict[str, Any]:
    """解绑(删除)一个外部公民。写操作 → 走来源门(本机/私网),不改注册表内部逻辑只调它的删。

    兼容两套命名:目标契约 reg.detach(domain, citizen_id)->bool;回退 reg.remove(domain, citizen_id)。
    删的是**挂载点**(复合键),不动外部主人的真软件(我们本就不托管它)。
    """
    reg = _registry(request.app)
    if reg is None:
        return {"ok": False, "reason": "未接注册表(C1 未 merge app.state.citizen_registry)"}
    if not _is_trusted_origin(request):
        client = (request.client.host if request.client else "") or ""
        return {"ok": False, "reason": f"删除只能从本机或同局域网触发(你的来源 {client} 不在可信网内)"}
    detach = getattr(reg, "detach", None)
    removed: Optional[bool] = None
    if callable(detach):
        try:
            removed = bool(detach(req.domain_id or "", req.citizen_id))
        except Exception as e:
            logger.warning(f"citizen_registry.detach 失败: {e}")
            return {"ok": False, "reason": f"detach 失败: {type(e).__name__}"}
    else:
        remove = getattr(reg, "remove", None)  # 回退:当前已 merge 的 remove()
        if not callable(remove):
            return {"ok": False, "reason": "注册表既无 detach 也无 remove(集成点未就绪)"}
        try:
            removed = bool(remove(req.domain_id or "", req.citizen_id))
        except Exception as e:
            logger.warning(f"citizen_registry.remove 失败: {e}")
            return {"ok": False, "reason": f"remove 失败: {type(e).__name__}"}
    if not removed:
        return {"ok": False, "reason": "公民不存在或已解绑", "citizen_id": req.citizen_id}
    return {"ok": True, "citizen_id": req.citizen_id, "domain_id": req.domain_id or ""}


# ---- GET /api/external/onboarding:按需接入引导(装没装 + 官方安装指引)----

@router.get("/external/onboarding")
def api_external_onboarding(request: Request) -> dict[str, Any]:
    """按需接入引导:机器上有没有可接入的外部 runtime + 没装时的官方安装指引骨架。

    这跟 [asr]/[ocr] 降级引导 + MCP registry 外链是同一套模式(确定性探测 + 前端 i18n 出文案/外链)。
    **红线**:我们绝不代托管/不 bundle/不 git clone 他家代码 —— 本端点只给"去哪装"的确定性事实,
    真安装由用户从**官方源**自己完成。永不执行候选 bin、永不联网、永不含 key。
    """
    from karvyloop.doctor_liveness import check_external_runtime
    findings = check_external_runtime()
    f = findings[0] if findings else None
    present = bool(f and f.code == "external_runtime_present")
    bins = (f.params.get("bins", "") if f else "") or ""
    # 状态语义:present(已自带,去面板接入)/ absent(按需:去官方源装一个再来接)。
    return {
        "present": present,
        "found_bins": [b.strip() for b in bins.split(",") if b.strip()],
        # 引导文案 + 官方源外链全在前端 i18n(公开仓中性表述;不写死具体产品名当依赖)。
        # 后端只给可判定事实 + 引导 key,前端据此渲染"从官方装"的指引。
        "guidance_key": "external.onboarding.absent" if not present else "external.onboarding.present",
        # 明确的边界声明(前端可直接展示,也是审计事实):我们不分发外部 runtime。
        "we_bundle_it": False,
    }


__all__ = ["router"]
