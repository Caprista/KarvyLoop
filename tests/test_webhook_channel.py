"""webhook 推送通道测试(channels 广度:决策卡推到你真正在的地方)。

覆盖:preset 成型正确(generic/ntfy/bark/slack + body_template)、节流、未配置零行为、
超时/非 2xx 优雅降级(不外溢、下轮再试)、脱敏(日志绝不出现 token/完整 URL)、
DEFER 老化口径与 email digest 一致。出站 HTTP 走 respx 拦截 / 注入 transport —— **不出网**。
"""
from __future__ import annotations

import json
import logging
from urllib.parse import quote

import pytest

from karvyloop import i18n
from karvyloop.channels.webhook_channel import (
    PushNote,
    WebhookPusher,
    WebhookSendError,
    build_request,
    build_webhook_channel,
    default_console_link,
    redact_url,
    webhook_channel_tick,
    _default_http_send,
    _header_value,
)
from karvyloop.config_channels import (
    WebhookChannelConfig,
    webhook_channel_config_from_dict,
)
from karvyloop.karvy.atoms import Proposal
from karvyloop.karvy.proposal_registry import PendingProposalRegistry

respx = pytest.importorskip("respx")
httpx = pytest.importorskip("httpx")

# 测试 fixture 凭证:必带 FAKE/DO-NOT-LEAK 字样(CLAUDE.md 安全纪律)+ 文末防泄露断言
FAKE_HOOK_TOKEN = "FAKE-DO-NOT-LEAK-hook-token"
FAKE_HOOK_URL = f"https://hooks.example.test/T000/{FAKE_HOOK_TOKEN}"
FAKE_AUTH_HEADER = "Bearer FAKE-DO-NOT-LEAK-bearer"
FAKE_CONSOLE_LINK = "http://192.168.9.9:8766/?token=FAKE-DO-NOT-LEAK-console-token"

T0 = 1_800_000_000.0  # 固定基准时刻(注入 now,不依赖真实时钟)


def make_config(**kw) -> WebhookChannelConfig:
    base = dict(url=FAKE_HOOK_URL, preset="generic", min_interval_s=600, timeout_s=5.0)
    base.update(kw)
    return WebhookChannelConfig(**base)


def make_proposal(kind: str = "route_to_role", summary: str = "把「整理周报」转给「秘书」",
                  pid: str = "", strength: float = 0.8) -> Proposal:
    return Proposal(summary=summary, options=("ACCEPT", "DEFER", "REJECT"), strength=strength,
                    evidence_refs=(), habit_id=0, model_ref="", ts=T0, kind=kind,
                    proposal_id=pid)


def make_note(link: str = FAKE_CONSOLE_LINK, level: str = "medium", count: int = 1) -> PushNote:
    return PushNote(title="[KarvyLoop] 1 decision card(s) waiting for you",
                    text="[1] 把「整理周报」转给「秘书」", link=link, level=level, count=count,
                    cards=({"proposal_id": "p-1", "kind": "route_to_role",
                            "summary": "把「整理周报」转给「秘书」", "level": level, "age_s": 60},))


class CaptureTransport:
    """HTTP 桩:只捕获成型好的请求,不出网。"""
    def __init__(self):
        self.sent = []

    def __call__(self, cfg, req):
        self.sent.append(req)


# =============================================================================
# 配置:默认不配 = 完全不跑;机密防泄露
# =============================================================================

