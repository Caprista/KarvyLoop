"""收件箱→决策卡管道测试(docs/49 ⑲-①)。

覆盖:IMAP 打桩(注入 transport,照 test_email_channel 先例)、三类分诊真形状、
垃圾 LLM 输出→纯通知不出卡、message-id+thread 去重、每轮节流+backlog 落盘、
「绝不外发」结构断言(源码级 grep:无发信库/无发送调用)、凭证 FAKE fixture+防泄露、
token_source 打标、ACCEPT 兑现只写本地台账。IMAP/LLM 全打桩 —— **不出网**。
"""
from __future__ import annotations

import json
import logging
import time
from email.message import EmailMessage

import pytest

from karvyloop.channels.inbox_pipe import (
    CATEGORY_DECISION,
    CATEGORY_NOTICE,
    CATEGORY_REPLY,
    KIND_INBOX_DECISION,
    KIND_INBOX_REPLY,
    SNIPPET_MAX,
    TOKEN_SOURCE,
    InboxLedger,
    InboxMail,
    InboxPipe,
    build_inbox_pipe,
    html_to_text,
    inbox_pipe_tick,
    make_gateway_triage,
    make_inbox_handlers,
    parse_inbox_message,
    proposal_for_inbox_decision,
    proposal_for_inbox_reply,
    triage_material,
    validate_triage,
)
from karvyloop.config_channels import (
    ImapEndpoint,
    InboxPipeConfig,
    inbox_pipe_config_from_dict,
    load_inbox_pipe_config,
)
from karvyloop.karvy.proposal_registry import PendingProposalRegistry, apply_payload_edits

# 测试 fixture 凭证:必带 FAKE/DO-NOT-LEAK 字样(安全纪律)+ 文末防泄露断言
FAKE_IMAP_PASSWORD = "FAKE-DO-NOT-LEAK-inbox-imap-authcode"

T0 = 1_800_000_000.0  # 固定基准时刻(注入 now,不依赖真实时钟)

SECRET_BODY_MARK = "SECRET-FULL-BODY-MUST-NOT-ENTER-CARD"


def make_config(max_cards: int = 5) -> InboxPipeConfig:
    return InboxPipeConfig(
        imap=ImapEndpoint(host="imap.example.test", port=993,
                          user="inbox@example.test", password=FAKE_IMAP_PASSWORD),
        folder="INBOX", poll_interval_s=300, max_cards_per_tick=max_cards,
    )


def raw_mail(sender="alice@example.test", subject="报价确认", body="请确认报价 3 万",
             msg_id="<m1@example.test>", references="", in_reply_to="",
             html: bool = False) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    if msg_id:
        msg["Message-ID"] = msg_id
    if references:
        msg["References"] = references
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    msg["Date"] = "Mon, 29 Jun 2026 10:00:00 +0000"
    if html:
        # 纯 HTML 邮件(无 text/plain part)→ 强制走剥 HTML 路径
        msg.set_content(f"<html><body><p>{body}</p></body></html>", subtype="html")
    else:
        msg.set_content(body)
    return msg.as_bytes()


def make_triage(mapping):
    """打桩分诊器:按 subject 查表;记录被分诊过哪些封(验证 token 纪律:超预算不预支)。"""
    calls = []

    async def triage(mail: InboxMail):
        calls.append(mail.subject)
        return mapping.get(mail.subject)

    triage.calls = calls
    return triage


def make_pipe(tmp_path, subjects_to_triage, raws, max_cards: int = 5):
    registry = PendingProposalRegistry()
    ledger = InboxLedger(tmp_path / "inbox_state.json")
    triage = make_triage(subjects_to_triage)
    pipe = InboxPipe(make_config(max_cards), registry, ledger,
                     triage=triage, transport=lambda cfg: list(raws))
    return pipe, registry, ledger, triage


# =============================================================================
# 邮件解析:剥 HTML 宁空勿毒、无 Message-ID 派生、thread 键
# =============================================================================

