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
        # pending 壳(建了码还没认领回连)不探活(它本就不在线);其余走注册表活性面。
        "liveness": ("pending" if (getattr(citizen, "status", "") == "pending")
                     else _liveness_of(reg, citizen)["status"]),
        # 醒目外部标识:恒 True —— 前端据此渲染 🔌 异色徽标(untrusted 外部执行体,不与原生角色混脸)。
        "is_external": True,
        # 直聊寻址(与 external_runtime.citizen_address 一致:role 段固定 "external")。
        "chat_peer": {"domain_id": getattr(citizen, "domain_id", "") or "",
                      "role": _EXTERNAL_ROLE, "agent_id": cid},
        # 能力卡里的非机密事实(版本/模型提示),供 UI 展示"这是个什么执行体"。
        "version": str(card.get("version") or ""),
        # 认领码握手:pending=建了壳发了码、还没认领回连(前端渲染"等待接入"卡);active 后翻正式公民。
        "pending": (getattr(citizen, "status", "") == "pending"),
        # capability_card.self_reported=True 表示能力是外部自报(untrusted),非我们探的 —— 供 UI 标注。
        "self_reported": bool(card.get("self_reported")),
    }


# ---- GET /api/external/citizens:列外部公民(带 tier + 活性)----

@router.get("/external/citizens")
def api_external_citizens(request: Request, domain: str = "") -> dict[str, Any]:
    """列已接入的外部公民(带 tier + 活性灯)。K4 只读(读注册表,不改)。

    domain 传空 = 全部挂载点;传具体域 = 只列该域(复合键 (域, citizen_id))。
    注册表未构造(--no-llm 或构造失败)→ 返空清单 + _integration_pending。
    """
    reg = _registry(request.app)
    if reg is None:
        return {"citizens": [], "_integration_pending": "外部公民注册表未就绪"}
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
        return {"ok": False, "reason": "外部公民注册表未就绪", "status": "unreachable"}
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
        return {"ok": False, "reason": "外部公民注册表未就绪"}
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
            return {"ok": False, "reason": "注册表不支持解绑操作"}
        try:
            removed = bool(remove(req.domain_id or "", req.citizen_id))
        except Exception as e:
            logger.warning(f"citizen_registry.remove 失败: {e}")
            return {"ok": False, "reason": f"remove 失败: {type(e).__name__}"}
    if not removed:
        return {"ok": False, "reason": "公民不存在或已解绑", "citizen_id": req.citizen_id}
    return {"ok": True, "citizen_id": req.citizen_id, "domain_id": req.domain_id or ""}


# ---- 认领码握手:建壳发码 → 外部 runtime 回连认领 → 激活(反向接入,不是本机填 bin)----

def _is_local_authority(host: str) -> bool:
    """Host 头的 host 部分是否是**本地/私网**权威(loopback IP / 私网 IP / localhost / *.local)。

    只信本地权威:Host 头是可被恶意页/错配反代影响的,而这个基址会**连同明文认领秘钥**拼进复制指令
    —— 若信了被引导的公网 Host,用户就会把一次性秘钥 POST 到攻击者端点。所以只反射能确定是本地的 Host,
    其余一律退回 request.base_url(由真实连接派生,不可伪造)。
    """
    import ipaddress
    h = (host or "").strip().lower()
    if not h:
        return False
    # 去端口:IPv6 字面量是 [::1]:port,IPv4/hostname 是 host:port。
    if h.startswith("[") and "]" in h:
        h = h[1:h.index("]")]
    elif h.count(":") == 1:
        h = h.split(":", 1)[0]
    if h in ("localhost",) or h.endswith(".local") or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False   # 不是 IP 字面量、也不是已知本地主机名 → 不信(退回真实连接基址)
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not ip.is_global   # loopback/私网/LAN/链路本地 → 本地权威可信


