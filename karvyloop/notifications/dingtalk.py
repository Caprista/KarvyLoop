from __future__ import annotations

import inspect
import json
from typing import Any, Awaitable, Callable

from karvyloop.config_channels import DingTalkChannelConfig
from .models import PermanentDeliveryError, RateLimitedError, RetryableDeliveryError


_SINGLE_URL = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
_GROUP_URL = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"

# 模块级 token 缓存:client_id → (accessToken, 过期时刻)。同进程多 adapter/多轮投递共享,
# 免去每次发送都取 token(钉钉 accessToken 有效期 ~2h;预留 5min buffer,与官方 SDK 同口径)。
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


class DingTalkAdapter:
    channel = "dingtalk"

    def __init__(
        self,
        config: DingTalkChannelConfig,
        *,
        http_sender: Callable[..., Any] | None = None,
        token_provider: Callable[[], Any] | Any | None = None,
        credential: Any | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not isinstance(config, DingTalkChannelConfig):
            raise TypeError("config must be DingTalkChannelConfig")
        if not config.client_id.strip() or not config.client_secret:
            raise PermanentDeliveryError("dingtalk credentials are required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.config = config
        self.http_sender = http_sender
        self.token_provider = token_provider if token_provider is not None else credential
        self.timeout = timeout

    async def _token(self) -> str:
        provider = self.token_provider
        if provider is not None:
            method = getattr(provider, "get_access_token", None)
            value = method() if method is not None else provider() if callable(provider) else provider
            value = await value if inspect.isawaitable(value) else value
            if isinstance(value, dict):
                value = value.get("accessToken") or value.get("access_token") or value.get("access_token_value")
            if not value:
                raise PermanentDeliveryError("DingTalk access token is empty")
            return str(value)
        # 默认路径:直连官方 oauth2 accessToken(实拍 bug 修复 —— dingtalk_stream.Credential
        # 根本没有 get_access_token(它在 DingTalkStreamClient 上),旧默认把 Credential 实例
        # 当 token 字符串发出去 → "不合法的access_token")。出站投递从此**零 SDK 依赖**
        # (SDK 只服务 Stream 入站);secret 只进请求体,绝不进日志/错误信息。
        import time as _t
        now = _t.time()
        cached = _TOKEN_CACHE.get(self.config.client_id)
        if cached and now < cached[1]:
            return cached[0]
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        body = {"appKey": self.config.client_id, "appSecret": self.config.client_secret}
        try:
            response = await self._send_http(_TOKEN_URL, headers, body)
        except (TimeoutError, OSError) as exc:
            raise RetryableDeliveryError("DingTalk token network error") from exc
        except Exception as exc:
            if exc.__class__.__module__.startswith("httpx"):
                raise RetryableDeliveryError("DingTalk token network error") from exc
            raise
        status = int(getattr(response, "status_code", 200))
        data = self._response_data(response)
        token = str(data.get("accessToken") or "")
        expire_in = int(data.get("expireIn") or 0)
        safe = f"{data.get('code') or data.get('errcode') or ''}: {data.get('message') or data.get('msg') or ''}"[:500]
        if status == 429:
            raise RateLimitedError(f"dingtalk token rate limited: {safe}", retry_after=60.0)
        if status >= 500:
            raise RetryableDeliveryError(f"dingtalk token server error: {safe}")
        if status >= 400 or not token:
            raise PermanentDeliveryError(f"dingtalk token failed: {safe or 'empty accessToken'}")
        _TOKEN_CACHE[self.config.client_id] = (token, now + max(60, expire_in - 300))
        return token

    async def _send_http(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        if self.http_sender is not None:
            result = self.http_sender(url=url, headers=headers, json=body, timeout=self.timeout)
            return await result if inspect.isawaitable(result) else result
        import httpx
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.post(url, headers=headers, json=body)

    @staticmethod
    def _response_data(response: Any) -> dict[str, Any]:
        data = response.json() if callable(getattr(response, "json", None)) else getattr(response, "json", {})
        return data if isinstance(data, dict) else {}

    async def send(self, *, address_type: str = "", address: str = "", recipient_address: str = "", content: str = "", **kwargs: Any) -> dict[str, str]:
        address = (address or recipient_address).strip()
        text = content.strip()
        kind = address_type.strip().lower()
        if kind in {"user", "direct", "single", "private"}:
            kind = "direct"
        elif kind in {"group", "conversation", "群聊"}:
            kind = "group"
        if kind not in {"direct", "group"}:
            raise PermanentDeliveryError("unknown dingtalk address_type")
        if not address:
            raise PermanentDeliveryError("dingtalk address is required")
        if not text:
            raise PermanentDeliveryError("dingtalk content is required")
        token = await self._token()
        params = json.dumps({"content": content}, ensure_ascii=False)
        body: dict[str, Any] = {"robotCode": self.config.client_id, "msgKey": "sampleText", "msgParam": params}
        if kind == "direct":
            body["userIds"] = [address]
        else:
            body["openConversationId"] = address
        headers = {"Content-Type": "application/json", "x-acs-dingtalk-access-token": token}
        try:
            response = await self._send_http(_SINGLE_URL if kind == "direct" else _GROUP_URL, headers, body)
        except (TimeoutError, OSError) as exc:
            raise RetryableDeliveryError("DingTalk network error") from exc
        except Exception as exc:
            if exc.__class__.__module__.startswith("httpx"):
                raise RetryableDeliveryError("DingTalk network error") from exc
            raise
        status = int(getattr(response, "status_code", 200))
        data = self._response_data(response)
        code = data.get("code") or data.get("errorCode") or data.get("errcode")
        message = data.get("message") or data.get("msg") or "DingTalk request failed"
        safe = f"{code}: {message}"[:500]
        if status == 429:
            raise RateLimitedError(safe, retry_after=60.0)
        if status >= 500:
            raise RetryableDeliveryError(safe)
        if status in {400, 401, 403, 404} or code:
            raise PermanentDeliveryError(safe)
        if status >= 400:
            raise RetryableDeliveryError(safe)
        process = data.get("processQueryKey", "")
        if isinstance(process, list):
            process = process[0] if process else ""
        request_id = (getattr(response, "headers", {}) or {}).get("request-id", "") or (getattr(response, "headers", {}) or {}).get("x-request-id", "")
        return {"message_id": str(process), "request_id": str(request_id)}


__all__ = ["DingTalkAdapter"]