class TestParse:
    def test_plain_text_parsed(self):
        mail = parse_inbox_message(raw_mail())
        assert mail is not None
        assert mail.sender == "alice@example.test"
        assert mail.subject == "报价确认"
        assert "3 万" in mail.body
        assert mail.msg_id == "<m1@example.test>"
        assert mail.thread_key == "<m1@example.test>"  # 无 References → 自身开 thread

    def test_html_stripped_to_text(self):
        mail = parse_inbox_message(raw_mail(body="<b>加粗</b>付款请求", html=True))
        assert mail is not None
        assert "<" not in mail.body and "付款请求" in mail.body

    def test_html_to_text_strips_script_and_style(self):
        text = html_to_text("<style>.x{}</style><script>evil()</script><p>正文</p>")
        assert text == "正文"

    def test_html_to_text_garbage_is_empty_not_poison(self):
        # bytes 进来(类型错)→ 宁空勿毒返空,绝不半剥半带标签
        assert html_to_text(b"\x00\xff not html") == ""  # type: ignore[arg-type]

    def test_garbage_bytes_returns_none(self):
        assert parse_inbox_message(b"") is None
        assert parse_inbox_message(b"\x00\x01\x02 not an email at all") is None

    def test_missing_message_id_derives_stable_dedup_key(self):
        raw = raw_mail(msg_id="")
        m1, m2 = parse_inbox_message(raw), parse_inbox_message(raw)
        assert m1.msg_id.startswith("derived-") and m1.msg_id == m2.msg_id

    def test_references_root_is_thread_key(self):
        mail = parse_inbox_message(raw_mail(
            msg_id="<m3@x>", references="<root@x> <mid@x>", in_reply_to="<mid@x>"))
        assert mail.thread_key == "<root@x>"

    def test_body_never_exceeds_keep_chars(self):
        from karvyloop.channels.inbox_pipe import BODY_KEEP_CHARS
        mail = parse_inbox_message(raw_mail(body="长" * (BODY_KEEP_CHARS * 3)))
        assert len(mail.body) <= BODY_KEEP_CHARS


# =============================================================================
# 分诊输出:严格 JSON;垃圾/形状不对 → None(= 纯通知,不出卡)
# =============================================================================

class TestTriageValidation:
    def test_three_valid_categories_pass(self):
        for cat in (CATEGORY_DECISION, CATEGORY_REPLY, CATEGORY_NOTICE):
            out = validate_triage({"category": cat, "reason": "r", "suggested_action": "a"})
            assert out is not None and out["category"] == cat

    def test_garbage_all_rejected(self):
        garbage = [
            None,                                        # 没有输出
            "prose",                                     # 非 dict
            {},                                          # 缺 category
            {"category": "maybe"},                       # 非法类别
            {"category": ["decision"]},                  # 类型错
            {"category": "urgent_decision"},             # 编造类别
            [],                                          # list
        ]
        for bad in garbage:
            assert validate_triage(bad) is None  # 一律当纯通知

    def test_fields_bounded(self):
        out = validate_triage({"category": "reply", "reason": "r" * 9999,
                               "suggested_action": "a" * 9999, "draft": "d" * 99999})
        assert len(out["reason"]) <= 200
        assert len(out["suggested_action"]) <= 200
        assert len(out["draft"]) <= 4000

    def test_material_contains_only_this_mail(self):
        mail = InboxMail(msg_id="<m@x>", thread_key="<m@x>", sender="s@x",
                         subject="subj", body="body text")
        mat = triage_material(mail)
        assert "s@x" in mat and "subj" in mat and "body text" in mat