class TestConfig:
    def test_unconfigured_means_none(self):
        assert webhook_channel_config_from_dict({}) is None
        assert webhook_channel_config_from_dict({"channels": {}}) is None
        assert webhook_channel_config_from_dict({"channels": {"webhook": {}}}) is None
        # enabled 缺省 = 不跑;显式 false 也不跑
        assert webhook_channel_config_from_dict(
            {"channels": {"webhook": {"url": FAKE_HOOK_URL}}}) is None
        assert webhook_channel_config_from_dict(
            {"channels": {"webhook": {"enabled": False, "url": FAKE_HOOK_URL}}}) is None

    def test_enabled_but_bad_required_means_none(self, caplog):
        with caplog.at_level(logging.DEBUG):
            # 缺 url
            assert webhook_channel_config_from_dict(
                {"channels": {"webhook": {"enabled": True}}}) is None
            # url 不是 http(s)
            assert webhook_channel_config_from_dict(
                {"channels": {"webhook": {"enabled": True, "url": "ftp://x/y"}}}) is None
            # preset 打错字 → fail-loud 不启动(静默按错格式推 = 假通道)
            assert webhook_channel_config_from_dict(
                {"channels": {"webhook": {"enabled": True, "url": FAKE_HOOK_URL,
                                          "preset": "ntfyy"}}}) is None
        assert FAKE_HOOK_TOKEN not in caplog.text  # 校验日志只报字段名,绝不带值

    def test_full_block_parses_with_aliases_and_defaults(self):
        cfg = webhook_channel_config_from_dict({"channels": {"webhook": {
            "enabled": True, "url": FAKE_HOOK_URL, "preset": "generic-json",
            "headers": {"Authorization": FAKE_AUTH_HEADER, "X-N": 7},
            "body_template": "", "min_interval_s": 120, "timeout_s": 3,
        }}})
        assert cfg is not None and cfg.preset == "generic"
        assert cfg.headers == {"Authorization": FAKE_AUTH_HEADER, "X-N": "7"}
        assert cfg.min_interval_s == 120 and cfg.timeout_s == 3.0
        # 默认值
        cfg2 = webhook_channel_config_from_dict(
            {"channels": {"webhook": {"enabled": True, "url": FAKE_HOOK_URL}}})
        assert cfg2 is not None and cfg2.preset == "generic"
        assert cfg2.min_interval_s == 3600 and cfg2.timeout_s == 10.0

    def test_secrets_never_in_repr(self):
        cfg = make_config(headers={"Authorization": FAKE_AUTH_HEADER})
        for secret in (FAKE_HOOK_TOKEN, FAKE_AUTH_HEADER):
            assert secret not in repr(cfg) and secret not in str(cfg)

    def test_build_unconfigured_returns_none(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("lang: zh\n", encoding="utf-8")
        assert build_webhook_channel(registry=PendingProposalRegistry(),
                                     config_path=cfg_file) is None
        assert build_webhook_channel(registry=PendingProposalRegistry(),
                                     config_path=tmp_path / "nope.yaml") is None


# =============================================================================
# preset 成型(build_request 是纯函数,不出网)
# =============================================================================

class TestPresetShaping:
    def test_generic_json_shape(self):
        req = build_request(make_config(preset="generic"), make_note())
        assert req.method == "POST" and req.headers["Content-Type"] == "application/json"
        body = json.loads(req.body.decode("utf-8"))
        assert body["source"] == "karvyloop" and body["event"] == "h2a.pending"
        assert body["count"] == 1 and body["level"] == "medium"
        assert body["console_url"] == FAKE_CONSOLE_LINK
        assert body["cards"][0]["proposal_id"] == "p-1"
        assert body["cards"][0]["kind"] == "route_to_role"

    def test_ntfy_shape(self):
        note = make_note(level="high")
        req = build_request(make_config(preset="ntfy"), note)
        assert req.method == "POST"
        assert req.headers["Title"] == note.title      # ASCII 标题原样
        assert req.headers["Priority"] == "high"       # 高价值 → 提优先级
        assert req.headers["Click"] == FAKE_CONSOLE_LINK
        assert req.body == note.text.encode("utf-8")
        # 非高价值 → default 优先级;无回链 → 无 Click 头
        req2 = build_request(make_config(preset="ntfy"), make_note(link="", level="low"))
        assert req2.headers["Priority"] == "default" and "Click" not in req2.headers

    def test_ntfy_non_ascii_title_is_rfc2047(self):
        i18n.set_locale("zh")
        try:
            title = "决策卡"
            note = PushNote(title=title, text="x", link="", level="low", count=1)
            req = build_request(make_config(preset="ntfy"), note)
            assert req.headers["Title"].startswith("=?utf-8?b?")
            req.headers["Title"].encode("ascii")  # header 值必须 ASCII-safe(不能抛)
        finally:
            i18n.set_locale(None)

    def test_header_value_ascii_passthrough(self):
        assert _header_value("plain ascii") == "plain ascii"

    def test_bark_is_get_path(self):
        note = make_note()
        req = build_request(make_config(preset="bark", url="https://api.day.app/FAKEKEY"),
                            note)
        assert req.method == "GET" and req.body is None
        assert req.url.startswith("https://api.day.app/FAKEKEY/")
        # 标题/正文进 path(已 %-转义),回链进 ?url=
        assert quote(note.title, safe="") in req.url
        assert f"?url={quote(FAKE_CONSOLE_LINK, safe='')}" in req.url

    def test_slack_shape(self):
        note = make_note()
        req = build_request(make_config(preset="slack"), note)
        body = json.loads(req.body.decode("utf-8"))
        assert set(body) == {"text"}                    # Slack 兼容:只有 text 字段
        assert note.title in body["text"] and FAKE_CONSOLE_LINK in body["text"]

    def test_user_headers_override_preset(self):
        cfg = make_config(preset="generic", headers={"Authorization": FAKE_AUTH_HEADER,
                                                     "Content-Type": "application/x-custom"})
        req = build_request(cfg, make_note())
        assert req.headers["Authorization"] == FAKE_AUTH_HEADER
        assert req.headers["Content-Type"] == "application/x-custom"  # 用户头覆盖 preset 头

    def test_body_template_overrides_preset_and_escapes_json(self):
        # 飞书式自定义承接方:$*_json 占位已转义(标题里的引号/换行注不坏 JSON)
        cfg = make_config(preset="slack", body_template=(
            '{"msg_type": "text", "content": {"text": ${title_json}, "n": $count}}'))
        note = PushNote(title='he said "hi"\nline2', text="t", link="", level="low", count=2)
        req = build_request(cfg, note)
        assert req.method == "POST"
        assert req.headers["Content-Type"] == "application/json"  # 模板长得像 JSON → 自动补头
        body = json.loads(req.body.decode("utf-8"))               # 必须仍是合法 JSON
        assert body["content"]["text"] == 'he said "hi"\nline2' and body["content"]["n"] == 2


# =============================================================================
# Pusher:节流 / 零行为 / 失败降级 / DEFER 口径 / 脱敏
# =============================================================================

class TestPusher:
    def test_push_and_throttle(self):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)
        cap = CaptureTransport()
        pusher = WebhookPusher(make_config(min_interval_s=600), registry,
                               transport=cap, console_link=lambda: FAKE_CONSOLE_LINK)
        assert pusher.push_if_due(now=T0) == {"sent": True, "cards": 1}
        assert len(cap.sent) == 1
        assert pusher.push_if_due(now=T0 + 60) == {"sent": False, "reason": "throttled"}
        assert pusher.push_if_due(now=T0 + 601)["sent"] is True  # 过了节流窗恢复

    def test_no_pending_means_zero_behavior(self):
        cap = CaptureTransport()
        pusher = WebhookPusher(make_config(), PendingProposalRegistry(),
                               transport=cap, console_link=lambda: "")
        assert pusher.push_if_due(now=T0) == {"sent": False, "reason": "no_pending"}
        assert cap.sent == []   # 没卡 = 一个字节都不出

    def test_deferred_card_excluded_same_as_email_digest(self):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(pid="p-defer"), now=T0)
        registry.decide("p-defer", "DEFER", now=T0)
        cap = CaptureTransport()
        pusher = WebhookPusher(make_config(), registry, transport=cap,
                               console_link=lambda: "")
        # DEFER 未满老化阈值 → 不计入(与 email digest 同一口径:channels/common)
        assert pusher.push_if_due(now=T0 + 60) == {"sent": False, "reason": "no_pending"}

    def test_deferred_card_reenters_push_after_aging_with_tag(self):
        """DEFER 满老化阈值后重新浮出推送,带「⏳挂了N天」语义(DEFER≠消失,与 email 同口径)。"""
        from karvyloop.karvy.proposal_registry import AGING_THRESHOLD_S
        registry = PendingProposalRegistry()
        registry.register(make_proposal(pid="p-defer"), now=T0)
        registry.decide("p-defer", "DEFER", now=T0)
        pusher = WebhookPusher(make_config(), registry, transport=CaptureTransport(),
                               console_link=lambda: "")
        assert pusher.build_note(T0 + 3600).count == 0            # 暂缓中,不打扰
        note = pusher.build_note(T0 + AGING_THRESHOLD_S + 3600)   # 满老化阈值回来
        assert note.count == 1
        # 挂龄从 created_ts 算(49h → 2 天),正文带老化标注
        assert i18n.t("channels.webhook.aging", days=2) in note.text

    def test_send_failure_is_swallowed_and_retryable(self, caplog):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)

        def broken_transport(cfg, req):
            raise RuntimeError(f"connect failed for {cfg.url}")  # 恶意异常文本也不放行

        pusher = WebhookPusher(make_config(), registry, transport=broken_transport,
                               console_link=lambda: FAKE_CONSOLE_LINK)
        with caplog.at_level(logging.DEBUG):
            assert pusher.push_if_due(now=T0) == {"sent": False, "reason": "send_failed"}
        # 失败不更新节流水位 → 下轮心跳立即可重试(与 email 通道同语义)
        cap = CaptureTransport()
        pusher._transport = cap
        assert pusher.push_if_due(now=T0 + 1)["sent"] is True
        # 脱敏:日志绝不出现 token / 完整 URL(query/path),只留 scheme+host
        assert FAKE_HOOK_TOKEN not in caplog.text
        assert FAKE_CONSOLE_LINK not in caplog.text
        assert "/T000/" not in caplog.text
        assert "https://hooks.example.test/…" in caplog.text

    def test_console_link_failure_does_not_block_push(self):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)
        cap = CaptureTransport()

        def broken_link() -> str:
            raise RuntimeError("no runtime")

        pusher = WebhookPusher(make_config(preset="generic"), registry,
                               transport=cap, console_link=broken_link)
        assert pusher.push_if_due(now=T0)["sent"] is True
        body = json.loads(cap.sent[0].body.decode("utf-8"))
        assert body["console_url"] == ""   # 回链拿不到 → 空,不阻断通知

    def test_note_truncates_and_folds_overflow(self):
        registry = PendingProposalRegistry()
        for i in range(7):
            registry.register(make_proposal(summary=f"卡{i} " + "长" * 300, pid=f"p-{i}"),
                              now=T0)
        pusher = WebhookPusher(make_config(), registry, transport=CaptureTransport(),
                               console_link=lambda: "")
        note = pusher.build_note(T0 + 60)
        assert note.count == 7
        assert len(note.text.splitlines()) == 6          # 5 张 + 1 行"…还有 2 张"
        assert "2" in note.text.splitlines()[-1]
        for c in note.cards:
            assert len(c["summary"]) <= 161              # SUMMARY_MAX + "…"

    def test_high_risk_marks_level_high(self):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(kind="fs_access_grant", summary="放行 /etc 读",
                                        pid="p-hr", strength=0.1), now=T0)
        pusher = WebhookPusher(make_config(), registry, transport=CaptureTransport(),
                               console_link=lambda: "")
        note = pusher.build_note(T0 + 1)
        assert note.level == "high"
        assert "⚠" in note.text        # 高危卡在正文标注"回控制台确认"


