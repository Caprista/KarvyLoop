"""inbox_pipe — 收件箱→决策卡管道(docs/49 ⑲-①:solopreneur 第一管道)。

拓扑(**只进不出**:出站 IMAP 轮询"待处理收件箱" → 分诊 → H2A 决策卡):

    你的收件箱 --出站 IMAP 轮询(UNSEEN)--> 分诊(一次受限 LLM 调用/封)
        → 需拍板   → KIND_INBOX_DECISION 卡(摘要+发件人+建议动作)
        → 需回复   → KIND_INBOX_REPLY 卡(带代拟草稿;ACCEPT=存台账+显示,自行复制发送)
        → 纯通知   → 归档(不出卡、不打扰)

诚实地板:
- **绝不外发(deontic 硬规矩)**:本模块结构上就发不了信 —— 不 import 任何发信库、
  没有任何发送调用;未经确认绝不对外发信,ACCEPT 一张卡也只是"记台账+显示草稿"。
  digest/回批的发信是 email_channel 的事,与本管道无关(收件管道只进不出)。
- **宁静默勿误卡**:分诊输出严格 JSON;解析失败/形状不对/拿不准 → 一律当纯通知归档,
  绝不凭猜出卡骚扰;正文剥 HTML 失败 → 当空(宁空勿毒)。
- **隐私分级**:邮件正文全文**不进卡**(卡只带 ≤160 字摘要);分诊 prompt 只含这一封邮件
  (发件人/主题/正文摘要),不含收件箱以外任何上下文。
- **凭证纪律**:IMAP 授权码与 API key 同级机密(config_channels repr=False),绝不打日志;
  日志只记异常类别,不带正文/凭证。
- **去重**:message-id + thread(References/In-Reply-To 根)双键台账,同一 thread 不重复出卡。
- **节流**:每轮最多出 max_cards_per_tick 张卡(默认 5),其余记 backlog(落盘,重启不丢)
  下轮优先处理;超预算的邮件**不预支分诊**(token 纪律)。
- **K5**:出卡=只通知与建议;拍板永远是用户按下的。ACCEPT 兑现 handler 无外部副作用
  (只写本地台账)。token 记账:分诊调用打 token_source("inbox_pipe") 标。
- 依赖:标准库(imaplib / email / html.parser)+ 注入的 gateway;transport 可注入(测试打桩)。
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from karvyloop.config_channels import InboxPipeConfig, load_inbox_pipe_config

logger = logging.getLogger(__name__)

# ---- kind 常量(handler 在本模块;注册进 build_proposal_handlers 由主线做)----
KIND_INBOX_DECISION = "inbox_decision"   # 需要拍板:报价/合同/付款类 → H2A 卡
KIND_INBOX_REPLY = "inbox_reply"         # 需要回复:卡带代拟草稿(ACCEPT=存台账+显示,不发送)

# ---- 分诊类别 ----
CATEGORY_DECISION = "decision"
CATEGORY_REPLY = "reply"
CATEGORY_NOTICE = "notice"
_VALID_CATEGORIES = (CATEGORY_DECISION, CATEGORY_REPLY, CATEGORY_NOTICE)

# ---- 预算/截断常量 ----
TOKEN_SOURCE = "inbox_pipe"
SNIPPET_MAX = 160            # 卡上正文摘要截断(与 email_channel digest 同纪律:全文不进卡)
BODY_KEEP_CHARS = 2000       # 解析后正文保留上限(分诊材料的原料)
BODY_TRIAGE_CHARS = 1500     # 进分诊 prompt 的正文上限
DRAFT_MAX_CHARS = 4000       # 代拟草稿上限
SEEN_CAP = 5000              # 去重台账上限(按时间剪最老)
BACKLOG_CAP = 200            # backlog 上限(超出丢弃并记日志)
STATE_FILENAME = "inbox_pipe_state.json"
ACTIONS_FILENAME = "inbox_actions.json"
ACTIONS_CAP = 500


# =============================================================================
# 邮件解析:原始 RFC822 bytes → InboxMail(剥 HTML,宁空勿毒)
# =============================================================================

@dataclasses.dataclass(frozen=True)
class InboxMail:
    """一封已解析的待分诊邮件(正文已剥 HTML + 截断;全文永不出本进程)。"""
    msg_id: str
    thread_key: str
    sender: str
    subject: str
    body: str          # 纯文本摘要(≤ BODY_KEEP_CHARS)
    ts: float = 0.0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "InboxMail":
        return cls(
            msg_id=str(d.get("msg_id", "") or ""),
            thread_key=str(d.get("thread_key", "") or ""),
            sender=str(d.get("sender", "") or ""),
            subject=str(d.get("subject", "") or ""),
            body=str(d.get("body", "") or "")[:BODY_KEEP_CHARS],
            ts=float(d.get("ts", 0.0) or 0.0),
        )


class _HtmlTextExtractor(HTMLParser):
    """极简 HTML → 文本:只收正文文字,跳过 script/style(剥不动 = 宁空勿毒,由 caller 兜)。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._chunks.append(data)

    def text(self) -> str:
        return " ".join(self._chunks)


