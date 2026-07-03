"""邮件决策闭环测试(docs/43 ⑤a)。

覆盖:HMAC 单次/过期/篡改拒、主题严格解析(乱格式全拒)、高危卡不带回批链接、
digest 节流、decide 回调走真 registry、export 排除 channel_secret、凭证防泄露。
SMTP/IMAP 全走注入 transport 打桩 —— **不发真邮件**。
"""
from __future__ import annotations

import sys
import time
from pathlib import PurePosixPath
from urllib.parse import unquote

import pytest

from karvyloop.channels.email_channel import (
    CODE_TTL_S,
    EmailChannel,
    EmailDecisionPoller,
    EmailDigestSender,
    UsedCodeStore,
    email_channel_tick,
    is_high_risk,
    load_or_create_secret,
    mint_code,
    verify_code,
)
from karvyloop.config_channels import (
    EmailChannelConfig,
    ImapEndpoint,
    SmtpEndpoint,
    email_channel_config_from_dict,
)
from karvyloop.karvy.atoms import Proposal
from karvyloop.karvy.proposal_registry import (
    AGING_THRESHOLD_S,
    KIND_FS_ACCESS,
    PendingProposalRegistry,
)

# 测试 fixture 凭证:必带 FAKE/DO-NOT-LEAK 字样(CLAUDE.md 安全纪律)+ 文末防泄露断言
FAKE_SMTP_PASSWORD = "FAKE-DO-NOT-LEAK-smtp-authcode"
FAKE_IMAP_PASSWORD = "FAKE-DO-NOT-LEAK-imap-authcode"

T0 = 1_800_000_000.0  # 固定基准时刻(注入 now,不依赖真实时钟)


def make_config(min_interval: int = 600) -> EmailChannelConfig:
    return EmailChannelConfig(
        smtp=SmtpEndpoint(host="smtp.example.test", port=465,
                          user="me@example.test", password=FAKE_SMTP_PASSWORD),
        imap=ImapEndpoint(host="imap.example.test", port=993,
                          user="inbox@example.test", password=FAKE_IMAP_PASSWORD),
        to="phone@example.test",
        digest_min_interval_s=min_interval,
    )


def make_proposal(kind: str = "route_to_role", summary: str = "把「整理周报」转给「秘书」",
                  pid: str = "") -> Proposal:
    return Proposal(summary=summary, options=("ACCEPT", "DEFER", "REJECT"), strength=0.8,
                    evidence_refs=(), habit_id=0, model_ref="", ts=T0, kind=kind,
                    proposal_id=pid)


@pytest.fixture()
def secret(tmp_path):
    return load_or_create_secret(tmp_path / "channel_secret")


@pytest.fixture()
def used_store(tmp_path):
    return UsedCodeStore(tmp_path / "channel_used_codes.json")


class CaptureTransport:
    """SMTP 桩:只捕获,不出网。"""
    def __init__(self):
        self.sent = []

    def __call__(self, cfg, msg):
        self.sent.append(msg)


# =============================================================================
# HMAC 回批码
# =============================================================================

