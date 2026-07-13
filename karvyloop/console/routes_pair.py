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
from typing import Any

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


@router.post("/pair/issue")
def api_pair_issue(request: Request) -> dict[str, Any]:
    """签一枚**一次性配对邀请**(15 分钟过期、首用即焚)→ {relay, room, fingerprint, code}。

    前端拿它渲染二维码/完成手机配对。relay 地址来自运行时(--relay / config),**不硬编码**
    (BYO-server:自建 relay 的用户,邀请天然指向他自己的服务器)。console 没挂 relay →
    诚实报错(配对是为了跨网,relay 不在配了也没用)。
    """
    if _via_relay(request):
        return {"ok": False, "reason": _MGMT_LOCAL_ONLY}
    relay_url = getattr(request.app.state, "relay_url", "") or ""
    if not relay_url:
        return {"ok": False, "reason": "还没连接中转服务器,先在启动时带上 --relay 或在 config.yaml 里设 relay。"}
    try:
        store = _store()
        code = store.new_code("full")          # 自有设备 = 完整访问(scope 语义见 pairing.py)
        return {"ok": True, "relay": relay_url, "room": store.rid(),
                "fingerprint": store.fingerprint(), "code": code, "ttl_s": 15 * 60}
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
