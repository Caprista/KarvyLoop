"""test_mesh_sync_client — mesh 同步客户端逻辑:跟对端 console 一个来回 → 两设备收敛。

relay 传输本身已在 test_relay::test_remote_client_end_to_end 验过;这里用 fake session 把请求路由到
对端 console 的 TestClient,专测**同步客户端**(拉 frontier→算 delta→推→合并对端回的 delta)收敛。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

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
