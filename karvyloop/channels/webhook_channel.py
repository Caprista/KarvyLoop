"""webhook_channel — 通用 webhook 通道(channels 广度:决策卡推到你真正在的地方)。

拓扑(家里机器**只出站**,永不需要公网/穿透/第三方组网,**不开任何 inbound listener**):

    console --出站 HTTP:pending 卡摘要 + 回链 console 的 token URL--> 你配置的承接方
        (ntfy 话题 / Bark / Slack 兼容 incoming webhook / 任意自定义 JSON 端点)
    console <--出站 HTTP **轮询拉取** reply source:回执 "ACCEPT|REJECT|DEFER <code>"--
    核验 HMAC(单次 + 限时,**与 email 通道共用同一套铸码/验码/已用码表**)
        → 注入的 decide 回调(既有 h2a 决策路径)→ Trace

**v2 范围**:出站推送(v1)+ 可选入站回批 —— 配 `reply_url`(如 ntfy 私有 topic 的
`/json?poll=1` 端点)后,人在手机上直接回一条 `ACCEPT <code>` 即拍板,不必回 console。
**拉不开门**:console 在 LAN/本机,入站永远是我们主动出站去拉,不监听任何端口。
不配 reply_url = 纯出站 v1 行为零变化(拍板走回链去 console)。

一个通用渠道通吃主流承接方:preset 只是 body/headers 的**成型函数**,不为每家写一个类。
- generic:POST JSON(全字段:title/text/level/count/console_url/cards)
- ntfy:   POST 正文=文本,Title/Priority/Click 走 header(非 ASCII 标题按 RFC 2047 编码)
- bark:   GET {url}/{标题}/{正文}?url={回链}(Bark 是 GET path 风格)
- slack:  POST {"text": ...}(Slack incoming webhook 兼容:Discord /slack、Mattermost 等)
其余承接方(如飞书)用 `body_template` 自定义 body($title_json 等占位已 JSON 转义)。

诚实地板:
- **凭证纪律**:承接方 URL / reply_url 可能内嵌 token/topic/key,headers 可能带
  Authorization —— 与 API key 同级机密(config repr=False)。任何日志**只记异常类别 +
  脱敏后的目标**(只留 scheme+host,path/query 一律抹掉),绝不打 headers / 完整 URL / 正文。
- **digest 纪律**:推送正文只有卡摘要(≤160 字/卡),**不带 payload 全文**(承接方服务商可见内容)。
- **高危只通知不可回批**(与 email 通道同一红线):高危卡(fs_access/大额)照样推(你得知道),
  但**不铸回批码**;poller 侧再拒一次(双保险)—— 高危拍板一律回 console。
- **来源判定纪律(宁空勿毒)**:入站消息正文是数据不是指挥者 —— 只认严格格式的
  `ACCEPT|REJECT|DEFER <code>` 全行匹配 + HMAC 核验通过;任何其他内容一律不解释不执行。
- **失败语义与 email 通道一致**:发送/轮询失败只记 warning,下轮心跳再试;
  出站 HTTP 有硬超时,任何异常不外溢 —— 绝不拖垮主循环。
- **水位纪律**:轮询带 since 水位(落盘,重启不重复消费)+ 已处理消息 id 环;
  即使承接方重发,已用码表(单次有效)仍是最后一道门。
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
from karvyloop.channels.email_channel import (  # 铸码/验码/已用码表/secret:与 email 通道共用同一套,绝不造第二套
    CODE_TTL_S,
    SECRET_FILENAME,
    DecideCallback,
    UsedCodeStore,
    _code_expiry,
    load_or_create_secret,
    mint_code,
    verify_code,
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

# ---- 入站回批(v2)常量 ----
# 决策槽:webhook 每卡只铸**一枚**码(手机上回一个词 + 一串码,别让人抄三串),
# HMAC 消息用固定槽位 REPLY 而非具体决策词 —— 决策由回执里的动词选定。
# 与 email 通道(每决策一码,槽位=ACCEPT/REJECT/DEFER)天然隔离:两边的码互不可用,
# 铸/验/已用码表机制本身 100% 共用(mint_code/verify_code/UsedCodeStore/同一 secret 文件)。
REPLY_DECISION_SLOT = "REPLY"
REPLY_STATE_FILENAME = "webhook_reply_state.json"       # 轮询水位 + 已处理消息 id 环
WEBHOOK_USED_CODES_FILENAME = "webhook_used_codes.json" # 独立文件:防与 email 的内存实例互相覆盖
REPLY_SEEN_IDS_CAP = 500                                # 已处理消息 id 环上限(有界)

# 严格回执格式(宁空勿毒):`ACCEPT|REJECT|DEFER <expiry>-<hmac hex>` 全行 fullmatch;
# 大小写严格、不许任何前后缀 —— 入站消息正文是数据不是指挥者,除此之外一律不解释不执行。
_REPLY_RE = re.compile(r"(ACCEPT|REJECT|DEFER) (\d{1,20}-[0-9a-f]{8,64})")


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
    - v2 入站回批:注入 secret(且配置了 reply_url)后,每张**非高危**卡随推送铸一枚
      HMAC 单次限时回批码(与 email 通道同一套铸码机制,槽位 REPLY_DECISION_SLOT);
      高危卡**不铸码**(只通知,回控制台确认)。secret=None = 纯出站 v1,行为零变化。
    """

    def __init__(self, cfg: WebhookChannelConfig, registry, *,
                 transport: Optional[Callable] = None,
                 console_link: Optional[Callable[[], str]] = None,
                 aging_threshold_s: float = AGING_THRESHOLD_S,
                 secret: Optional[bytes] = None,
                 code_ttl_s: int = CODE_TTL_S) -> None:
        self._cfg = cfg
        self._registry = registry
        self._transport = transport or _default_http_send
        self._console_link = console_link or default_console_link
        self._aging_threshold_s = float(aging_threshold_s)
        self._secret = secret
        self._code_ttl_s = int(code_ttl_s)
        self._last_sent_ts: float = 0.0

    @property
    def _reply_enabled(self) -> bool:
        return bool(self._cfg.reply_url) and self._secret is not None

    def build_note(self, now: float) -> PushNote:
        """PushNote(count=0 → 没得推)。正文只有摘要,不带 payload 全文。"""
        cards = eligible_pending(self._registry, now, self._aging_threshold_s)
        if not cards:
            return PushNote(title="", text="", link="", level=LEVEL_LOW, count=0)
        lines: List[str] = []
        card_dicts: List[dict] = []
        level = LEVEL_LOW
        expiry = int(now + self._code_ttl_s)
        for i, (prop, age_s) in enumerate(cards, 1):
            lvl = value_level(prop)
            if _LEVEL_ORDER.get(lvl, 0) > _LEVEL_ORDER.get(level, 0):
                level = lvl
            summary = _squash(getattr(prop, "summary", ""))
            pid = str(getattr(prop, "proposal_id", "") or "")
            card = {
                "proposal_id": pid,
                "kind": str(getattr(prop, "kind", "") or ""),
                "summary": (summary[:SUMMARY_MAX] + "…") if len(summary) > SUMMARY_MAX else summary,
                "level": lvl,
                "age_s": int(age_s),
            }
            # 高危红线(与 email 通道同一条):高危卡**不铸码** —— 只通知,拍板回控制台
            if self._reply_enabled and pid and not is_high_risk(prop):
                card["reply_code"] = mint_code(self._secret, pid, REPLY_DECISION_SLOT, expiry)
            card_dicts.append(card)
            if i <= MAX_CARDS_IN_TEXT:
                line = _card_line(i, prop, age_s, self._aging_threshold_s)
                if card.get("reply_code"):
                    line += " " + i18n.t("channels.webhook.reply_code", code=card["reply_code"])
                lines.append(line)
        if len(cards) > MAX_CARDS_IN_TEXT:
            lines.append(i18n.t("channels.webhook.more", n=len(cards) - MAX_CARDS_IN_TEXT))
        if any(c.get("reply_code") for c in card_dicts):
            lines.append(i18n.t("channels.webhook.reply_hint"))
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
# 入站回批(v2):解析 reply source 消息(宁空勿毒)
# =============================================================================

