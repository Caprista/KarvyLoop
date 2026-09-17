"""NotificationRuntime 端到端:提交 → 审批 → 后台投递;入站自动绑定;REST 审批面。"""
import asyncio
from types import SimpleNamespace

from karvyloop.notifications import (NotificationCommand, NotificationRuntime,
                                     OutboxStore, build_notification_runtime,
                                     notification_drive_kwargs,
                                     register_dingtalk_binding)


class FakeAdapter:
    channel = "dingtalk"

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, **payload):
        self.sent.append(payload)
        return {"message_id": "pm-1", "request_id": "req-1"}


def make_store(**binding_kwargs) -> OutboxStore:
    store = OutboxStore(":memory:")
    values = dict(platform_user="u1", channel="dingtalk",
                  address_type="direct", address_id="st100")
    values.update(binding_kwargs)
    store.upsert_binding(**values)
    return store


def test_runtime_start_stop_delivers_queued_notification():
    async def scenario():
        store = make_store()
        adapter = FakeAdapter()
        rt = NotificationRuntime(store=store, adapters={"dingtalk": adapter},
                                 dispatch_interval_s=0.05)
        receipt = store.submit(NotificationCommand(
            recipient="current_user", content="日报已生成", actor_id="a",
            runtime_context={"current_user_id": "u1", "preauthorized": True}))
        assert receipt.status == "queued"
        rt.start()
        for _ in range(100):
            if adapter.sent:
                break
            await asyncio.sleep(0.05)
        assert len(adapter.sent) == 1
        assert adapter.sent[0]["address"] == "st100"
        assert adapter.sent[0]["content"] == "日报已生成"
        assert store.list_notifications(10)[0]["status"] == "delivered"
        await rt.stop()

    asyncio.run(scenario())


def test_pending_requires_approval_then_dispatch_after_approve():
    store = make_store()
    adapter = FakeAdapter()
    rt = NotificationRuntime(store=store, adapters={"dingtalk": adapter})
    receipt = store.submit(NotificationCommand(
        recipient="user:u1", content="需要审批", actor_id="a"))
    assert receipt.status == "pending_approval"
    assert asyncio.run(rt.dispatcher.dispatch_once()) == 0
    assert adapter.sent == []
    assert store.transition_approval(receipt.notification_id, True).status == "queued"
    assert asyncio.run(rt.dispatcher.dispatch_once()) == 1
    assert adapter.sent[0]["address"] == "st100"
    assert store.list_notifications(10)[0]["status"] == "delivered"


def test_reject_leaves_no_delivery():
    store = make_store()
    rt = NotificationRuntime(store=store, adapters={"dingtalk": FakeAdapter()})
    receipt = store.submit(NotificationCommand(recipient="user:u1", content="x", actor_id="a"))
    assert store.transition_approval(receipt.notification_id, False).status == "rejected"
    assert store.lease_deliveries("worker") == []


def test_auto_binding_direct_and_group_and_dispatch_address():
    store = OutboxStore(":memory:")
    cfg = SimpleNamespace(client_id="app1")
    register_dingtalk_binding(store, cfg, sender="st100", chat="cid9", chat_type="group")
    direct = store.resolve_binding(
        "user:st100", NotificationCommand(recipient="user:st100", content="x", channel="dingtalk"))
    assert direct["address_type"] == "direct"
    group = store.resolve_binding(
        "conversation:cid9",
        NotificationCommand(recipient="conversation:cid9", content="x", channel="dingtalk"))
    assert group["address_type"] == "group"
    # 群投递地址取 conversation_address,不是 staffId
    receipt = store.submit(NotificationCommand(
        recipient="conversation:cid9", content="群通知", actor_id="a"))
    assert receipt.status == "pending_approval"
    assert store.transition_approval(receipt.notification_id, True).status == "queued"
    delivery = store.lease_deliveries("w")[0]
    assert store.read_delivery(delivery["id"])["address"] == "cid9"


def test_owner_mapping_resolves_current_user_without_context():
    store = OutboxStore(":memory:")
    register_dingtalk_binding(store, SimpleNamespace(client_id="app"),
                              sender="st100", chat="", chat_type="direct")
    store.owner_user_id = "st100"
    receipt = store.submit(NotificationCommand(
        recipient="current_user", content="提醒", actor_id="a",
        runtime_context={"preauthorized": True}))
    assert receipt.status == "queued"


def test_build_runtime_from_dingtalk_config(monkeypatch):
    from karvyloop.config_channels import DingTalkChannelConfig
    import karvyloop.config_channels as cc
    cfg = DingTalkChannelConfig(client_id="app", client_secret="sec",
                                role="role_a", allow_senders=("st100",))
    monkeypatch.setattr(cc, "load_dingtalk_channel_configs", lambda path=None: [cfg])
    store = OutboxStore(":memory:")
    rt = build_notification_runtime(config_path="ignored", store=store)
    assert rt is not None
    assert "dingtalk" in rt.adapters
    assert store.owner_user_id == "st100"
    rt.close()