def _console_base_url(request: Request) -> str:
    """拼出外部 runtime 回连的 console 基址(用于把认领回调 URL 拼进复制指令)。

    **安全**:这个基址会连同明文认领秘钥一起进复制指令,绝不能被伪造的 Host 头引导到攻击者端点。
    - 只在 Host 头是**本地/私网权威**(_is_local_authority)时才反射它(可达性最好:机主浏览器用啥连的就用啥回连)。
    - 否则**不信 Host、也不用 request.base_url**(base_url 本身由 Host 头派生,同样可伪造)——退回 ASGI
      `scope["server"]`(服务器真实绑定的 host:port,不受 Host 头影响,不可伪造)。
    scheme 跟随请求(本地优先常是 http)。
    """
    scheme = request.url.scheme or "http"
    host = (request.headers.get("host") or "").strip()
    if host and _is_local_authority(host):
        return f"{scheme}://{host}"
    # 不可信 Host → 用服务器真实绑定地址(scope["server"] = (host, port),Host 头改不了它)。
    server = request.scope.get("server") or ()
    if server and server[0]:
        srv_host = str(server[0])
        srv_port = server[1]
        # 绑到 0.0.0.0/:: 时对外用回环表述(连接器同机/同网,127.0.0.1 可达且不假装某公网名)。
        if srv_host in ("0.0.0.0", "::", ""):
            srv_host = "127.0.0.1"
        authority = f"{srv_host}:{srv_port}" if srv_port else srv_host
        return f"{scheme}://{authority}"
    # 兜底(scope 无 server,极少见):回环 + 请求端口。
    port = request.url.port
    return f"{scheme}://127.0.0.1{(':' + str(port)) if port else ''}"


class ExternalCreatePendingRequest(BaseModel):
    citizen_id: str = Field(..., min_length=1, max_length=64)
    domain_id: str = Field(default="", max_length=64)
    # 定型:前端选的 runtime 类型(generic_cli / single_json_cli / raw_text_sidecar)。
    # 空 = 未选(壳没定型,取不到配方、驱动不了)—— 前端应逼选;后端不硬崩,只如实建空壳(诚实反映"待定型")。
    runtime_kind: str = Field(default="", max_length=64)
    # 多 agent 支持:single_json_cli 形态的 argv 有 `--agent {agent_id}` —— 选此型时可指定接哪个 agent
    # (默认 main)。存进壳的 capability_card.configured_agent_id,驱动时填进 argv 的 {agent_id} 槽。
    agent_id: str = Field(default="", max_length=64)


def _stamp_agent_id(reg: Any, pending: Any, agent_id: str) -> Any:
    """把用户选的 agent_id 盖进 pending 壳的 capability_card(configured_agent_id),再 upsert 回注册表。

    不改注册表内部逻辑、只用其公共面(add 是同键 upsert + 落盘)。agent_id 空 → 原样返回不动壳。
    落盘失败不阻断建壳(壳/秘钥已发);只是 agent_id 没持久,fail-loud 记一条 warning(不含秘钥)。
    """
    aid = (agent_id or "").strip()
    if not aid:
        return pending
    add = getattr(reg, "add", None)
    if not callable(add):
        return pending  # 注册表无 upsert 面(降级):agent_id 无处落,壳仍是 pending 可认领
    try:
        import dataclasses
        card = dict(getattr(pending, "capability_card", {}) or {})
        card["configured_agent_id"] = aid
        stamped = dataclasses.replace(pending, capability_card=card)
        if not add(stamped):
            logger.warning("create_pending: agent_id 落盘失败(壳已建,agent_id 未持久)")
        return stamped
    except Exception as e:  # noqa: BLE001 — 盖 agent_id 失败不拖垮建壳
        logger.warning(f"create_pending: 盖 agent_id 出错: {type(e).__name__}")
        return pending