class TestGatewayTriage:
    """默认分诊器:严格 JSON 解析 + token_source 打标 + 失败降级。"""

    class _FakeGateway:
        """gateway 桩:回放脚本文本,记录调用时的 token_source。"""

        def __init__(self, text: str):
            self._text = text
            self.seen_sources = []
            self.seen_system = []

        def resolve_model(self, scope):
            return "stub-model"

        async def complete(self, messages, tools, model_ref, *, system=None, **kw):
            from karvyloop.gateway.events import TextDelta
            from karvyloop.llm.token_ledger import current_source
            self.seen_sources.append(current_source())
            self.seen_system.append(system)
            yield TextDelta(text=self._text)

    MAIL = InboxMail(msg_id="<m@x>", thread_key="<m@x>", sender="a@x",
                     subject="报价", body="请确认")

    async def test_strict_json_accepted_and_source_tagged(self):
        gw = self._FakeGateway('{"category": "decision", "reason": "报价需拍板", '
                               '"suggested_action": "确认价格", "draft": ""}')
        out = await make_gateway_triage(gw)(self.MAIL)
        assert out == {"category": "decision", "reason": "报价需拍板",
                       "suggested_action": "确认价格", "draft": ""}
        assert gw.seen_sources == [TOKEN_SOURCE]  # token_source 打 inbox_pipe 标

    async def test_fenced_json_unwrapped(self):
        gw = self._FakeGateway('```json\n{"category": "notice"}\n```')
        out = await make_gateway_triage(gw)(self.MAIL)
        assert out is not None and out["category"] == CATEGORY_NOTICE

    async def test_prose_output_means_notice(self):
        gw = self._FakeGateway("我认为这封邮件需要拍板,category 是 decision。")
        assert await make_gateway_triage(gw)(self.MAIL) is None  # prose 不抽

    async def test_gateway_exception_means_notice(self):
        class Boom(self._FakeGateway):
            async def complete(self, *a, **kw):
                raise RuntimeError("upstream 500")
                yield  # pragma: no cover

        assert await make_gateway_triage(Boom(""))(self.MAIL) is None

    async def test_no_gateway_means_notice(self):
        assert await make_gateway_triage(None)(self.MAIL) is None


# =============================================================================
# 三类分诊 → 卡的真形状(payload/kind/basis;全文绝不进卡)
# =============================================================================

# 标记放在 SNIPPET_MAX 之外:snippet(≤160 字)合法,全文(含深处标记)绝不进卡
LONG_BODY = "细节" * 400 + f"。{SECRET_BODY_MARK}。尾"


def _mail(subject="报价确认", body=LONG_BODY, msg_id="<m1@x>") -> InboxMail:
    return InboxMail(msg_id=msg_id, thread_key=msg_id, sender="alice@example.test",
                     subject=subject, body=body, ts=T0)


class TestCardShapes:
    def test_decision_card_shape(self):
        # 卡文案走 i18n(按当前 locale 定稿)→ 锁 zh 断言中文原文(模板层),数据字段 locale 无关
        from karvyloop import i18n
        triage = {"category": CATEGORY_DECISION, "reason": "涉及付款",
                  "suggested_action": "确认后回复对方", "draft": ""}
        try:
            i18n.set_locale("zh")
            p = proposal_for_inbox_decision(_mail(), triage, ts=T0)
            assert "涉及付款" in p.basis and "绝不对外发信" in p.basis
        finally:
            i18n.set_locale(None)
        assert p.kind == KIND_INBOX_DECISION
        assert p.options == ("ACCEPT", "DEFER", "REJECT")
        assert p.payload["from"] == "alice@example.test"
        assert p.payload["subject"] == "报价确认"
        assert p.payload["message_id"] == "<m1@x>"
        assert p.payload["suggested_action"] == "确认后回复对方"
        assert len(p.payload["snippet"]) <= SNIPPET_MAX + 1  # +1 = 省略号
        assert p.proposal_id.startswith(f"{KIND_INBOX_DECISION}-0-")

    def test_reply_card_carries_editable_draft(self):
        from karvyloop import i18n
        triage = {"category": CATEGORY_REPLY, "reason": "普通答疑",
                  "suggested_action": "回复对方", "draft": "您好,收到,明天回复您。"}
        try:
            i18n.set_locale("zh")
            p = proposal_for_inbox_reply(_mail(), triage, ts=T0)
            assert "自行复制发送" in p.basis and "不代发" in p.basis
        finally:
            i18n.set_locale(None)
        assert p.kind == KIND_INBOX_REPLY
        assert p.payload["draft"] == "您好,收到,明天回复您。"
        # draft 是 payload 里的 str → 走「改了再批」白名单,用户可就地改草稿再批
        edited = apply_payload_edits(p, {"draft": "改过的草稿"})
        assert edited.payload["draft"] == "改过的草稿"

    def test_full_body_never_enters_card(self):
        """隐私分级:正文全文不进卡 —— payload/basis/summary 任何角落都没有。"""
        for factory, triage in (
            (proposal_for_inbox_decision,
             {"category": CATEGORY_DECISION, "reason": "r", "suggested_action": "a", "draft": ""}),
            (proposal_for_inbox_reply,
             {"category": CATEGORY_REPLY, "reason": "r", "suggested_action": "a", "draft": "d"}),
        ):
            p = factory(_mail(), triage, ts=T0)
            whole_card = json.dumps({"summary": p.summary, "basis": p.basis,
                                     "payload": p.payload}, ensure_ascii=False)
            assert SECRET_BODY_MARK not in whole_card
            assert LONG_BODY not in whole_card

    def test_same_thread_same_proposal_id(self):
        """幂等:同 thread 两封 → proposal_id 相同 → registry 收敛一张卡,不刷屏。"""
        triage = {"category": CATEGORY_DECISION, "reason": "r", "suggested_action": "a", "draft": ""}
        m1 = InboxMail(msg_id="<a@x>", thread_key="<root@x>", sender="s", subject="x", body="")
        m2 = InboxMail(msg_id="<b@x>", thread_key="<root@x>", sender="s", subject="Re: x", body="")
        assert (proposal_for_inbox_decision(m1, triage, ts=T0).proposal_id
                == proposal_for_inbox_decision(m2, triage, ts=T0).proposal_id)


