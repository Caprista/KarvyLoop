"""notify_user 审批闭环端到端:工具免审 → 升卡 → 拍板 → 投递。

回归背景(2026-09 实拍):notify_user 名字里的 "notify" 被 outbound_gate 判成外发工具,
调用在执行咽喉被截成 outbound_draft 卡;ACCEPT 兑现 handler 只回查 runtime_kwargs["mcp_tools"]
(工具是每轮 drive 动态挂载,不在池里)→ 批准后发送落空,outbox 零记录。
修复:①notify_user 注册 outbound_bypass(自具审批链);②pending 通知升
notification_approval 决策卡(ACCEPT→queued,REJECT 钩子→rejected)。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from karvyloop.capability.outbound_gate import (
    clear_outbound_bypass,
    is_outbound_send_tool,
)
from karvyloop.karvy.proposal_registry import (
    KIND_NOTIFICATION_APPROVAL,
    PendingProposalRegistry,
    proposal_for_notification_approval,
)
from karvyloop.notifications import (NotificationCommand, NotificationRuntime,
                                     OutboxStore, make_notify_user_tool)


class FakeAdapter:
    channel = "dingtalk"

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, **payload):
        self.sent.append(payload)
        return {"message_id": "pm-1", "request_id": "req-1"}


def make_store() -> OutboxStore:
    store = OutboxStore(":memory:")
    store.upsert_binding(platform_user="u1", channel="dingtalk",
                         address_type="direct", address_id="st100")
    return store


def make_app(store: OutboxStore, registry: PendingProposalRegistry):
    rt = NotificationRuntime(store=store, adapters={"dingtalk": FakeAdapter()})
    return SimpleNamespace(state=SimpleNamespace(
        notification_runtime=rt, proposal_registry=registry, ws_clients=set()))


def teardown_function(_):
    clear_outbound_bypass()


def test_notify_user_exempt_from_outbound_gate():
    """根因回归:notify_user 必须被外发闸结构性豁免(治理由其自具策略闸承担)。

    名字判定本身命中(notify ∈ SEND_SELF_SUFFICIENT),但它是平台自治理工具 ——
    豁免在判定面里(outbound_gate._PLATFORM_EXEMPT),不依赖工具是否已挂载。"""
    assert is_outbound_send_tool("notify_user") is False
    # 内置工具不变量(docs/96 刀0):豁免后 BUILTIN_TOOL_NAMES 全量零命中。
    from karvyloop.atoms.tool_catalog import BUILTIN_TOOL_NAMES
    hits = [n for n in BUILTIN_TOOL_NAMES if is_outbound_send_tool(n)]
    assert hits == []


def test_proposal_factory_shape_and_stable_id():
    card = proposal_for_notification_approval(
        notification_id="nid-1", recipient_ref="user:u1", content="日报已生成",
        title="日报", actor_id="王强", channel="dingtalk", ts=123.0)
    assert card.kind == KIND_NOTIFICATION_APPROVAL
    assert card.proposal_id == f"{KIND_NOTIFICATION_APPROVAL}-0-nid-1"
    assert card.payload["notification_id"] == "nid-1"
    assert "日报已生成" in card.basis
    again = proposal_for_notification_approval(
        notification_id="nid-1", recipient_ref="user:u1", content="日报已生成", ts=9.0)
    assert again.proposal_id == card.proposal_id   # 幂等键


def test_raise_cards_then_accept_dispatches_to_dingtalk():
    from karvyloop.console.proposals import raise_notification_cards

    async def scenario():
        store = make_store()
        app = make_app(store, PendingProposalRegistry())
        receipt = store.submit(NotificationCommand(
            recipient="user:u1", content="需要审批的通知", actor_id="a"))
        assert receipt.status == "pending_approval"
        # 升卡:第一次 1 张,再跑幂等 0 张
        assert await raise_notification_cards(app) == 1
        assert await raise_notification_cards(app) == 0
        pid = f"{KIND_NOTIFICATION_APPROVAL}-0-{receipt.notification_id}"
        assert app.state.proposal_registry.get(pid) is not None

        # ACCEPT(生产 handler 表语义)→ queued → dispatch 真投递
        handlers = {KIND_NOTIFICATION_APPROVAL:
                    __import__("karvyloop.console.proposal_handlers", fromlist=["x"]).
                    _notification_approval_handler(app)}
        res = app.state.proposal_registry.decide(pid, "ACCEPT", handlers=handlers)
        assert res.ok is True, res.detail
        assert res.detail == "已批准,进入投递队列(稍后送达钉钉)"
        rt = app.state.notification_runtime
        assert await rt.dispatcher.dispatch_once() == 1
        assert rt.adapters["dingtalk"].sent[0]["address"] == "st100"
        assert store.list_notifications(5)[0]["status"] == "delivered"

    asyncio.run(scenario())


def test_raise_cards_then_reject_marks_outbox_rejected():
    from karvyloop.console.proposals import raise_notification_cards
    from karvyloop.console.proposal_handlers import (
        _notification_approval_handler, _notification_approval_reject_handler)

    async def scenario():
        store = make_store()
        app = make_app(store, PendingProposalRegistry())
        receipt = store.submit(NotificationCommand(
            recipient="user:u1", content="驳回我", actor_id="a"))
        assert await raise_notification_cards(app) == 1
        pid = f"{KIND_NOTIFICATION_APPROVAL}-0-{receipt.notification_id}"
        handlers = {
            KIND_NOTIFICATION_APPROVAL: _notification_approval_handler(app),
            f"{KIND_NOTIFICATION_APPROVAL}:reject": _notification_approval_reject_handler(app),
        }
        res = app.state.proposal_registry.decide(pid, "REJECT", handlers=handlers)
        assert res.ok is True
        assert res.detail == "已驳回,该通知不会发送"
        row = store.list_notifications(5)[0]
        assert row["status"] == "rejected"
        # 驳回后通知离开 pending → 升卡函数不再出卡
        assert await raise_notification_cards(app) == 0

    asyncio.run(scenario())


def test_raise_cards_noop_unwired_and_empty():
    from karvyloop.console.proposals import raise_notification_cards

    async def scenario():
        unwired = SimpleNamespace(state=SimpleNamespace(notification_runtime=None))
        assert await raise_notification_cards(unwired) == 0
        store = make_store()
        app = make_app(store, PendingProposalRegistry())
        assert await raise_notification_cards(app) == 0   # 无待审批 → 0

    asyncio.run(scenario())
