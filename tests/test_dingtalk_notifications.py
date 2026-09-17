import asyncio
import json

import pytest

from karvyloop.config_channels import DingTalkChannelConfig
from karvyloop.notifications import DingTalkAdapter, PermanentDeliveryError, RateLimitedError, RetryableDeliveryError


class Response:
    def __init__(self, status_code=200, data=None, headers=None):
        self.status_code = status_code
        self._data = data or {}
        self.headers = headers or {}

    def json(self):
        return self._data


def adapter(sender):
    return DingTalkAdapter(
        DingTalkChannelConfig(client_id="robot", client_secret="secret"),
        http_sender=sender,
        token_provider=lambda: "token",
    )


def test_single_request_shape_and_success_id():
    calls = []

    async def sender(**kwargs):
        calls.append(kwargs)
        return Response(data={"processQueryKey": ["single-id"]}, headers={"request-id": "req-1"})

    result = asyncio.run(adapter(sender).send(address_type="direct", address="user-1", content="你好"))
    assert result == {"message_id": "single-id", "request_id": "req-1"}
    assert calls[0]["url"].endswith("oToMessages/batchSend")
    assert calls[0]["headers"]["x-acs-dingtalk-access-token"] == "token"
    assert calls[0]["json"] == {"robotCode": "robot", "userIds": ["user-1"], "msgKey": "sampleText", "msgParam": json.dumps({"content": "你好"}, ensure_ascii=False)}


def test_group_request_shape_and_success_id():
    calls = []

    async def sender(**kwargs):
        calls.append(kwargs)
        return Response(data={"processQueryKey": "group-id"}, headers={"x-request-id": "req-2"})

    result = asyncio.run(adapter(sender).send(address_type="group", address="conversation-1", content="hello"))
    assert result == {"message_id": "group-id", "request_id": "req-2"}
    assert calls[0]["url"].endswith("groupMessages/send")
    assert calls[0]["json"]["openConversationId"] == "conversation-1"


@pytest.mark.parametrize("status,error", [(401, PermanentDeliveryError), (400, PermanentDeliveryError), (429, RateLimitedError), (500, RetryableDeliveryError)])
def test_http_error_classification(status, error):
    async def sender(**kwargs):
        return Response(status, {"code": "E", "message": "safe failure"})

    with pytest.raises(error):
        asyncio.run(adapter(sender).send(address_type="direct", address="u", content="x"))


def test_business_error_is_permanent_and_validation():
    async def sender(**kwargs):
        return Response(data={"errorCode": "InvalidRobot", "message": "bad"})

    with pytest.raises(PermanentDeliveryError):
        asyncio.run(adapter(sender).send(address_type="direct", address="u", content="x"))
    with pytest.raises(PermanentDeliveryError):
        asyncio.run(adapter(sender).send(address_type="unknown", address="u", content="x"))
    with pytest.raises(PermanentDeliveryError):
        asyncio.run(adapter(sender).send(address_type="direct", address="", content="x"))


def test_default_token_fetch_and_cache(monkeypatch):
    """默认 token 路径回归(实拍 bug:dingtalk_stream.Credential 没有 get_access_token,
    旧默认把 Credential 实例当 token 发出去 → "不合法的access_token")。

    现默认直连官方 oauth2 accessToken(与 SDK 同 endpoint),带模块级缓存。"""
    from karvyloop.notifications import dingtalk as dt
    monkeypatch.setattr(dt, "_TOKEN_CACHE", {})

    calls = []

    async def sender(**kwargs):
        calls.append(kwargs)
        if kwargs["url"].endswith("/oauth2/accessToken"):
            assert kwargs["json"] == {"appKey": "robot", "appSecret": "secret"}
            return Response(data={"accessToken": "tok-1", "expireIn": 7200})
        assert kwargs["headers"]["x-acs-dingtalk-access-token"] == "tok-1"
        return Response(data={"processQueryKey": "ok"})

    ad = DingTalkAdapter(DingTalkChannelConfig(client_id="robot", client_secret="secret"),
                         http_sender=sender)
    result = asyncio.run(ad.send(address_type="direct", address="u", content="x"))
    assert result["message_id"] == "ok"
    assert len(calls) == 2   # token + send
    # 第二次发送命中缓存 → 不再取 token(仍 2 次调用,token 调用不增加)
    asyncio.run(ad.send(address_type="direct", address="u", content="x"))
    token_calls = [c for c in calls if c["url"].endswith("/oauth2/accessToken")]
    assert len(token_calls) == 1


def test_default_token_failure_classification(monkeypatch):
    from karvyloop.notifications import dingtalk as dt
    monkeypatch.setattr(dt, "_TOKEN_CACHE", {})

    async def sender(**kwargs):
        if kwargs["url"].endswith("/oauth2/accessToken"):
            return Response(400, {"code": "InvalidAuthentication", "message": "bad key"})
        return Response(data={"processQueryKey": "never"})

    ad = DingTalkAdapter(DingTalkChannelConfig(client_id="robot", client_secret="secret"),
                         http_sender=sender)
    with pytest.raises(PermanentDeliveryError):
        asyncio.run(ad.send(address_type="direct", address="u", content="x"))
