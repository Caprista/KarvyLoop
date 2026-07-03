"""email_channel — 邮件决策闭环(docs/43 ⑤a:卡片外推)。

拓扑(家里机器**只出站**,永不需要公网/穿透/第三方组网):

    console --出站 SMTP:pending 卡摘要 + mailto 回批链接--> 你自己的邮箱 --> 手机邮件 App
    console <--出站 IMAP 轮询:回信主题 "DECIDE <id> <ACCEPT|REJECT|DEFER> <code>"--
    核验 HMAC(单次 + 限时)→ 注入的 decide 回调(既有 h2a 决策路径)→ Trace

诚实地板:
- **宁空勿毒**:回信只认严格主题格式,自由文本一律不解析——解析不出 = 当没收到,卡照挂。
- **高危卡只通知不可回批**(kind 含 fs_access / 大额):正文写明"此类需回控制台确认",
  不铸码、不带 mailto;poller 侧再拒一次(双保险)。
- digest 正文 = 卡摘要,**不带敏感 payload 全文**(邮箱服务商可见邮件内容)。
- **凭证纪律**:SMTP/IMAP 授权码与 API key 同级机密——绝不打日志、config repr 遮罩;
  HMAC secret 落 ~/.karvyloop/channel_secret(0600 语义),在 export 排除表。
- **K5**:回信 = 用户亲手拍的板;poller 只核验转达,不造决策。decide 回调依赖注入
  (对齐 /api/h2a_decide 语义:proposal_id + decision),本模块**不 import console 层**。
- 依赖:全标准库(smtplib / imaplib / email / hmac / secrets)。
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac as hmac_mod
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import quote

from karvyloop.config_channels import EmailChannelConfig, load_email_channel_config
from karvyloop.karvy.proposal_registry import AGING_THRESHOLD_S

logger = logging.getLogger(__name__)

# ---- 常量 ----
CODE_TTL_S = 24 * 3600           # 回批码限时(默认 24h)
CODE_DIGEST_HEX = 20             # HMAC-SHA256 截 20 hex = 80 bit(邮件主题可读性 × 强度折中)
SECRET_FILENAME = "channel_secret"
USED_CODES_FILENAME = "channel_used_codes.json"
SUMMARY_MAX = 160                # digest 每卡摘要截断(不带 payload 全文)

# 高危分级(docs/43 ⑤a #4):命中任一 → 邮件只通知不可回批(必须回控制台拍板)。
# - kind 标记:子串匹配 proposal.kind(fs_access = 放行文件系统路径,天然高危)
# - 文本标记:出现在 summary 里(如"大额"付款/开销类建议)
HIGH_RISK_KIND_MARKERS = ("fs_access",)
HIGH_RISK_TEXT_MARKERS = ("大额",)

# 严格主题格式(宁空勿毒):`DECIDE <proposal_id> <ACCEPT|REJECT|DEFER> <expiry>-<hmac hex>`
# 全行 fullmatch;不匹配 = 不是决策回信,一律忽略(自由文本永不解析)。
_SUBJECT_RE = re.compile(
    r"DECIDE ([A-Za-z0-9._\-]{1,128}) (ACCEPT|REJECT|DEFER) (\d{1,20}-[0-9a-f]{8,64})"
)
_CODE_RE = re.compile(r"(\d{1,20})-([0-9a-f]{8,64})")

DecideCallback = Callable[[str, str], object]
"""注入的决策回调:(proposal_id, decision) → DispatchResult|dict|None。
语义对齐 /api/h2a_decide 处理器(registry.decide + handlers);None = 未知 proposal。"""


# =============================================================================
# HMAC 回批码:hmac(secret, proposal_id|decision|expiry) —— 单次有效 + 限时
# =============================================================================

def load_or_create_secret(path=None) -> bytes:
    """读/生成本机通道 secret(默认 ~/.karvyloop/channel_secret,0600 语义)。

    机密纪律:绝不打日志、绝不进 export(cli/export_cmd 排除表);Windows 上 chmod
    是尽力而为(NTFS 无 POSIX 位),POSIX 上真 0600。
    """
    import os
    import secrets as secrets_mod
    p = Path(path) if path else Path.home() / ".karvyloop" / SECRET_FILENAME
    if p.exists():
        text = p.read_text(encoding="utf-8").strip()
        if text:
            return text.encode("utf-8")
    p.parent.mkdir(parents=True, exist_ok=True)
    token = secrets_mod.token_hex(32)
    p.write_text(token, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:  # pragma: no cover - 平台差异
        pass
    return token.encode("utf-8")


def mint_code(secret: bytes, proposal_id: str, decision: str, expiry_ts: int) -> str:
    """铸一枚回批码:`<expiry>-<hmac_hex[:20]>`。expiry 编进码里 + 参与签名(不可篡改)。"""
    expiry = int(expiry_ts)
    mac = hmac_mod.new(
        secret, f"{proposal_id}|{decision.upper()}|{expiry}".encode("utf-8"), hashlib.sha256
    ).hexdigest()[:CODE_DIGEST_HEX]
    return f"{expiry}-{mac}"


def verify_code(secret: bytes, proposal_id: str, decision: str, code: str, *,
                now: float, used_store: Optional["UsedCodeStore"] = None) -> tuple:
    """核验回批码 → (ok, reason)。四道门:格式 → 限时 → HMAC(恒时比较)→ 单次。

    重放防护:used_store 里出现过的码直接拒(标记"已用"由 caller 在 decide 成功后做——
    避免"标了却没兑现"把用户唯一一次机会烧掉)。
    """
    m = _CODE_RE.fullmatch((code or "").strip())
    if not m:
        return False, "malformed"
    expiry = int(m.group(1))
    if now > expiry:
        return False, "expired"
    expected = mint_code(secret, proposal_id, decision, expiry)
    # 恒时全串比较(重铸完整码;不比截断前缀,防"短码撞前缀"降强度)
    if not hmac_mod.compare_digest(expected, f"{expiry}-{m.group(2)}"):
        return False, "bad_signature"
    if used_store is not None and used_store.is_used(code):
        return False, "used"
    return True, "ok"


class UsedCodeStore:
    """已用回批码表(单次有效的落地):存码的 sha256 → expiry,过期条目自动剪。

    存哈希不存原码(表本身不含可重放物);文件损坏 fail-safe 成空表(码仍有 HMAC+限时两道门)。
    """

    def __init__(self, path=None) -> None:
        self._path = Path(path) if path else Path.home() / ".karvyloop" / USED_CODES_FILENAME
        self._used: dict = {}
        self._load()

    @staticmethod
    def _key(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._used = {str(k): float(v) for k, v in data.items()}
        except Exception:
            logger.warning("[channels.email] 已用码表损坏,重建(码仍有 HMAC+限时门)")
            self._used = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._used), encoding="utf-8")
        except Exception as e:
            logger.warning("[channels.email] 已用码表落盘失败(不阻断):%s", e)

    def is_used(self, code: str) -> bool:
        return self._key(code) in self._used

    def mark_used(self, code: str, expiry_ts: float, *, now: Optional[float] = None) -> None:
        t = time.time() if now is None else float(now)
        # 剪已过期条目(过期码本来就过不了限时门,不必再记)→ 表有界
        self._used = {k: v for k, v in self._used.items() if v >= t - 3600}
        self._used[self._key(code)] = float(expiry_ts)
        self._save()


def _code_expiry(code: str) -> float:
    m = _CODE_RE.fullmatch((code or "").strip())
    return float(m.group(1)) if m else 0.0


# =============================================================================
# 内容分级
# =============================================================================

def is_high_risk(proposal) -> bool:
    """高危卡判定(邮件只通知不可回批):kind 含 fs_access 类标记,或摘要含"大额"类标记。"""
    kind = str(getattr(proposal, "kind", "") or "")
    if any(mark in kind for mark in HIGH_RISK_KIND_MARKERS):
        return True
    summary = str(getattr(proposal, "summary", "") or "")
    return any(mark in summary for mark in HIGH_RISK_TEXT_MARKERS)


# =============================================================================
# EmailDigestSender — SMTP 出站发 pending 卡摘要
# =============================================================================

def _default_smtp_send(cfg: EmailChannelConfig, msg) -> None:
    """默认 SMTP transport:465 走 SMTP_SSL,其余端口强制 STARTTLS(拒绝明文送授权码)。"""
    import smtplib
    s = cfg.smtp
    if int(s.port) == 465:
        with smtplib.SMTP_SSL(s.host, s.port, timeout=30) as client:
            if s.user:
                client.login(s.user, s.password)
            client.send_message(msg)
        return
    with smtplib.SMTP(s.host, s.port, timeout=30) as client:
        client.ehlo()
        try:
            client.starttls()
            client.ehlo()
        except smtplib.SMTPNotSupportedError:
            raise RuntimeError(
                "SMTP server does not support STARTTLS — refusing to send credentials in plaintext"
            )
        if s.user:
            client.login(s.user, s.password)
        client.send_message(msg)


class EmailDigestSender:
    """把 pending 决策卡打成一封 digest 邮件发出去(每卡带 mailto 回批链接)。

    - 节流:两封 digest 间隔 ≥ digest_min_interval_s(防唠叨)。
    - DEFER 语义:DEFER 过的卡在 AGING_THRESHOLD_S 内不计入 digest;满阈值重新计入
      (DEFER≠消失)。挂龄超阈值的老卡置顶标「⏳挂了N天」。
    - transport 可注入(测试打桩,不发真邮件)。
    """

    def __init__(self, cfg: EmailChannelConfig, secret: bytes, registry, *,
                 transport: Optional[Callable] = None,
                 code_ttl_s: int = CODE_TTL_S,
                 aging_threshold_s: float = AGING_THRESHOLD_S) -> None:
        self._cfg = cfg
        self._secret = secret
        self._registry = registry
        self._transport = transport or _default_smtp_send
        self._code_ttl_s = int(code_ttl_s)
        self._aging_threshold_s = float(aging_threshold_s)
        self._last_sent_ts: float = 0.0

    # ---- 卡挑选:pending − 未满老化阈值的 DEFER 卡;按挂龄降序(老卡置顶)----
    def _eligible(self, now: float) -> List[tuple]:
        cards: List[tuple] = []
        for prop in self._registry.pending():
            pid = getattr(prop, "proposal_id", "") or ""
            meta = self._registry.proposal_meta(pid) if hasattr(self._registry, "proposal_meta") else {}
            deferred_at = meta.get("deferred_at")
            if deferred_at and (now - float(deferred_at)) < self._aging_threshold_s:
                continue  # DEFER=暂缓:满老化阈值才重新计入(DEFER≠消失)
            created = meta.get("created_ts") or now
            cards.append((prop, now - float(created)))
        cards.sort(key=lambda t: -t[1])
        return cards

    def _card_block(self, idx: int, prop, age_s: float, now: float) -> List[str]:
        pid = getattr(prop, "proposal_id", "") or ""
        kind = str(getattr(prop, "kind", "") or "?")
        summary = str(getattr(prop, "summary", "") or "").strip().replace("\n", " ")
        if len(summary) > SUMMARY_MAX:
            summary = summary[:SUMMARY_MAX] + "…"
        tags = []
        if age_s >= self._aging_threshold_s:
            tags.append(f"⏳挂了{max(int(age_s // 86400), 1)}天")
        tags.append(f"kind={kind}")
        lines = [f"[{idx}] {' · '.join(tags)}", f"    {summary}"]
        if is_high_risk(prop):
            # 高危:不铸码、不带链接 —— 邮箱链路担不了这一级的责
            lines.append("    ⚠ 高危决策:此类不可邮件回批,请回控制台确认。")
            return lines
        expiry = int(now + self._code_ttl_s)
        reply_addr = self._cfg.reply_addr
        links = []
        for decision in ("ACCEPT", "REJECT", "DEFER"):
            code = mint_code(self._secret, pid, decision, expiry)
            subject = f"DECIDE {pid} {decision} {code}"
            links.append(f"    {decision}: mailto:{reply_addr}?subject={quote(subject, safe='')}")
        lines.extend(links)
        return lines

    def build_digest(self, now: float) -> tuple:
        """(subject, body, n_cards)。n_cards=0 → 没得发。正文只有摘要,不带 payload 全文。"""
        cards = self._eligible(now)
        if not cards:
            return "", "", 0
        body_lines = [
            f"KarvyLoop:{len(cards)} 张决策卡等你拍板。",
            "点下面的链接会预填一封回信,直接发送即拍板(链接 24 小时内有效、单次有效)。",
            "回信主题请勿改动;高危决策不可邮件回批,请回控制台。",
            "",
        ]
        for i, (prop, age_s) in enumerate(cards, 1):
            body_lines.extend(self._card_block(i, prop, age_s, now))
            body_lines.append("")
        subject = f"[KarvyLoop] {len(cards)} 张决策卡待处理"
        return subject, "\n".join(body_lines), len(cards)

    def send_digest_if_due(self, now: Optional[float] = None) -> dict:
        """有卡且过了节流窗才发。返回 {"sent": bool, "reason"/"cards": ...};发送失败不抛。"""
        t = time.time() if now is None else float(now)
        if self._last_sent_ts and (t - self._last_sent_ts) < self._cfg.digest_min_interval_s:
            return {"sent": False, "reason": "throttled"}
        subject, body, n = self.build_digest(t)
        if n == 0:
            return {"sent": False, "reason": "no_pending"}
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = self._cfg.smtp.user or self._cfg.reply_addr
        msg["To"] = self._cfg.to
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            self._transport(self._cfg, msg)
        except Exception as e:
            # 只记错误类别,绝不带凭证/正文
            logger.warning("[channels.email] digest 发送失败(下轮再试):%s", type(e).__name__)
            return {"sent": False, "reason": "send_failed"}
        self._last_sent_ts = t
        return {"sent": True, "cards": n}


# =============================================================================
# EmailDecisionPoller — IMAP 出站轮询收件箱,核验回信 → 注入的 decide 回调
# =============================================================================

def _default_imap_fetch(cfg: EmailChannelConfig) -> List[str]:
    """默认 IMAP transport:取 INBOX 未读且主题含 DECIDE 的邮件主题(取走即置已读)。"""
    import imaplib
    from email.header import decode_header, make_header
    i = cfg.imap
    subjects: List[str] = []
    with imaplib.IMAP4_SSL(i.host, i.port) as client:
        client.login(i.user, i.password)
        client.select("INBOX")
        typ, data = client.search(None, "UNSEEN", "SUBJECT", "DECIDE")
        if typ != "OK" or not data or not data[0]:
            return subjects
        for num in data[0].split():
            # 非 PEEK fetch → 该邮件置 \Seen(处理过的不再重取)
            typ, msg_data = client.fetch(num, "(BODY[HEADER.FIELDS (SUBJECT)])")
            if typ != "OK":
                continue
            for part in msg_data:
                if not isinstance(part, tuple) or len(part) < 2:
                    continue
                raw = part[1].decode("utf-8", errors="replace")
                m = re.search(r"(?im)^Subject:\s*(.*(?:\r?\n[ \t].*)*)", raw)
                if not m:
                    continue
                try:
                    subjects.append(str(make_header(decode_header(m.group(1)))))
                except Exception:
                    subjects.append(m.group(1))
    return subjects


class EmailDecisionPoller:
    """轮询收件箱 → 严格解析主题 → 核验 HMAC(单次+限时)→ 注入的 decide 回调。

    宁空勿毒:只认 `DECIDE <id> <ACCEPT|REJECT|DEFER> <code>` 全行匹配;
    自由文本 / 乱格式一律不解析 —— 解析不出 = 当没收到,卡照挂。
    decide 回调签名对齐 /api/h2a_decide 语义(proposal_id, decision),依赖注入,
    本模块不 import console 层。
    """

    def __init__(self, cfg: EmailChannelConfig, secret: bytes, used_store: UsedCodeStore,
                 decide: DecideCallback, *,
                 transport: Optional[Callable] = None,
                 get_proposal: Optional[Callable[[str], object]] = None) -> None:
        self._cfg = cfg
        self._secret = secret
        self._used = used_store
        self._decide = decide
        self._transport = transport or _default_imap_fetch
        self._get_proposal = get_proposal

    def poll_once(self, now: Optional[float] = None) -> List[dict]:
        """拉一轮收件箱,逐封处理;任何异常不外溢(下轮再来)。返回处理结果列表。"""
        t = time.time() if now is None else float(now)
        try:
            subjects = self._transport(self._cfg)
        except Exception as e:
            logger.warning("[channels.email] IMAP 轮询失败(下轮再试):%s", type(e).__name__)
            return []
        return [self._handle_subject(s, t) for s in (subjects or [])]

    def _handle_subject(self, raw_subject: str, now: float) -> dict:
        # 邮件头折行/多空白归一成单空格后再严格 fullmatch(结构仍一字不差)
        subject = re.sub(r"\s+", " ", str(raw_subject or "")).strip()
        m = _SUBJECT_RE.fullmatch(subject)
        if not m:
            # 宁空勿毒:非严格格式(含自由文本)一律当没收到,不解析、不兑现
            return {"status": "ignored", "reason": "not_a_decision_subject"}
        pid, decision, code = m.group(1), m.group(2), m.group(3)
        ok, reason = verify_code(self._secret, pid, decision, code,
                                 now=now, used_store=self._used)
        if not ok:
            logger.info("[channels.email] 回批码核验拒绝 proposal=%s reason=%s", pid, reason)
            return {"status": "rejected", "proposal_id": pid, "reason": reason}
        # 高危双保险:即使有人拿到有效码,高危卡也不许从邮件通道兑现
        if self._get_proposal is not None:
            prop = self._get_proposal(pid)
            if prop is not None and is_high_risk(prop):
                logger.info("[channels.email] 高危卡拒绝邮件回批 proposal=%s", pid)
                return {"status": "rejected", "proposal_id": pid,
                        "reason": "high_risk_console_only"}
        try:
            result = self._decide(pid, decision)
        except Exception as e:
            logger.warning("[channels.email] decide 回调异常 proposal=%s:%s", pid, type(e).__name__)
            return {"status": "error", "proposal_id": pid, "reason": "decide_failed"}
        if result is None:
            # 未知 proposal(已被处理/过期清理)→ 不标已用(码对已消失的卡本就无效)
            return {"status": "rejected", "proposal_id": pid, "reason": "unknown_proposal"}
        # 单次有效:兑现成功后才烧码(避免"标了没兑现"烧掉用户唯一机会)
        self._used.mark_used(code, _code_expiry(code), now=now)
        logger.info("[channels.email] 邮件回批已兑现 proposal=%s decision=%s", pid, decision)
        return {"status": "decided", "proposal_id": pid, "decision": decision}


# =============================================================================
# 组装 + 循环骨架(接线由主线做:本模块不碰 console/app.py)
# =============================================================================

@dataclasses.dataclass
class EmailChannel:
    """一套接好线的邮件通道(sender 必有;imap 未配则 poller=None,只发不收)。"""
    sender: EmailDigestSender
    poller: Optional[EmailDecisionPoller]


def build_email_channel(*, registry, decide: DecideCallback,
                        config_path=None, home=None,
                        transport_send: Optional[Callable] = None,
                        transport_fetch: Optional[Callable] = None) -> Optional[EmailChannel]:
    """从 config.yaml 组一套邮件通道;**默认不配 = 返 None,完全不跑**(零负担)。

    decide 依赖注入(对齐 /api/h2a_decide 处理器语义);registry 需有
    pending()/get()/proposal_meta()(PendingProposalRegistry 满足)。
    """
    cfg = load_email_channel_config(config_path)
    if cfg is None:
        return None
    home_dir = Path(home) if home else Path.home() / ".karvyloop"
    secret = load_or_create_secret(home_dir / SECRET_FILENAME)
    sender = EmailDigestSender(cfg, secret, registry, transport=transport_send)
    poller: Optional[EmailDecisionPoller] = None
    if cfg.imap.host:
        used = UsedCodeStore(home_dir / USED_CODES_FILENAME)
        poller = EmailDecisionPoller(cfg, secret, used, decide,
                                     transport=transport_fetch,
                                     get_proposal=registry.get)
    return EmailChannel(sender=sender, poller=poller)


async def email_channel_tick(channel: Optional[EmailChannel], *,
                             now: Optional[float] = None) -> dict:
    """一次通道心跳:发 digest(有节流)+ 轮询 IMAP(未配 poller 则跳过)。

    SMTP/IMAP 是阻塞 IO → 丢线程跑,不占事件循环。channel=None(未配置)→ 空转返回。
    **本函数不自己接 app.py**——接线(lifespan 里起循环)由主线做。
    """
    import asyncio
    out: dict = {"digest": None, "poll": None}
    if channel is None:
        return out
    out["digest"] = await asyncio.to_thread(channel.sender.send_digest_if_due, now)
    if channel.poller is not None:
        out["poll"] = await asyncio.to_thread(channel.poller.poll_once, now)
    return out


__all__ = [
    "CODE_TTL_S",
    "HIGH_RISK_KIND_MARKERS",
    "HIGH_RISK_TEXT_MARKERS",
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