@router.post("/external/create_pending")
def api_external_create_pending(req: ExternalCreatePendingRequest, request: Request) -> dict[str, Any]:
    """建壳 + 发一次性认领秘钥:点「＋添加外部 runtime」走这里。写操作 → 走来源门(本机/私网)。

    返回:pending 壳视图 + **一次性明文认领秘钥**(只此一次)+ 认领回调 URL + 一段复制指令
    (含秘钥 + 回调 URL,用户复制去自己的 runtime 里跑)。秘钥绝不进日志(和 API key 同纪律)。

    定型:runtime_kind 决定壳取哪份配方(空=没定型、驱动不了);agent_id(single_json 形态可选,默认 main)
    盖进 capability_card.configured_agent_id → 驱动时进 argv 的 {agent_id} 槽,明确"接哪个 agent"。
    """
    reg = _registry(request.app)
    if reg is None:
        return {"ok": False, "reason": "外部公民注册表未就绪"}
    if not _is_trusted_origin(request):
        client = (request.client.host if request.client else "") or ""
        return {"ok": False, "reason": f"添加外部 runtime 只能从本机或同局域网触发(你的来源 {client} 不在可信网内)"}
    create = getattr(reg, "create_pending", None)
    if not callable(create):
        return {"ok": False, "reason": "注册表不支持认领码握手(create_pending 未接)", "_integration_pending": True}
    try:
        pending, full_secret, err = create(
            req.citizen_id, domain_id=req.domain_id or "", runtime_kind=req.runtime_kind or "")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"citizen_registry.create_pending 失败: {type(e).__name__}")  # 不记秘钥/入参明文
        return {"ok": False, "reason": f"建壳失败: {type(e).__name__}"}
    if pending is None:
        return {"ok": False, "reason": err or "建壳失败"}
    # 定型后盖 agent_id(选了 single_json 型 + 填了 agent 才有值;空则不动壳)。
    pending = _stamp_agent_id(reg, pending, req.agent_id)
    base = _console_base_url(request)
    claim_url = f"{base}/api/external/claim"
    # 复制指令:一段薄命令,POST 到 claim 端点带秘钥。**秘钥只在这个响应体里出现一次**;不落日志。
    # 用 connector 脚本(推荐)或直接 curl(应急)。前端把这段放进"复制这段到你的 runtime 里跑"框。
    return {
        "ok": True,
        "citizen": _citizen_view(reg, pending),
        "claim_url": claim_url,
        # 明文认领秘钥:一次性返回。前端展示、用户复制;刷新后系统不再持有明文(只留摘要)。
        "claim_secret": full_secret,
        # 现成可跑的两种复制指令(前端择一展示):
        #   ① connector 脚本(推荐,自报身份/能力):python -m karvyloop.external_runtime.connector ...
        #      预填 --runtime-kind(选的定型),让自报 kind 一致;single_json 型 + 填了 agent 再预填 --agent-id。
        #   ② 应急 curl(裸 POST)。
        "connector_cmd": _connector_cmd(
            claim_url, full_secret, req.citizen_id, req.runtime_kind or "", req.agent_id or ""),
        "curl_cmd": (
            f'curl -X POST "{claim_url}" -H "Content-Type: application/json" '
            f'-d \'{{"secret": "{full_secret}"}}\''),
    }


def _connector_cmd(claim_url: str, secret: str, citizen_id: str,
                   runtime_kind: str, agent_id: str) -> str:
    """拼连接器复制指令。预填 --runtime-kind(选的定型)+ 可选 --agent-id(single_json 多 agent 时)。

    argv 元素带引号包裹(值可能含空格/特殊字符);秘钥进 --secret(一次性,只在此响应体出现一次)。
    """
    parts = [
        "python -m karvyloop.external_runtime.connector",
        f'--claim-url "{claim_url}"',
        f'--secret "{secret}"',
        f'--citizen-id "{citizen_id}"',
    ]
    rk = (runtime_kind or "").strip()
    if rk:
        parts.append(f'--runtime-kind "{rk}"')
    aid = (agent_id or "").strip()
    if aid:
        parts.append(f'--agent-id "{aid}"')
    return " ".join(parts)


class ExternalCancelPendingRequest(BaseModel):
    citizen_id: str = Field(..., min_length=1, max_length=64)
    domain_id: str = Field(default="", max_length=64)


@router.post("/external/cancel_pending")
def api_external_cancel_pending(req: ExternalCancelPendingRequest, request: Request) -> dict[str, Any]:
    """撤掉一个还没认领的 pending 壳(用户取消等待):删壳 + 作废它的秘钥。走来源门。"""
    reg = _registry(request.app)
    if reg is None:
        return {"ok": False, "reason": "外部公民注册表未就绪"}
    if not _is_trusted_origin(request):
        client = (request.client.host if request.client else "") or ""
        return {"ok": False, "reason": f"取消只能从本机或同局域网触发(你的来源 {client} 不在可信网内)"}
    cancel = getattr(reg, "cancel_pending", None)
    if not callable(cancel):
        return {"ok": False, "reason": "注册表不支持取消 pending(cancel_pending 未接)"}
    try:
        ok = bool(cancel(req.domain_id or "", req.citizen_id))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"取消失败: {type(e).__name__}"}
    if not ok:
        return {"ok": False, "reason": "没有这个待接入壳(或已激活)", "citizen_id": req.citizen_id}
    return {"ok": True, "citizen_id": req.citizen_id, "domain_id": req.domain_id or ""}