def parse_reply_messages(raw: str) -> List[dict]:
    """reply source 响应体 → [{"id","time","text"}, ...](宁空勿毒)。

    只认两种形态:JSON 数组(元素 = 对象或纯字符串)/ NDJSON 行(ntfy `/json?poll=1` 风格,
    每行 `{"id","time","event","message"}`;event 存在且非 "message" 的行(open/keepalive)跳过)。
    正文字段依次取 message/text/body(必须是非空字符串)。解析不出的行/元素一律丢弃,
    整体不是这两种形态 → 空列表 —— 绝不把垃圾喂给回批解析。
    """
    out: List[dict] = []
    body = (raw or "").strip()
    if not body:
        return out
    items: List = []
    if body.startswith("["):
        try:
            arr = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return out
        items = arr if isinstance(arr, list) else []
    else:
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue  # 单行垃圾只丢该行
    for obj in items:
        if isinstance(obj, str):
            if obj.strip():
                out.append({"id": "", "time": 0.0, "text": obj})
            continue
        if not isinstance(obj, dict):
            continue
        event = str(obj.get("event", "") or "")
        if event and event != "message":
            continue  # ntfy 的 open/keepalive 等非消息事件
        text = obj.get("message") or obj.get("text") or obj.get("body") or ""
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            ts = float(obj.get("time") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        out.append({"id": str(obj.get("id", "") or ""), "time": ts, "text": text})
    return out


class ReplyStateStore:
    """轮询水位(since,unix 秒)+ 已处理消息 id 环 —— 落盘,重启不重复消费。

    首跑(无水位)语义:水位 = 当下,**不消费历史消息**(启动前躺在 topic 里的旧内容
    不解释不执行);文件损坏 fail-safe 成首跑 —— 即使承接方重发,已用码表(单次有效)
    仍是最后一道门。
    """

    def __init__(self, path=None) -> None:
        self._path = Path(path) if path else Path.home() / ".karvyloop" / REPLY_STATE_FILENAME
        self.since: float = 0.0
        self._seen_ids: List[str] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.since = float(data.get("since") or 0.0)
            ids = data.get("seen_ids") or []
            if isinstance(ids, list):
                self._seen_ids = [str(x) for x in ids][-REPLY_SEEN_IDS_CAP:]
        except Exception:
            logger.warning("[channels.webhook] 回批水位文件损坏,按首跑重建(单次码仍兜重放)")
            self.since, self._seen_ids = 0.0, []

    def is_seen(self, msg_id: str) -> bool:
        return bool(msg_id) and msg_id in self._seen_ids

    def advance(self, since: float, new_ids: tuple = ()) -> None:
        """推进水位(只进不退)+ 记入已处理 id(有界环),并落盘。"""
        self.since = max(float(since), self.since)
        for mid in new_ids:
            if mid and mid not in self._seen_ids:
                self._seen_ids.append(str(mid))
        self._seen_ids = self._seen_ids[-REPLY_SEEN_IDS_CAP:]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"version": 1, "since": self.since, "seen_ids": self._seen_ids}),
                encoding="utf-8")
        except Exception as e:
            logger.warning("[channels.webhook] 回批水位落盘失败(不阻断):%s", type(e).__name__)