# =============================================================================
# 默认 HTTP transport(respx 拦截):2xx / 非 2xx / 超时
# =============================================================================

class TestHttpTransport:
    @respx.mock
    def test_end_to_end_ok(self):
        route = respx.post(FAKE_HOOK_URL).mock(return_value=httpx.Response(200))
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)
        pusher = WebhookPusher(make_config(preset="generic"), registry,
                               console_link=lambda: FAKE_CONSOLE_LINK)  # 真默认 transport
        assert pusher.push_if_due(now=T0) == {"sent": True, "cards": 1}
        assert route.called
        sent = json.loads(route.calls[0].request.content.decode("utf-8"))
        assert sent["console_url"] == FAKE_CONSOLE_LINK

    @respx.mock
    def test_non_2xx_raises_safe_error_and_degrades(self, caplog):
        respx.post(FAKE_HOOK_URL).mock(return_value=httpx.Response(500))
        with pytest.raises(WebhookSendError) as ei:
            _default_http_send(make_config(), build_request(make_config(), make_note()))
        assert str(ei.value) == "HTTP 500"       # 异常文本只含状态码,可安全入日志
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)
        pusher = WebhookPusher(make_config(), registry, console_link=lambda: "")
        with caplog.at_level(logging.DEBUG):
            assert pusher.push_if_due(now=T0) == {"sent": False, "reason": "send_failed"}
        assert "HTTP 500" in caplog.text and FAKE_HOOK_TOKEN not in caplog.text

    @respx.mock
    def test_timeout_degrades_gracefully(self, caplog):
        respx.post(FAKE_HOOK_URL).mock(side_effect=httpx.ConnectTimeout("boom"))
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)
        pusher = WebhookPusher(make_config(timeout_s=0.1), registry,
                               console_link=lambda: "")
        with caplog.at_level(logging.DEBUG):
            assert pusher.push_if_due(now=T0) == {"sent": False, "reason": "send_failed"}
        assert "ConnectTimeout" in caplog.text and FAKE_HOOK_TOKEN not in caplog.text