class TestHmacCode:
    def test_mint_and_verify_ok(self, secret, used_store):
        code = mint_code(secret, "p-1", "ACCEPT", int(T0 + CODE_TTL_S))
        ok, reason = verify_code(secret, "p-1", "ACCEPT", code, now=T0, used_store=used_store)
        assert ok and reason == "ok"

    def test_expired_rejected(self, secret, used_store):
        code = mint_code(secret, "p-1", "ACCEPT", int(T0 + 10))
        ok, reason = verify_code(secret, "p-1", "ACCEPT", code, now=T0 + 11, used_store=used_store)
        assert not ok and reason == "expired"

    def test_tampered_digest_rejected(self, secret, used_store):
        code = mint_code(secret, "p-1", "ACCEPT", int(T0 + 100))
        head, mac = code.split("-", 1)
        flipped = ("0" if mac[0] != "0" else "1") + mac[1:]
        ok, reason = verify_code(secret, "p-1", "ACCEPT", f"{head}-{flipped}",
                                 now=T0, used_store=used_store)
        assert not ok and reason == "bad_signature"

    def test_code_bound_to_decision_and_proposal(self, secret, used_store):
        """ACCEPT 的码不能拿去 REJECT,也不能挪给别的卡(HMAC 覆盖 pid+decision+expiry)。"""
        code = mint_code(secret, "p-1", "ACCEPT", int(T0 + 100))
        assert not verify_code(secret, "p-1", "REJECT", code, now=T0, used_store=used_store)[0]
        assert not verify_code(secret, "p-2", "ACCEPT", code, now=T0, used_store=used_store)[0]

    def test_tampered_expiry_rejected(self, secret, used_store):
        """把码里的 expiry 往后改续命 → 签名对不上(expiry 参与签名)。"""
        code = mint_code(secret, "p-1", "ACCEPT", int(T0 + 10))
        _, mac = code.split("-", 1)
        ok, reason = verify_code(secret, "p-1", "ACCEPT", f"{int(T0 + 99999)}-{mac}",
                                 now=T0 + 11, used_store=used_store)
        assert not ok and reason == "bad_signature"

    def test_single_use(self, secret, used_store):
        code = mint_code(secret, "p-1", "ACCEPT", int(T0 + 100))
        assert verify_code(secret, "p-1", "ACCEPT", code, now=T0, used_store=used_store)[0]
        used_store.mark_used(code, T0 + 100, now=T0)
        ok, reason = verify_code(secret, "p-1", "ACCEPT", code, now=T0, used_store=used_store)
        assert not ok and reason == "used"

    def test_malformed_code_rejected(self, secret, used_store):
        for bad in ("", "abc", "123", "-abcd", "12-XYZ", "999-"):
            ok, reason = verify_code(secret, "p-1", "ACCEPT", bad, now=T0, used_store=used_store)
            assert not ok and reason == "malformed"

    def test_used_store_persists(self, tmp_path, secret):
        p = tmp_path / "used.json"
        store = UsedCodeStore(p)
        code = mint_code(secret, "p-1", "ACCEPT", int(T0 + 100))
        store.mark_used(code, T0 + 100, now=T0)
        again = UsedCodeStore(p)  # 重启后仍记得(重放防护跨进程)
        assert again.is_used(code)

    def test_secret_created_once_and_stable(self, tmp_path):
        p = tmp_path / "channel_secret"
        s1 = load_or_create_secret(p)
        s2 = load_or_create_secret(p)
        assert s1 == s2 and len(s1) == 64  # 32 字节 hex
        if sys.platform != "win32":
            assert (p.stat().st_mode & 0o777) == 0o600  # 0600 语义(POSIX 真校验)


# =============================================================================
# 主题严格解析(宁空勿毒)
# =============================================================================

def make_poller(secret, used_store, decide, subjects, get_proposal=None):
    return EmailDecisionPoller(make_config(), secret, used_store, decide,
                               transport=lambda cfg: list(subjects),
                               get_proposal=get_proposal)


class TestSubjectStrictParse:
    def test_garbage_subjects_all_ignored(self, secret, used_store):
        calls = []
        garbage = [
            "帮我把那张卡 ACCEPT 了",                      # 自由文本
            "DECIDE",                                       # 缺字段
            "DECIDE p-1 ACCEPT",                            # 缺 code
            "DECIDE p-1 MAYBE 123-abcdef1234567890abcd",    # 非法决策词
            "Re: [KarvyLoop] 3 张决策卡待处理",              # 回复 digest 本身
            "decide p-1 accept 123-abcdef1234567890abcd",   # 小写(严格大小写)
            "DECIDE p-1 ACCEPT 123-abcd 还有一句话",          # 尾随自由文本
            "xDECIDE p-1 ACCEPT 123-abcdef1234567890abcd",  # 前缀污染
            "",
        ]
        poller = make_poller(secret, used_store, lambda pid, d: calls.append((pid, d)), garbage)
        results = poller.poll_once(now=T0)
        assert calls == []  # 一次 decide 都没发生
        assert all(r["status"] == "ignored" for r in results)

    def test_valid_subject_decides(self, secret, used_store):
        calls = []
        code = mint_code(secret, "p-1", "ACCEPT", int(T0 + 100))
        poller = make_poller(secret, used_store,
                             lambda pid, d: calls.append((pid, d)) or {"ok": True},
                             [f"DECIDE p-1 ACCEPT {code}"])
        results = poller.poll_once(now=T0)
        assert calls == [("p-1", "ACCEPT")]
        assert results[0]["status"] == "decided"

    def test_folded_header_whitespace_normalized(self, secret, used_store):
        """邮件头折行(CRLF+空格)归一后仍严格匹配结构。"""
        calls = []
        code = mint_code(secret, "p-1", "DEFER", int(T0 + 100))
        poller = make_poller(secret, used_store,
                             lambda pid, d: calls.append((pid, d)) or {"ok": True},
                             [f"DECIDE p-1\r\n DEFER {code}"])
        poller.poll_once(now=T0)
        assert calls == [("p-1", "DEFER")]

    def test_replayed_subject_rejected_second_time(self, secret, used_store):
        """同一封回信被重放 → 第一次兑现,第二次拒(单次有效)。"""
        calls = []
        code = mint_code(secret, "p-1", "DEFER", int(T0 + 100))
        subjects = [f"DECIDE p-1 DEFER {code}"]
        poller = make_poller(secret, used_store,
                             lambda pid, d: calls.append((pid, d)) or {"ok": True}, subjects)
        r1 = poller.poll_once(now=T0)
        r2 = poller.poll_once(now=T0 + 1)
        assert r1[0]["status"] == "decided"
        assert r2[0]["status"] == "rejected" and r2[0]["reason"] == "used"
        assert len(calls) == 1

    def test_unknown_proposal_not_burned(self, secret, used_store):
        """decide 返 None(卡已不在)→ 不烧码、如实回 unknown。"""
        code = mint_code(secret, "p-gone", "ACCEPT", int(T0 + 100))
        poller = make_poller(secret, used_store, lambda pid, d: None,
                             [f"DECIDE p-gone ACCEPT {code}"])
        r = poller.poll_once(now=T0)
        assert r[0]["status"] == "rejected" and r[0]["reason"] == "unknown_proposal"
        assert not used_store.is_used(code)

    def test_decide_exception_contained(self, secret, used_store):
        def boom(pid, d):
            raise RuntimeError("db locked")
        code = mint_code(secret, "p-1", "ACCEPT", int(T0 + 100))
        poller = make_poller(secret, used_store, boom, [f"DECIDE p-1 ACCEPT {code}"])
        r = poller.poll_once(now=T0)
        assert r[0]["status"] == "error"
        assert not used_store.is_used(code)  # 没兑现不烧码,用户可重试