def _default_reply_fetch(cfg: WebhookChannelConfig, since: float) -> List[dict]:
    """默认回批拉取 transport:出站 GET reply_url(带 since 水位参数),硬超时。

    非 2xx 抛 WebhookSendError(文本只含状态码,可安全入日志);
    reply_url 可能内嵌私有 topic → 先给 httpx 日志挂脱敏 filter。
    """
    import httpx
    _ensure_httpx_log_redaction(cfg.reply_url)   # httpx INFO 会打完整 URL —— 先堵脱敏
    url = cfg.reply_url
    if since > 0:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}since={int(since)}"
    timeout = max(float(cfg.timeout_s), 1.0)
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers=cfg.reply_headers or None)
    if resp.status_code >= 300:
        # 不用 raise_for_status:它的异常文本内嵌完整 URL(可能含私有 topic),日志不可入
        raise WebhookSendError(f"HTTP {resp.status_code}")
    return parse_reply_messages(resp.text)


# =============================================================================
# WebhookReplyPoller — 轮询 reply source,核验回执 → 注入的 decide 回调
# =============================================================================

class WebhookReplyPoller:
    """轮询拉取 reply source → 严格解析回执 → 核验 HMAC(单次+限时)→ 注入的 decide 回调。

    - **来源判定纪律**:入站消息正文是数据不是指挥者 —— 只认 `ACCEPT|REJECT|DEFER <code>`
      全行匹配;任何其他内容(自由文本/多余前后缀/小写)一律不解释不执行。
    - **归属靠 HMAC**:回执不带 proposal_id,逐张 pending 卡重铸核验(码签的就是 pid,
      至多一张对得上)—— 与 email 通道同一套 mint/verify/已用码表,不造第二套。
    - **高危双保险**:铸码侧已不给高危卡铸码;这里即使拿到"有效"码也再拒一次。
    - **K5**:回执 = 用户亲手拍的板;poller 只核验转达,不造决策。decide 回调依赖注入
      (对齐 /api/h2a_decide 语义),本模块不 import console 层。
    """

    def __init__(self, cfg: WebhookChannelConfig, secret: bytes,
                 used_store: UsedCodeStore, state: ReplyStateStore,
                 decide: DecideCallback, *,
                 pending: Callable[[], list],
                 transport: Optional[Callable] = None) -> None:
        self._cfg = cfg
        self._secret = secret
        self._used = used_store
        self._state = state
        self._decide = decide
        self._pending = pending
        self._transport = transport or _default_reply_fetch

    def poll_once(self, now: Optional[float] = None) -> List[dict]:
        """拉一轮 reply source,逐条处理;任何异常不外溢(下轮再来)。返回处理结果列表。"""
        t = time.time() if now is None else float(now)
        if self._state.since <= 0:
            # 首跑:立水位不消费历史(topic 里启动前的旧内容一律不看)
            self._state.advance(t)
            return []
        try:
            msgs = self._transport(self._cfg, self._state.since)
        except WebhookSendError as e:
            # e 的文本由我们自己铸(只含状态码),可安全入日志;目标只留 scheme+host
            logger.warning("[channels.webhook] 回批轮询失败(下轮再试):%s target=%s",
                           e, redact_url(self._cfg.reply_url))
            return []
        except Exception as e:
            # 第三方异常文本可能内嵌完整 URL —— 只记异常类别,绝不 str(e)
            logger.warning("[channels.webhook] 回批轮询失败(下轮再试):%s target=%s",
                           type(e).__name__, redact_url(self._cfg.reply_url))
            return []
        results: List[dict] = []
        max_ts = self._state.since
        new_ids: List[str] = []
        for msg in (msgs or []):
            mid = str(msg.get("id") or "")
            try:
                mts = float(msg.get("time") or 0.0)
            except (TypeError, ValueError):
                mts = 0.0
            if mts > max_ts:
                max_ts = mts
            if self._state.is_seen(mid):
                results.append({"status": "ignored", "reason": "duplicate"})
                continue
            if mid:
                new_ids.append(mid)
            results.append(self._handle_text(str(msg.get("text") or ""), t))
        self._state.advance(max_ts, tuple(new_ids))
        return results

    def _handle_text(self, raw_text: str, now: float) -> dict:
        # 多空白归一成单空格后再严格 fullmatch(结构仍一字不差)
        text = re.sub(r"\s+", " ", str(raw_text or "")).strip()
        m = _REPLY_RE.fullmatch(text)
        if not m:
            # 宁空勿毒:非严格格式(含自由文本)一律当没收到,不解析、不兑现
            return {"status": "ignored", "reason": "not_a_decision_reply"}
        decision, code = m.group(1), m.group(2)
        # 归属核验:码的 HMAC 签着 proposal_id,逐张 pending 重铸比对(恒时比较在 verify 里)
        matched = None
        reason = "no_match"
        for prop in (self._pending() or []):
            pid = str(getattr(prop, "proposal_id", "") or "")
            if not pid:
                continue
            ok, r = verify_code(self._secret, pid, REPLY_DECISION_SLOT, code,
                                now=now, used_store=self._used)
            if ok:
                matched = prop
                break
            if r in ("malformed", "expired"):
                reason = r     # 与 pid 无关,全场同判 —— 不必再试其他卡
                break
            if r == "used":
                reason = r     # HMAC 已对上这张卡,只是码已烧(重放)
                break
        if matched is None:
            # 不打回执正文(数据不入日志),只记核验结论
            logger.info("[channels.webhook] 回批码核验拒绝 reason=%s", reason)
            return {"status": "rejected", "reason": reason}
        pid = str(getattr(matched, "proposal_id", ""))
        # 高危双保险(与 email 通道同一红线):铸码侧不给高危卡铸码,这里再拒一次
        if is_high_risk(matched):
            logger.info("[channels.webhook] 高危卡拒绝 webhook 回批 proposal=%s", pid)
            return {"status": "rejected", "proposal_id": pid,
                    "reason": "high_risk_console_only"}
        try:
            result = self._decide(pid, decision)
        except Exception as e:
            logger.warning("[channels.webhook] decide 回调异常 proposal=%s:%s",
                           pid, type(e).__name__)
            return {"status": "error", "proposal_id": pid, "reason": "decide_failed"}
        if result is None:
            # 未知 proposal(刚被处理/清理)→ 不烧码(码对已消失的卡本就无效)
            return {"status": "rejected", "proposal_id": pid, "reason": "unknown_proposal"}
        # 单次有效:兑现成功后才烧码(避免"标了没兑现"烧掉用户唯一机会)
        self._used.mark_used(code, _code_expiry(code), now=now)
        logger.info("[channels.webhook] webhook 回批已兑现 proposal=%s decision=%s",
                    pid, decision)
        return {"status": "decided", "proposal_id": pid, "decision": decision}


