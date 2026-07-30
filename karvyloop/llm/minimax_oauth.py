"""minimax_oauth — MiniMax 大模型接入的 OAuth **设备码流**(device authorization grant)。

Hardy 2026-07-30:"用 OAuth 接 MiniMax,别贴 key"。参照工程 openclaw 早就实现了(读了它
`extensions/minimax/oauth.ts` 的真实流程,clean-room 用 Python 重写,不抄代码)——关键发现:
MiniMax 走的是**设备码流**(不是浏览器回调):

  1. 拿设备码:POST {oauth_base}/oauth2/device/code(PKCE + scope)→ 拿到 user_code +
     verification_uri;
  2. **给你一个网址 + 一个码**:你在**任意浏览器**(笔记本/手机)打开、登录、输码、批准;
  3. **轮询换 token**:POST {oauth_base}/oauth2/token(grant=user_code)直到 success。

**为什么这对我们特别香**:设备码流**不需要 localhost 回调、不需要 redirect_uri** —— 跨机/headless
(你的 VM)天生就行(你在自己浏览器批准,KarvyLoop 在 VM 上轮询)。正是我之前给 MCP OAuth
纠结的"跨机回调"的干净解。

**client_id**:openclaw 用它自己注册的 MiniMax client;本模块把 client_id 做成**参数**,不写死。
KarvyLoop 要 consent 屏显示自己名字,得注册自己的 MiniMax OAuth client(真实世界一步,待办)。
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Optional
from uuid import uuid4

# MiniMax 公开 OAuth 参数(端点/scope/grant 是 MiniMax 的,不是 openclaw 的代码)
_REGIONS = {
    "cn": {"oauth": "https://account.minimaxi.com", "api": "https://api.minimaxi.com"},
    "global": {"oauth": "https://account.minimax.io", "api": "https://api.minimax.io"},
}
_SCOPE = "group_id profile model.completion"
_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:user_code"
_DEVICE_PATH = "/oauth2/device/code"
_TOKEN_PATH = "/oauth2/token"
_POLL_CAP_S = 600.0   # 轮询封顶(兜底,防 expired_in 语义歧义时无限转)


class MiniMaxOAuthError(RuntimeError):
    """MiniMax OAuth 流程出错(拿码失败 / 授权被拒 / 超时 / 形状不对)。"""


@dataclass
class DeviceCode:
    user_code: str
    verification_uri: str
    verifier: str          # PKCE code_verifier,轮询换 token 要用
    interval: float        # 轮询间隔秒
    expires_at: float      # 绝对截止(time.time() 基)


@dataclass
class MiniMaxToken:
    access: str
    refresh: str
    expires_at: float      # 绝对截止秒(access 何时过期)
    resource_url: Optional[str] = None


def _endpoints(region: str) -> dict:
    r = _REGIONS.get((region or "").strip().lower())
    if r is None:
        raise MiniMaxOAuthError(f"未知 region: {region!r}(只支持 cn / global)")
    return r


def _pkce() -> tuple[str, str, str]:
    verifier = secrets.token_urlsafe(72)[:128]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(16)
    return verifier, challenge, state


def _abs_deadline(expired_in, now: float) -> float:
    """expired_in 语义在 MiniMax 侧有歧义(相对秒 / 绝对 ms)。稳妥:相对秒→now+它;疑似绝对
    ms(极大)→转秒;都封顶 _POLL_CAP_S,绝不无限转。"""
    try:
        v = float(expired_in)
    except (TypeError, ValueError):
        return now + _POLL_CAP_S
    if v <= 0:
        return now + _POLL_CAP_S
    if v > 1e12:            # 绝对毫秒时间戳
        return min(v / 1000.0, now + _POLL_CAP_S)
    if v > 1e9:             # 绝对秒时间戳
        return min(v, now + _POLL_CAP_S)
    return now + min(v, _POLL_CAP_S)   # 相对秒 TTL


def request_device_code(*, region: str, client_id: str, timeout: float = 15.0,
                        now: Callable[[], float] = time.time) -> DeviceCode:
    """第一步:拿设备码(user_code + verification_uri)。CSRF:核对回包 state==发出的 state。"""
    import httpx
    ep = _endpoints(region)
    verifier, challenge, state = _pkce()
    r = httpx.post(
        ep["oauth"] + _DEVICE_PATH,
        data={"response_type": "code", "client_id": client_id, "scope": _SCOPE,
              "code_challenge": challenge, "code_challenge_method": "S256", "state": state},
        headers={"Accept": "application/json", "x-request-id": str(uuid4())},
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise MiniMaxOAuthError(f"拿设备码失败:{r.text[:200] or r.status_code}")
    d = r.json()
    if not d.get("user_code") or not d.get("verification_uri"):
        raise MiniMaxOAuthError(d.get("error") or "设备码回包缺 user_code/verification_uri")
    if d.get("state") != state:
        raise MiniMaxOAuthError("state 不匹配:可能 CSRF 或会话串了")
    return DeviceCode(
        user_code=str(d["user_code"]),
        verification_uri=str(d["verification_uri"]),
        verifier=verifier,
        interval=max(float(d.get("interval") or 2.0), 2.0),
        expires_at=_abs_deadline(d.get("expired_in"), now()),
    )


def poll_token_once(*, region: str, client_id: str, user_code: str, verifier: str,
                    timeout: float = 15.0):
    """轮询一次换 token。返回 ("success", MiniMaxToken) / ("pending", None) / 抛错(error)。"""
    import httpx
    ep = _endpoints(region)
    r = httpx.post(
        ep["oauth"] + _TOKEN_PATH,
        data={"grant_type": _GRANT_TYPE, "client_id": client_id,
              "user_code": user_code, "code_verifier": verifier},
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    return _parse_token(r, now=time.time())


def _parse_token(r, *, now: float):
    try:
        d = r.json()
    except Exception:
        d = None
    if r.status_code >= 400:
        msg = (d or {}).get("base_resp", {}).get("status_msg") if d else None
        raise MiniMaxOAuthError(f"换 token 失败:{msg or (r.text[:200] if hasattr(r,'text') else r.status_code)}")
    if not isinstance(d, dict):
        raise MiniMaxOAuthError("换 token 回包解析失败")
    status = d.get("status")
    if status == "error":
        raise MiniMaxOAuthError("MiniMax 返回错误,稍后再试")
    if status != "success":
        return ("pending", None)
    if not d.get("access_token") or not d.get("refresh_token"):
        raise MiniMaxOAuthError("token 回包不完整(缺 access/refresh)")
    return ("success", MiniMaxToken(
        access=str(d["access_token"]),
        refresh=str(d["refresh_token"]),
        expires_at=_abs_deadline(d.get("expired_in"), now),
        resource_url=(str(d["resource_url"]) if d.get("resource_url") else None),
    ))


def login_device_flow(*, region: str, client_id: str,
                      on_prompt: Callable[[str, str], None],
                      sleep: Callable[[float], None] = time.sleep,
                      now: Callable[[], float] = time.time) -> MiniMaxToken:
    """完整设备码登录:拿码 → on_prompt(网址,码)让用户去批准 → 轮询到 success 返回 token。

    on_prompt(verification_uri, user_code):把"去这个网址、输这个码"告诉用户(CLI 打印 / UI 弹卡 /
    顺手开浏览器)。不阻塞在本机浏览器,用户在**任意设备**批准即可 —— 所以跨机/headless 天生行。
    """
    dc = request_device_code(region=region, client_id=client_id, now=now)
    on_prompt(dc.verification_uri, dc.user_code)
    while now() < dc.expires_at:
        status, token = poll_token_once(
            region=region, client_id=client_id, user_code=dc.user_code, verifier=dc.verifier)
        if status == "success" and token is not None:
            return token
        sleep(max(dc.interval, 2.0))
    raise MiniMaxOAuthError("授权超时:没在有效期内完成批准。重试。")


__all__ = [
    "MiniMaxOAuthError", "DeviceCode", "MiniMaxToken",
    "request_device_code", "poll_token_once", "login_device_flow",
]
