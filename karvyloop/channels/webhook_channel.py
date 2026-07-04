"""webhook_channel — 通用出站 webhook 推送(channels 广度:决策卡推到你真正在的地方)。

拓扑(家里机器**只出站**,永不需要公网/穿透/第三方组网):

    console --出站 HTTP:pending 卡摘要 + 回链 console 的 token URL--> 你配置的承接方
        (ntfy 话题 / Bark / Slack 兼容 incoming webhook / 任意自定义 JSON 端点)
    拍板本身回 console 完成(点通知里的回链)。

**v1 范围(诚实标注)**:只做出站推送 —— 通知到手,拍板走回链去 console(与既有
token 链接鉴权同一条路)。**入站批复不做**(邮件通道已有入站回批;webhook 入站是 P2:
需要收端点/验签/防重放一整套,v1 不半吊子上)。

一个通用渠道通吃主流承接方:preset 只是 body/headers 的**成型函数**,不为每家写一个类。
- generic:POST JSON(全字段:title/text/level/count/console_url/cards)
- ntfy:   POST 正文=文本,Title/Priority/Click 走 header(非 ASCII 标题按 RFC 2047 编码)
- bark:   GET {url}/{标题}/{正文}?url={回链}(Bark 是 GET path 风格)
- slack:  POST {"text": ...}(Slack incoming webhook 兼容:Discord /slack、Mattermost 等)
其余承接方(如飞书)用 `body_template` 自定义 body($title_json 等占位已 JSON 转义)。

诚实地板:
- **凭证纪律**:承接方 URL 可能内嵌 token/topic/key,headers 可能带 Authorization ——
  与 API key 同级机密(config repr=False)。任何日志**只记异常类别 + 脱敏后的目标**
  (只留 scheme+host,path/query 一律抹掉),绝不打 headers / 完整 URL / 通知正文。
- **digest 纪律**:推送正文只有卡摘要(≤160 字/卡),**不带 payload 全文**(承接方服务商可见内容)。
- **高危只通知**:高危卡(fs_access/大额)照样推(你得知道),但 webhook 本来就没有远程
  回批 —— 拍板一律回 console,高危卡在 console 侧照旧把关。
- **失败语义与 email 通道一致**:发送失败只记 warning、返回 {"sent": False},下轮心跳再试;
  出站 HTTP 有硬超时,任何异常不外溢 —— 绝不拖垮主循环。
- **默认不配 = 完全不跑**(零负担):config 无 channels.webhook / enabled 非真 → build 返 None。
- 回链 = console 的访问链接(可能带 token):只进推送 body(送到你自己的承接端),绝不打日志。
- 依赖:httpx(仓内既有依赖);transport 可注入(测试打桩,不出网)。
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import time
from base64 import b64encode
from pathlib import Path
from string import Template
from typing import Callable, List, Optional
from urllib.parse import quote, urlsplit

from karvyloop import i18n
from karvyloop.channels.common import (
    LEVEL_HIGH,
    LEVEL_LOW,
    LEVEL_MEDIUM,
    SUMMARY_MAX,
    eligible_pending,
    is_high_risk,
    value_level,
)
from karvyloop.config_channels import WebhookChannelConfig, load_webhook_channel_config
from karvyloop.karvy.proposal_registry import AGING_THRESHOLD_S

logger = logging.getLogger(__name__)

# ---- 常量 ----
TITLE_MAX = 120                 # 推送标题截断
MAX_CARDS_IN_TEXT = 5           # 正文最多列几张卡(其余折叠成"…还有 N 张")
BARK_SEG_MAX = 300              # Bark 走 GET path,正文段截断(URL 长度纪律)
_LEVEL_ORDER = {LEVEL_LOW: 0, LEVEL_MEDIUM: 1, LEVEL_HIGH: 2}

RUNTIME_FILENAME = "console.runtime.json"


class WebhookSendError(RuntimeError):
    """出站失败(非 2xx)。message 只含状态码 —— **绝不含 URL/headers/正文**(可安全入日志)。"""


# =============================================================================
# 脱敏:日志里只留 scheme+host(path/query 可能内嵌 token/topic/key,一律抹掉)
# =============================================================================

def redact_url(url: str) -> str:
    """URL 脱敏(日志专用):只留 scheme+host[:port],path/query/userinfo 全抹。"""
    try:
        parts = urlsplit(str(url or ""))
        host = parts.hostname or "?"
        port = f":{parts.port}" if parts.port else ""
        scheme = parts.scheme or "http"
        return f"{scheme}://{host}{port}/…"
    except Exception:
        return "<unparseable-url>"


# =============================================================================
# httpx 日志脱敏:httpx 自己会在 INFO 级打完整请求 URL("HTTP Request: POST <url>")
# —— 承接方 URL 的 path/query 可能内嵌 token,这条不堵等于凭证纪律形同虚设。
# 只对**已注册的敏感 host** 做缩短(scheme://host/…),不碰其他 httpx 使用方的日志。
# 诚实边界:盖住 httpx 的 INFO 面;httpcore 在 DEBUG/TRACE 级仍会打请求行(开发者模式),
# 生产默认日志级别不经过那里。
# =============================================================================

class _RedactHostUrlFilter(logging.Filter):
    """把日志里指向敏感 host 的完整 URL 缩成 scheme://host/…(path/query 抹掉)。"""

    def __init__(self) -> None:
        super().__init__()
        self.hosts: set = set()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            new = msg
            for host in self.hosts:
                new = re.sub(r"(https?://" + re.escape(host) + r")[^\s\"']*",
                             r"\1/…", new, flags=re.IGNORECASE)
            if new != msg:
                record.msg = new
                record.args = ()
        except Exception:  # 脱敏失败绝不反过来弄丢日志
            pass
        return True


