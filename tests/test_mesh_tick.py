"""test_mesh_tick — mesh ticker(docs/74 探活地基):持续同步 + 探活闭环。

语义锁:①单机用户(花名册无可拨对端)零动作零流量;②成功 → mark_seen(presence 新鲜);
③失败 debug 级吞掉、单台炸不阻其它台 —— 连不上=它下线,last_seen 自然变陈,**这就是探活**;
④tick 间隔 < ONLINE_WINDOW_S(一次瞬断不足以判离线,SWIM suspect 心智);
⑤console lifespan 只在挂了 relay 时才起 ticker(mesh 是增益不是地基)。
测试身份一律 FAKE。
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import karvyloop.console.mesh_tick as mt  # noqa: E402
from karvyloop.mesh.registry import ONLINE_WINDOW_S, DeviceRecord, DeviceRegistry  # noqa: E402


def _app(sd, relay="wss://mine.relay"):
    """mesh_tick 只碰 app.state.{mesh_state_dir, relay_url} → 轻量桩即可。"""
    return SimpleNamespace(state=SimpleNamespace(mesh_state_dir=sd, relay_url=relay))


def _peer(device_id="peer-fp-FAKE-1", room="m" + "a" * 21, relay="wss://peer.relay"):
    return DeviceRecord(device_id=device_id, room=room, relay_url=relay)


def test_tick_interval_inside_online_window():
    """60s tick < 90s 在线窗 ≈ 1.5 tick:一次瞬断(单 tick 失败)不足以把在线对端判离线。"""
    assert mt.MESH_TICK_S < ONLINE_WINDOW_S


def test_no_peers_zero_action(tmp_path, monkeypatch):
    """单机用户:花名册空 / 只有本机 → 零流量(绝不拨号;任务板本地对账照跑,tasks 键如实带)。"""
    async def _boom(*a, **k):
        raise AssertionError("无对端时不该有任何同步调用")
    monkeypatch.setattr(mt, "mesh_sync_with_peer", _boom)
    out = asyncio.run(mt.mesh_tick(_app(tmp_path)))
    # compact = 低频维护 pass 的膨胀观测(启动首轮会跑一次),与同步/探活语义无关 → 滤掉
    assert {k: v for k, v in out.items() if k not in ("tasks", "compact")} \
        == {"peers": 0, "synced": 0, "failed": 0}
    assert "tasks" in out   # 任务板对账结果如实带回(单机=本地账,零流量)
    DeviceRegistry(tmp_path).register(DeviceRecord(
        device_id="self-FAKE", is_self=True, room="m" + "b" * 21, relay_url="wss://x"))
    assert asyncio.run(mt.mesh_tick(_app(tmp_path)))["peers"] == 0


def test_peer_missing_room_or_relay_is_skipped(tmp_path, monkeypatch):
    """room/relay_url 不齐的对端(还没互换过 advert)拨不了 → 不算对端,不瞎拨。"""
    reg = DeviceRegistry(tmp_path)
    reg.register(DeviceRecord(device_id="no-room-FAKE", relay_url="wss://r"))
    reg.register(DeviceRecord(device_id="no-relay-FAKE", room="m" + "c" * 21))
    async def _boom(*a, **k):
        raise AssertionError("缺 room/relay_url 的对端不该被拨")
    monkeypatch.setattr(mt, "mesh_sync_with_peer", _boom)
    assert asyncio.run(mt.mesh_tick(_app(tmp_path)))["peers"] == 0


def test_no_identity_honest_skip(tmp_path, monkeypatch):
    """本机没 relay 身份 → 拨不了,诚实返回 reason,不假装同步。"""
    DeviceRegistry(tmp_path).register(_peer())
    monkeypatch.setattr(mt, "device_fingerprint", lambda sd: {"device_id": ""})
    called = []
    async def _record(*a, **k):
        called.append(1)
    monkeypatch.setattr(mt, "mesh_sync_with_peer", _record)
    out = asyncio.run(mt.mesh_tick(_app(tmp_path)))
    assert out["reason"] == "no_identity" and called == []


def test_sync_success_marks_seen(tmp_path, monkeypatch):
    """成功同步 → mark_seen(last_seen 新鲜 = online);调用参数对齐花名册记录。"""
    DeviceRegistry(tmp_path).register(_peer())
    calls = []
    async def _fake_sync(relay_url, room, *, fingerprint, my_device_id, state_dir=None, **kw):
        calls.append((relay_url, room, fingerprint, my_device_id))
        return {"pulled": 0, "pushed": 0}
    monkeypatch.setattr(mt, "mesh_sync_with_peer", _fake_sync)
    monkeypatch.setattr(mt, "device_fingerprint", lambda sd: {"device_id": "self-fp-FAKE"})
    out = asyncio.run(mt.mesh_tick(_app(tmp_path)))
    assert {k: v for k, v in out.items() if k not in ("tasks", "compact")} \
        == {"peers": 1, "synced": 1, "failed": 0}
    # fingerprint=对端 device_id(花名册的 device_id 就是它的 relay 身份指纹,防中间人验它)
    assert calls == [("wss://peer.relay", "m" + "a" * 21, "peer-fp-FAKE-1", "self-fp-FAKE")]
    assert DeviceRegistry(tmp_path).get("peer-fp-FAKE-1").last_seen > 0


def test_one_peer_failing_does_not_block_others(tmp_path, monkeypatch, caplog):
    """单台炸(room_busy/超时)→ debug 吞掉继续拨别台;坏台 last_seen 保持陈旧=探活。"""
    reg = DeviceRegistry(tmp_path)
    reg.register(_peer("bad-peer-FAKE", room="m" + "d" * 21))
    reg.register(_peer("good-peer-FAKE", room="m" + "e" * 21))
    async def _fake_sync(relay_url, room, *, fingerprint, **kw):
        if fingerprint == "bad-peer-FAKE":
            raise TimeoutError("room_busy")
        return {"pulled": 0, "pushed": 0}
    monkeypatch.setattr(mt, "mesh_sync_with_peer", _fake_sync)
    monkeypatch.setattr(mt, "device_fingerprint", lambda sd: {"device_id": "self-fp-FAKE"})
    with caplog.at_level(logging.DEBUG, logger="karvyloop.console.mesh_tick"):
        out = asyncio.run(mt.mesh_tick(_app(tmp_path)))
    assert {k: v for k, v in out.items() if k not in ("tasks", "compact")} \
        == {"peers": 2, "synced": 1, "failed": 1}
    reg2 = DeviceRegistry(tmp_path)
    assert reg2.get("good-peer-FAKE").last_seen > 0
    assert reg2.get("bad-peer-FAKE").last_seen == 0.0    # 连不上 → last_seen 陈旧,这就是探活
    # 失败绝不 warning/error 刷屏(对端不在场是常态)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_console_lifespan_starts_ticker_only_with_relay(tmp_path):
    """接线语义:没挂 relay → 不起 ticker(拨号无从谈起);挂了 → supervisor 下起一条。"""
    from fastapi.testclient import TestClient

    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.mesh_state_dir = tmp_path
    with TestClient(app):
        assert getattr(app.state, "mesh_tick_task", None) is None

    app2 = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app2.state.mesh_state_dir = tmp_path
    app2.state.relay_url = "wss://relay.example"
    with TestClient(app2):
        task = getattr(app2.state, "mesh_tick_task", None)
        assert task is not None and not task.done()