# =============================================================================
# 内容分级:高危卡只通知不可回批
# =============================================================================

class TestHighRiskGrading:
    def test_fs_access_is_high_risk(self):
        assert is_high_risk(make_proposal(kind=KIND_FS_ACCESS, summary="请求读取 /etc"))
        assert is_high_risk(make_proposal(summary="大额付款:向供应商支付 8 万"))
        assert not is_high_risk(make_proposal())

    def test_high_risk_card_has_no_reply_links(self, secret):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(kind=KIND_FS_ACCESS, summary="角色请求写入工作区外路径"),
                          now=T0)
        sender = EmailDigestSender(make_config(), secret, registry)
        _, body, n = sender.build_digest(T0)
        assert n == 1
        assert "mailto:" not in body               # 不铸码、不带回批链接
        assert "回控制台确认" in body               # 正文写明去控制台

    def test_normal_card_has_all_three_links(self, secret):
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        sender = EmailDigestSender(make_config(), secret, registry)
        _, body, _ = sender.build_digest(T0)
        for decision in ("ACCEPT", "REJECT", "DEFER"):
            assert f"DECIDE%20{pid}%20{decision}%20" in body

    def test_poller_rejects_high_risk_even_with_valid_code(self, secret, used_store):
        """双保险:即使拿到有效码,高危卡也不能从邮件通道兑现。"""
        prop = make_proposal(kind=KIND_FS_ACCESS, summary="放行 /etc")
        registry = PendingProposalRegistry()
        registry.register(prop, now=T0)
        code = mint_code(secret, prop.proposal_id, "ACCEPT", int(T0 + 100))
        calls = []
        poller = make_poller(secret, used_store, lambda pid, d: calls.append(pid),
                             [f"DECIDE {prop.proposal_id} ACCEPT {code}"],
                             get_proposal=registry.get)
        r = poller.poll_once(now=T0)
        assert r[0]["reason"] == "high_risk_console_only"
        assert calls == [] and registry.get(prop.proposal_id) is not None  # 卡照挂

    def test_digest_body_has_no_payload_dump(self, secret):
        registry = PendingProposalRegistry()
        prop = Proposal(summary="转给秘书", options=("ACCEPT", "DEFER", "REJECT"), strength=0.8,
                        evidence_refs=(), habit_id=0, model_ref="", ts=T0, kind="route_to_role",
                        payload={"requirement": "SENSITIVE-PAYLOAD-BODY-TEXT"})
        registry.register(prop, now=T0)
        sender = EmailDigestSender(make_config(), secret, registry)
        _, body, _ = sender.build_digest(T0)
        assert "SENSITIVE-PAYLOAD-BODY-TEXT" not in body  # 卡摘要,不带 payload 全文