# =============================================================================
# 管道整轮:三类走真 registry、垃圾输出→纯通知、去重、节流+backlog
# =============================================================================

class TestPipePollOnce:
    async def test_three_categories_end_to_end(self, tmp_path):
        raws = [
            raw_mail(subject="报价确认", msg_id="<d1@x>"),
            raw_mail(subject="约个时间", msg_id="<r1@x>"),
            raw_mail(subject="系统通知", msg_id="<n1@x>"),
        ]
        pipe, registry, _, _ = make_pipe(tmp_path, {
            "报价确认": {"category": "decision", "reason": "付款", "suggested_action": "拍板"},
            "约个时间": {"category": "reply", "reason": "答疑", "suggested_action": "回复",
                        "draft": "周三下午可以。"},
            "系统通知": {"category": "notice"},
        }, raws)
        stats = await pipe.poll_once(now=T0)
        assert stats == {"fetched": 3, "cards": 1, "replies": 1, "notices": 1,
                         "deduped": 0, "backlog": 0}
        kinds = sorted(p.kind for p in registry.pending())
        assert kinds == sorted([KIND_INBOX_DECISION, KIND_INBOX_REPLY])
        reply = next(p for p in registry.pending() if p.kind == KIND_INBOX_REPLY)
        assert reply.payload["draft"] == "周三下午可以。"

    async def test_garbage_triage_means_notice_no_card(self, tmp_path):
        """分诊器输出垃圾(形状不对/异常)→ 一律纯通知,绝不误卡。"""
        raws = [raw_mail(subject=f"垃圾{i}", msg_id=f"<g{i}@x>") for i in range(3)]

        outputs = [{"category": "urgent!!"}, "prose", None]

        async def garbage_triage(mail):
            out = outputs.pop(0)
            if out == "prose":
                raise RuntimeError("LLM went off the rails")
            return out

        registry = PendingProposalRegistry()
        pipe = InboxPipe(make_config(), registry, InboxLedger(tmp_path / "s.json"),
                         triage=garbage_triage, transport=lambda cfg: list(raws))
        stats = await pipe.poll_once(now=T0)
        assert stats["notices"] == 3 and stats["cards"] == 0 and stats["replies"] == 0
        assert len(registry) == 0  # 宁静默勿误卡

    async def test_dedup_by_message_id_across_ticks(self, tmp_path):
        raws = [raw_mail(subject="报价确认", msg_id="<d1@x>")]
        pipe, registry, _, triage = make_pipe(tmp_path, {
            "报价确认": {"category": "decision", "reason": "r", "suggested_action": "a"},
        }, raws)
        await pipe.poll_once(now=T0)
        stats2 = await pipe.poll_once(now=T0 + 300)  # 同一封又被拉到(未置已读等场景)
        assert stats2["deduped"] == 1 and stats2["cards"] == 0
        assert triage.calls == ["报价确认"]  # 第二轮没预支分诊(token 纪律)
        assert len(registry) == 1

    async def test_dedup_by_thread(self, tmp_path):
        """同 thread 第二封(不同 message-id)→ 不再出卡不再分诊。"""
        raws = [
            raw_mail(subject="合同", msg_id="<t1@x>"),
            raw_mail(subject="Re: 合同", msg_id="<t2@x>",
                     references="<t1@x>", in_reply_to="<t1@x>"),
        ]
        pipe, registry, _, triage = make_pipe(tmp_path, {
            "合同": {"category": "decision", "reason": "r", "suggested_action": "a"},
            "Re: 合同": {"category": "decision", "reason": "r", "suggested_action": "a"},
        }, raws)
        stats = await pipe.poll_once(now=T0)
        assert stats["cards"] == 1 and stats["deduped"] == 1
        assert triage.calls == ["合同"]
        assert len(registry) == 1

    async def test_throttle_and_backlog_no_prepaid_triage(self, tmp_path):
        """8 封全 decision:预算 5 → 出 5 张卡;其余 3 封记 backlog 且**没被分诊**。"""
        raws = [raw_mail(subject=f"决策{i}", msg_id=f"<b{i}@x>") for i in range(8)]
        mapping = {f"决策{i}": {"category": "decision", "reason": "r", "suggested_action": "a"}
                   for i in range(8)}
        pipe, registry, ledger, triage = make_pipe(tmp_path, mapping, raws, max_cards=5)
        stats = await pipe.poll_once(now=T0)
        assert stats["cards"] == 5 and stats["backlog"] == 3
        assert len(triage.calls) == 5  # 超预算不预支分诊(token 纪律)
        assert len(registry) == 5

        # backlog 落盘,重启不丢
        reloaded = InboxLedger(tmp_path / "inbox_state.json")
        assert [m.subject for m in reloaded.backlog()] == ["决策5", "决策6", "决策7"]

    async def test_backlog_processed_first_next_tick(self, tmp_path):
        raws = [raw_mail(subject=f"决策{i}", msg_id=f"<b{i}@x>") for i in range(6)]
        mapping = {f"决策{i}": {"category": "decision", "reason": "r", "suggested_action": "a"}
                   for i in range(6)}
        pipe, registry, _, triage = make_pipe(tmp_path, mapping, raws, max_cards=5)
        await pipe.poll_once(now=T0)
        # 第二轮:不再来新邮件;backlog 那封先处理,其余 5 封走 message-id 去重
        pipe._transport = lambda cfg: []
        stats2 = await pipe.poll_once(now=T0 + 300)
        assert stats2 == {"fetched": 0, "cards": 1, "replies": 0, "notices": 0,
                          "deduped": 0, "backlog": 0}
        assert triage.calls[-1] == "决策5"
        assert len(registry) == 6

    async def test_imap_failure_swallowed(self, tmp_path):
        def broken(cfg):
            raise RuntimeError(f"login failed for {cfg.imap.password}")

        registry = PendingProposalRegistry()
        pipe = InboxPipe(make_config(), registry, InboxLedger(tmp_path / "s.json"),
                         triage=make_triage({}), transport=broken)
        stats = await pipe.poll_once(now=T0)
        assert stats["fetched"] == 0 and len(registry) == 0  # 下轮再试,不外溢

    async def test_tick_none_is_noop(self):
        assert await inbox_pipe_tick(None, now=T0) == {"inbox": None}

    async def test_corrupt_ledger_rebuilds(self, tmp_path):
        p = tmp_path / "inbox_state.json"
        p.write_text("{not json!!", encoding="utf-8")
        ledger = InboxLedger(p)  # fail-safe 成空,不炸
        assert ledger.backlog() == [] and not ledger.is_seen("<x@x>")


