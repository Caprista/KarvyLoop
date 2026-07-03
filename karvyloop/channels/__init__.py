"""channels — 卡片外推通道(docs/43 ⑤a)。

核心思想:家里机器**只出站**(SMTP 发 digest、IMAP 轮询回信),永不需要公网 IP /
内网穿透 / 第三方组网 —— 决策卡随邮件到手机,回信主题就是拍板。
"""
from karvyloop.channels.email_channel import (
    EmailChannel,
    EmailDecisionPoller,
    EmailDigestSender,
    UsedCodeStore,
    build_email_channel,
    email_channel_tick,
    is_high_risk,
    load_or_create_secret,
    mint_code,
    verify_code,
)

__all__ = [
    "EmailChannel",
    "EmailDigestSender",
    "EmailDecisionPoller",
    "UsedCodeStore",
    "build_email_channel",
    "email_channel_tick",
    "is_high_risk",
    "load_or_create_secret",
    "mint_code",
    "verify_code",
]