# =============================================================================
# tick / 回链 / 脱敏工具
# =============================================================================

class TestTickAndHelpers:
    async def test_tick_none_is_noop(self):
        assert await webhook_channel_tick(None, now=T0) == {"push": None, "poll": None}

    async def test_tick_runs_pusher(self):
        registry = PendingProposalRegistry()
        registry.register(make_proposal(), now=T0)
        cap = CaptureTransport()
        pusher = WebhookPusher(make_config(), registry, transport=cap,
                               console_link=lambda: "")
        # 兼容裸 pusher(纯出站,历史接口):poll 腿为 None
        out = await webhook_channel_tick(pusher, now=T0)
        assert out["push"] == {"sent": True, "cards": 1} and len(cap.sent) == 1
        assert out["poll"] is None

    def test_redact_url_keeps_only_scheme_host(self):
        assert redact_url(FAKE_HOOK_URL) == "https://hooks.example.test/…"
        assert redact_url("https://u:p@h.example.test:8443/a/b?token=x") == \
            "https://h.example.test:8443/…"
        assert FAKE_HOOK_TOKEN not in redact_url(FAKE_HOOK_URL)

    def test_default_console_link_reads_runtime(self, tmp_path):
        rt = tmp_path / "console.runtime.json"
        assert default_console_link(rt) == ""            # 没跑过 console → 空,不炸
        rt.write_text(json.dumps({"token": "FAKE-DO-NOT-LEAK-rt", "host": "127.0.0.1",
                                  "port": 8766}), encoding="utf-8")
        assert default_console_link(rt) == "http://localhost:8766/"   # loopback → 本机链接
        rt.write_text(json.dumps({"token": "FAKE-DO-NOT-LEAK-rt", "host": "192.168.1.5",
                                  "port": 8766}), encoding="utf-8")
        link = default_console_link(rt)
        assert link.startswith("http://192.168.1.5:8766/") and "FAKE-DO-NOT-LEAK-rt" in link
        rt.write_text("not json", encoding="utf-8")
        assert default_console_link(rt) == ""            # 损坏 → 空,不炸

    def test_i18n_keys_exist_in_both_locales(self):
        from karvyloop.i18n._strings import TABLES
        for key in ("channels.webhook.title", "channels.webhook.aging",
                    "channels.webhook.high_risk", "channels.webhook.more",
                    "channels.webhook.open", "channels.webhook.reply_code",
                    "channels.webhook.reply_hint"):
            assert key in TABLES["en"] and key in TABLES["zh"]