# =============================================================================
# ACCEPT 兑现:只写本地台账,零外部副作用;走真 registry.decide
# =============================================================================

class TestAcceptHandlers:
    async def test_decision_accept_writes_ledger_only(self, tmp_path):
        raws = [raw_mail(subject="报价确认", msg_id="<d1@x>")]
        pipe, registry, _, _ = make_pipe(tmp_path, {
            "报价确认": {"category": "decision", "reason": "r", "suggested_action": "确认报价"},
        }, raws)
        await pipe.poll_once(now=T0)
        pid = registry.pending()[0].proposal_id
        handlers = make_inbox_handlers(home=tmp_path)
        out = registry.decide(pid, "ACCEPT", handlers=handlers, now=T0)
        assert out.ok is True and "不代发" in out.detail
        actions = json.loads((tmp_path / "inbox_actions.json").read_text(encoding="utf-8"))
        assert actions[-1]["kind"] == KIND_INBOX_DECISION
        assert actions[-1]["message_id"] == "<d1@x>"

    async def test_reply_accept_shows_edited_draft_no_send(self, tmp_path):
        raws = [raw_mail(subject="约个时间", msg_id="<r1@x>")]
        pipe, registry, _, _ = make_pipe(tmp_path, {
            "约个时间": {"category": "reply", "reason": "r", "suggested_action": "a",
                        "draft": "原草稿"},
        }, raws)
        await pipe.poll_once(now=T0)
        pid = registry.pending()[0].proposal_id
        out = registry.decide(pid, "ACCEPT", handlers=make_inbox_handlers(home=tmp_path),
                              edits={"draft": "改过的草稿"}, now=T0)  # 改了再批
        assert out.ok is True and "自行发送" in out.detail and "改过的草稿" in out.detail
        actions = json.loads((tmp_path / "inbox_actions.json").read_text(encoding="utf-8"))
        assert actions[-1]["draft"] == "改过的草稿"


