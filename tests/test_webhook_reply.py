"""webhook 入站回批测试(v2:轮询拉取 reply source,手机回一条 `ACCEPT <code>` 即拍板)。

覆盖:出站卡铸码(高危不铸/未配零变化)、回执严格解析(垃圾全忽略)、HMAC 单次/过期/
重放/篡改拒、高危双保险、水位+id 环不重复消费(落盘跨重启)、decide 走真 registry、
未配置零行为、默认 HTTP 拉取(respx 拦截,不出网)、凭证/私有 topic 防泄露。
与 email 通道共用同一套铸码/验码机制(mint_code/verify_code/UsedCodeStore/同一 secret 文件)。
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys

import pytest

from karvyloop.channels.email_channel import (
    CODE_TTL_S,
    SECRET_FILENAME,
    UsedCodeStore,
    load_or_create_secret,
    mint_code,
    verify_code,
)
from karvyloop.channels.webhook_channel import (
    REPLY_DECISION_SLOT,
    REPLY_STATE_FILENAME,
    WEBHOOK_USED_CODES_FILENAME,
    ReplyStateStore,
    WebhookChannel,
    WebhookPusher,
    WebhookReplyPoller,
    build_webhook_channel,
    parse_reply_messages,
    webhook_channel_tick,
)
from karvyloop.config_channels import (
    WebhookChannelConfig,
    webhook_channel_config_from_dict,
)
from karvyloop.karvy.atoms import Proposal
from karvyloop.karvy.proposal_registry import KIND_FS_ACCESS, PendingProposalRegistry

respx = pytest.importorskip("respx")
httpx = pytest.importorskip("httpx")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 测试 fixture 凭证:必带 FAKE/DO-NOT-LEAK 字样(CLAUDE.md 安全纪律)+ 防泄露断言
FAKE_HOOK_TOKEN = "FAKE-DO-NOT-LEAK-hook-token"
FAKE_HOOK_URL = f"https://hooks.example.test/T000/{FAKE_HOOK_TOKEN}"
FAKE_REPLY_TOPIC = "FAKE-DO-NOT-LEAK-reply-topic"
FAKE_REPLY_URL = f"https://ntfy.example.test/{FAKE_REPLY_TOPIC}/json?poll=1"
FAKE_REPLY_AUTH = "Bearer FAKE-DO-NOT-LEAK-reply-bearer"

T0 = 1_800_000_000.0  # 固定基准时刻(注入 now,不依赖真实时钟)


def make_config(**kw) -> WebhookChannelConfig:
    base = dict(url=FAKE_HOOK_URL, preset="generic", min_interval_s=600, timeout_s=5.0,
                reply_url=FAKE_REPLY_URL)
    base.update(kw)
    return WebhookChannelConfig(**base)


def make_proposal(kind: str = "route_to_role", summary: str = "把「整理周报」转给「秘书」",
                  pid: str = "") -> Proposal:
    return Proposal(summary=summary, options=("ACCEPT", "DEFER", "REJECT"), strength=0.8,
                    evidence_refs=(), habit_id=0, model_ref="", ts=T0, kind=kind,
                    proposal_id=pid)


@pytest.fixture()
def secret(tmp_path):
    return load_or_create_secret(tmp_path / SECRET_FILENAME)


@pytest.fixture()
def used_store(tmp_path):
    return UsedCodeStore(tmp_path / WEBHOOK_USED_CODES_FILENAME)


@pytest.fixture()
def state(tmp_path):
    s = ReplyStateStore(tmp_path / REPLY_STATE_FILENAME)
    s.advance(T0 - 100)   # 预立水位,绕过首跑 bootstrap(bootstrap 单独测)
    return s


def make_poller(secret, used_store, state, decide, messages, *, pending, transport=None):
    """messages: List[dict] 或 (cfg, since) -> List[dict] 的可调用。"""
    fetch = transport or (lambda cfg, since: list(messages))
    return WebhookReplyPoller(make_config(), secret, used_store, state, decide,
                              pending=pending, transport=fetch)


# =============================================================================
# 配置:reply 字段(前任已加解析,这里补测)
# =============================================================================

class TestReplyConfig:
    def test_reply_url_parsed_and_optional(self):
        cfg = webhook_channel_config_from_dict({"channels": {"webhook": {
            "enabled": True, "url": FAKE_HOOK_URL,
            "reply_url": FAKE_REPLY_URL,
            "reply_headers": {"Authorization": FAKE_REPLY_AUTH, "X-N": 7},
        }}})
        assert cfg is not None and cfg.reply_url == FAKE_REPLY_URL
        assert cfg.reply_headers == {"Authorization": FAKE_REPLY_AUTH, "X-N": "7"}
        # 不配 reply = 纯出站,字段留空
        cfg2 = webhook_channel_config_from_dict(
            {"channels": {"webhook": {"enabled": True, "url": FAKE_HOOK_URL}}})
        assert cfg2 is not None and cfg2.reply_url == "" and cfg2.reply_headers == {}

    def test_bad_reply_url_fails_loud(self, caplog):
        # 配错 → 整条通道不启动(静默降级成只出站 = 用户以为能手机回批实际黑洞)
        with caplog.at_level(logging.DEBUG):
            assert webhook_channel_config_from_dict({"channels": {"webhook": {
                "enabled": True, "url": FAKE_HOOK_URL, "reply_url": "ftp://x/y",
            }}}) is None
        assert FAKE_HOOK_TOKEN not in caplog.text   # 校验日志只报字段名,绝不带值

    def test_reply_secrets_never_in_repr(self):
        cfg = make_config(reply_headers={"Authorization": FAKE_REPLY_AUTH})
        for s in (FAKE_REPLY_TOPIC, FAKE_REPLY_AUTH):
            assert s not in repr(cfg) and s not in str(cfg)


# =============================================================================
# 出站铸码:非高危卡随推送带回批码;高危不铸;未配 reply = v1 零变化
# =============================================================================

class TestMintOnPush:
    def test_note_carries_reply_code_and_hint(self, secret):
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        pusher = WebhookPusher(make_config(), registry, transport=lambda c, r: None,
                               console_link=lambda: "", secret=secret)
        note = pusher.build_note(T0)
        code = note.cards[0].get("reply_code")
        assert code and code in note.text                     # 卡行带码
        assert "ACCEPT <" in note.text or "ACCEPT <码>" in note.text  # 带回执指令说明
        # 铸出的码走同一套 verify(槽位 REPLY,与 email 通道机制共用、码互不可用)
        ok, reason = verify_code(secret, pid, REPLY_DECISION_SLOT, code, now=T0)
        assert ok and reason == "ok"
        assert not verify_code(secret, pid, "ACCEPT", code, now=T0)[0]  # email 槽位验不过

    def test_high_risk_card_gets_no_code(self, secret):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(kind=KIND_FS_ACCESS, summary="放行 /etc 读"), now=T0)
        pusher = WebhookPusher(make_config(), registry, transport=lambda c, r: None,
                               console_link=lambda: "", secret=secret)
        note = pusher.build_note(T0)
        assert "reply_code" not in note.cards[0]     # 高危:不铸码,只通知
        assert "⚠" in note.text
        assert "ACCEPT <" not in note.text and "↩" not in note.text  # 连指令都不带

    def test_mixed_cards_only_normal_ones_coded(self, secret):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(pid="p-normal"), now=T0)
        registry.register(make_proposal(kind=KIND_FS_ACCESS, summary="放行 /etc",
                                        pid="p-risky"), now=T0)
        pusher = WebhookPusher(make_config(), registry, transport=lambda c, r: None,
                               console_link=lambda: "", secret=secret)
        note = pusher.build_note(T0)
        by_id = {c["proposal_id"]: c for c in note.cards}
        assert "reply_code" in by_id["p-normal"]
        assert "reply_code" not in by_id["p-risky"]

    def test_no_reply_config_means_v1_unchanged(self, secret):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)
        # 没配 reply_url(即使误注入 secret)/ 没注入 secret:都不铸码 —— v1 行为零变化
        for kw in (dict(cfg=make_config(reply_url=""), secret=secret),
                   dict(cfg=make_config(), secret=None)):
            pusher = WebhookPusher(kw["cfg"], registry, transport=lambda c, r: None,
                                   console_link=lambda: "", secret=kw["secret"])
            note = pusher.build_note(T0)
            assert all("reply_code" not in c for c in note.cards)
            assert "↩" not in note.text and "ACCEPT <" not in note.text


# =============================================================================
# reply source 响应解析(宁空勿毒)
# =============================================================================

class TestParseReplyMessages:
    def test_ntfy_ndjson_skips_non_message_events(self):
        raw = "\n".join([
            json.dumps({"id": "a1", "time": 1, "event": "open", "topic": "t"}),
            json.dumps({"id": "a2", "time": 2, "event": "keepalive"}),
            json.dumps({"id": "a3", "time": 3, "event": "message", "message": "ACCEPT 1-ab"}),
            "not json at all {{{",
            json.dumps({"id": "a4", "time": 4, "event": "message", "message": ""}),  # 空正文丢
        ])
        msgs = parse_reply_messages(raw)
        assert msgs == [{"id": "a3", "time": 3.0, "text": "ACCEPT 1-ab"}]

    def test_json_array_of_objects_and_strings(self):
        raw = json.dumps([
            {"id": "m1", "time": 5, "message": "hello"},
            {"id": "m2", "text": "REJECT 9-ff"},
            "bare string reply",
            {"id": "m3"},          # 无正文 → 丢
            42, None, ["nested"],  # 形状不对 → 丢
        ])
        msgs = parse_reply_messages(raw)
        assert [m["text"] for m in msgs] == ["hello", "REJECT 9-ff", "bare string reply"]
        assert msgs[1]["id"] == "m2" and msgs[2]["id"] == ""

    def test_garbage_bodies_yield_empty(self):
        for raw in ("", "   ", "<html>oops</html>", "[not json", json.dumps({"a": 1})):
            assert parse_reply_messages(raw) == []

    def test_non_string_message_field_dropped(self):
        raw = json.dumps([{"id": "x", "message": {"nested": "obj"}}])
        assert parse_reply_messages(raw) == []


# =============================================================================
# poller:严格解析 + HMAC 门(单次/限时/篡改)+ 高危双保险
# =============================================================================

class TestReplyPoller:
    def test_garbage_texts_all_ignored(self, secret, used_store, state):
        calls = []
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        code = mint_code(secret, pid, REPLY_DECISION_SLOT, int(T0 + 100))
        garbage = [
            "帮我把那张卡 ACCEPT 了",                # 自由文本
            "ACCEPT",                                 # 缺码
            f"accept {code}",                         # 小写(严格大小写)
            f"Please ACCEPT {code}",                  # 前缀污染
            f"ACCEPT {code} thanks!",                 # 尾随自由文本(多余内容拒)
            f"MAYBE {code}",                          # 非法决策词
            "ACCEPT 123-XYZ",                         # 码格式非法
            "",
        ]
        msgs = [{"id": f"g{i}", "time": T0, "text": t} for i, t in enumerate(garbage)]
        poller = make_poller(secret, used_store, state,
                             lambda p, d: calls.append((p, d)), msgs,
                             pending=registry.pending)
        results = poller.poll_once(now=T0)
        assert calls == []                            # 一次 decide 都没发生
        assert all(r["status"] == "ignored" for r in results)
        assert registry.get(pid) is not None          # 卡照挂

    def test_valid_code_decides_via_real_registry(self, secret, used_store, state):
        """有效码真走 decide:与 console/email 同一条 registry.decide + handlers 路。"""
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        code = mint_code(secret, pid, REPLY_DECISION_SLOT, int(T0 + 100))
        handled = []
        handlers = {"route_to_role": lambda p: (handled.append(p) or (True, "done"))}
        decide = lambda p, d: registry.decide(p, d, handlers=handlers)
        poller = make_poller(secret, used_store, state, decide,
                             [{"id": "m1", "time": T0, "text": f"ACCEPT {code}"}],
                             pending=registry.pending)
        r = poller.poll_once(now=T0)
        assert r == [{"status": "decided", "proposal_id": pid, "decision": "ACCEPT"}]
        assert len(handled) == 1                      # 真走了 kind 兑现
        assert registry.get(pid) is None              # ACCEPT 后 pending 消失
        assert used_store.is_used(code)               # 兑现后烧码

    def test_defer_keeps_card_and_stamps(self, secret, used_store, state):
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        code = mint_code(secret, pid, REPLY_DECISION_SLOT, int(T0 + 100))
        decide = lambda p, d: registry.decide(p, d, now=T0 + 30)
        poller = make_poller(secret, used_store, state, decide,
                             [{"id": "m1", "time": T0, "text": f"DEFER {code}"}],
                             pending=registry.pending)
        assert poller.poll_once(now=T0 + 30)[0]["status"] == "decided"
        assert registry.get(pid) is not None                          # DEFER 卡照挂
        assert registry.proposal_meta(pid)["deferred_at"] == T0 + 30  # 戳上了

    def test_tampered_code_rejected(self, secret, used_store, state, caplog):
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        code = mint_code(secret, pid, REPLY_DECISION_SLOT, int(T0 + 100))
        head, mac = code.split("-", 1)
        flipped = f"{head}-" + ("0" if mac[0] != "0" else "1") + mac[1:]
        calls = []
        poller = make_poller(secret, used_store, state, lambda p, d: calls.append(p),
                             [{"id": "m1", "time": T0, "text": f"ACCEPT {flipped}"}],
                             pending=registry.pending)
        with caplog.at_level(logging.DEBUG):
            r = poller.poll_once(now=T0)
        assert r[0] == {"status": "rejected", "reason": "no_match"}
        assert calls == [] and registry.get(pid) is not None
        assert flipped not in caplog.text     # 回执正文是数据,不入日志

    def test_expired_code_rejected(self, secret, used_store, state):
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        code = mint_code(secret, pid, REPLY_DECISION_SLOT, int(T0 + 10))
        poller = make_poller(secret, used_store, state, lambda p, d: {"ok": True},
                             [{"id": "m1", "time": T0 + 11, "text": f"ACCEPT {code}"}],
                             pending=registry.pending)
        r = poller.poll_once(now=T0 + 11)
        assert r[0] == {"status": "rejected", "reason": "expired"}

    def test_replay_rejected_even_with_other_verb(self, secret, used_store, state):
        """同一枚码只兑现一次:第二次(换个决策词也一样)拒 used。"""
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        code = mint_code(secret, pid, REPLY_DECISION_SLOT, int(T0 + 100))
        calls = []
        decide = lambda p, d: calls.append((p, d)) or registry.decide(p, d, now=T0)
        msgs = [{"id": "m1", "time": T0, "text": f"DEFER {code}"},
                {"id": "m2", "time": T0 + 1, "text": f"ACCEPT {code}"}]
        poller = make_poller(secret, used_store, state, decide, msgs,
                             pending=registry.pending)
        r = poller.poll_once(now=T0 + 2)
        assert r[0]["status"] == "decided" and r[0]["decision"] == "DEFER"
        assert r[1] == {"status": "rejected", "reason": "used"}
        assert len(calls) == 1

    def test_high_risk_rejected_even_with_valid_code(self, secret, used_store, state):
        """双保险:铸码侧不给高危卡铸码;即使(模拟铸码侧出 bug)拿到有效码也拒。"""
        registry = PendingProposalRegistry()
        prop = make_proposal(kind=KIND_FS_ACCESS, summary="放行 /etc")
        registry.register(prop, now=T0)
        code = mint_code(secret, prop.proposal_id, REPLY_DECISION_SLOT, int(T0 + 100))
        calls = []
        poller = make_poller(secret, used_store, state, lambda p, d: calls.append(p),
                             [{"id": "m1", "time": T0, "text": f"ACCEPT {code}"}],
                             pending=registry.pending)
        r = poller.poll_once(now=T0)
        assert r[0]["reason"] == "high_risk_console_only"
        assert calls == [] and registry.get(prop.proposal_id) is not None  # 卡照挂
        assert not used_store.is_used(code)

    def test_unknown_proposal_not_burned(self, secret, used_store, state):
        """decide 返 None(卡刚被别处处理)→ 不烧码、如实回 unknown。"""
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        code = mint_code(secret, pid, REPLY_DECISION_SLOT, int(T0 + 100))
        poller = make_poller(secret, used_store, state, lambda p, d: None,
                             [{"id": "m1", "time": T0, "text": f"ACCEPT {code}"}],
                             pending=registry.pending)
        r = poller.poll_once(now=T0)
        assert r[0]["reason"] == "unknown_proposal"
        assert not used_store.is_used(code)

    def test_decide_exception_contained(self, secret, used_store, state, caplog):
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        code = mint_code(secret, pid, REPLY_DECISION_SLOT, int(T0 + 100))

        def boom(p, d):
            raise RuntimeError(f"db locked at {FAKE_REPLY_URL}")  # 恶意异常文本也不放行

        poller = make_poller(secret, used_store, state, boom,
                             [{"id": "m1", "time": T0, "text": f"ACCEPT {code}"}],
                             pending=registry.pending)
        with caplog.at_level(logging.DEBUG):
            r = poller.poll_once(now=T0)
        assert r[0]["status"] == "error"
        assert not used_store.is_used(code)   # 没兑现不烧码,用户可重试
        assert FAKE_REPLY_TOPIC not in caplog.text


# =============================================================================
# 水位 + id 环:不重复消费,落盘跨重启
# =============================================================================

class TestWatermark:
    def test_first_run_bootstraps_without_consuming_history(self, tmp_path, secret, used_store):
        called = []
        fresh = ReplyStateStore(tmp_path / "fresh_state.json")
        poller = WebhookReplyPoller(make_config(), secret, used_store, fresh,
                                    lambda p, d: {"ok": True}, pending=lambda: [],
                                    transport=lambda c, s: called.append(s) or [])
        assert poller.poll_once(now=T0) == []
        assert called == []              # 首跑:立水位,不拉取(历史消息不消费)
        assert fresh.since == T0
        poller.poll_once(now=T0 + 60)
        assert called == [T0]            # 第二轮才开始拉,带上水位

    def test_watermark_advances_and_dedupes_ids(self, tmp_path, secret, used_store, state):
        calls = []

        def fetch(cfg, since):
            calls.append(since)
            return [{"id": "m1", "time": T0 + 10, "text": "not a directive"}]

        poller = WebhookReplyPoller(make_config(), secret, used_store, state,
                                    lambda p, d: {"ok": True}, pending=lambda: [],
                                    transport=fetch)
        r1 = poller.poll_once(now=T0 + 20)
        r2 = poller.poll_once(now=T0 + 40)
        assert calls == [T0 - 100, T0 + 10]                       # 第二轮带新水位
        assert r1 == [{"status": "ignored", "reason": "not_a_decision_reply"}]
        assert r2 == [{"status": "ignored", "reason": "duplicate"}]  # id 环去重
        # 落盘跨重启:同一路径新实例,水位/已处理 id 都还在
        reborn = ReplyStateStore(tmp_path / REPLY_STATE_FILENAME)
        assert reborn.since == T0 + 10 and reborn.is_seen("m1")

    def test_corrupt_state_file_fails_safe(self, tmp_path):
        p = tmp_path / "corrupt.json"
        p.write_text("not json", encoding="utf-8")
        s = ReplyStateStore(p)
        assert s.since == 0.0 and not s.is_seen("x")   # 按首跑重建(单次码仍兜重放)

    def test_fetch_failure_swallowed_and_redacted(self, secret, used_store, state, caplog):
        def broken(cfg, since):
            raise RuntimeError(f"connect failed for {cfg.reply_url}")  # 恶意异常文本

        poller = WebhookReplyPoller(make_config(), secret, used_store, state,
                                    lambda p, d: {"ok": True}, pending=lambda: [],
                                    transport=broken)
        with caplog.at_level(logging.DEBUG):
            assert poller.poll_once(now=T0) == []
        assert FAKE_REPLY_TOPIC not in caplog.text     # 只记异常类别 + scheme+host
        assert "https://ntfy.example.test/…" in caplog.text


# =============================================================================
# 默认 HTTP 拉取(respx 拦截,不出网)+ 全链路金线
# =============================================================================

class TestHttpFetchAndGoldenLoop:
    @respx.mock
    def test_golden_loop_push_then_reply_decides(self, tmp_path, caplog):
        """金线:config → 推送铸码 → 手机回 `ACCEPT <code>` → 轮询拉取 → 真 decide 消卡。"""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "channels:\n  webhook:\n    enabled: true\n"
            f"    url: {FAKE_HOOK_URL}\n    preset: generic\n"
            f"    reply_url: {FAKE_REPLY_URL}\n", encoding="utf-8")
        registry = PendingProposalRegistry()
        pid = registry.register(make_proposal(), now=T0)
        handled = []
        handlers = {"route_to_role": lambda p: (handled.append(p) or (True, "done"))}
        decide = lambda p, d: registry.decide(p, d, handlers=handlers)
        channel = build_webhook_channel(registry=registry, decide=decide,
                                        config_path=cfg_file, home=tmp_path,
                                        console_link=lambda: "")
        assert isinstance(channel, WebhookChannel) and channel.poller is not None
        assert (tmp_path / SECRET_FILENAME).exists()   # secret 与 email 通道同一文件

        # ① 出站推送(respx 拦 POST):卡带回批码
        post_route = respx.post(FAKE_HOOK_URL).mock(return_value=httpx.Response(200))
        channel.poller.poll_once(now=T0 - 60)          # 首跑立水位(不消费历史)
        assert channel.pusher.push_if_due(now=T0) == {"sent": True, "cards": 1}
        pushed = json.loads(post_route.calls[0].request.content.decode("utf-8"))
        code = pushed["cards"][0]["reply_code"]
        assert code and "ACCEPT" in pushed["text"]

        # ② 手机上回了一条(respx 拦 GET reply source,ntfy NDJSON 形态)
        ndjson = "\n".join([
            json.dumps({"id": "o1", "time": int(T0), "event": "open"}),
            json.dumps({"id": "r1", "time": int(T0 + 5), "event": "message",
                        "message": f"ACCEPT {code}"}),
        ])
        get_route = respx.route(method="GET", host="ntfy.example.test").mock(
            return_value=httpx.Response(200, text=ndjson))
        with caplog.at_level(logging.DEBUG):
            results = channel.poller.poll_once(now=T0 + 10)

        # ③ 与 console/email 同一条 decide 路兑现,卡消失
        assert {"status": "decided", "proposal_id": pid, "decision": "ACCEPT"} in results
        assert len(handled) == 1 and registry.get(pid) is None
        # 水位参数真的带上了(不重复消费)
        assert "since=" in str(get_route.calls[0].request.url)
        # 私有 topic 绝不入日志
        assert FAKE_REPLY_TOPIC not in caplog.text

    @respx.mock
    def test_non_2xx_poll_degrades(self, tmp_path, secret, used_store, state, caplog):
        respx.route(method="GET", host="ntfy.example.test").mock(
            return_value=httpx.Response(500))
        poller = WebhookReplyPoller(make_config(), secret, used_store, state,
                                    lambda p, d: {"ok": True}, pending=lambda: [])
        with caplog.at_level(logging.DEBUG):
            assert poller.poll_once(now=T0) == []
        assert "HTTP 500" in caplog.text and FAKE_REPLY_TOPIC not in caplog.text

    @respx.mock
    def test_timeout_poll_degrades(self, secret, used_store, state, caplog):
        respx.route(method="GET", host="ntfy.example.test").mock(
            side_effect=httpx.ConnectTimeout("boom"))
        poller = WebhookReplyPoller(make_config(timeout_s=0.1), secret, used_store, state,
                                    lambda p, d: {"ok": True}, pending=lambda: [])
        with caplog.at_level(logging.DEBUG):
            assert poller.poll_once(now=T0) == []
        assert "ConnectTimeout" in caplog.text and FAKE_REPLY_TOPIC not in caplog.text

    @respx.mock
    def test_reply_headers_sent_never_logged(self, secret, used_store, state, caplog):
        route = respx.route(method="GET", host="ntfy.example.test").mock(
            return_value=httpx.Response(200, text=""))
        poller = WebhookReplyPoller(
            make_config(reply_headers={"Authorization": FAKE_REPLY_AUTH}),
            secret, used_store, state, lambda p, d: {"ok": True}, pending=lambda: [])
        with caplog.at_level(logging.DEBUG):
            poller.poll_once(now=T0)
        assert route.calls[0].request.headers["Authorization"] == FAKE_REPLY_AUTH
        assert FAKE_REPLY_AUTH not in caplog.text


# =============================================================================
# 组装 + tick + 接线:未配置零行为;与出站并行同一心跳
# =============================================================================

class TestBuildAndTick:
    def test_build_without_reply_url_has_no_poller_and_zero_footprint(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            f"channels:\n  webhook:\n    enabled: true\n    url: {FAKE_HOOK_URL}\n",
            encoding="utf-8")
        channel = build_webhook_channel(registry=PendingProposalRegistry(),
                                        decide=lambda p, d: None,
                                        config_path=cfg_file, home=tmp_path)
        assert channel is not None and channel.poller is None
        # 纯出站不碰任何入站文件(零足迹):连 secret 都不生成
        for name in (SECRET_FILENAME, WEBHOOK_USED_CODES_FILENAME, REPLY_STATE_FILENAME):
            assert not (tmp_path / name).exists()

    def test_build_reply_url_without_decide_warns_and_disables_inbound(self, tmp_path, caplog):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            f"channels:\n  webhook:\n    enabled: true\n    url: {FAKE_HOOK_URL}\n"
            f"    reply_url: {FAKE_REPLY_URL}\n", encoding="utf-8")
        with caplog.at_level(logging.DEBUG):
            channel = build_webhook_channel(registry=PendingProposalRegistry(),
                                            config_path=cfg_file, home=tmp_path)
        assert channel is not None and channel.poller is None
        assert "reply_url" in caplog.text and FAKE_REPLY_TOPIC not in caplog.text

    async def test_tick_runs_both_legs(self, secret, used_store, state):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)
        sent = []
        pusher = WebhookPusher(make_config(), registry,
                               transport=lambda c, r: sent.append(r),
                               console_link=lambda: "", secret=secret)
        poller = make_poller(secret, used_store, state, lambda p, d: {"ok": True}, [],
                             pending=registry.pending)
        out = await webhook_channel_tick(WebhookChannel(pusher=pusher, poller=poller), now=T0)
        assert out["push"] == {"sent": True, "cards": 1} and out["poll"] == []
        assert len(sent) == 1

    def test_entry_wiring_present(self):
        """防"后端造了没接线"复发:entry 的 decide 桥与 email 同喂决策信号。"""
        src = (ROOT / "karvyloop" / "console" / "entry.py").read_text(encoding="utf-8")
        assert "build_webhook_channel" in src
        assert "_webhook_decide" in src
        assert src.count("record_decision_signals") >= 2   # email + webhook 两条桥
