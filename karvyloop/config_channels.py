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
      webhook:                        # 通用出站推送(ntfy / Bark / Slack 兼容 / 任意 JSON 端点)
        enabled: true
        url: https://ntfy.sh/your-topic   # 承接方 URL(可能内嵌 token/topic/key → 机密级)
        preset: ntfy                  # generic | ntfy | bark | slack(默认 generic)
        headers: {Authorization: "Bearer ..."}   # 可选附加头(覆盖 preset 同名头;机密级)
        body_template: ""             # 可选自定义 body 模板(设了就不走 preset 成型)
        min_interval_s: 3600          # 推送节流(默认 1h,与 email digest 同语义)
        timeout_s: 10                 # 出站 HTTP 超时(秒)
        reply_url: https://ntfy.sh/your-topic/json?poll=1   # 可选:入站回批的**轮询拉取**源
                                      # (返回 JSON 消息数组/NDJSON 的端点;不配 = 纯出站 v1)
        reply_headers: {}             # 可选:轮询拉取的附加头(机密级,同 headers 纪律)

铁律:
- **默认不配 = 完全不跑**(零负担):块缺失 / enabled 非真 / 必填缺 → 返 None,通道不启动。
- **邮箱授权码 / webhook URL·headers 与 API key 同级机密**:机密字段一律 `repr=False`,
  绝不打日志、绝不进 export(config.yaml 本来就在 export 排除表)。校验失败**只报字段名不带值**。
  本模块只读不写,不动别人的 config 读写路径。
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


# =============================================================================
# Webhook 推送通道(channels 广度:决策卡推到你真正在的地方)
# =============================================================================

DEFAULT_WEBHOOK_PRESET = "generic"
DEFAULT_WEBHOOK_MIN_INTERVAL_S = 3600
DEFAULT_WEBHOOK_TIMEOUT_S = 10.0

# 内置 preset(成型函数在 channels/webhook_channel.py;这里只做名字校验)
WEBHOOK_PRESETS = ("generic", "ntfy", "bark", "slack")
_WEBHOOK_PRESET_ALIASES = {
    "generic-json": "generic",
    "json": "generic",
    "slack-compatible": "slack",
}


@dataclasses.dataclass(frozen=True)
class WebhookChannelConfig:
    """webhook 推送通道配置(enabled 且必填齐才会被 load 返回)。

    url / headers 是机密级(URL 可能内嵌 token/topic/key,headers 可能带 Authorization)
    → `repr=False`,绝不打日志。
    """
    url: str = dataclasses.field(default="", repr=False)       # 机密:不进 repr/日志
    preset: str = DEFAULT_WEBHOOK_PRESET
    headers: dict = dataclasses.field(default_factory=dict, repr=False)  # 机密:不进 repr/日志
    body_template: str = ""
    min_interval_s: int = DEFAULT_WEBHOOK_MIN_INTERVAL_S
    timeout_s: float = DEFAULT_WEBHOOK_TIMEOUT_S
    # 入站回批(v2,可选):轮询拉取的 reply source(如 ntfy 的 /json?poll=1 端点)。
    # URL 内嵌私有 topic = 机密级;不配(空)= 纯出站 v1 行为零变化。
    reply_url: str = dataclasses.field(default="", repr=False)  # 机密:不进 repr/日志
    reply_headers: dict = dataclasses.field(default_factory=dict, repr=False)  # 机密
    enabled: bool = True


