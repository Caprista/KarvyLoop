"""routes_pair — 📱 设备配对管理端点(/api/pair/*):颁发一次性配对邀请 / 列已授权设备 / 吊销。

docs/74 配对身份切片的**管理面**(三层鉴权的第三层):
- 会话 token = LAN 临时门(现状);持久授权 = 配对身份(X25519,relay/pairing.py);
- **管理权 = 本地(端点级锁):经隧道的请求带 `x-karvy-via-relay` 标(relay/client.py 咽喉注入,
  远端伪造不进),本模块见标即 403** —— 偷来的手机经隧道最多用 /m 受限面,永远造不出新授权、
  吊销不了别的设备。重出二维码 = 每次新签一次性邀请码(15 分钟过期),**绝不显示持久令牌**。

复用 relay/pairing.py 全套(一次性码首用即焚/list_paired/revoke 撤销即断),零新鉴权机制。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_MGMT_LOCAL_ONLY = "授权管理只能在本机/局域网操作(经隧道的远程会话不能颁发/吊销授权)"


def _via_relay(request: Request) -> bool:
    """这个请求是不是经 relay 隧道回环进来的(咽喉注入标,见 relay/client.py)。"""
    return bool(request.headers.get("x-karvy-via-relay"))


def _store():
    from karvyloop.relay.pairing import PairingStore
    return PairingStore()


class PairIssueRequest(BaseModel):
    """可选请求体:role 非空 = 签**分享码**(给顾问/朋友看的,docs/78 §4.3)——
    scope 收到 read,且绑定被访角色:对方经隧道召回时只放该角色的升层兵法(谓词③白名单刀)。

    scope(分享 UI,docs/73 §9.6):显式 `"read"` = 纯只读分享码(**可不绑 role**——
    对方能看、召回全拒)。绑了 role 的码无论请求什么 scope 一律 read(绝不升权);
    未知 scope 走 normalize_scope deny-by-default 降 read;都没给 = 自有设备 full(零回归)。"""
    role: str = Field("", max_length=80)
    scope: str = Field("", max_length=16)


@router.post("/pair/issue")
def api_pair_issue(request: Request, req: Optional[PairIssueRequest] = None) -> dict[str, Any]:
    """签一枚**一次性配对邀请**(15 分钟过期、首用即焚)→ {relay, room, fingerprint, code, scope, role}。

    前端拿它渲染二维码/完成手机配对。relay 地址来自运行时(--relay / config),**不硬编码**
    (BYO-server:自建 relay 的用户,邀请天然指向他自己的服务器)。console 没挂 relay →
    诚实报错(配对是为了跨网,relay 不在配了也没用)。

    不带 body / role、scope 都空 = 原行为:自有设备 full 码。body 带 role = 分享码(给顾问看):
    scope **read** + per-channel role 绑定 —— 用它配对的设备只读,且外部召回只放该角色的
    升层兵法(没绑 role 的 read 码召回全拒,deny-by-default)。body 带 scope="read"(分享 UI)=
    纯只读分享码,role 可空。坏 role(超长/控制字符)宁空勿毒降成**无绑定的 read 分享码**
    (用户意图是分享,失误绝不能反向发出 full 全权码),响应里 role="" 让调用方看得见绑定没生效。
    """
    if _via_relay(request):
        return {"ok": False, "reason": _MGMT_LOCAL_ONLY}
    relay_url = getattr(request.app.state, "relay_url", "") or ""
    if not relay_url:
        return {"ok": False, "reason": "还没连接中转服务器,先在启动时带上 --relay 或在 config.yaml 里设 relay。"}
    try:
        from karvyloop.relay.pairing import clean_role, normalize_scope
        store = _store()
        raw_role = (req.role if req is not None else "") or ""
        raw_scope = ((req.scope if req is not None else "") or "").strip()
        role = clean_role(raw_role)
        # scope 判定(方向只许收窄,绝不因笔误发全权):
        # ① 给了 role(哪怕最终被消毒成空)= 分享意图 → read,无视请求的 scope(绝不升权);
        # ② 显式给 scope = 按 normalize_scope 归一(未知 deny-by-default 降 read);
        # ③ 都没给 = 自有设备(full,旧前端零回归)。
        if raw_role.strip():
            scope = "read"
        elif raw_scope:
            scope = normalize_scope(raw_scope)
        else:
            scope = "full"
        code = store.new_code(scope, role=role)
        # mesh_room:同主人设备做 mesh 同步拨的**第二**房(docs/74;主房 client 位被 away
        # 浏览器占着)。旧客户端不认识多出的字段,无害。
        return {"ok": True, "relay": relay_url, "room": store.rid(),
                "mesh_room": store.mesh_rid(),
                "fingerprint": store.fingerprint(), "code": code, "ttl_s": 15 * 60,
                "scope": scope, "role": role}
    except Exception as e:                      # 缺 cryptography 等:诚实报,不 500
        logger.warning(f"[pair] issue 失败: {type(e).__name__}")
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


@router.get("/pair/devices")
def api_pair_devices(request: Request) -> dict[str, Any]:
    """列已授权的远程设备(指纹/scope/授权时间;label 可空)。"""
    if _via_relay(request):
        return {"ok": False, "reason": _MGMT_LOCAL_ONLY, "devices": []}
    try:
        return {"ok": True, "devices": _store().list_paired()}
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}", "devices": []}


@router.get("/access_url")
def api_access_url(request: Request) -> dict[str, Any]:
    """取本机 console 的跨设备访问链接(供设备面板渲染**手机扫码二维码**)。

    返回 {console: 带token主页, m: 带token拍板台}(绑 localhost 时 remote 为空 → 前端给
    "改绑 0.0.0.0"的引导)。安全口径:调用方能打到这个端点 = 已过 token 门(本机免密/
    LAN 已带 token),回同一枚 token 不是提权;**经隧道 403**(管理权=本地,偷来的手机
    经 relay 永远拿不到 LAN 令牌)。token 每次重启即刷新,截图泄露窗口有限。
    """
    if _via_relay(request):
        return {"ok": False, "reason": _MGMT_LOCAL_ONLY}
    from karvyloop.console.access import access_urls, read_runtime
    rt = read_runtime()
    if not rt:
        return {"ok": False, "reason": "console 运行时信息不在(~/.karvyloop/console.runtime.json)——重启 console 再试。"}
    urls = access_urls(str(rt.get("host", "127.0.0.1")), int(rt.get("port", 8766)), str(rt.get("token", "")))
    remote = urls.get("remote", "")
    m_url = remote.replace("/?token=", "/m?token=") if remote else ""
    return {"ok": True, "console": remote, "m": m_url, "local_only": not remote}


class PairRevokeRequest(BaseModel):
    ident: str = Field(..., min_length=4, max_length=128)   # 指纹或公钥 hex


@router.post("/pair/revoke")
def api_pair_revoke(req: PairRevokeRequest, request: Request) -> dict[str, Any]:
    """吊销一台已授权设备(按指纹/公钥)。撤销即断:它的下一个请求就被 403(回源在线校验)。"""
    if _via_relay(request):
        return {"ok": False, "reason": _MGMT_LOCAL_ONLY}
    try:
        removed = _store().revoke(req.ident)
        return {"ok": removed, "reason": "" if removed else "没有匹配的已授权设备"}
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}"}


__all__ = ["router"]
