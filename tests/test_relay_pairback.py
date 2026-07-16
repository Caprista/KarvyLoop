"""test_relay_pairback — 同主人**一步互配**(docs/74 对等语义,2026-07-15 两机 E2E 实测缝):
host 拿 VM 的一次性码配进 VM 授权表后,VM 反向拨 host 不该再要第二枚码。

信任不变量(三重门,缺一不回配):
① 我方**主动**用一次性码发起配对(code 非空;已配对复连 code=None 不写);
② 指纹 pin 验证通过(open_remote_session 里 client_complete,不过就抛);
③ full scope 已证 —— read 分享码在对端咽喉就把 POST /api/mesh/sync 403 掉
  (scope_read_only,见 test_relay.TestScopeEnforcement),整个同步失败,走不到回配。
**绝不**因收到 advert / 被动被配就信任别人(advert 只进花名册,不进授权表)。
坏数据宁空勿毒:peer_pub 缺/坏 → 不写;已在表 → 不重复写、不改既有 scope(不静默提权)。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import socket
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.security   # 授权表 = 安全面:回配写错人 = 把家门钥匙发给陌生 console


# =====================================================================
# 1. PairingStore.trust_peer 单元(回配的写入门)
# =====================================================================

class TestTrustPeer:
    @pytest.fixture(autouse=True)
    def _need_crypto(self):
        pytest.importorskip("cryptography")

    def test_trust_peer_writes_full_record_and_unlocks_reconnect(self, tmp_path):
        """回配写结构化记录(scope full)→ 对端免码重连(verify_and_consume 直接过)。"""
        from karvyloop.relay import e2e
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        _, peer_pub = e2e.gen_keypair()
        assert store.trust_peer(peer_pub, label="Hardy 的 VM") is True
        paired = store.list_paired()
        assert len(paired) == 1
        assert paired[0]["pub"] == peer_pub.hex() and paired[0]["scope"] == "full"
        assert paired[0]["label"] == "Hardy 的 VM"
        # 「免第二枚码」的真语义:它反向拨我,HELLO 不带码也过验证门
        assert store.verify_and_consume(peer_pub, b"no-code-mac-ignored") is True

    def test_trust_peer_idempotent_no_duplicate(self, tmp_path):
        from karvyloop.relay import e2e
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        _, peer_pub = e2e.gen_keypair()
        assert store.trust_peer(peer_pub) is True
        assert store.trust_peer(peer_pub) is False          # 重复回配 = 不写
        assert len(store.list_paired()) == 1

    def test_trust_peer_never_upgrades_existing_scope(self, tmp_path):
        """已按 read 码配过的设备,回配**不许**静默升到 full(不做提权通道)。"""
        from karvyloop.relay import e2e
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        code = store.new_code("read")
        _, pub = e2e.gen_keypair()
        assert store.verify_and_consume(pub, e2e.pair_mac(code, pub)) is True
        assert store.trust_peer(pub) is False
        assert store.scope_for(pub.hex()) == "read"          # 原 scope 纹丝不动

    def test_trust_peer_rejects_garbage_pub(self, tmp_path):
        """宁空勿毒:非 32B / 非 bytes 一律拒写,授权表不长垃圾。"""
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        for bad in (b"", b"short", b"\x00" * 31, b"\x00" * 33, "deadbeef" * 8, None, 42):
            assert store.trust_peer(bad) is False
        assert store.list_paired() == []

    def test_trust_peer_skips_own_pub(self, tmp_path):
        """自己的公钥不写(同目录自拨的测试形态,别把自己列成'已配对设备')。"""
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        _, own_pub = store.identity()
        assert store.trust_peer(own_pub) is False
        assert store.list_paired() == []


# =====================================================================
# 2. mesh_sync_with_peer 回配落点(fake session,relay 层另测)
# =====================================================================

def _peer_console_app(peer_dir):
    """对端 console(TestClient 版,样式照 test_mesh_sync_client)。"""
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.mesh.synclog import MeshLog
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.mesh_state_dir = peer_dir
    app.state.mesh_log = MeshLog("dev-peer")
    app.state.mesh_log_store = None
    return app


def _fake_open(a_client, peer_pub: bytes, *, post_status: int = 200):
    """fake open_remote_session:请求路由到对端 TestClient;session 带指纹验过的 peer_pub。"""
    class _FakeSession:
        def __init__(self):
            self.peer_pub = peer_pub

        async def request(self, method, path, *, headers=None, body=b"", timeout=30.0):
            if method.upper() != "GET" and post_status != 200:
                return {"status": post_status, "headers": {}, "body": b"",
                        "error": "scope_read_only"}
            if method.upper() == "GET":
                r = a_client.get(path)
            else:
                r = a_client.request(method, path, content=body, headers=headers or {})
            return {"status": r.status_code, "headers": dict(r.headers),
                    "body": r.content, "error": ""}

        async def close(self):
            pass

    async def _open(*a, **k):
        return (None, _FakeSession())
    return _open


class TestMeshSyncPairBack:
    @pytest.fixture(autouse=True)
    def _need_crypto(self):
        pytest.importorskip("cryptography")

    def _run(self, tmp_path, monkeypatch, *, code, post_status=200, peer_pub=None):
        from fastapi.testclient import TestClient

        from karvyloop.mesh.sync_client import mesh_sync_with_peer
        from karvyloop.relay import e2e
        from karvyloop.relay.pairing import PairingStore
        peer_dir, my_dir = tmp_path / "peer", tmp_path / "me"
        if peer_pub is None:
            _, peer_pub = e2e.gen_keypair()
        a_client = TestClient(_peer_console_app(peer_dir))
        monkeypatch.setattr("karvyloop.relay.remote.open_remote_session",
                            _fake_open(a_client, peer_pub, post_status=post_status))
        out = asyncio.run(mesh_sync_with_peer(
            "ws://relay", "room-peer", fingerprint="fp-pinned",
            my_device_id="dev-me", code=code, state_dir=my_dir))
        return out, PairingStore(my_dir).list_paired(), peer_pub

    def test_first_pair_with_code_writes_peer_into_my_table(self, tmp_path, monkeypatch):
        """首配(code 非空)成功 → 我方 paired 长出对端 console 身份(scope full)。"""
        out, paired, peer_pub = self._run(tmp_path, monkeypatch, code="AAAA-BBBB")
        assert out == {"pulled": 0, "pushed": 0}
        assert len(paired) == 1
        assert paired[0]["pub"] == peer_pub.hex() and paired[0]["scope"] == "full"

    def test_reconnect_without_code_never_writes(self, tmp_path, monkeypatch):
        """已配对复连(code=None)→ 不重复写(mesh_tick 每 60s 拨一轮,不许轮轮写盘)。"""
        out, paired, _ = self._run(tmp_path, monkeypatch, code=None)
        assert out == {"pulled": 0, "pushed": 0}
        assert paired == []

    def test_read_scope_sync_fails_and_never_pairs_back(self, tmp_path, monkeypatch):
        """read 分享码:对端咽喉 403 掉 POST(scope_read_only)→ 同步整个失败,**绝不回配**
        (三重门之③;403 门本身见 test_relay.TestScopeEnforcement)。"""
        from karvyloop.relay.pairing import PairingStore
        with pytest.raises(RuntimeError, match="sync failed"):
            self._run(tmp_path, monkeypatch, code="READ-CODE", post_status=403)
        assert PairingStore(tmp_path / "me").list_paired() == []

    def test_bad_peer_pub_no_poison_sync_still_succeeds(self, tmp_path, monkeypatch):
        """peer_pub 坏(长度不对)→ 宁空勿毒不写;同步结果照常返回(回配是增益不是命脉)。"""
        out, paired, _ = self._run(tmp_path, monkeypatch, code="AAAA-BBBB",
                                   peer_pub=b"\x01" * 8)
        assert out == {"pulled": 0, "pushed": 0}
        assert paired == []


# =====================================================================
# 3. 金线(真 relay + 两台真 console):一枚码,双向免码 —— 复现并闭合 E2E 缝
# =====================================================================

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_uvicorn(app, port: int):
    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if getattr(server, "started", False):
            break
        time.sleep(0.1)
    assert server.started, "uvicorn 没起来"
    return server, thread


async def _wait_attached(relay_app, rid: str):
    for _ in range(100):
        room = relay_app.state.rooms.get(rid)
        if room is not None and room.console is not None:
            return
        await asyncio.sleep(0.1)
    pytest.fail(f"console 没 attach 上 relay(rid={rid})")


async def test_one_code_pairs_both_directions_over_relay(tmp_path):
    """两机 E2E 缝的真路径复现+闭合:host 用 VM 的一枚码 mesh 首配 →
    ① VM 授权表长出 host 的**设备身份**(relay_key 指纹那把,不是 remote_key)
    ② host 授权表回配长出 VM 的 console 身份(scope full)
    ③ VM 反向拨 host **不带码**,同步直接成功 —— 第二枚码不存在了。"""
    pytest.importorskip("cryptography")
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.mesh.sync_client import mesh_sync_with_peer
    from karvyloop.relay.client import run_relay_client
    from karvyloop.relay.pairing import PairingStore
    from karvyloop.relay.server import build_relay_app

    relay_app = build_relay_app()
    relay_port = _free_port()
    relay_srv, relay_thr = _start_uvicorn(relay_app, relay_port)
    base = f"ws://127.0.0.1:{relay_port}"

    host_dir, vm_dir = tmp_path / "host", tmp_path / "vm"
    servers, stops, tasks = [(relay_srv, relay_thr)], [], []
    try:
        sides = {}
        for name, sd in (("host", host_dir), ("vm", vm_dir)):
            store = PairingStore(sd)
            _, pub = store.identity()
            app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
            app.state.mesh_state_dir = sd
            app.state.relay_url = base
            port = _free_port()
            servers.append(_start_uvicorn(app, port))
            stop = asyncio.Event()
            stops.append(stop)
            tasks.append(asyncio.create_task(run_relay_client(
                base, console_port=port, token="", state_dir=sd,
                heartbeat_s=5.0, stop=stop, rid=store.mesh_rid())))
            sides[name] = {"dir": sd, "store": store, "pub": pub,
                           "fp": store.fingerprint(), "room": store.mesh_rid()}
        for s in sides.values():
            await _wait_attached(relay_app, s["room"])

        host, vm = sides["host"], sides["vm"]
        # ── 首配:host 拿 VM 的一次性码拨 VM 的 mesh 房(唯一的一枚码)──
        code = vm["store"].new_code()                       # full(自有设备默认)
        out = await mesh_sync_with_peer(base, vm["room"], fingerprint=vm["fp"],
                                        my_device_id=host["fp"], code=code,
                                        state_dir=host["dir"], my_relay_url=base)
        assert out == {"pulled": 0, "pushed": 0}
        # ① VM 授权表记的是 host 的**设备身份**(relay_key),对得上花名册 device_id
        vm_paired = PairingStore(vm["dir"]).list_paired()
        assert [p["pub"] for p in vm_paired] == [host["pub"].hex()]
        assert vm_paired[0]["scope"] == "full"
        assert not (host["dir"] / "remote_key").exists(), "mesh 拨出不该生成 remote_key"
        # ② host 授权表回配长出 VM 的 console 身份(scope full)
        host_paired = PairingStore(host["dir"]).list_paired()
        assert [p["pub"] for p in host_paired] == [vm["pub"].hex()]
        assert host_paired[0]["scope"] == "full"

        # ── 缝本尊:VM 反向拨 host,**不带码** ── 直接成功
        out2 = await mesh_sync_with_peer(base, host["room"], fingerprint=host["fp"],
                                         my_device_id=vm["fp"], code=None,
                                         state_dir=vm["dir"], my_relay_url=base)
        assert out2 == {"pulled": 0, "pushed": 0}
        # 复连不重复写:两边授权表各自仍只有一条
        assert len(PairingStore(host["dir"]).list_paired()) == 1
        assert len(PairingStore(vm["dir"]).list_paired()) == 1
    finally:
        for stop in stops:
            stop.set()
        for t in tasks:
            try:
                await asyncio.wait_for(t, timeout=5)
            except BaseException:
                t.cancel()
        for srv, thr in servers:
            srv.should_exit = True
            thr.join(timeout=5)


async def test_default_remote_path_unchanged_no_pairback(tmp_path):
    """`karvyloop remote` 原路径零回归:默认身份仍是 remote_key,且**不**自动回配
    (回配只在 mesh 首配整来回成功后;接入端配家里 console 不改接入端自己的授权表)。"""
    pytest.importorskip("cryptography")
    import json as _j

    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.relay.client import run_relay_client
    from karvyloop.relay.pairing import PairingStore
    from karvyloop.relay.remote import open_remote_session
    from karvyloop.relay.server import build_relay_app

    relay_app = build_relay_app()
    relay_port = _free_port()
    relay_srv, relay_thr = _start_uvicorn(relay_app, relay_port)
    base = f"ws://127.0.0.1:{relay_port}"

    home_dir, device_dir = tmp_path / "home", tmp_path / "device"
    store = PairingStore(home_dir)
    store.identity()
    rid, code, fp = store.rid(), store.new_code(), store.fingerprint()

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    console_port = _free_port()
    con_srv, con_thr = _start_uvicorn(app, console_port)
    stop = asyncio.Event()
    task = asyncio.create_task(run_relay_client(
        base, console_port=console_port, token="", state_dir=home_dir,
        heartbeat_s=5.0, stop=stop))
    try:
        await _wait_attached(relay_app, rid)
        ws, sess = await open_remote_session(base, rid, fingerprint=fp,
                                             code=code, state_dir=device_dir)
        try:
            resp = await sess.request("GET", "/api/update_status")
            assert resp["status"] == 200 and "current" in _j.loads(resp["body"].decode())
        finally:
            await ws.close()
        assert (device_dir / "remote_key").exists()          # 默认身份不变
        assert PairingStore(device_dir).list_paired() == []  # 接入端不自动回配
        # 家里配对的是接入端 remote_key(原语义):不是 relay_key(接入端也没有)
        assert not (device_dir / "relay_key").exists()
        assert len(PairingStore(home_dir).list_paired()) == 1
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except BaseException:
            task.cancel()
        con_srv.should_exit = True
        con_thr.join(timeout=5)
        relay_srv.should_exit = True
        relay_thr.join(timeout=5)


def test_sync_client_never_trusts_from_advert():
    """结构性断言:回配调用点只吃**指纹验过**的 session.peer_pub,绝不吃 advert 里的字段
    (advert 是数据不是指挥者 —— 只进花名册,不进授权表)。"""
    src = (ROOT / "karvyloop" / "mesh" / "sync_client.py").read_text(encoding="utf-8")
    assert 'getattr(sess, "peer_pub"' in src, "回配必须来自握手验证过的 peer_pub"
    # advert 只该流向 register_peer / label(展示),绝不该出现在 trust_peer 的身份位
    flat = src.replace(" ", "")
    assert "trust_peer(peer_pub" in flat
    assert "trust_peer(adv" not in flat
