"""config_channels — 卡片外推通道的配置解析(config.yaml `channels:` 块)。

设计(docs/43 ⑤a;inbox 块 = docs/49 ⑲-① 收件箱→决策卡管道):
    channels:
      email:
        enabled: true
        smtp: {host: smtp.example.com, port: 465, user: me@example.com, password: "..."}
        imap: {host: imap.example.com, port: 993, user: me@example.com, password: "..."}
        to: me@example.com            # digest 发给谁(手机邮件 App 收)
        digest_min_interval_s: 3600   # digest 节流(默认 1h)
        inbox:                        # 待处理收件箱分诊(独立于回批 poller;默认关)
          enabled: true
          folder: INBOX               # 盯哪个邮箱文件夹
          poll_interval_s: 300        # 轮询间隔(接线方参考值)
          max_cards_per_tick: 5       # 每轮最多出几张决策卡(其余记 backlog)

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
DEFAULT_INBOX_FOLDER = "INBOX"
DEFAULT_INBOX_POLL_INTERVAL_S = 300
DEFAULT_INBOX_MAX_CARDS = 5


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


@dataclasses.dataclass(frozen=True)
class InboxPipeConfig:
    """收件箱分诊管道配置(docs/49 ⑲-①)。**只收不发**:仅 IMAP 端点,结构上没有 SMTP 字段。

    独立开关:`channels.email.inbox.enabled` 显式 true 才跑(默认关,零负担);
    IMAP 连接凭证**复用** `channels.email.imap`(同一收件箱账号,同级机密纪律)。
    注意:inbox 块不受 `channels.email.enabled`(digest/回批通道的开关)控制 —— 两条腿各自独立。
    """
    imap: ImapEndpoint
    folder: str = DEFAULT_INBOX_FOLDER
    poll_interval_s: int = DEFAULT_INBOX_POLL_INTERVAL_S
    max_cards_per_tick: int = DEFAULT_INBOX_MAX_CARDS
    enabled: bool = True


def _positive_int(value, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def inbox_pipe_config_from_dict(cfg: dict) -> Optional[InboxPipeConfig]:
    """从整份 config dict 解析 channels.email.inbox;不配 / 未启用 / IMAP 缺 → None(管道完全不跑)。"""
    channels = (cfg or {}).get("channels") or {}
    if not isinstance(channels, dict):
        return None
    email = channels.get("email") or {}
    if not isinstance(email, dict):
        return None
    inbox = email.get("inbox") or {}
    if not isinstance(inbox, dict) or not inbox:
        return None
    if not bool(inbox.get("enabled")):
        return None  # 显式 enabled: true 才跑(零负担默认)

    imap = _endpoint(email.get("imap") or {}, ImapEndpoint, DEFAULT_IMAP_PORT)
    missing = [name for name, val in (("imap.host", imap.host), ("imap.user", imap.user)) if not val]
    if missing:
        # 只报字段名,绝不带值(值可能含机密)
        logger.warning("[channels.inbox] 已 enabled 但缺必填字段 %s —— 管道不启动", missing)
        return None

    folder = str(inbox.get("folder") or DEFAULT_INBOX_FOLDER).strip() or DEFAULT_INBOX_FOLDER
    return InboxPipeConfig(
        imap=imap,
        folder=folder,
        poll_interval_s=_positive_int(inbox.get("poll_interval_s"), DEFAULT_INBOX_POLL_INTERVAL_S),
        max_cards_per_tick=_positive_int(inbox.get("max_cards_per_tick"), DEFAULT_INBOX_MAX_CARDS),
        enabled=True,
    )


def load_inbox_pipe_config(config_path=None) -> Optional[InboxPipeConfig]:
    """读 config.yaml 的 channels.email.inbox。文件缺失/读不出/块缺失 → None(完全不跑)。"""
    p = Path(config_path) if config_path else _default_config_path()
    if not p.exists():
        return None
    try:
        import yaml
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("[channels.inbox] config.yaml 读取失败 —— 管道不启动")
        return None
    if not isinstance(cfg, dict):
        return None
    return inbox_pipe_config_from_dict(cfg)


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
    "InboxPipeConfig",
    "email_channel_config_from_dict",
    "load_email_channel_config",
    "inbox_pipe_config_from_dict",
    "load_inbox_pipe_config",
    "DEFAULT_DIGEST_MIN_INTERVAL_S",
    "DEFAULT_INBOX_FOLDER",
    "DEFAULT_INBOX_POLL_INTERVAL_S",
    "DEFAULT_INBOX_MAX_CARDS",
]