# =============================================================================
# 组装 + tick(接线由主线做:本模块不碰 console/app.py)
# =============================================================================

@dataclasses.dataclass
class WebhookChannel:
    """一套接好线的 webhook 通道(pusher 必有;reply_url 未配则 poller=None,只推不收)。"""
    pusher: WebhookPusher
    poller: Optional[WebhookReplyPoller]


def build_webhook_channel(*, registry, decide: Optional[DecideCallback] = None,
                          config_path=None, home=None,
                          transport: Optional[Callable] = None,
                          transport_fetch: Optional[Callable] = None,
                          console_link: Optional[Callable[[], str]] = None
                          ) -> Optional[WebhookChannel]:
    """从 config.yaml 组一套 webhook 通道;**默认不配 = 返 None,完全不跑**(零负担)。

    registry 需有 pending()/proposal_meta()(PendingProposalRegistry 满足)。
    decide 依赖注入(对齐 /api/h2a_decide 处理器语义);配了 reply_url 且注入了 decide
    才装入站腿(secret 与 email 通道共用同一份 ~/.karvyloop/channel_secret)。
    """
    cfg = load_webhook_channel_config(config_path)
    if cfg is None:
        return None
    secret: Optional[bytes] = None
    poller: Optional[WebhookReplyPoller] = None
    if cfg.reply_url:
        if decide is None:
            # fail-loud:配了 reply_url 却没接 decide = 用户以为能手机回批实际黑洞
            logger.warning("[channels.webhook] 配了 reply_url 但未注入 decide 回调 —— 入站回批未启用")
        else:
            home_dir = Path(home) if home else Path.home() / ".karvyloop"
            secret = load_or_create_secret(home_dir / SECRET_FILENAME)
            used = UsedCodeStore(home_dir / WEBHOOK_USED_CODES_FILENAME)
            state = ReplyStateStore(home_dir / REPLY_STATE_FILENAME)
            poller = WebhookReplyPoller(cfg, secret, used, state, decide,
                                        pending=registry.pending,
                                        transport=transport_fetch)
    pusher = WebhookPusher(cfg, registry, transport=transport,
                           console_link=console_link, secret=secret)
    return WebhookChannel(pusher=pusher, poller=poller)