# =============================================================================
# 「绝不外发」结构断言(deontic 级):模块源码没有任何发信路径
# =============================================================================

class TestNeverSendsStructurally:
    def test_module_source_has_no_send_capability(self):
        """结构断言:inbox_pipe 不 import 发信库、无任何发送调用 —— 结构上发不了信。

        代码体(剥掉 docstring/注释后)grep 发信痕迹;import 面走 AST 精确断言
        (docstring 里解释"发信是别的通道的事"是合法文档,不算借道)。
        """
        import ast
        import inspect

        import karvyloop.channels.inbox_pipe as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)

        # ① import 面:不 import 任何发信库,也不借道 email_channel 的发信面
        forbidden_modules = ("smtplib", "aiosmtplib", "email_channel")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            else:
                continue
            joined = " ".join(names)
            for bad in forbidden_modules:
                assert bad not in joined, f"inbox_pipe import 了发信面:{bad}({joined})"

        # ② 调用面:剥 docstring 后的代码体没有任何发送调用痕迹
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and \
                        isinstance(body[0].value, ast.Constant) and \
                        isinstance(body[0].value.value, str):
                    body[0].value.value = ""  # 抹 docstring,只留代码体
        code = ast.unparse(tree)
        for forbidden in ("smtplib", "sendmail", "send_message", "SMTP(",
                          "SMTP_SSL", "aiosmtplib", "starttls", "email_channel"):
            assert forbidden not in code, f"inbox_pipe 代码体出现发信痕迹:{forbidden}"

    def test_inbox_config_structurally_receive_only(self):
        """InboxPipeConfig 只有 IMAP 端点 —— 结构上没有 SMTP 字段可用。"""
        import dataclasses
        names = {f.name for f in dataclasses.fields(InboxPipeConfig)}
        assert "smtp" not in names and "imap" in names

    def test_handlers_have_no_network_side_effect(self, tmp_path, monkeypatch):
        """ACCEPT 兑现期间连 socket 都不许开(兜底防未来回归)。"""
        import socket

        def no_net(*a, **kw):
            raise AssertionError("inbox handler 试图出网")

        monkeypatch.setattr(socket.socket, "connect", no_net)
        handlers = make_inbox_handlers(home=tmp_path)
        p = proposal_for_inbox_reply(
            _mail(), {"category": "reply", "reason": "r",
                      "suggested_action": "a", "draft": "草稿"}, ts=T0)
        ok, detail = handlers[KIND_INBOX_REPLY](p)
        assert ok is True


# =============================================================================
# 配置:默认不配 = 完全不跑;独立于 email.enabled;凭证防泄露
# =============================================================================

BASE_IMAP = {"host": "imap.h", "user": "i@x", "password": FAKE_IMAP_PASSWORD}