def test_build_runtime_seeds_auto_consent_binding(monkeypatch):
    """notify_auto_approve=True → 启动即播 consent=auto 的 direct 绑定(无须等入站消息)。"""
    from karvyloop.config_channels import DingTalkChannelConfig
    import karvyloop.config_channels as cc
    cfg = DingTalkChannelConfig(client_id="app", client_secret="sec",
                                role="role_a", allow_senders=("st100",),
                                notify_auto_approve=True)
    monkeypatch.setattr(cc, "load_dingtalk_channel_configs", lambda path=None: [cfg])
    store = OutboxStore(":memory:")
    rt = build_notification_runtime(config_path="ignored", store=store)
    assert rt is not None
    # 无预授权的业务 actor 给 auto 绑定接收人发 → 直接入队(免审批)
    receipt = store.submit(NotificationCommand(
        recipient="user:st100", content="业务角色直投", actor_id="王强"))
    assert receipt.status == "queued"
    rt.close()


def test_reconcile_group_binding_follows_flag(monkeypatch):
    """开关语义扩展(实拍:群聊也要直发):
    - 已存在的群绑定 consent=1 → 开启后 reconcile 升 2 → 群通知免审直投;
    - 关闭开关 → reconcile 回 1 → 恢复审批;
    - 入站登记的群绑定也跟随开关。"""
    from karvyloop.config_channels import DingTalkChannelConfig
    import karvyloop.config_channels as cc
    from karvyloop.notifications import register_dingtalk_binding

    def cfg(auto: bool):
        return DingTalkChannelConfig(client_id="app", client_secret="sec",
                                     role="role_a", allow_senders=("st100",),
                                     notify_auto_approve=auto)

    store = OutboxStore(":memory:")
    # 旧世界:开关未开时登记的群绑定 consent=1
    register_dingtalk_binding(store, cfg(False), sender="st100", chat="cid9", chat_type="group")
    group_row = store._conn.execute(
        "SELECT consent FROM notification_recipient_bindings WHERE address_type='group'").fetchone()
    assert group_row["consent"] == 1

    monkeypatch.setattr(cc, "load_dingtalk_channel_configs", lambda path=None: [cfg(True)])
    rt = build_notification_runtime(config_path="ignored", store=store)
    group_row = store._conn.execute(
        "SELECT consent FROM notification_recipient_bindings WHERE address_type='group'").fetchone()
    assert group_row["consent"] == 2
    receipt = store.submit(NotificationCommand(
        recipient="conversation:cid9", content="群通知直投", actor_id="王强"))
    assert receipt.status == "queued"

    # 关开关 → 回档审批
    monkeypatch.setattr(cc, "load_dingtalk_channel_configs", lambda path=None: [cfg(False)])
    rt2 = build_notification_runtime(config_path="ignored", store=store)
    group_row = store._conn.execute(
        "SELECT consent FROM notification_recipient_bindings WHERE address_type='group'").fetchone()
    assert group_row["consent"] == 1
    receipt = store.submit(NotificationCommand(
        recipient="conversation:cid9", content="群通知需审批", actor_id="王强", dedupe_key="k2"))
    assert receipt.status == "pending_approval"
    rt.close()
    rt2.close()


def test_build_runtime_none_without_config(monkeypatch):
    import karvyloop.config_channels as cc
    monkeypatch.setattr(cc, "load_dingtalk_channel_configs", lambda path=None: [])
    assert build_notification_runtime(store=OutboxStore(":memory:")) is None


def test_drive_kwargs_wired_and_unwired():
    unwired = SimpleNamespace(state=SimpleNamespace(notification_runtime=None))
    assert notification_drive_kwargs(unwired) == {}
    store = OutboxStore(":memory:")
    rt = NotificationRuntime(store=store, adapters={})
    wired = SimpleNamespace(state=SimpleNamespace(notification_runtime=rt))
    kwargs = notification_drive_kwargs(wired, task_id="t1")
    assert kwargs["notification_store"] is store
    assert kwargs["notification_task_id"] == "t1"
    assert kwargs["notification_trace_ref"] == ""


def test_console_notification_endpoints():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from karvyloop.console.routes_notifications import router

    store = make_store()
    rt = NotificationRuntime(store=store, adapters={"dingtalk": FakeAdapter()})
    app = FastAPI()
    app.include_router(router)
    app.state.notification_runtime = rt
    client = TestClient(app)

    assert client.get("/api/notifications/pending").json()["notifications"] == []
    receipt = store.submit(NotificationCommand(
        recipient="user:u1", content="需要审批的通知", actor_id="a"))
    assert receipt.status == "pending_approval"
    pend = client.get("/api/notifications/pending").json()["notifications"]
    assert len(pend) == 1 and pend[0]["id"] == receipt.notification_id
    resp = client.post(f"/api/notifications/{receipt.notification_id}/approve").json()
    assert resp["ok"] is True and resp["status"] == "queued"
    other = store.submit(NotificationCommand(
        recipient="user:u1", content="拒绝我", actor_id="a", dedupe_key="k2"))
    resp = client.post(f"/api/notifications/{other.notification_id}/reject").json()
    assert resp["ok"] is True
    statuses = {n["status"] for n in client.get("/api/notifications").json()["notifications"]}
    assert statuses == {"queued", "rejected"}


def test_console_notification_endpoints_unwired():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from karvyloop.console.routes_notifications import router

    app = FastAPI()
    app.include_router(router)
    app.state.notification_runtime = None
    client = TestClient(app)
    assert (client.get("/api/notifications/pending").json()["reason"]
            == "notification_runtime_not_wired")
    assert client.post("/api/notifications/none/approve").json()["ok"] is False
