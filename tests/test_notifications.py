import asyncio

from karvyloop.notifications import (Dispatcher, NotificationCommand, OutboxStore,
                                     PermanentDeliveryError, make_notify_user_tool)


def store_with_binding():
    store = OutboxStore(":memory:")
    store.upsert_binding(platform_user="u1", channel="auto", address_id="addr1")
    return store


def command(**kwargs):
    values = dict(recipient="user:u1", content="hi", actor_id="agent", task_id="t", trace_ref="r")
    values.update(kwargs)
    return NotificationCommand(**values)


def test_default_approval_and_agent_cannot_choose_policy():
    store = store_with_binding()
    receipt = store.submit(command())
    assert receipt.status == "pending_approval"
    tool = make_notify_user_tool(store=store, actor_id="agent")
    result = asyncio.run(tool.call({"recipient": "user:u1", "content": "x", "policy": "auto"}, None, None))
    assert result["status"] == "rejected"


def test_preauthorized_current_user_queues_and_unbound_rejects():
    store = OutboxStore(":memory:")
    store.upsert_binding(platform_user="u1", channel="auto", address_id="addr1")
    assert store.submit(command(recipient="current_user", runtime_context={"current_user_id": "u1", "preauthorized": True})).status == "queued"
    assert store.submit(command(recipient="user:missing")).status == "rejected"


def test_dedupe_and_approval_creates_delivery():
    store = store_with_binding()
    first = store.submit(command(dedupe_key="same"))
    duplicate = store.submit(command(dedupe_key="same"))
    assert duplicate.status == "duplicate"
    assert store.transition_approval(first.notification_id, True, "ap-1").status == "queued"
    assert store.lease_deliveries("worker")


def test_dispatch_success_provider_id_and_permanent_failure():
    store = store_with_binding()
    receipt = store.submit(command(recipient="current_user", runtime_context={"current_user_id": "u1", "preauthorized": True}))

    class Adapter:
        async def send(self, **payload):
            return {"message_id": "pm", "request_id": "pr"}

    delivery = store.lease_deliveries("worker")[0]
    store.mark_retry(delivery["id"], "worker", "later", next_attempt_at=0)
    assert asyncio.run(Dispatcher(store, {"auto": Adapter()}).dispatch_once()) == 1
    row = store.read_delivery(delivery["id"])
    assert row["provider_message_id"] == "pm"

    receipt = store.submit(command(recipient="current_user", dedupe_key="bad", runtime_context={"current_user_id": "u1", "preauthorized": True}))
    failed = store.lease_deliveries("worker2")[0]
    store.mark_permanent_failure(failed["id"], "worker2", "bad")
    assert store.read_delivery(failed["id"])["status"] == "failed"


def test_tool_schema_and_runtime_identity():
    store = OutboxStore(":memory:")
    tool = make_notify_user_tool(store=store, actor_id="actor", task_id="task", trace_ref="trace")
    assert tool.required_mode.value == "full"
    assert tool.input_schema["additionalProperties"] is False
    assert "policy" not in tool.input_schema["properties"]


def test_binding_consent_auto_direct_dispatch():
    """绑定级自动放行:consent=auto(2) 的接收人,任何 actor(无预授权)直投免审。"""
    store = OutboxStore(":memory:")
    store.upsert_binding(platform_user="u1", channel="dingtalk",
                         address_type="direct", address_id="st100", consent=2)
    assert store.submit(command(recipient="user:u1", channel="dingtalk")).status == "queued"
    # 普通 consent=1 绑定仍需审批
    store.upsert_binding(platform_user="u2", channel="dingtalk",
                         address_type="direct", address_id="st200", consent=1)
    assert store.submit(command(recipient="user:u2", channel="dingtalk")).status == "pending_approval"
    # 群绑定 consent=2 同样直投 —— 开关语义:单聊+群聊一致(见 runtime 播种/reconcile)
    store.upsert_binding(platform_user="conversation:c1", channel="dingtalk",
                         address_type="group", address_id="c1", conversation_address="c1",
                         consent=2)
    assert store.submit(command(recipient="conversation:c1", channel="dingtalk")).status == "queued"


def test_bare_staffid_recipient_resolves():
    """裸 staffId 精确匹配绑定(agent 实拍乱填兜底);匹配不到仍拒。"""
    store = OutboxStore(":memory:")
    store.upsert_binding(platform_user="u1", channel="dingtalk",
                         address_type="direct", address_id="st100", consent=2)
    assert store.submit(command(recipient="st100", channel="dingtalk")).status == "queued"
    assert store.submit(command(recipient="王强", channel="dingtalk")).status == "rejected"
