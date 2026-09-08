from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import yaml
from fastapi.testclient import TestClient

from karvyloop.channels.dingtalk_runtime import PendingSenderCache, reconcile_dingtalk_channels
from karvyloop.config_channels import DingTalkChannelConfig
from karvyloop.console import build_console_app
from karvyloop.console.task_events import (
    WS_TYPE_CHANNEL_MESSAGE, WS_TYPE_CHANNEL_SENDER_PENDING,
    broadcast_channel_message, broadcast_channel_sender_pending,
)
from karvyloop.karvy.observer import WorkbenchObserver


def _client(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("llm:\n  marker: keep\n", encoding="utf-8")
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.config_path = str(config)
    app.state.main_event_loop = None
    return TestClient(app), config, app


def test_dingtalk_crud_preserves_yaml_and_never_returns_secret(tmp_path, monkeypatch):
    client, config, _app = _client(tmp_path)
    monkeypatch.setattr("karvyloop.channels.dingtalk_channel.DingTalkChannel.start",
                        lambda self, loop: True)
    body = {"name": "资料机器人", "client_id": "ding-a", "client_secret": "DO-NOT-LEAK",
            "role": "资料管家", "allow_senders": ["u1", "u1"]}
    created = client.post("/api/channels/dingtalk", json=body)
    assert created.status_code == 201
    assert "DO-NOT-LEAK" not in created.text
    ident = created.json()["instance"]["id"]
    assert ident
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["llm"]["marker"] == "keep"
    assert data["channels"]["dingtalk"][0]["client_secret"] == "DO-NOT-LEAK"
    assert data["channels"]["dingtalk"][0]["allow_senders"] == ["u1"]
    if os.name != "nt":
        assert config.stat().st_mode & 0o777 == 0o600

    updated = client.put(f"/api/channels/dingtalk/{ident}", json={
        **body, "client_secret": None, "role": "新角色"})
    assert updated.status_code == 200
    assert "DO-NOT-LEAK" not in updated.text
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["channels"]["dingtalk"][0]["client_secret"] == "DO-NOT-LEAK"
    assert client.delete(f"/api/channels/dingtalk/{ident}").json()["ok"] is True


def test_get_migrates_legacy_instance_id_without_exposing_secret(tmp_path):
    client, config, _app = _client(tmp_path)
    config.write_text("channels:\n  dingtalk:\n    enabled: true\n    client_id: a\n    client_secret: hidden\n    role: r\n", encoding="utf-8")
    response = client.get("/api/channels/dingtalk")
    assert response.status_code == 200 and "hidden" not in response.text
    assert response.json()["instances"][0]["id"]
    assert isinstance(yaml.safe_load(config.read_text(encoding="utf-8"))["channels"]["dingtalk"], list)


def test_dingtalk_required_fields_reject_whitespace_after_trim(tmp_path):
    client, _config, _app = _client(tmp_path)
    base = {"client_id": "a", "client_secret": "secret", "role": "r"}
    for field in ("client_id", "client_secret", "role"):
        response = client.post("/api/channels/dingtalk", json={**base, field: "   "})
        assert response.status_code == 422
        assert response.json()["detail"] == f"{field} 必填"


def test_allow_sender_trims_decoded_path_value(tmp_path, monkeypatch):
    client, config, _app = _client(tmp_path)
    config.write_text("channels:\n  dingtalk:\n    - id: i1\n      enabled: true\n      client_id: a\n      client_secret: hidden\n      role: r\n", encoding="utf-8")
    monkeypatch.setattr("karvyloop.console.routes_channels._reconcile",
                        lambda app, path: {})

    response = client.post("/api/channels/dingtalk/i1/senders/%20u1%20/allow")

    assert response.status_code == 200
    item = yaml.safe_load(config.read_text(encoding="utf-8"))["channels"]["dingtalk"][0]
    assert item["allow_senders"] == ["u1"]
    assert client.post("/api/channels/dingtalk/i1/senders/%20%20/allow").status_code == 422


def test_pending_sender_cache_ttl_capacity_and_attempt_count():
    cache = PendingSenderCache(ttl_s=10, max_items=2)
    item = cache.put("i1", "u1", sender_nick="甲")
    assert item["attempt_count"] == 1
    repeated = cache.put("i1", "u1", sender_nick="甲")
    assert repeated["attempt_count"] == 2
    assert repeated["first_seen_at"] == item["first_seen_at"]
    cache.put("i1", "u2")
    cache.put("i1", "u3")
    assert {entry["sender"] for entry in cache.list()} == {"u2", "u3"}
    assert cache.list(now=repeated["last_seen_at"] + 11) == []

    class Ws:
        def __init__(self): self.sent = []
        async def send_json(self, message): self.sent.append(message)
    ws = Ws()
    app = SimpleNamespace(state=SimpleNamespace(ws_clients={ws}))
    asyncio.run(broadcast_channel_message(app, {"text": "x"}))
    asyncio.run(broadcast_channel_sender_pending(app, {"sender": "u1"}))
    assert [x["type"] for x in ws.sent] == [WS_TYPE_CHANNEL_MESSAGE, WS_TYPE_CHANNEL_SENDER_PENDING]


def test_runtime_reconcile_updates_non_credentials_without_reconnect(monkeypatch):
    calls = []

    class FakeChannel:
        def __init__(self, app, cfg): self._cfg = cfg
        def start(self, loop): calls.append(("start", self._cfg.instance_id)); return True
        def stop(self): calls.append(("stop", self._cfg.instance_id))

    monkeypatch.setattr("karvyloop.channels.dingtalk_channel.DingTalkChannel", FakeChannel)
    app = SimpleNamespace(state=SimpleNamespace(dingtalk_channels=[], dingtalk_channel=None,
                                                 main_event_loop=object()))
    original = DingTalkChannelConfig(instance_id="a", client_id="ca", client_secret="s", role="r")
    reconcile_dingtalk_channels(app, [original])
    channel = app.state.dingtalk_channels[0]
    changed = DingTalkChannelConfig(instance_id="a", client_id="ca", client_secret="s",
                                    role="new-role", allow_senders=("u1",), name="new-name")

    result = reconcile_dingtalk_channels(app, [changed])

    assert result == {"active": 1, "started": 0, "stopped": 0, "kept": 1,
                      "failed": 0, "hot_reload": True}
    assert app.state.dingtalk_channels == [channel]
    assert channel._cfg is changed
    assert calls == [("start", "a")]


def test_runtime_reconcile_starts_new_credentials_before_stopping_old(monkeypatch):
    calls = []

    class FakeChannel:
        def __init__(self, app, cfg): self._cfg = cfg
        def start(self, loop): calls.append(("start", self._cfg.client_secret)); return True
        def stop(self): calls.append(("stop", self._cfg.client_secret))

    monkeypatch.setattr("karvyloop.channels.dingtalk_channel.DingTalkChannel", FakeChannel)
    old_cfg = DingTalkChannelConfig(instance_id="a", client_id="ca", client_secret="old", role="r")
    old_channel = FakeChannel(None, old_cfg)
    app = SimpleNamespace(state=SimpleNamespace(dingtalk_channels=[old_channel],
                                                 dingtalk_channel=old_channel,
                                                 main_event_loop=object()))
    changed = DingTalkChannelConfig(instance_id="a", client_id="ca", client_secret="new", role="r")

    result = reconcile_dingtalk_channels(app, [changed])

    assert result == {"active": 1, "started": 1, "stopped": 1, "kept": 0,
                      "failed": 0, "hot_reload": True}
    assert app.state.dingtalk_channels[0] is not old_channel
    assert calls == [("start", "new"), ("stop", "old")]


def test_runtime_reconcile_keeps_old_when_new_credentials_fail(monkeypatch):
    calls = []

    class FakeChannel:
        def __init__(self, app, cfg): self._cfg = cfg
        def start(self, loop): calls.append(("start", self._cfg.client_secret)); return False
        def stop(self): calls.append(("stop", self._cfg.client_secret))

    monkeypatch.setattr("karvyloop.channels.dingtalk_channel.DingTalkChannel", FakeChannel)
    old_cfg = DingTalkChannelConfig(instance_id="a", client_id="ca", client_secret="old", role="r")
    old_channel = FakeChannel(None, old_cfg)
    app = SimpleNamespace(state=SimpleNamespace(dingtalk_channels=[old_channel],
                                                 dingtalk_channel=old_channel,
                                                 main_event_loop=object()))
    changed = DingTalkChannelConfig(instance_id="a", client_id="ca", client_secret="new", role="new-role")

    result = reconcile_dingtalk_channels(app, [changed])

    assert result == {"active": 1, "started": 0, "stopped": 0, "kept": 1,
                      "failed": 1, "hot_reload": True}
    assert app.state.dingtalk_channels == [old_channel]
    assert app.state.dingtalk_channel is old_channel
    assert old_channel._cfg is old_cfg
    assert calls == [("start", "new")]