class TestConfig:
    def test_unconfigured_means_none(self):
        assert inbox_pipe_config_from_dict({}) is None
        assert inbox_pipe_config_from_dict({"channels": {"email": {}}}) is None
        # inbox 块在但 enabled 缺省/false → 不跑
        assert inbox_pipe_config_from_dict(
            {"channels": {"email": {"imap": BASE_IMAP, "inbox": {"folder": "X"}}}}) is None
        assert inbox_pipe_config_from_dict(
            {"channels": {"email": {"imap": BASE_IMAP,
                                    "inbox": {"enabled": False}}}}) is None

    def test_enabled_but_imap_missing_means_none(self):
        assert inbox_pipe_config_from_dict(
            {"channels": {"email": {"inbox": {"enabled": True}}}}) is None
        assert inbox_pipe_config_from_dict(
            {"channels": {"email": {"imap": {"host": "h"},  # 缺 user
                                    "inbox": {"enabled": True}}}}) is None

    def test_independent_of_email_channel_enabled(self):
        """inbox 不受 channels.email.enabled(digest 通道开关)控制 —— 两条腿各自独立。"""
        cfg = inbox_pipe_config_from_dict(
            {"channels": {"email": {"enabled": False, "imap": BASE_IMAP,
                                    "inbox": {"enabled": True}}}})
        assert cfg is not None and cfg.imap.user == "i@x"

    def test_defaults_and_bad_values(self):
        cfg = inbox_pipe_config_from_dict(
            {"channels": {"email": {"imap": BASE_IMAP,
                                    "inbox": {"enabled": True, "poll_interval_s": "abc",
                                              "max_cards_per_tick": -3}}}})
        assert cfg.folder == "INBOX"
        assert cfg.poll_interval_s == 300 and cfg.max_cards_per_tick == 5

    def test_build_pipe_unconfigured_returns_none(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("lang: zh\n", encoding="utf-8")
        assert build_inbox_pipe(registry=PendingProposalRegistry(),
                                config_path=cfg_file, home=tmp_path) is None

    def test_build_pipe_from_yaml(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "channels:\n  email:\n    imap:\n      host: imap.h\n      user: i@x\n"
            f"      password: '{FAKE_IMAP_PASSWORD}'\n"
            "    inbox:\n      enabled: true\n      max_cards_per_tick: 2\n",
            encoding="utf-8")
        pipe = build_inbox_pipe(registry=PendingProposalRegistry(),
                                config_path=cfg_file, home=tmp_path)
        assert pipe is not None and pipe._cfg.max_cards_per_tick == 2
        assert load_inbox_pipe_config(cfg_file).folder == "INBOX"

    def test_credentials_never_leak_in_repr_cards_or_logs(self, tmp_path, caplog):
        cfg = make_config()
        assert FAKE_IMAP_PASSWORD not in repr(cfg) and FAKE_IMAP_PASSWORD not in str(cfg)

        # IMAP 失败(恶意异常文本含授权码)→ 日志只记异常类别,不带凭证
        def broken(c):
            raise RuntimeError(f"login failed: {c.imap.password}")

        registry = PendingProposalRegistry()
        pipe = InboxPipe(cfg, registry, InboxLedger(tmp_path / "s.json"),
                         triage=make_triage({}), transport=broken)
        with caplog.at_level(logging.DEBUG):
            import asyncio
            stats = asyncio.run(pipe.poll_once(now=T0))
        assert stats["fetched"] == 0
        assert FAKE_IMAP_PASSWORD not in caplog.text

    async def test_cards_and_state_never_carry_credentials(self, tmp_path):
        raws = [raw_mail(subject="报价确认", msg_id="<d1@x>")]
        pipe, registry, _, _ = make_pipe(tmp_path, {
            "报价确认": {"category": "decision", "reason": "r", "suggested_action": "a"},
        }, raws)
        await pipe.poll_once(now=T0)
        card = registry.pending()[0]
        wire = json.dumps({"summary": card.summary, "basis": card.basis,
                           "payload": card.payload}, ensure_ascii=False)
        assert FAKE_IMAP_PASSWORD not in wire
        state = (tmp_path / "inbox_state.json").read_text(encoding="utf-8")
        assert FAKE_IMAP_PASSWORD not in state
