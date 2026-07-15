"""test_mesh_sync_client — mesh 同步客户端逻辑:跟对端 console 一个来回 → 两设备收敛。

relay 传输本身已在 test_relay::test_remote_client_end_to_end 验过;这里用 fake session 把请求路由到
对端 console 的 TestClient,专测**同步客户端**(拉 frontier→算 delta→推→合并对端回的 delta)收敛。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.mesh.store import MeshLogStore  # noqa: E402
from karvyloop.mesh.sync_client import mesh_sync_with_peer  # noqa: E402
from karvyloop.mesh.synclog import MeshLog  # noqa: E402


def test_mesh_sync_client_converges_with_peer(tmp_path, monkeypatch):
    # 对端设备 A 的 console,mesh_log 里有一条它学到的记忆
    a_app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    a_app.state.mesh_state_dir = tmp_path / "a"
    a_log = MeshLog("dev-a")
    a_log.append("belief-created", {"m": "A 学到的一条"}, wall=1000)
    a_app.state.mesh_log = a_log
    a_app.state.mesh_log_store = None
    a_client = TestClient(a_app)

    # fake relay 会话:把 sess.request 路由到 A 的 TestClient(替掉真 relay,那层已另测)
    class _FakeSession:
        async def request(self, method, path, *, headers=None, body=b"", timeout=30.0):
            if method.upper() == "GET":
                r = a_client.get(path)
            else:
                r = a_client.request(method, path, content=body, headers=headers or {})
            return {"status": r.status_code, "headers": dict(r.headers), "body": r.content, "error": ""}

        async def close(self):
            pass

    async def _fake_open(*a, **k):
        return (None, _FakeSession())

    monkeypatch.setattr("karvyloop.relay.remote.open_remote_session", _fake_open)

    # 本设备 B:盘上预置一条它结晶的技能
    b_dir = tmp_path / "b"
    b_log = MeshLog("dev-b")
    b_log.append("skill-crystallized", {"s": "B 做的表"}, wall=1000)
    MeshLogStore(b_dir).append(b_log.entries())

    # B 跟 A 同步一次
    out = asyncio.run(mesh_sync_with_peer("ws://relay", "room-a", fingerprint="fp",
                                          my_device_id="dev-b", state_dir=b_dir))
    assert out["pulled"] == 1, f"B 没拉到 A 的记忆: {out}"       # 拉了 A 的 1 条
    assert out["pushed"] == 1, f"A 没收到 B 的技能: {out}"       # 推了 B 的 1 条

    # 两设备收敛:A 有了 B 的技能,B(重载盘)有了 A 的记忆
    a_ids = {e.event_id for e in a_app.state.mesh_log.entries()}
    b_ids = {e.event_id for e in MeshLogStore(b_dir).open_log("dev-b").entries()}
    assert a_ids == b_ids and len(a_ids) == 2, "mesh 同步后两设备未收敛"


def test_mesh_sync_exchanges_adverts_both_rosters(tmp_path, monkeypatch):
    """docs/74 花名册双录:一次 mesh-sync 双方花名册都齐 —— B 入 A 册(POST 体带 B 的 advert,
    A 端点 register_peer),A 入 B 册(frontier 响应带 A 的 advert,sync_client register_peer)。"""
    pytest.importorskip("cryptography")
    from karvyloop.mesh.registry import DeviceRegistry
    from karvyloop.mesh.synclog import MeshLog as _MeshLog
    from karvyloop.relay.pairing import PairingStore

    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    a_store, b_store = PairingStore(a_dir), PairingStore(b_dir)
    a_store.identity(); b_store.identity()               # 双方都有 relay 身份(FAKE 测试目录)
    a_fp, b_fp = a_store.fingerprint(), b_store.fingerprint()

    a_app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    a_app.state.mesh_state_dir = a_dir
    a_app.state.relay_url = "wss://a.relay"              # A 的 advert 报它自己的 relay
    a_app.state.mesh_log = _MeshLog(a_fp)                # 生产语义:log device_id = relay 指纹
    a_app.state.mesh_log_store = None
    a_client = TestClient(a_app)

    class _FakeSession:                                   # 请求路由到 A 的 TestClient(relay 层已另测)
        async def request(self, method, path, *, headers=None, body=b"", timeout=30.0):
            if method.upper() == "GET":
                r = a_client.get(path)
            else:
                r = a_client.request(method, path, content=body, headers=headers or {})
            return {"status": r.status_code, "headers": dict(r.headers), "body": r.content, "error": ""}

        async def close(self):
            pass

    async def _fake_open(*a, **k):
        return (None, _FakeSession())

    monkeypatch.setattr("karvyloop.relay.remote.open_remote_session", _fake_open)

    out = asyncio.run(mesh_sync_with_peer("ws://dial.relay", "room-a", fingerprint=a_fp,
                                          my_device_id=b_fp, state_dir=b_dir))
    assert out == {"pulled": 0, "pushed": 0}             # 两边日志都空,同步本身零事件
    # B 的花名册有 A(含怎么连回 A:A 自己的 relay + A 的 mesh 房)
    a_rec = DeviceRegistry(b_dir).get(a_fp)
    assert a_rec is not None and a_rec.is_self is False
    assert a_rec.relay_url == "wss://a.relay"
    assert a_rec.room == PairingStore(a_dir).mesh_rid() and a_rec.room.startswith("m")
    assert a_rec.last_seen > 0                           # register_peer/mark_seen → presence 新鲜
    # A 的花名册有 B(B 的 POST advert;my_relay_url 缺省 → 报拨出用的 relay)
    b_rec = DeviceRegistry(a_dir).get(b_fp)
    assert b_rec is not None and b_rec.is_self is False
    assert b_rec.room == PairingStore(b_dir).mesh_rid()
    assert b_rec.relay_url == "ws://dial.relay"