_redact_filter = _RedactHostUrlFilter()


def _ensure_httpx_log_redaction(url: str) -> None:
    """给 httpx logger 挂脱敏 filter(幂等),并登记这个 URL 的 host 为敏感 host。"""
    try:
        host = urlsplit(str(url or "")).hostname
        if host:
            _redact_filter.hosts.add(host.lower())
        httpx_logger = logging.getLogger("httpx")
        if _redact_filter not in httpx_logger.filters:
            httpx_logger.addFilter(_redact_filter)
    except Exception:  # pragma: no cover - 防御:装不上 filter 也不阻断通道
        pass


# =============================================================================
# 回链:console 的访问链接(拍板回这里完成)
# =============================================================================

def default_console_link(runtime_path=None) -> str:
    """读 console.runtime.json(console 启动时落的 host/port/token)拼访问链接。

    优先跨设备 token 链接(webhook 收端多半是手机);console 没跑过 / 读不出 → 空串
    (推送照发,只是不带回链)。链接可能含 token —— 只进推送 body,**绝不打日志**。
    """
    p = Path(runtime_path) if runtime_path else Path.home() / ".karvyloop" / RUNTIME_FILENAME
    if not p.exists():
        return ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ""
        host = str(data.get("host") or "127.0.0.1")
        port = int(data.get("port") or 8766)
        token = str(data.get("token") or "")
        # 复用访问链接的单一真理源(薄工具模块,纯标准库);lazy import,失败退空
        from karvyloop.console.access import access_urls
        urls = access_urls(host, port, token)
        return str(urls.get("remote") or urls.get("local") or "")
    except Exception:
        return ""


# =============================================================================
# PushNote:一次推送的内容(与承接方无关的中间形态)
# =============================================================================