# =============================================================================
# digest 节流 + DEFER 老化计入 + tick 骨架
# =============================================================================

class TestDigestSendAndAging:
    def test_throttle(self, secret):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)
        cap = CaptureTransport()
        sender = EmailDigestSender(make_config(min_interval=600), secret, registry, transport=cap)
        assert sender.send_digest_if_due(now=T0)["sent"] is True
        assert sender.send_digest_if_due(now=T0 + 10) == {"sent": False, "reason": "throttled"}
        assert sender.send_digest_if_due(now=T0 + 700)["sent"] is True
        assert len(cap.sent) == 2

    def test_no_pending_no_mail(self, secret):
        cap = CaptureTransport()
        sender = EmailDigestSender(make_config(), secret, PendingProposalRegistry(), transport=cap)
        assert sender.send_digest_if_due(now=T0) == {"sent": False, "reason": "no_pending"}
        assert cap.sent == []

    def test_deferred_card_leaves_then_reenters_digest(self, secret):
        """DEFER 后未满老化阈值不计入 digest;满阈值重新计入(DEFER≠消失)。"""
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        registry.decide(pid, "DEFER", now=T0)
        sender = EmailDigestSender(make_config(), secret, registry)
        assert sender.build_digest(T0 + 3600)[2] == 0                        # 暂缓中,不打扰
        _, body, n = sender.build_digest(T0 + AGING_THRESHOLD_S + 3600)      # 满 48h 回来
        assert n == 1 and "⏳挂了2天" in body

    def test_aged_card_flagged_on_top(self, secret):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(summary="老卡", pid="p-old"), now=T0 - 3 * 86400)
        registry.register(make_proposal(summary="新卡", pid="p-new"), now=T0)
        sender = EmailDigestSender(make_config(), secret, registry)
        _, body, n = sender.build_digest(T0)
        assert n == 2
        assert "⏳挂了3天" in body
        assert body.index("老卡") < body.index("新卡")  # 老卡置顶

    async def test_email_channel_tick_runs_both_legs(self, secret, used_store):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)
        cap = CaptureTransport()
        sender = EmailDigestSender(make_config(), secret, registry, transport=cap)
        poller = make_poller(secret, used_store, lambda pid, d: {"ok": True}, [])
        out = await email_channel_tick(EmailChannel(sender=sender, poller=poller), now=T0)
        assert out["digest"]["sent"] is True and out["poll"] == []

    async def test_email_channel_tick_none_is_noop(self):
        out = await email_channel_tick(None, now=T0)
        assert out == {"digest": None, "poll": None}


# =============================================================================
# decide 回调走真 registry(端到端:digest → 回信 → ACCEPT → pending 消失)
# =============================================================================

class TestDecideAgainstRealRegistry:
    def test_full_loop_accept_removes_pending(self, tmp_path, secret, used_store):
        registry = PendingProposalRegistry(persist_path=tmp_path / "pending.json")
        pid = registry.register(make_proposal(), now=T0)
        cap = CaptureTransport()
        sender = EmailDigestSender(make_config(), secret, registry, transport=cap)
        assert sender.send_digest_if_due(now=T0)["sent"] is True

        # 从真 digest 正文抠出 ACCEPT mailto 主题(用户点链接后发出的就是它)
        body = cap.sent[0].get_content()
        import re as _re
        m = _re.search(r"mailto:[^?\s]+\?subject=(\S+)", body.split("ACCEPT: ")[1])
        subject = unquote(m.group(1))
        assert subject.startswith(f"DECIDE {pid} ACCEPT ")

        handled = []
        handlers = {"route_to_role": lambda p: (handled.append(p) or (True, "done"))}
        decide = lambda p, d: registry.decide(p, d, handlers=handlers)
        poller = make_poller(secret, used_store, decide, [subject], get_proposal=registry.get)
        results = poller.poll_once(now=T0 + 60)

        assert results[0] == {"status": "decided", "proposal_id": pid, "decision": "ACCEPT"}
        assert len(handled) == 1                     # 真走了 kind 兑现
        assert registry.get(pid) is None             # ACCEPT 后 pending 消失
        assert len(registry) == 0

    def test_defer_via_mail_keeps_card_and_stamps(self, secret, used_store):
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        code = mint_code(secret, pid, "DEFER", int(T0 + 100))
        decide = lambda p, d: registry.decide(p, d, now=T0 + 30)
        poller = make_poller(secret, used_store, decide, [f"DECIDE {pid} DEFER {code}"])
        r = poller.poll_once(now=T0 + 30)
        assert r[0]["status"] == "decided"
        assert registry.get(pid) is not None                              # DEFER 卡照挂
        assert registry.proposal_meta(pid)["deferred_at"] == T0 + 30       # 戳上了


