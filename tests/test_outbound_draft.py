"""docs/96 刀0「对外发送永不直发,一律草稿决策卡」验收测试。

覆盖(任务书 ①-⑦;⑧=既有 deontic/capability/mcp 套件另跑):
  ① 发送类工具名判定表(误判/漏判边界;内置工具全量零命中;策展显式标注 > 名字猜测)
  ② MCP 发信工具在执行咽喉被截成草稿(不真执行、真参数全在草稿/卡里、agent 收到诚实回执)
  ③ ACCEPT 真调原工具原参数;REJECT 零副作用
  ④ 域 deontic forbid 叠加在前(域里禁了外发 → 连草稿卡都不出,直接拒)
  ⑤ outbound_draft ∈ HIGH_RISK_KINDS,绝不被"挣来的静音"自动兑现
  ⑥ 读类工具零回归(照常执行,不出草稿)
  ⑦ 无 store(CLI 裸跑/--no-llm)fail-closed:截住但不放行直发,回执诚实
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from karvyloop.atoms import Terminal, TerminalEvent, ToolResultEvent, run
from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round, tool_round
from karvyloop.capability import outbound_gate as og
from karvyloop.capability.outbound_gate import (
    OutboundDraftStore, is_outbound_send_tool, register_curated_outbound,
)
from karvyloop.gateway import GatewayClient, ModelRegistry
from karvyloop.schemas import AtomSpec, Capability, CapabilityToken


# ---------------------------------------------------------------- 公共脚手架

@pytest.fixture(autouse=True)
def _clean_gate():
    """每个测试前后清全局草稿 store + 策展名单(全局注册表,防串测)。"""
    og.register_store(None)
    og.clear_curated_outbound()
    yield
    og.register_store(None)
    og.clear_curated_outbound()


class _Tool:
    """测试工具:call_log 记录每次真实调用的 input(② 的"没真执行"就看它)。"""

    def __init__(self, name: str):
        self.name = name
        self.call_log: list[dict] = []
        self.description = f"test tool {name}"
        self.parameters = {"type": "object", "properties": {}}

    async def __call__(self, input: dict) -> Any:
        self.call_log.append(dict(input))
        return {"echo": dict(input)}

    def is_concurrency_safe(self, input: dict) -> bool:
        return False


def _atom() -> AtomSpec:
    return AtomSpec(
        id="a-outbound", kind="task", prompt="t",
        input_schema={"type": "object"}, output_schema={"type": "object"},
        tools=[], model="p/a")


def _tok() -> CapabilityToken:
    return CapabilityToken(task_id="t", grants=[Capability(resource="fs:/tmp", ops=["read"])],
                           expiry=time.time() + 3600)


def _gw(adapter: ScriptedMockAdapter) -> GatewayClient:
    reg = ModelRegistry.from_config({
        "models": {"providers": {"p": {"base_url": "x", "models": [
            {"id": "p/a", "api": "anthropic-messages", "context_window": 1000,
             "max_tokens": 100},
        ]}}},
        "agents": {"defaults": {"model": "p/a"}},
        "embedding": {"model": "p/a"},
    })
    return GatewayClient(reg, adapters={"anthropic-messages": adapter})


async def _drive(tools: dict, *, rounds: list, input: dict | None = None) -> list:
    adapter = ScriptedMockAdapter(rounds=rounds + [text_round("done")])
    gw = _gw(adapter)
    return [ev async for ev in run(_atom(), input or {"intent": "测试任务"}, _tok(),
                                   gateway=gw, tools=tools)]


def _result_events(events: list) -> dict:
    return {ev.result.tool_use_id: ev.result
            for ev in events if isinstance(ev, ToolResultEvent)}


SEND_INPUT = {"to": "boss@example.com", "subject": "周报", "body": "本周进展:一切顺利。",
              "cc": ["a@example.com"]}


# ================================================================ ① 判定表
class TestJudgment:
    # 对抗验收 LEAK 修后的判定表(真实 MCP 工具名,与验收方 65 名判定表同口径)
    POSITIVE = [
        "mcp_gmail_send_message", "mcp_gmail_send_email", "send_email", "sendEmail",
        "mcp_slack_post_message", "mcp_twitter_tweet", "mcp_x_dm", "mcp_twilio_send_sms",
        "mcp_twilio_sms", "notify_user", "mcp_gmail_reply", "mcp_gmail_forward_email",
        "mcp_gmail_create_email", "create_message", "publish_post",
        "postMessage", "broadcast_message",
        # LEAK 补漏(对抗验收点名的真实名字,全部必须 True)
        "mcp_gmail_gmail_send",                 # gmail server + 裸 send(裸 send 自足)
        "mcp_resend_send", "mcp_wechat_send",   # 裸 send 自足(唯一排除=send_keys/send_key)
        "broadcast",                            # 裸 broadcast 自足
        "mcp_slack_chat_scheduleMessage",       # 定时发消息 = 延迟直发
        "mcp_slack_chat_meMessage",             # chat × message
        "mcp_slack_chat_postEphemeral",
        "mcp_slack_conversations_add_message",  # korotovsky 真名(add 特例只配 message)
        "mcp_whatsapp_send_file",               # 发文件 = 对外发送(送出动词×内容名词)
        "mcp_paypal_send_invoice",              # 把账单发给客户
        "mcp_gmail_get_thread_and_reply",       # 名含 get 但真会发(reply 压过读豁免)
        "mcp_telegram_forward_message", "mcp_reddit_submit_post",
        "mcp_outlook_send-mail", "mcp_notion_notion-create-comment",
    ]
    # 纯读 / 非发送动作 / 拿不准 → 绝不误判(宁漏勿误拦读操作)
    NEGATIVE = [
        # 读类(get/list/search/read… 任意位置,MCP 前缀名也认得出)
        "mcp_gmail_list_emails", "mcp_gmail_get_message", "mcp_gmail_search_emails",
        "read_file", "web_fetch", "web_search", "list_dir", "mark_email_read",
        "get_send_history", "check_mail", "fetch_emails",
        "mcp_slack_slack_get_channel_history", "mcp_twitter_search_tweets",
        # 非发送动作(删/归档/改/建草稿 ≠ 发)
        "delete_tweet", "archive_mail", "mcp_gmail_delete_email", "update_draft",
        "mcp_gmail_draft_email",                # 只建草稿不发(对抗验收:拦了算误拦方向)
        "mcp_gmail_modify_email", "mcp_gmail_batch_modify_emails",
        "mcp_slack_chat_update", "mcp_slack_chat_delete", "mcp_x_delete_tweet",
        "mcp_notion_notion-update-page",
        # 拿不准不拦(兜底=策展显式标注)
        "mcp_notion_create_page", "mcp_notion_notion-create-pages", "send_keys",
        "browser_send_key", "mcp_github_create_issue", "mcp_github_add_issue_comment",
        "mcp_github_create_pull_request", "mcp_slack_slack_add_reaction",
        "mcp_paypal_create_invoice",            # 只建账单不发
        "run_command", "write_file", "edit_file",
    ]

    def test_positive_table(self):
        for name in self.POSITIVE:
            assert is_outbound_send_tool(name), f"漏判: {name}"

    def test_negative_table(self):
        for name in self.NEGATIVE:
            assert not is_outbound_send_tool(name), f"误判: {name}"

    def test_builtin_tools_all_negative(self):
        """自有内置工具没有一个落在"对外发送"语义(docs/96 刀0 梳理结论,锁住)。"""
        from karvyloop.atoms.tool_catalog import BUILTIN_TOOL_NAMES
        hits = [n for n in BUILTIN_TOOL_NAMES if is_outbound_send_tool(n)]
        assert hits == [], f"内置工具被误判外发: {hits}"

    def test_curated_explicit_beats_name_guess(self):
        """策展显式标注 > 名字猜测:名字判不出(notion-create-pages 名判 False)也拦。"""
        assert not is_outbound_send_tool("mcp_notion_notion_create_pages")
        register_curated_outbound(["mcp_notion_notion_create_pages"])
        assert is_outbound_send_tool("mcp_notion_notion_create_pages")
        # extra_outbound 参数形态(逐调用注入)同效
        assert is_outbound_send_tool("mcp_foo_go", extra_outbound=["mcp_foo_go"])
        # 归一:连字符/大小写
        register_curated_outbound(["Notion-Create-Pages"])
        assert is_outbound_send_tool("notion_create_pages")

    def test_deontic_shares_single_token_table(self):
        """deontic_gate 与本刀共用一份 token 表(单一事实源,别写两份)。"""
        from karvyloop.capability import deontic_gate as dg
        assert dg._SEND_STRONG is og.SEND_STRONG
        assert dg._SEND_NOUNS is og.SEND_NOUNS
        assert dg._SEND_VERBS is og.SEND_VERBS
        assert dg._MAIL_PROGRAMS is og.MAIL_PROGRAMS
        # 扩表在 deontic 侧生效(只收紧):reply/post/publish 组合形态命中 external_send
        assert dg._match_external_send("post_comment", {}) is not None
        assert dg._match_external_send("mcp_gmail_create_email", {}) is not None
        # 旧命中不回退
        assert dg._match_external_send("mcp_gmail_send_message", {}) is not None
        assert dg._match_external_send("mcp_gmail_get_message", {}) is None


# ================================================================ ②⑥⑦ 执行咽喉截点
class TestExecutorIntercept:
    @pytest.mark.asyncio
    async def test_send_tool_intercepted_not_executed_draft_recorded(self):
        """② MCP 发信工具在私聊(无域 forbid)场景被截:不真执行 + 草稿带完整原参 +
        agent 收到诚实回执(草稿已递交,不装发送成功)。"""
        from karvyloop import i18n
        store = OutboundDraftStore()
        og.register_store(store)
        tool = _Tool("mcp_gmail_send_message")
        events = await _drive({"mcp_gmail_send_message": tool},
                              rounds=[tool_round("c1", "mcp_gmail_send_message", SEND_INPUT)],
                              input={"intent": "给老板发周报"})
        assert tool.call_log == []                     # 没真执行
        results = _result_events(events)
        r = results["c1"]
        assert r.is_error is False                     # 合成回执不是错误(agent 可继续)
        assert r.content == {"text": i18n.t("outbound.draft_submitted",
                                            tool="mcp_gmail_send_message")}
        drafts = store.pop_drafts()
        assert len(drafts) == 1
        d = drafts[0]
        assert d["tool"] == "mcp_gmail_send_message"
        assert d["tool_input"] == SEND_INPUT           # 完整入参原样(一个字节不改)
        assert d["intent"] == "给老板发周报"
        assert d["atom_id"] == "a-outbound"
        # 循环正常收尾(agent 继续干别的)
        assert isinstance(events[-1], TerminalEvent)
        assert events[-1].reason == Terminal.COMPLETED

    @pytest.mark.asyncio
    async def test_no_store_fail_closed(self):
        """⑦ 无 store(CLI 裸跑/--no-llm/测试桩):截住但注册不了卡 → 回执=「草稿未能递交」,
        绝不放行直发。"""
        from karvyloop import i18n
        tool = _Tool("mcp_gmail_send_message")
        events = await _drive({"mcp_gmail_send_message": tool},
                              rounds=[tool_round("c1", "mcp_gmail_send_message", SEND_INPUT)])
        assert tool.call_log == []                     # fail-closed:照样不执行
        r = _result_events(events)["c1"]
        assert r.is_error is False
        assert r.content == {"ok": False, "error_message": i18n.t(
            "outbound.draft_channel_missing", tool="mcp_gmail_send_message")}

    @pytest.mark.asyncio
    async def test_read_tools_zero_regression(self):
        """⑥ 读类/普通工具照常执行,不出草稿。"""
        store = OutboundDraftStore()
        og.register_store(store)
        read = _Tool("read_file")
        events = await _drive({"read_file": read},
                              rounds=[tool_round("c1", "read_file", {"path": "/tmp/a"})])
        assert read.call_log == [{"path": "/tmp/a"}]   # 真执行了
        assert store.pop_drafts() == []
        r = _result_events(events)["c1"]
        assert r.is_error is False and r.content == {"echo": {"path": "/tmp/a"}}

    @pytest.mark.asyncio
    async def test_outbound_name_not_in_toolset_stays_unknown(self):
        """模型幻觉调不存在的发信工具名 → 走 run_tools unknown 错误,不出草稿(咽喉只拦
        本次 run 工具集里真存在的名字)。"""
        store = OutboundDraftStore()
        og.register_store(store)
        events = await _drive({}, rounds=[tool_round("c1", "mcp_gmail_send_message", SEND_INPUT)])
        r = _result_events(events)["c1"]
        assert r.is_error is True and "unknown tool" in r.error_reason
        assert store.pop_drafts() == []

    @pytest.mark.asyncio
    async def test_domain_forbid_stacks_first_no_card(self):
        """④ 域 deontic_forbid 硬闸叠加在前:域里禁了外发 → 直接拒(capability_denied +
        deontic 诚实 reason),**连草稿卡都不出**,工具也不执行。"""
        from karvyloop.capability.deontic_gate import build_scope, deontic_scope
        store = OutboundDraftStore()
        og.register_store(store)
        tool = _Tool("mcp_gmail_send_message")
        scope = build_scope(["禁止对外发送邮件"], domain="理财域")
        assert scope is not None
        with deontic_scope(scope):
            events = await _drive({"mcp_gmail_send_message": tool},
                                  rounds=[tool_round("c1", "mcp_gmail_send_message",
                                                     SEND_INPUT)])
        assert tool.call_log == []                     # 没执行
        assert store.pop_drafts() == []                # 连草稿都不出(域闸在前)
        r = _result_events(events)["c1"]
        assert r.is_error is True
        assert "capability_denied" in r.error_reason
        assert "deontic" in r.error_reason or "治理禁止" in r.error_reason

    @pytest.mark.asyncio
    async def test_curated_tool_intercepted_even_if_name_unjudgeable(self):
        """策展标注的工具即使名字判不出外发(notion-create-pages 名判 False),也在咽喉被截。"""
        store = OutboundDraftStore()
        og.register_store(store)
        register_curated_outbound(["mcp_notion_notion_create_pages"])
        tool = _Tool("mcp_notion_notion_create_pages")
        await _drive({"mcp_notion_notion_create_pages": tool},
                     rounds=[tool_round("c1", "mcp_notion_notion_create_pages",
                                        {"pages": [{"content": "hi"}]})])
        assert tool.call_log == []
        assert len(store.pop_drafts()) == 1


# ================================================================ 卡 + 桥 + ③ handler
def _card(tool: str = "mcp_gmail_send_message", tool_input: dict | None = None,
          draft_id: str = "d1"):
    from karvyloop.karvy.proposal_registry import proposal_for_outbound_draft
    return proposal_for_outbound_draft(
        tool=tool, tool_input=dict(tool_input if tool_input is not None else SEND_INPUT),
        ts=time.time(), atom_id="a-outbound", intent="给老板发周报", draft_id=draft_id)


class TestCard:
    def test_card_carries_full_params_and_human_summary(self):
        card = _card()
        assert card.kind == "outbound_draft"
        assert card.payload["tool_input"] == SEND_INPUT     # 完整原参在卡里(可核)
        assert card.payload["tool"] == "mcp_gmail_send_message"
        assert card.payload["server"] == "gmail"
        # summary 人话:要发给谁/干什么
        assert "boss@example.com" in card.summary and "周报" in card.summary
        # basis 详情:收件人/主题/正文原样可核 + 任务归属
        assert "boss@example.com" in card.basis and "本周进展" in card.basis
        assert "给老板发周报" in card.basis
        assert card.proposal_id == "outbound_draft-0-d1"

    def test_unextractable_input_falls_back_to_tool_name(self):
        card = _card(tool="mcp_foo_bar_send_message", tool_input={"weird": 1}, draft_id="d2")
        assert "mcp_foo_bar_send_message" in card.summary
        assert card.payload["tool_input"] == {"weird": 1}

    def test_each_draft_its_own_card(self):
        """逐张拍:不同 draft_id 不幂等收敛。"""
        assert _card(draft_id="x1").proposal_id != _card(draft_id="x2").proposal_id

    # ---- 对抗验收 BUG-1(盲拍):收件人全集 + 隐藏收件人红标 ----

    def test_all_recipient_keys_shown_never_first_hit_only(self):
        """cc/bcc/reply_to 一个都不许丢:prompt 注入塞 bcc 攻击者,summary/basis 必须可见。"""
        evil = {"to": "boss@example.com", "subject": "周报", "body": "ok",
                "cc": ["team@example.com"], "bcc": ["exfil@attacker.com"],
                "reply_to": "shadow@attacker.com"}
        card = _card(tool_input=evil, draft_id="ev1")
        face = card.summary + " " + card.basis
        for addr in ("boss@example.com", "team@example.com",
                     "exfil@attacker.com", "shadow@attacker.com"):
            assert addr in face, f"收件人 {addr} 没铺到卡面(盲拍复发)"
        # bcc/cc 值全部渲染得出 → 不算隐藏,不误红标
        assert card.payload["hidden_recipient_keys"] == []
        # 前端逐条铺出的数据源(单一事实源在后端)
        lines = " ".join(card.payload["recipient_lines"])
        assert "bcc" in lines and "cc" in lines and "reply_to" in lines
        # 完整入参全文在 basis 里可核(前端另有 tool_input 原样 JSON)
        assert "exfil@attacker.com" in card.basis

    def test_hidden_recipient_keys_red_flagged(self):
        """收件人类键值渲不成串(嵌套结构塞法)→ hidden_recipient_keys + summary 红标。"""
        from karvyloop import i18n
        sneaky = {"to": "boss@example.com", "body": "hi",
                  "bcc": [{"email": {"addr": "exfil@attacker.com"}}]}   # 深嵌套渲不出
        card = _card(tool_input=sneaky, draft_id="ev2")
        assert "bcc" in card.payload["hidden_recipient_keys"]
        assert card.summary.startswith(i18n.t("proposal.outbound_draft.hidden_warn"))
        # 完整入参 JSON 仍在 basis(藏起来的地址在全文里可核)
        assert "exfil@attacker.com" in card.basis

    def test_nested_recipient_keys_walked(self):
        """嵌套里的收件人键也扫出来铺出(personalizations[0].to 形态)。"""
        nested = {"personalizations": [{"to": ["a@x.com", "b@x.com"]}], "body": "hi"}
        card = _card(tool_input=nested, draft_id="ev3")
        face = card.summary + " " + card.basis
        assert "a@x.com" in face and "b@x.com" in face
        assert card.payload["hidden_recipient_keys"] == []

    def test_summary_truncates_with_plus_n_not_silently(self):
        """收件人超量:截断必须带 +N(绝不静默丢)。"""
        many = {"to": [f"user{i}@x.com" for i in range(12)], "body": "hi"}
        card = _card(tool_input=many, draft_id="ev4")
        assert "user0@x.com" in card.summary
        # 单键 12 个地址渲染成一条;再加 9 个独立键逼出 +N
        spread = {f"cc_{i}": f"p{i}@x.com" for i in range(9)}
        spread["to"] = "main@x.com"
        card2 = _card(tool_input=spread, draft_id="ev5")
        assert "+" in card2.summary   # 截断带 +N

    @pytest.mark.asyncio
    async def test_bridge_raises_cards_from_store(self):
        """drive 收尾桥(raise_outbound_draft_cards):store 草稿 → 进待决表,队列清空。"""
        from karvyloop.console.proposals import raise_outbound_draft_cards
        from karvyloop.karvy.proposal_registry import PendingProposalRegistry
        store = OutboundDraftStore()
        og.register_store(store)
        store.note_draft("mcp_gmail_send_message", SEND_INPUT,
                         atom_id="a-outbound", intent="给老板发周报")
        reg = PendingProposalRegistry()
        app = SimpleNamespace(state=SimpleNamespace(proposal_registry=reg))
        n = await raise_outbound_draft_cards(app)
        assert n == 1
        assert len(reg) == 1
        (card,) = reg.pending()
        assert card.kind == "outbound_draft"
        assert card.payload["tool_input"] == SEND_INPUT
        assert store.pop_drafts() == []                    # 已消费
        # 再跑一次:无草稿零副作用
        assert await raise_outbound_draft_cards(app) == 0


class _SendTool:
    """handler 测试用假 MCP 工具(CodingTool 形状,async __call__)。"""

    def __init__(self, *, ok: bool = True):
        self.ok = ok
        self.calls: list[dict] = []

    async def __call__(self, inp: dict):
        self.calls.append(dict(inp))
        if self.ok:
            return {"ok": True, "payload": {"text": "message sent"}}
        return {"ok": False, "error_message": "smtp 530 auth failed"}


def _app_with_tool(tool) -> Any:
    return SimpleNamespace(state=SimpleNamespace(
        runtime_kwargs={"mcp_tools": {"mcp_gmail_send_message": tool}}))


class TestHandler:
    def test_accept_invokes_original_tool_with_original_args(self):
        """③ ACCEPT = 真调原工具、原参数一个字节不改;回执人话报成功。"""
        from karvyloop.console.proposal_handlers import _outbound_draft_handler
        from karvyloop.karvy.proposal_registry import PendingProposalRegistry
        tool = _SendTool(ok=True)
        handler = _outbound_draft_handler(_app_with_tool(tool))
        reg = PendingProposalRegistry()
        card = _card()
        reg.register(card)
        res = reg.decide(card.proposal_id, "ACCEPT", handlers={"outbound_draft": handler})
        assert res is not None and res.ok is True
        assert tool.calls == [SEND_INPUT]                  # 原参数原样
        assert len(reg) == 0                               # 兑现后离开待决表
        assert "mcp_gmail_send_message" in res.detail

    def test_accept_send_failure_honest(self):
        from karvyloop.console.proposal_handlers import _outbound_draft_handler
        tool = _SendTool(ok=False)
        handler = _outbound_draft_handler(_app_with_tool(tool))
        ok, detail = handler(_card())
        assert ok is False
        assert "smtp 530 auth failed" in detail            # 真因如实携带
        assert tool.calls == [SEND_INPUT]                  # 调了但失败,不装成功

    def test_accept_tool_missing_honest_no_send(self):
        from karvyloop.console.proposal_handlers import _outbound_draft_handler
        app = SimpleNamespace(state=SimpleNamespace(runtime_kwargs={}))
        handler = _outbound_draft_handler(app)
        ok, detail = handler(_card())
        assert ok is False and "mcp_gmail_send_message" in detail

    def test_reject_zero_side_effect(self):
        """③ REJECT = 丢弃 + 流水:工具零调用,卡移除。"""
        from karvyloop.console.proposal_handlers import _outbound_draft_handler
        from karvyloop.karvy.proposal_registry import PendingProposalRegistry
        tool = _SendTool(ok=True)
        handler = _outbound_draft_handler(_app_with_tool(tool))
        reg = PendingProposalRegistry()
        card = _card()
        reg.register(card)
        res = reg.decide(card.proposal_id, "REJECT", handlers={"outbound_draft": handler})
        assert res is not None and res.ok is True and res.detail == "rejected"
        assert tool.calls == []                            # 零副作用
        assert len(reg) == 0

    def test_handler_registered_in_build_table(self):
        """build_proposal_handlers 真挂了 outbound_draft(重启后恢复的卡也能兑现)。"""
        from karvyloop.console.proposal_handlers import build_proposal_handlers
        handlers = build_proposal_handlers(
            SimpleNamespace(state=SimpleNamespace()))
        assert callable(handlers.get("outbound_draft"))

    # ---- 对抗验收 BUG-2(并发双发):decide 先 pop 后 dispatch,全 kind 生效 ----

    def test_concurrent_accept_dispatches_exactly_once(self):
        """两路(WS+REST/双设备)同时 ACCEPT 同一张卡:恰好发**一次**;后来者拿到
        "已被处置"(或 None=卡已不在,同旧 404 语义)——绝不双发。"""
        import threading
        from karvyloop.console.proposal_handlers import _outbound_draft_handler
        from karvyloop.karvy.proposal_registry import PendingProposalRegistry

        class _SlowTool:
            def __init__(self):
                self.calls = 0
                self._lk = threading.Lock()

            async def __call__(self, inp):
                with self._lk:
                    self.calls += 1
                await asyncio.sleep(0.15)   # 模拟真实发送网络往返窗口(放大竞态窗)
                return {"ok": True, "payload": {"text": "sent"}}

        tool = _SlowTool()
        app = SimpleNamespace(state=SimpleNamespace(
            runtime_kwargs={"mcp_tools": {"mcp_gmail_send_message": tool}}))
        handler = _outbound_draft_handler(app)
        reg = PendingProposalRegistry()
        card = _card(draft_id="race1")
        reg.register(card)
        results: dict = {}
        barrier = threading.Barrier(2)

        def worker(tag):
            barrier.wait()   # 两路同刻进 decide,压最窄的竞态窗
            results[tag] = reg.decide(card.proposal_id, "ACCEPT",
                                      handlers={"outbound_draft": handler})

        t1 = threading.Thread(target=worker, args=("t1",))
        t2 = threading.Thread(target=worker, args=("t2",))
        t1.start(); t2.start(); t1.join(); t2.join()
        assert tool.calls == 1, f"双发!tool.calls={tool.calls}"
        oks = [r for r in results.values() if r is not None and r.ok]
        assert len(oks) == 1                       # 恰一路兑现成功
        loser = [r for r in results.values() if r is None or not r.ok]
        assert len(loser) == 1                     # 另一路:None(已不在)或诚实"已被处置"
        assert len(reg) == 0

    def test_concurrent_reject_accept_no_dispatch_after_reject_wins(self):
        """REJECT 先落地的并发面:输家 ACCEPT 拿"已被处置",工具零调用。"""
        from karvyloop import i18n
        from karvyloop.console.proposal_handlers import _outbound_draft_handler
        from karvyloop.karvy.proposal_registry import PendingProposalRegistry
        tool = _SendTool(ok=True)
        handler = _outbound_draft_handler(_app_with_tool(tool))
        reg = PendingProposalRegistry()
        card = _card(draft_id="race2")
        reg.register(card)
        # 模拟另一路已 REJECT 干净(卡被 pop);本路 ACCEPT 时卡还在自己手里的旧快照里
        snapshot_id = card.proposal_id
        assert reg.decide(snapshot_id, "REJECT",
                          handlers={"outbound_draft": handler}).ok is True
        late = reg.decide(snapshot_id, "ACCEPT", handlers={"outbound_draft": handler})
        assert late is None or (late.ok is False)   # 旧 404 语义 或 诚实"已被处置"
        assert tool.calls == []                     # 绝不发出
        # decide 原子面直查:remove 只有一路拿得到
        card2 = _card(draft_id="race3")
        reg.register(card2)
        first = reg.remove(card2.proposal_id)
        second = reg.remove(card2.proposal_id)
        assert first is not None and second is None
        # "已被处置"回执文案存在于 i18n(en/zh parity 由 i18n 测锁)
        assert i18n.t("proposal.decide.already_handled") != "proposal.decide.already_handled"


# ================================================================ ⑤ 永不静音
class TestNeverSilenced:
    def test_in_high_risk_kinds(self):
        from karvyloop.karvy.silence import HIGH_RISK_KINDS
        assert "outbound_draft" in HIGH_RISK_KINDS

    def test_grant_hard_floor_refuses(self):
        """静音授权硬地板:outbound_draft 桶永远授不出权(卡被伪造也授不出)。"""
        from karvyloop.karvy.silence import SilenceGrantStore
        assert SilenceGrantStore().grant("outbound_draft") is None

    def test_try_silence_never_takes_over(self):
        from karvyloop.karvy.silence import try_silence
        app = SimpleNamespace(state=SimpleNamespace())
        assert try_silence(app, _card()) is False


# ================================================================ 配置面(策展元数据)
class TestCuratedConfig:
    def test_config_yaml_outbound_tools_parsed(self, tmp_path):
        """config.yaml 手写 `outbound_tools:` → McpServerConfig 带回(接入时登记)。"""
        import yaml
        from karvyloop.coding.tools.mcp_tool import read_mcp_server_configs
        cfg = {"mcp": {"servers": [
            {"name": "wechat", "command": "uvx", "args": ["wechat-mcp"],
             "outbound_tools": ["send", "push_text"]},
            {"name": "notion", "url": "https://mcp.notion.com/mcp", "transport": "http",
             "outbound_tools": ["notion-create-pages"]},
        ]}}
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        got = read_mcp_server_configs(str(p))
        assert [c.outbound_tools for c in got] == [["send", "push_text"],
                                                   ["notion-create-pages"]]

    def test_register_short_and_full_names(self):
        """接入登记短名 + 全名(mcp_<server>_<tool>)两种形态皆命中。"""
        register_curated_outbound(["send", "mcp_wechat_send"])
        assert is_outbound_send_tool("mcp_wechat_send")
        assert is_outbound_send_tool("send")

    def test_preset_outbound_tools_declared_for_write_capable_apps(self):
        """预设目录的策展标注(刀1 数据面)→ 本刀消费口径核对:github/notion/slack 的
        发送/写出类工具在 PRESETS 里有显式名单(mcp_manager 接入时按 server 名合并登记)。"""
        from karvyloop.console.mcp_presets import PRESETS
        by_id = {p["id"]: p for p in PRESETS}
        assert by_id["github"]["outbound_tools"]
        assert by_id["notion"]["outbound_tools"]
        # 对抗验收 LEAK③:Slack(占位卡)也先备好策展 —— 官方 server + korotovsky +
        # Web API chat.* 常见发送名;读类绝不进策展
        slack_ob = set(by_id["slack"]["outbound_tools"])
        assert {"slack_post_message", "slack_reply_to_thread",
                "conversations_add_message", "chat_postMessage",
                "chat_scheduleMessage", "chat_meMessage"} <= slack_ob
        assert not any("get" in t or "list" in t or "history" in t for t in slack_ob)