async def webhook_channel_tick(channel, *, now: Optional[float] = None) -> dict:
    """一次通道心跳:推一条(有节流)+ 轮询回批(未配 poller 则跳过)。

    出站 HTTP 是阻塞 IO → 丢线程跑,不占事件循环。channel=None(未配置)→ 空转返回;
    也兼容直接传裸 WebhookPusher(纯出站,历史接口)。
    **本函数不自己接 app.py** —— 接线由主线做。
    """
    import asyncio
    out: dict = {"push": None, "poll": None}
    if channel is None:
        return out
    if isinstance(channel, WebhookPusher):   # 兼容:裸 pusher = 纯出站
        pusher, poller = channel, None
    else:
        pusher, poller = channel.pusher, channel.poller
    if pusher is not None:
        out["push"] = await asyncio.to_thread(pusher.push_if_due, now)
    if poller is not None:
        out["poll"] = await asyncio.to_thread(poller.poll_once, now)
    return out


__all__ = [
    "MAX_CARDS_IN_TEXT",
    "REPLY_DECISION_SLOT",
    "TITLE_MAX",
    "PushNote",
    "ReplyStateStore",
    "WebhookChannel",
    "WebhookPusher",
    "WebhookReplyPoller",
    "WebhookRequest",
    "WebhookSendError",
    "build_request",
    "build_webhook_channel",
    "default_console_link",
    "parse_reply_messages",
    "redact_url",
    "webhook_channel_tick",
]