# =============================================================================
# 配置:默认不配 = 完全不跑;export 排除;凭证防泄露
# =============================================================================

class TestConfigAndSecrets:
    def test_unconfigured_means_none(self):
        assert email_channel_config_from_dict({}) is None
        assert email_channel_config_from_dict({"channels": {}}) is None
        assert email_channel_config_from_dict({"channels": {"email": {}}}) is None
        # enabled 缺省 = 不跑;显式 false 也不跑
        assert email_channel_config_from_dict(
            {"channels": {"email": {"smtp": {"host": "h"}, "to": "a@b"}}}) is None
        assert email_channel_config_from_dict(
            {"channels": {"email": {"enabled": False, "smtp": {"host": "h"}, "to": "a@b"}}}) is None

    def test_enabled_but_missing_required_means_none(self):
        assert email_channel_config_from_dict(
            {"channels": {"email": {"enabled": True, "to": "a@b"}}}) is None      # 缺 smtp.host
        assert email_channel_config_from_dict(
            {"channels": {"email": {"enabled": True, "smtp": {"host": "h"}}}}) is None  # 缺 to

    def test_full_block_parses(self):
        cfg = email_channel_config_from_dict({"channels": {"email": {
            "enabled": True,
            "smtp": {"host": "smtp.h", "port": 587, "user": "u", "password": FAKE_SMTP_PASSWORD},
            "imap": {"host": "imap.h", "user": "i", "password": FAKE_IMAP_PASSWORD},
            "to": "phone@x", "digest_min_interval_s": 120,
        }}})
        assert cfg is not None and cfg.smtp.port == 587 and cfg.imap.port == 993
        assert cfg.digest_min_interval_s == 120 and cfg.reply_addr == "i"

    def test_build_email_channel_unconfigured_returns_none(self, tmp_path):
        from karvyloop.channels.email_channel import build_email_channel
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("lang: zh\n", encoding="utf-8")
        assert build_email_channel(registry=PendingProposalRegistry(), decide=lambda p, d: None,
                                   config_path=cfg_file, home=tmp_path) is None
        assert not (tmp_path / "channel_secret").exists()  # 完全不跑 = 连 secret 都不生成

    def test_export_excludes_channel_secret(self):
        from karvyloop.cli.export_cmd import _is_excluded
        assert _is_excluded(PurePosixPath("channel_secret"))
        assert _is_excluded(PurePosixPath("config.yaml"))
        assert not _is_excluded(PurePosixPath("channel_used_codes.json"))  # 只有哈希,可携带
        assert not _is_excluded(PurePosixPath("atoms.json"))

    def test_credentials_never_leak(self, secret):
        """授权码与 API key 同级机密:不进 config repr、不进 digest 邮件全文。"""
        cfg = make_config()
        assert FAKE_SMTP_PASSWORD not in repr(cfg) and FAKE_IMAP_PASSWORD not in repr(cfg)
        assert FAKE_SMTP_PASSWORD not in str(cfg)
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)
        cap = CaptureTransport()
        sender = EmailDigestSender(cfg, secret, registry, transport=cap)
        sender.send_digest_if_due(now=T0)
        wire = str(cap.sent[0])  # 整封邮件(头+体)上行内容
        assert FAKE_SMTP_PASSWORD not in wire and FAKE_IMAP_PASSWORD not in wire
        assert secret.decode("utf-8") not in wire  # HMAC secret 本体绝不上行

    def test_credentials_never_hit_logs(self, secret, used_store, caplog):
        """发送失败/核验拒绝路径打日志 → 日志里绝不出现授权码。"""
        import logging as _logging
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)

        def broken_transport(cfg, msg):
            raise RuntimeError(f"auth failed for {cfg.smtp.password}")  # 恶意异常文本也不放行

        sender = EmailDigestSender(make_config(), secret, registry, transport=broken_transport)
        with caplog.at_level(_logging.DEBUG):
            assert sender.send_digest_if_due(now=T0) == {"sent": False, "reason": "send_failed"}
            poller = make_poller(secret, used_store, lambda p, d: None,
                                 ["DECIDE p-1 ACCEPT 123-deadbeefdeadbeefdead"])
            poller.poll_once(now=T0)
        assert FAKE_SMTP_PASSWORD not in caplog.text
        assert FAKE_IMAP_PASSWORD not in caplog.text
