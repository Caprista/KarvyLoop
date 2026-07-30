"""oauth_broker — 跨机 OAuth 回调中枢(docs/43 远程访问 + docs/96 刀2)。

病根(Hardy 逼出):mcp_oauth 的 localhost 回调只在"浏览器和 KarvyLoop 同机"时成立。真实部署——
家里小服务器 / 云 VPS(headless)/ 局域网 / karvy.chat 远程——浏览器和 console **不同机**,
`127.0.0.1` 回调收不到 → 那些机器上 OAuth 直接废。而"不能限制用户在哪台机器跑 KarvyLoop"。

正解:回调**不写死 localhost**,而是打到"你此刻访问 console 用的那个地址"上 console 自己的一个
路由(`/api/oauth/callback`)——浏览器天然够得到(你正用它开着 console)。云主机有公网 https
最顺,karvy.chat 隧道复用已有路,局域网走 LAN IP。本模块是把"授权码从那个路由送回**正在等它的**
OAuth 流程"的中枢,按 **state**(OAuth 自带的 CSRF 参数,SDK 生成并最终校验)关联。

headless 关键:redirect_handler **绝不 webbrowser.open**(服务器没本地浏览器),而是把授权 URL
**挂出来**供前端把**用户的**浏览器导过去。token 换到后仍落**运行 KarvyLoop 那台机器**的 0600 文件。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger(__name__)

CALLBACK_PATH = "/api/oauth/callback"
_FLOW_TIMEOUT_S = 300.0


class OAuthCallbackError(RuntimeError):
    """回调阶段出错(授权被拒 / 超时 / server 回 error=)。"""


class _Flow:
    """一次授权流程:按 state 关联"挂出授权 URL"与"等回调送回 code"。"""

    def __init__(self, broker: "OAuthBroker", server_name: str) -> None:
        self._broker = broker
        self.server_name = server_name
        self.state: Optional[str] = None
        self.auth_url: Optional[str] = None
        self._future: Optional[asyncio.Future] = None

    def _ensure_future(self) -> asyncio.Future:
        if self._future is None:
            self._future = asyncio.get_running_loop().create_future()
        return self._future

    async def redirect_handler(self, authorization_url: str) -> None:
        """SDK 拿到授权 URL → 按 state 登记本 flow + 把 URL 挂出来(供前端导航用户浏览器)。
        **不开本地浏览器**(headless 机器没有)。"""
        fut = self._ensure_future()  # noqa: F841 —— 提前建好,callback_handler 才 await 得到
        self.state = (parse_qs(urlsplit(authorization_url).query).get("state") or [""])[0]
        self.auth_url = authorization_url
        if self.state:
            self._broker._by_state[self.state] = self
        self._broker._pending[self.server_name] = self

    async def callback_handler(self) -> tuple[str, Optional[str]]:
        """等浏览器带着 code 打回 console 的 /api/oauth/callback。超时/被拒 → fail-loud。"""
        fut = self._ensure_future()
        try:
            code = await asyncio.wait_for(fut, timeout=_FLOW_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise OAuthCallbackError(
                f"等授权回调超时({int(_FLOW_TIMEOUT_S)}s)—— 没在浏览器完成授权登录。重试。")
        finally:
            self._cleanup()
        return code, self.state

    def fail(self, reason: str) -> None:
        fut = self._ensure_future()
        if not fut.done():
            fut.set_exception(OAuthCallbackError(reason))

    def _cleanup(self) -> None:
        if self.state:
            self._broker._by_state.pop(self.state, None)
        self._broker._pending.pop(self.server_name, None)


class OAuthBroker:
    """进程级 OAuth 回调中枢:所有跨机授权流程共用一个,挂在 app.state.oauth_broker。"""

    def __init__(self) -> None:
        self._by_state: dict[str, _Flow] = {}       # state -> 等待中的 flow
        self._pending: dict[str, _Flow] = {}         # server_name -> flow(供前端取授权 URL)

    def new_flow(self, server_name: str) -> _Flow:
        return _Flow(self, server_name)

    def deliver(self, state: str, code: str) -> bool:
        """/api/oauth/callback 收到 code → 唤醒对应 state 的 flow。命中并送达 → True。"""
        f = self._by_state.get(str(state or ""))
        if f is None:
            return False
        fut = f._ensure_future()
        if fut.done():
            return False
        fut.set_result(str(code or ""))
        return True

    def deliver_error(self, state: str, reason: str) -> bool:
        """回调带 error=(授权被拒)→ 让对应 flow fail-loud。"""
        f = self._by_state.get(str(state or ""))
        if f is None:
            return False
        f.fail(f"授权被拒或出错:{reason}")
        return True

    def pending_auth_url(self, server_name: str) -> Optional[str]:
        """前端取某 server 待授权的 URL(把用户浏览器导过去)。"""
        f = self._pending.get(server_name)
        return f.auth_url if f is not None else None


def get_broker(app) -> OAuthBroker:
    """取(或懒建)app 上的进程级 broker。"""
    b = getattr(app.state, "oauth_broker", None)
    if b is None:
        b = OAuthBroker()
        app.state.oauth_broker = b
    return b


__all__ = ["OAuthBroker", "OAuthCallbackError", "CALLBACK_PATH", "get_broker"]