def html_to_text(html: str) -> str:
    """剥 HTML 成纯文本;任何异常 → 空串(宁空勿毒:剥不干净不如不要)。"""
    try:
        parser = _HtmlTextExtractor()
        parser.feed(html or "")
        return _squash_ws(parser.text())
    except Exception:
        return ""


def _squash_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _extract_body_text(msg) -> str:
    """从 email.message 取正文:偏好 text/plain,退 text/html(剥);全失败 → 空。"""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is None:
            return ""
        content = part.get_content()
        if part.get_content_type() == "text/html":
            return html_to_text(content)
        return _squash_ws(content)
    except Exception:
        return ""  # 宁空勿毒:解不出正文就按空正文分诊(主题/发件人仍在)


def parse_inbox_message(raw: bytes) -> Optional[InboxMail]:
    """原始 RFC822 bytes → InboxMail;解析不出关键头 → None(跳过,不猜)。"""
    from email import message_from_bytes, policy
    from email.utils import parsedate_to_datetime
    try:
        msg = message_from_bytes(bytes(raw), policy=policy.default)
    except Exception:
        return None
    sender = _squash_ws(msg.get("From", ""))
    subject = _squash_ws(msg.get("Subject", ""))
    msg_id = _squash_ws(msg.get("Message-ID", ""))
    if not sender and not subject and not msg_id:
        return None  # 连一个可用头都没有 = 不是邮件,跳过
    if not msg_id:
        # 无 Message-ID(少见但存在):从稳定头派生,保证去重仍工作
        stable = f"{sender}|{subject}|{_squash_ws(msg.get('Date', ''))}"
        msg_id = "derived-" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:16]
    refs = _squash_ws(msg.get("References", "")).split()
    thread_key = refs[0] if refs else (_squash_ws(msg.get("In-Reply-To", "")) or msg_id)
    ts = 0.0
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        if dt is not None:
            ts = dt.timestamp()
    except Exception:
        ts = 0.0
    return InboxMail(
        msg_id=msg_id,
        thread_key=thread_key,
        sender=sender[:200],
        subject=subject[:300],
        body=_extract_body_text(msg)[:BODY_KEEP_CHARS],
        ts=ts,
    )


# =============================================================================
# 台账:去重(message-id + thread)+ backlog(节流溢出,落盘,重启不丢)
# =============================================================================

class InboxLedger:
    """收件管道状态台账:seen(去重键 → ts)+ backlog(超预算待分诊邮件)。

    文件损坏 fail-safe 成空(最坏 = 同一封重新分诊一次,幂等 proposal_id 兜底不重复出卡)。
    """

    def __init__(self, path=None) -> None:
        self._path = Path(path) if path else Path.home() / ".karvyloop" / STATE_FILENAME
        self._seen: Dict[str, float] = {}
        self._backlog: List[InboxMail] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            seen = data.get("seen") or {}
            if isinstance(seen, dict):
                self._seen = {str(k): float(v) for k, v in seen.items()}
            for item in (data.get("backlog") or []):
                if isinstance(item, dict):
                    self._backlog.append(InboxMail.from_dict(item))
        except Exception:
            logger.warning("[channels.inbox] 台账损坏,重建(幂等 proposal_id 兜底不重复出卡)")
            self._seen, self._backlog = {}, []

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "seen": self._seen,
                "backlog": [m.to_dict() for m in self._backlog],
            }
            self._path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning("[channels.inbox] 台账落盘失败(不阻断):%s", type(e).__name__)

    # ---- 去重 ----
    def is_seen(self, key: str) -> bool:
        return bool(key) and key in self._seen

    def mark_seen(self, *keys: str, now: Optional[float] = None) -> None:
        t = time.time() if now is None else float(now)
        for key in keys:
            if key:
                self._seen[key] = t
        if len(self._seen) > SEEN_CAP:  # 有界:按时间剪最老
            keep = sorted(self._seen.items(), key=lambda kv: -kv[1])[:SEEN_CAP]
            self._seen = dict(keep)

    # ---- backlog ----
    def backlog(self) -> List[InboxMail]:
        return list(self._backlog)

    def set_backlog(self, mails: List[InboxMail]) -> None:
        if len(mails) > BACKLOG_CAP:
            logger.warning("[channels.inbox] backlog 超上限,丢弃最旧 %d 封", len(mails) - BACKLOG_CAP)
            mails = mails[-BACKLOG_CAP:]
        self._backlog = list(mails)


