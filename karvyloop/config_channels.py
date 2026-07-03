"""config_channels — 卡片外推通道的配置解析(config.yaml `channels:` 块)。

设计(docs/43 ⑤a):
    channels:
      email:
        enabled: true
        smtp: {host: smtp.example.com, port: 465, user: me@example.com, password: "..."}
        imap: {host: imap.example.com, port: 993, user: me@example.com, password: "..."}
        to: me@example.com            # digest 发给谁(手机邮件 App 收)
        digest_min_interval_s: 3600   # digest 节流(默认 1h)

铁律:
- **默认不配 = 完全不跑**(零负担):块缺失 / enabled 非真 / 必填缺 → 返 None,通道不启动。
- **邮箱授权码与 API key 同级机密**:password 字段 `repr=False`,绝不打日志、绝不进 export
  (config.yaml 本来就在 export 排除表)。本模块只读不写,不动别人的 config 读写路径。
"""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SMTP_PORT = 465
DEFAULT_IMAP_PORT = 993
DEFAULT_DIGEST_MIN_INTERVAL_S = 3600


def _default_config_path() -> Path:
    return Path.home() / ".karvyloop" / "config.yaml"


@dataclasses.dataclass(frozen=True)
class SmtpEndpoint:
    host: str = ""
    port: int = DEFAULT_SMTP_PORT
    user: str = ""
    password: str = dataclasses.field(default="", repr=False)  # 机密:不进 repr/日志


@dataclasses.dataclass(frozen=True)
class ImapEndpoint:
    host: str = ""
    port: int = DEFAULT_IMAP_PORT
    user: str = ""
    password: str = dataclasses.field(default="", repr=False)  # 机密:不进 repr/日志


@dataclasses.dataclass(frozen=True)
class EmailChannelConfig:
    """邮件通道配置(enabled 且必填齐才会被 load 返回)。"""
    smtp: SmtpEndpoint
    imap: ImapEndpoint
    to: str
    digest_min_interval_s: int = DEFAULT_DIGEST_MIN_INTERVAL_S
    enabled: bool = True

    @property
    def reply_addr(self) -> str:
        """mailto 回批链接的收件地址 = poller 盯的那个收件箱(IMAP 账号;缺则 SMTP 账号)。"""
        return self.imap.user or self.smtp.user


def _endpoint(d: dict, cls, default_port: int):
    d = d or {}
    try:
        port = int(d.get("port") or default_port)
    except (TypeError, ValueError):
        port = default_port
    return cls(
        host=str(d.get("host") or "").strip(),
        port=port,
        user=str(d.get("user") or "").strip(),
        password=str(d.get("password") or ""),
    )


def email_channel_config_from_dict(cfg: dict) -> Optional[EmailChannelConfig]:
    """从整份 config dict 解析 channels.email;不配 / 未启用 / 必填缺 → None(通道完全不跑)。"""
    channels = (cfg or {}).get("channels") or {}
    if not isinstance(channels, dict):
        return None
    email = channels.get("email") or {}
    if not isinstance(email, dict) or not email:
        return None
    if not bool(email.get("enabled")):
        return None  # 显式 enabled: true 才跑(零负担默认)

    smtp = _endpoint(email.get("smtp") or {}, SmtpEndpoint, DEFAULT_SMTP_PORT)
    imap = _endpoint(email.get("imap") or {}, ImapEndpoint, DEFAULT_IMAP_PORT)
    to = str(email.get("to") or "").strip()

    missing = [name for name, val in (("smtp.host", smtp.host), ("to", to)) if not val]
    if missing:
        # 只报字段名,绝不带值(值可能含机密)
        logger.warning("[channels.email] 已 enabled 但缺必填字段 %s —— 通道不启动", missing)
        return None

    try:
        interval = int(email.get("digest_min_interval_s") or DEFAULT_DIGEST_MIN_INTERVAL_S)
    except (TypeError, ValueError):
        interval = DEFAULT_DIGEST_MIN_INTERVAL_S
    return EmailChannelConfig(
        smtp=smtp, imap=imap, to=to,
        digest_min_interval_s=max(interval, 0), enabled=True,
    )


def load_email_channel_config(config_path=None) -> Optional[EmailChannelConfig]:
    """读 config.yaml 的 channels.email。文件缺失/读不出/块缺失 → None(完全不跑)。"""
    p = Path(config_path) if config_path else _default_config_path()
    if not p.exists():
        return None
    try:
        import yaml
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("[channels.email] config.yaml 读取失败 —— 通道不启动")
        return None
    if not isinstance(cfg, dict):
        return None
    return email_channel_config_from_dict(cfg)


__all__ = [
    "EmailChannelConfig",
    "SmtpEndpoint",
    "ImapEndpoint",
    "email_channel_config_from_dict",
    "load_email_channel_config",
    "DEFAULT_DIGEST_MIN_INTERVAL_S",
]