class ExternalClaimRequest(BaseModel):
    """外部 runtime 回连认领的载荷。secret=一次性认领秘钥。其余字段=**untrusted 自报**(登记不提权)。"""
    secret: str = Field(..., min_length=1, max_length=256)
    runtime_kind: str = Field(default="", max_length=64)
    bin_path: str = Field(default="", max_length=512)
    version: str = Field(default="", max_length=128)
    capabilities: list[str] = Field(default_factory=list, max_length=64)


@router.post("/external/claim")
def api_external_claim(req: ExternalClaimRequest, request: Request) -> dict[str, Any]:
    """认领回调:外部 runtime 拿秘钥连回 → 校验(一次性/未过期/匹配某 pending 壳)→ 激活 → 秘钥作废。

    秘钥是主认证;来源门(本机/私网)是纵深防御(挡公网裸暴时陌生人拿泄露秘钥远程认领)。
    外部自报的身份/能力 = **untrusted 数据**:登记进能力卡(标 self_reported),但绝不当指令、不据此提权
    (tier/域由建壳侧定,自报改不了)。秘钥绝不进日志(fail-loud 只回统一拒绝话术,不透露是哪种错)。
    """
    reg = _registry(request.app)
    if reg is None:
        return {"ok": False, "reason": "外部公民注册表未就绪"}
    if not _is_trusted_origin(request):
        client = (request.client.host if request.client else "") or ""
        return {"ok": False, "reason": f"认领只能从本机或同局域网连回(来源 {client} 不在可信网内)"}
    claim = getattr(reg, "claim", None)
    if not callable(claim):
        return {"ok": False, "reason": "注册表不支持认领(claim 未接)"}
    reported = {
        "runtime_kind": req.runtime_kind or "", "bin_path": req.bin_path or "",
        "version": req.version or "", "capabilities": list(req.capabilities or []),
    }
    try:
        # 只把 secret 交给注册表;**绝不 log secret**(注册表内部也只比对摘要)。
        result = claim(req.secret, reported=reported)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"citizen_registry.claim 失败: {type(e).__name__}")  # 不记 secret
        return {"ok": False, "reason": f"认领处理出错: {type(e).__name__}"}
    return result if isinstance(result, dict) else {"ok": False, "reason": "认领返回异常"}


# ---- GET /api/external/detect:探本机装了哪类可接入 runtime(辅助添加流"检测到:X")----

def _detect_local_runtimes(which: Optional[Any] = None) -> list[dict[str, Any]]:
    """按内置配方的 probe_bins 探 PATH,返回 [{runtime_kind, bin}] —— 只报**我们有配方能真驱动 + 确知
    用此确切 CLI** 的 bin(probe_bins 非空的配方)。

    **纪律**:不硬编码产品名;runtime_kind + 探到的 bin 都是确定性事实(PATH 上有没有这个可执行名)。
    没有 probe_bins 的配方(shape-only,不认领具体产品)**不探**——那些靠添加流的形态自选。
    永不执行候选 bin、永不联网、永不抛。which 可注入(测试);默认 shutil.which。
    """
    def _default_which(name: str) -> bool:
        try:
            import shutil
            return shutil.which(name) is not None
        except Exception:
            return False
    probe = which or _default_which
    out: list[dict[str, Any]] = []
    try:
        from karvyloop.external_runtime.recipe import builtin_kinds, builtin_recipe
    except Exception:
        return out
    seen: set[tuple[str, str]] = set()
    for kind in builtin_kinds():
        try:
            r = builtin_recipe(kind)
        except Exception:
            r = None
        if r is None:
            continue
        for b in (getattr(r, "probe_bins", ()) or ()):
            b = (b or "").strip()
            if not b or (kind, b) in seen:
                continue
            try:
                present = bool(probe(b))
            except Exception:
                present = False
            if present:
                seen.add((kind, b))
                out.append({"runtime_kind": kind, "bin": b})
    return out


@router.get("/external/detect")
def api_external_detect(request: Request) -> dict[str, Any]:
    """探本机装了哪类可接入 runtime,返回 {runtime_kind, bin} 列表让添加流"检测到:X"辅助自选。

    只是**辅助**:探不到不影响主流程(用户仍靠形态描述对号入座)。探到的项前端可给"直接接入(本机)"
    快捷(定型 + bin 已知)。只读、确定性、永不执行候选 bin、永不联网、不含 key。
    """
    detected = _detect_local_runtimes()
    return {
        "detected": detected,
        "n": len(detected),
        # 边界声明(审计事实):探到 = 本机 PATH 上有这个可执行名,不代表我们分发/托管它。
        "we_bundle_it": False,
    }


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