# =============================================================================
# 分诊:一次受限 LLM 调用/封 → 严格 JSON;失败 = 纯通知(宁静默勿误卡)
# =============================================================================

_TRIAGE_SYSTEM = """你是个人收件箱分诊器。输入是一封邮件(发件人/主题/正文摘要),你判断它需要主人做什么。

只输出**一个 JSON 对象**(无围栏、无解释):
{"category": "decision" | "reply" | "notice", "reason": "一句话依据", "suggested_action": "建议动作(一句话)", "draft": "回复草稿(仅 reply 给,其余留空)"}

分类标准:
- decision = 需要主人拍板才能推进:报价/询价请求、合同或条款确认、付款/退款/发票相关、承诺时间或资源;
- reply = 需要回复、但可以先代拟草稿等主人批:普通业务往来、答疑、约时间;
- notice = 纯通知/营销/订阅/自动发送,归档即可,不打扰主人。

硬纪律:
- 拿不准一律 notice(宁静默勿误卡);
- draft 用与来信一致的语言,简短克制,**不承诺任何未经主人授权的事项**;
- 严格 JSON,除 JSON 外不输出任何字符。"""

TriageFn = Callable[[InboxMail], Awaitable[Optional[dict]]]
"""注入的分诊函数:mail → {"category","reason","suggested_action","draft"} | None(=纯通知)。"""