def webhook_channel_config_from_dict(cfg: dict) -> Optional[WebhookChannelConfig]:
    """从整份 config dict 解析 channels.webhook;不配 / 未启用 / 必填缺 / preset 不认识 → None。

    校验失败只报字段名,**绝不带值**(url/headers 可能含机密)。
    """
    channels = (cfg or {}).get("channels") or {}
    if not isinstance(channels, dict):
        return None
    hook = channels.get("webhook") or {}
    if not isinstance(hook, dict) or not hook:
        return None
    if not bool(hook.get("enabled")):
        return None  # 显式 enabled: true 才跑(零负担默认)

    url = str(hook.get("url") or "").strip()
    if not url or not (url.startswith("https://") or url.startswith("http://")):
        # 缺失或不是 http(s) —— 只报字段名,不带值
        logger.warning("[channels.webhook] 已 enabled 但缺必填字段 ['url'](须为 http(s) URL)—— 通道不启动")
        return None

    preset = str(hook.get("preset") or DEFAULT_WEBHOOK_PRESET).strip().lower()
    preset = _WEBHOOK_PRESET_ALIASES.get(preset, preset)
    if preset not in WEBHOOK_PRESETS:
        # fail-loud:preset 打错字不如不启动(静默按错格式推等于假通道)
        logger.warning("[channels.webhook] preset 不认识(可选:%s)—— 通道不启动",
                       "/".join(WEBHOOK_PRESETS))
        return None

    raw_headers = hook.get("headers") or {}
    headers = ({str(k): str(v) for k, v in raw_headers.items()}
               if isinstance(raw_headers, dict) else {})

    # 入站回批(v2,可选):reply_url 配了就必须是 http(s) —— 配错 fail-loud 不启动
    # (静默降级成"只出站"= 用户以为能手机回批实际黑洞,比不启动更糟)。
    reply_url = str(hook.get("reply_url") or "").strip()
    if reply_url and not (reply_url.startswith("https://") or reply_url.startswith("http://")):
        logger.warning("[channels.webhook] reply_url 不是 http(s) URL —— 通道不启动")
        return None
    raw_reply_headers = hook.get("reply_headers") or {}
    reply_headers = ({str(k): str(v) for k, v in raw_reply_headers.items()}
                     if isinstance(raw_reply_headers, dict) else {})

    try:
        interval = int(hook.get("min_interval_s") or DEFAULT_WEBHOOK_MIN_INTERVAL_S)
    except (TypeError, ValueError):
        interval = DEFAULT_WEBHOOK_MIN_INTERVAL_S
    try:
        timeout = float(hook.get("timeout_s") or DEFAULT_WEBHOOK_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout = DEFAULT_WEBHOOK_TIMEOUT_S
    return WebhookChannelConfig(
        url=url,
        preset=preset,
        headers=headers,
        body_template=str(hook.get("body_template") or ""),
        min_interval_s=max(interval, 0),
        timeout_s=timeout if timeout > 0 else DEFAULT_WEBHOOK_TIMEOUT_S,
        reply_url=reply_url,
        reply_headers=reply_headers,
        enabled=True,
    )


def load_webhook_channel_config(config_path=None) -> Optional[WebhookChannelConfig]:
    """读 config.yaml 的 channels.webhook。文件缺失/读不出/块缺失 → None(完全不跑)。"""
    p = Path(config_path) if config_path else _default_config_path()
    if not p.exists():
        return None
    try:
        import yaml
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("[channels.webhook] config.yaml 读取失败 —— 通道不启动")
        return None
    if not isinstance(cfg, dict):
        return None
    return webhook_channel_config_from_dict(cfg)


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
    "WebhookChannelConfig",
    "email_channel_config_from_dict",
    "load_email_channel_config",
    "inbox_pipe_config_from_dict",
    "load_inbox_pipe_config",
    "webhook_channel_config_from_dict",
    "load_webhook_channel_config",
    "DEFAULT_DIGEST_MIN_INTERVAL_S",
    "DEFAULT_INBOX_FOLDER",
    "DEFAULT_INBOX_POLL_INTERVAL_S",
    "DEFAULT_INBOX_MAX_CARDS",
    "DEFAULT_WEBHOOK_MIN_INTERVAL_S",
    "DEFAULT_WEBHOOK_TIMEOUT_S",
    "WEBHOOK_PRESETS",
]