@dataclasses.dataclass(frozen=True)
class PushNote:
    """一次推送的内容:标题 + 正文(卡摘要,无 payload 全文)+ 回链 + 价值等级。"""
    title: str
    text: str
    # console 回链(可能带 token:只进 body,repr 也不出 —— 防"顺手 log 对象"级泄露);可为空
    link: str = dataclasses.field(repr=False)
    level: str         # 全体卡的最高价值等级(high/medium/low)
    count: int
    cards: tuple = ()  # 每卡结构化摘要(generic preset 用)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _card_line(idx: int, prop, age_s: float, aging_threshold_s: float) -> str:
    summary = _squash(getattr(prop, "summary", ""))
    if len(summary) > SUMMARY_MAX:
        summary = summary[:SUMMARY_MAX] + "…"
    parts = [f"[{idx}]"]
    if age_s >= aging_threshold_s:
        parts.append(i18n.t("channels.webhook.aging", days=max(int(age_s // 86400), 1)))
    parts.append(summary)
    line = " ".join(parts)
    if is_high_risk(prop):
        line += " — " + i18n.t("channels.webhook.high_risk")
    return line


# =============================================================================
# preset 成型函数:PushNote → WebhookRequest(不为每家写一个类)
# =============================================================================

@dataclasses.dataclass(frozen=True)
class WebhookRequest:
    """一次成型好的出站请求。headers/url 可能含机密 → repr 不进日志(整个对象不打日志)。"""
    method: str
    url: str = dataclasses.field(repr=False)
    headers: dict = dataclasses.field(default_factory=dict, repr=False)
    body: Optional[bytes] = dataclasses.field(default=None, repr=False)


def _header_value(value: str) -> str:
    """HTTP header 值:ASCII 原样;非 ASCII 按 RFC 2047 编码(ntfy 明确支持此格式)。"""
    v = str(value or "")
    try:
        v.encode("ascii")
        return v
    except UnicodeEncodeError:
        return "=?utf-8?b?" + b64encode(v.encode("utf-8")).decode("ascii") + "?="


def _shape_generic(cfg: WebhookChannelConfig, note: PushNote) -> WebhookRequest:
    payload = {
        "source": "karvyloop",
        "event": "h2a.pending",
        "title": note.title,
        "text": note.text,
        "level": note.level,
        "count": note.count,
        "console_url": note.link,
        "cards": list(note.cards),
    }
    return WebhookRequest(
        method="POST", url=cfg.url,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


def _shape_ntfy(cfg: WebhookChannelConfig, note: PushNote) -> WebhookRequest:
    headers = {
        "Title": _header_value(note.title),
        "Priority": "high" if note.level == LEVEL_HIGH else "default",
    }
    if note.link:
        headers["Click"] = note.link   # 点通知直达 console(URL 本身是 ASCII)
    return WebhookRequest(method="POST", url=cfg.url, headers=headers,
                          body=note.text.encode("utf-8"))


def _shape_bark(cfg: WebhookChannelConfig, note: PushNote) -> WebhookRequest:
    base = cfg.url.rstrip("/")
    title_seg = quote(note.title[:TITLE_MAX], safe="")
    body_seg = quote(_squash(note.text)[:BARK_SEG_MAX], safe="")
    url = f"{base}/{title_seg}/{body_seg}"
    if note.link:
        url += f"?url={quote(note.link, safe='')}"
    return WebhookRequest(method="GET", url=url, headers={}, body=None)


def _shape_slack(cfg: WebhookChannelConfig, note: PushNote) -> WebhookRequest:
    text = f"*{note.title}*\n{note.text}"
    if note.link:
        text += "\n" + i18n.t("channels.webhook.open", url=note.link)
    return WebhookRequest(
        method="POST", url=cfg.url,
        headers={"Content-Type": "application/json"},
        body=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
    )


_PRESET_SHAPERS = {
    "generic": _shape_generic,
    "ntfy": _shape_ntfy,
    "bark": _shape_bark,
    "slack": _shape_slack,
}


def build_request(cfg: WebhookChannelConfig, note: PushNote) -> WebhookRequest:
    """成型一次出站请求:body_template 优先(自定义承接方),否则走 preset;用户 headers 覆盖同名头。

    body_template 占位($ 语法,safe_substitute):
      $title $text $link $level $count —— 原文;
      $title_json $text_json $link_json —— 已 JSON 转义(含引号),嵌 JSON 模板用这组防注坏。
    """
    if cfg.body_template:
        rendered = Template(cfg.body_template).safe_substitute(
            title=note.title, text=note.text, link=note.link,
            level=note.level, count=str(note.count),
            title_json=json.dumps(note.title, ensure_ascii=False),
            text_json=json.dumps(note.text, ensure_ascii=False),
            link_json=json.dumps(note.link, ensure_ascii=False),
        )
        headers = {}
        if rendered.lstrip().startswith("{"):
            headers["Content-Type"] = "application/json"
        headers.update(cfg.headers)
        return WebhookRequest(method="POST", url=cfg.url, headers=headers,
                              body=rendered.encode("utf-8"))
    shaper = _PRESET_SHAPERS.get(cfg.preset, _shape_generic)
    req = shaper(cfg, note)
    if cfg.headers:
        return dataclasses.replace(req, headers={**req.headers, **cfg.headers})
    return req


# =============================================================================
# 默认 transport:httpx 出站(硬超时;非 2xx 抛安全异常)
# =============================================================================

def _default_http_send(cfg: WebhookChannelConfig, req: WebhookRequest) -> None:
    """默认 HTTP transport:硬超时;非 2xx 抛 WebhookSendError(message 只含状态码,可安全入日志)。"""
    import httpx
    _ensure_httpx_log_redaction(cfg.url)   # httpx INFO 会打完整 URL —— 先把这条脱敏堵上
    timeout = max(float(cfg.timeout_s), 1.0)
    with httpx.Client(timeout=timeout) as client:
        resp = client.request(req.method, req.url,
                              headers=req.headers or None, content=req.body)
    if resp.status_code >= 300:
        # 不用 raise_for_status:它的异常文本内嵌完整 URL(可能含 token),日志不可入
        raise WebhookSendError(f"HTTP {resp.status_code}")


# =============================================================================
# WebhookPusher — 出站推送本体(与 EmailDigestSender 同节流/同失败语义)
# =============================================================================

class WebhookPusher:
    """把 pending 决策卡打成一条推送发到承接方(节流;失败只记类别,下轮再试)。

    - 卡挑选与 email digest 同一口径(channels/common.eligible_pending:DEFER 老化语义)。
    - transport / console_link 可注入(测试打桩,不出网)。
    """

    def __init__(self, cfg: WebhookChannelConfig, registry, *,
                 transport: Optional[Callable] = None,
                 console_link: Optional[Callable[[], str]] = None,
                 aging_threshold_s: float = AGING_THRESHOLD_S) -> None:
        self._cfg = cfg
        self._registry = registry
        self._transport = transport or _default_http_send
        self._console_link = console_link or default_console_link
        self._aging_threshold_s = float(aging_threshold_s)
        self._last_sent_ts: float = 0.0

    def build_note(self, now: float) -> PushNote:
        """PushNote(count=0 → 没得推)。正文只有摘要,不带 payload 全文。"""
        cards = eligible_pending(self._registry, now, self._aging_threshold_s)
        if not cards:
            return PushNote(title="", text="", link="", level=LEVEL_LOW, count=0)
        lines: List[str] = []
        card_dicts: List[dict] = []
        level = LEVEL_LOW
        for i, (prop, age_s) in enumerate(cards, 1):
            lvl = value_level(prop)
            if _LEVEL_ORDER.get(lvl, 0) > _LEVEL_ORDER.get(level, 0):
                level = lvl
            summary = _squash(getattr(prop, "summary", ""))
            card_dicts.append({
                "proposal_id": str(getattr(prop, "proposal_id", "") or ""),
                "kind": str(getattr(prop, "kind", "") or ""),
                "summary": (summary[:SUMMARY_MAX] + "…") if len(summary) > SUMMARY_MAX else summary,
                "level": lvl,
                "age_s": int(age_s),
            })
            if i <= MAX_CARDS_IN_TEXT:
                lines.append(_card_line(i, prop, age_s, self._aging_threshold_s))
        if len(cards) > MAX_CARDS_IN_TEXT:
            lines.append(i18n.t("channels.webhook.more", n=len(cards) - MAX_CARDS_IN_TEXT))
        try:
            link = str(self._console_link() or "")
        except Exception:
            link = ""   # 回链拿不到不阻断推送(通知本身仍有价值)
        title = i18n.t("channels.webhook.title", n=len(cards))[:TITLE_MAX]
        return PushNote(title=title, text="\n".join(lines), link=link,
                        level=level, count=len(cards), cards=tuple(card_dicts))

    def push_if_due(self, now: Optional[float] = None) -> dict:
        """有卡且过了节流窗才推。返回 {"sent": bool, "reason"/"cards": ...};发送失败不抛。"""
        t = time.time() if now is None else float(now)
        if self._last_sent_ts and (t - self._last_sent_ts) < self._cfg.min_interval_s:
            return {"sent": False, "reason": "throttled"}
        note = self.build_note(t)
        if note.count == 0:
            return {"sent": False, "reason": "no_pending"}
        req = build_request(self._cfg, note)
        try:
            self._transport(self._cfg, req)
        except WebhookSendError as e:
            # e 的文本由我们自己铸(只含状态码),可安全入日志;目标只留 scheme+host
            logger.warning("[channels.webhook] 推送失败(下轮再试):%s target=%s",
                           e, redact_url(self._cfg.url))
            return {"sent": False, "reason": "send_failed"}
        except Exception as e:
            # 第三方异常文本可能内嵌完整 URL —— 只记异常类别,绝不 str(e)
            logger.warning("[channels.webhook] 推送失败(下轮再试):%s target=%s",
                           type(e).__name__, redact_url(self._cfg.url))
            return {"sent": False, "reason": "send_failed"}
        self._last_sent_ts = t
        return {"sent": True, "cards": note.count}


# =============================================================================
# 组装 + tick(接线由主线做:本模块不碰 console/app.py)
# =============================================================================

def build_webhook_channel(*, registry, config_path=None,
                          transport: Optional[Callable] = None,
                          console_link: Optional[Callable[[], str]] = None
                          ) -> Optional[WebhookPusher]:
    """从 config.yaml 组一条 webhook 推送通道;**默认不配 = 返 None,完全不跑**(零负担)。

    registry 需有 pending()/proposal_meta()(PendingProposalRegistry 满足)。
    """
    cfg = load_webhook_channel_config(config_path)
    if cfg is None:
        return None
    return WebhookPusher(cfg, registry, transport=transport, console_link=console_link)


async def webhook_channel_tick(pusher: Optional[WebhookPusher], *,
                               now: Optional[float] = None) -> dict:
    """一次通道心跳:推一条(有节流)。出站 HTTP 是阻塞 IO → 丢线程跑,不占事件循环。

    pusher=None(未配置)→ 空转返回。**本函数不自己接 app.py** —— 接线由主线做。
    """
    import asyncio
    if pusher is None:
        return {"push": None}
    return {"push": await asyncio.to_thread(pusher.push_if_due, now)}


__all__ = [
    "MAX_CARDS_IN_TEXT",
    "TITLE_MAX",
    "PushNote",
    "WebhookPusher",
    "WebhookRequest",
    "WebhookSendError",
    "build_request",
    "build_webhook_channel",
    "default_console_link",
    "redact_url",
    "webhook_channel_tick",
]
