"""channels — 卡片外推通道(docs/43 ⑤a)。

核心思想:家里机器**只出站**(SMTP 发 digest、IMAP 轮询回信、HTTP 推 webhook),
永不需要公网 IP / 内网穿透 / 第三方组网 —— 决策卡随邮件/推送到手机;
回批走邮件回信(邮件通道)或点回链回 console 拍板(webhook 通道 v1 只出站)。
"""
from karvyloop.channels.common import (
    eligible_pending,
    is_high_risk,
    value_level,
)
from karvyloop.channels.email_channel import (
    EmailChannel,
    EmailDecisionPoller,
    EmailDigestSender,
    UsedCodeStore,
    build_email_channel,
    email_channel_tick,
    load_or_create_secret,
    mint_code,
    verify_code,
)
from karvyloop.channels.webhook_channel import (
    WebhookPusher,
    build_webhook_channel,
    webhook_channel_tick,
)

__all__ = [
    "EmailChannel",
    "EmailDigestSender",
    "EmailDecisionPoller",
    "UsedCodeStore",
    "WebhookPusher",
    "build_email_channel",
    "build_webhook_channel",
    "email_channel_tick",
    "webhook_channel_tick",
    "eligible_pending",
    "is_high_risk",
    "value_level",
    "load_or_create_secret",
    "mint_code",
    "verify_code",
]