def _parse_json_obj(text: str) -> Optional[dict]:
    """严格 JSON(只剥外层围栏;prose 不抽;解析失败 → None)。与 fuzzy_dispatch 同款纪律。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        nl = raw.find("\n")
        raw = raw[nl + 1:] if nl != -1 else raw
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]
    raw = raw.strip()
    if not raw.startswith("{"):
        return None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def validate_triage(obj: Optional[dict]) -> Optional[dict]:
    """形状验证:category 必须是三类之一,字段全部收敛成有界字符串;不合格 → None(当纯通知)。"""
    if not isinstance(obj, dict):
        return None
    category = str(obj.get("category", "") or "").strip().lower()
    if category not in _VALID_CATEGORIES:
        return None
    return {
        "category": category,
        "reason": str(obj.get("reason", "") or "").strip()[:200],
        "suggested_action": str(obj.get("suggested_action", "") or "").strip()[:200],
        "draft": str(obj.get("draft", "") or "").strip()[:DRAFT_MAX_CHARS],
    }


def triage_material(mail: InboxMail) -> str:
    """分诊材料 = 只有这一封邮件(隐私分级:不含收件箱以外任何上下文)。"""
    if len(mail.body) > BODY_TRIAGE_CHARS:
        # B-5 #9 标定埋点 `governance_truncated`(1500 帽族;fail-soft,只在真截时落)
        try:
            from karvyloop.cognition.calibration import emit
            emit("governance_truncated", {
                "site": "inbox_pipe.triage_material",
                "orig_len": len(mail.body), "cap": BODY_TRIAGE_CHARS})
        except Exception:
            pass
    return (
        f"发件人:{mail.sender}\n"
        f"主题:{mail.subject}\n"
        f"正文摘要:\n{mail.body[:BODY_TRIAGE_CHARS]}"
    )


def make_gateway_triage(gateway: Any, model_ref: str = "") -> TriageFn:
    """默认分诊器:一次受限 gateway 调用,token_source 打 inbox_pipe 标。

    无 gateway / 调用失败 / 输出不是严格 JSON / 类别不合法 → None(= 纯通知,不出卡)。
    """

    async def triage(mail: InboxMail) -> Optional[dict]:
        if gateway is None:
            return None
        try:
            from karvyloop.gateway import ResolveScope
            from karvyloop.gateway.system import SystemPrompt
            from karvyloop.llm.token_ledger import token_source
            try:
                ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
            except Exception:
                ref = model_ref
            out = ""
            with token_source(TOKEN_SOURCE):
                async for ev in gateway.complete(
                    [{"role": "user", "content": triage_material(mail)}], [], ref,
                    system=SystemPrompt(static=[_TRIAGE_SYSTEM]),
                ):
                    if type(ev).__name__ == "TextDelta":
                        out += getattr(ev, "text", "")
            return validate_triage(_parse_json_obj(out))
        except Exception as e:  # noqa: BLE001 — 分诊失败降级纯通知,绝不误卡
            logger.warning("[channels.inbox] 分诊调用失败(该封当纯通知):%s", type(e).__name__)
            return None

    return triage


# =============================================================================
# 出卡:需拍板 → inbox_decision;需回复 → inbox_reply(带草稿)。全文永不进卡。
# =============================================================================

def _snippet(mail: InboxMail) -> str:
    s = _squash_ws(mail.body)
    return (s[:SNIPPET_MAX] + "…") if len(s) > SNIPPET_MAX else s


def _card_payload(mail: InboxMail, triage: dict) -> dict:
    """卡 payload:摘要+发件人+建议动作 —— **绝不带正文全文**(隐私分级)。"""
    return {
        "message_id": mail.msg_id,
        "thread_key": mail.thread_key,
        "from": mail.sender,
        "subject": mail.subject,
        "snippet": _snippet(mail),
        "category": triage["category"],
        "suggested_action": triage["suggested_action"],
    }


def proposal_for_inbox_decision(mail: InboxMail, triage: dict, *, ts: float,
                                strength: float = 0.8):
    """需拍板邮件 → H2A 决策卡。**只通知与建议**:ACCEPT 也不代发任何邮件(deontic 硬规矩)。

    幂等:proposal_id 按 thread_key 稳定派生 → 同一 thread 收敛成一张卡,不刷屏。
    """
    from karvyloop import i18n
    from karvyloop.karvy.atoms import Proposal  # 局部 import 避免模块级循环
    reason = triage.get("reason") or i18n.t("proposal.inbox_decision.default_reason")
    action = triage.get("suggested_action") or i18n.t("proposal.inbox_decision.default_action")
    digest = hashlib.sha1(mail.thread_key.encode("utf-8")).hexdigest()[:8]
    return Proposal(
        summary=i18n.t("proposal.inbox_decision.summary", sender=mail.sender, subject=mail.subject),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_INBOX_DECISION,
        payload=_card_payload(mail, triage),
        proposal_id=f"{KIND_INBOX_DECISION}-0-{digest}",
        basis=i18n.t("proposal.inbox_decision.basis", reason=reason, action=action,
                     snippet=(_snippet(mail) or i18n.t("proposal.inbox.no_body"))),
    )


def proposal_for_inbox_reply(mail: InboxMail, triage: dict, *, ts: float,
                             strength: float = 0.6):
    """需回复邮件 → 带代拟草稿的卡。v1 剪贴板语义:ACCEPT = 草稿存台账+显示,**不自动发送**。

    payload["draft"] 是 str → 走 registry 的「改了再批」edits 白名单(用户可就地改草稿再批)。
    """
    from karvyloop import i18n
    from karvyloop.karvy.atoms import Proposal  # 局部 import 避免模块级循环
    reason = triage.get("reason") or i18n.t("proposal.inbox_reply.default_reason")
    digest = hashlib.sha1(mail.thread_key.encode("utf-8")).hexdigest()[:8]
    payload = _card_payload(mail, triage)
    payload["draft"] = triage.get("draft") or ""
    return Proposal(
        summary=i18n.t("proposal.inbox_reply.summary", sender=mail.sender, subject=mail.subject),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_INBOX_REPLY,
        payload=payload,
        proposal_id=f"{KIND_INBOX_REPLY}-0-{digest}",
        basis=i18n.t("proposal.inbox_reply.basis", reason=reason),
    )


# =============================================================================
# ACCEPT 兑现 handler(注册进 build_proposal_handlers 由主线做):只写本地台账,零外部副作用
# =============================================================================

def _append_action(path: Path, entry: dict) -> None:
    """往动作台账追加一条(有界;损坏 fail-safe 重建)。"""
    items: List[dict] = []
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                items = [x for x in data if isinstance(x, dict)]
    except Exception:
        items = []
    items.append(entry)
    items = items[-ACTIONS_CAP:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[channels.inbox] 动作台账落盘失败(不阻断):%s", type(e).__name__)


def make_inbox_handlers(home=None) -> Dict[str, Callable[[object], Tuple[bool, str]]]:
    """两个 kind 的 ACCEPT 兑现 handler(签名对齐 build_proposal_handlers:proposal → (ok, detail))。

    结构性保证:handler **只写本地台账**(inbox_actions.json)—— 不发信、不出网、无外部副作用。
    """
    ledger_path = (Path(home) if home else Path.home() / ".karvyloop") / ACTIONS_FILENAME

    def _decision_handler(proposal) -> Tuple[bool, str]:
        payload = dict(getattr(proposal, "payload", {}) or {})
        _append_action(ledger_path, {
            "ts": time.time(),
            "kind": KIND_INBOX_DECISION,
            "message_id": payload.get("message_id", ""),
            "from": payload.get("from", ""),
            "subject": payload.get("subject", ""),
            "suggested_action": payload.get("suggested_action", ""),
            "decision": "ACCEPT",
        })
        action = payload.get("suggested_action") or "(见卡面)"
        return True, (f"已记录你的拍板({action})。系统不代发邮件、不自动执行外部动作 —— "
                      f"后续动作由你亲自完成。")

    def _reply_handler(proposal) -> Tuple[bool, str]:
        payload = dict(getattr(proposal, "payload", {}) or {})
        draft = str(payload.get("draft", "") or "")[:DRAFT_MAX_CHARS]
        _append_action(ledger_path, {
            "ts": time.time(),
            "kind": KIND_INBOX_REPLY,
            "message_id": payload.get("message_id", ""),
            "from": payload.get("from", ""),
            "subject": payload.get("subject", ""),
            "draft": draft,
            "decision": "ACCEPT",
        })
        if not draft:
            return True, "已记录(草稿为空)。系统不代发邮件,请自行回复。"
        return True, (f"草稿已存台账,请复制后**自行发送**(系统不代发邮件):\n---\n{draft}")

    return {
        KIND_INBOX_DECISION: _decision_handler,
        KIND_INBOX_REPLY: _reply_handler,
    }


# =============================================================================
# IMAP 拉取(默认 transport;可注入打桩)
# =============================================================================

def _imap_quote_folder(folder: str) -> str:
    f = (folder or "INBOX").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{f}"'


def _default_imap_fetch(cfg: InboxPipeConfig) -> List[bytes]:
    """默认 IMAP transport:拉待处理文件夹的 UNSEEN 邮件全文(非 PEEK → 取走即置已读)。"""
    import imaplib
    out: List[bytes] = []
    i = cfg.imap
    with imaplib.IMAP4_SSL(i.host, i.port) as client:
        client.login(i.user, i.password)
        client.select(_imap_quote_folder(cfg.folder))
        typ, data = client.search(None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return out
        for num in data[0].split():
            typ, msg_data = client.fetch(num, "(RFC822)")
            if typ != "OK":
                continue
            for part in msg_data:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                    out.append(bytes(part[1]))
    return out


# =============================================================================
# InboxPipe — 收件箱管道本体
# =============================================================================

class InboxPipe:
    """收件箱→决策卡管道:拉 UNSEEN → 去重 → 分诊 → 出卡(节流,溢出记 backlog)。

    只进不出:本类没有任何发信路径。registry 需有 register()(PendingProposalRegistry 满足)。
    """

    def __init__(self, cfg: InboxPipeConfig, registry, ledger: InboxLedger, *,
                 triage: Optional[TriageFn] = None,
                 gateway: Any = None, model_ref: str = "",
                 transport: Optional[Callable[[InboxPipeConfig], List[bytes]]] = None) -> None:
        self._cfg = cfg
        self._registry = registry
        self._ledger = ledger
        self._triage: TriageFn = triage or make_gateway_triage(gateway, model_ref)
        self._transport = transport or _default_imap_fetch

    def _fetch(self) -> List[InboxMail]:
        """拉一轮新邮件并解析;IMAP 失败 → 空(下轮再来,不外溢)。"""
        try:
            raws = self._transport(self._cfg)
        except Exception as e:
            logger.warning("[channels.inbox] IMAP 轮询失败(下轮再试):%s", type(e).__name__)
            return []
        mails: List[InboxMail] = []
        for raw in (raws or []):
            mail = parse_inbox_message(raw)
            if mail is not None:
                mails.append(mail)
        return mails

    async def poll_once(self, now: Optional[float] = None) -> dict:
        """一轮管道:backlog 优先 → 拉新 → 去重 → 分诊(逐封)→ 出卡(≤ max_cards_per_tick)。

        返回统计 {"fetched","cards","replies","notices","deduped","backlog"}。
        超预算的邮件不预支分诊(token 纪律),原样记 backlog 下轮。
        """
        t = time.time() if now is None else float(now)
        stats = {"fetched": 0, "cards": 0, "replies": 0, "notices": 0, "deduped": 0, "backlog": 0}
        fetched = await asyncio.to_thread(self._fetch)
        stats["fetched"] = len(fetched)
        pending = self._ledger.backlog() + fetched
        budget = max(int(self._cfg.max_cards_per_tick), 1)
        issued = 0
        leftover: List[InboxMail] = []
        for mail in pending:
            if self._ledger.is_seen(mail.msg_id) or self._ledger.is_seen(mail.thread_key):
                stats["deduped"] += 1
                self._ledger.mark_seen(mail.msg_id, now=t)  # 同 thread 后续封也不再查/不再分诊
                continue
            if issued >= budget:
                leftover.append(mail)  # 节流:不预支分诊,原样记 backlog
                continue
            try:
                triage = validate_triage(await self._triage(mail))
            except Exception as e:  # noqa: BLE001 — 注入分诊器的异常也不拖垮管道
                logger.warning("[channels.inbox] 分诊异常(该封当纯通知):%s", type(e).__name__)
                triage = None
            self._ledger.mark_seen(mail.msg_id, mail.thread_key, now=t)
            if triage is None or triage["category"] == CATEGORY_NOTICE:
                stats["notices"] += 1  # 宁静默勿误卡:归档,不打扰
                continue
            if triage["category"] == CATEGORY_DECISION:
                self._registry.register(proposal_for_inbox_decision(mail, triage, ts=t), now=t)
                issued += 1
                stats["cards"] += 1
            else:  # CATEGORY_REPLY
                self._registry.register(proposal_for_inbox_reply(mail, triage, ts=t), now=t)
                issued += 1
                stats["replies"] += 1
        self._ledger.set_backlog(leftover)
        self._ledger.save()
        stats["backlog"] = len(leftover)
        return stats


# =============================================================================
# 组装 + tick(接线由主线做:本模块不碰 console/app.py)
# =============================================================================

def build_inbox_pipe(*, registry, config_path=None, home=None,
                     gateway: Any = None, model_ref: str = "",
                     triage: Optional[TriageFn] = None,
                     transport: Optional[Callable] = None) -> Optional[InboxPipe]:
    """从 config.yaml 组一条收件管道;**默认不配 = 返 None,完全不跑**(零负担)。

    gateway 依赖注入(分诊用;None = 全部当纯通知,不出卡不烧 token)。
    """
    cfg = load_inbox_pipe_config(config_path)
    if cfg is None:
        return None
    home_dir = Path(home) if home else Path.home() / ".karvyloop"
    ledger = InboxLedger(home_dir / STATE_FILENAME)
    return InboxPipe(cfg, registry, ledger,
                     triage=triage, gateway=gateway, model_ref=model_ref,
                     transport=transport)


async def inbox_pipe_tick(pipe: Optional[InboxPipe], *, now: Optional[float] = None) -> dict:
    """一次管道心跳。pipe=None(未配置)→ 空转返回。

    **本函数不自己接 app.py** —— 接线(lifespan 里按 poll_interval_s 起循环)由主线做。
    """
    if pipe is None:
        return {"inbox": None}
    return {"inbox": await pipe.poll_once(now)}


__all__ = [
    "KIND_INBOX_DECISION",
    "KIND_INBOX_REPLY",
    "CATEGORY_DECISION",
    "CATEGORY_REPLY",
    "CATEGORY_NOTICE",
    "TOKEN_SOURCE",
    "InboxMail",
    "InboxLedger",
    "InboxPipe",
    "build_inbox_pipe",
    "inbox_pipe_tick",
    "make_inbox_handlers",
    "make_gateway_triage",
    "parse_inbox_message",
    "html_to_text",
    "proposal_for_inbox_decision",
    "proposal_for_inbox_reply",
    "triage_material",
    "validate_triage",
]
