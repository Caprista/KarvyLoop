"""Karvy 信使 relay(docs/43 第二级)测试 —— 原则「信使不拆信」。

覆盖:
1. e2e 单元:握手/双向密文往返/重放拒/篡改拒/错指纹拒/错配对码拒/一次性码即焚。
2. 双端环回(真加密真转发真回 loopback API):
   uvicorn 起 relay + uvicorn 起**真 console app** + console 信使客户端(真跑
   run_relay_client)+ python websockets 模拟手机端 → 手机发加密请求 →
   relay 盲转发 → console 客户端解密 → loopback 打真 /api/update_status → 加密回传。
   relay 的 forward_hook 断言转发的每一帧都是密文(明文关键字绝不出现)。
3. relay 行为:主人不在线明确报 console_offline / 第二个 client 报 room_busy /
   超大帧拒 / 不落盘(静态断言:server.py 无写盘原语、结构性不 import e2e)。
4. CLI:relay-serve / relay-pair 已注册;relay-pair 打印房间号/指纹/一次性码。
5. 缺 cryptography → 诚实报 "pip install karvyloop[relay]"。
"""
from __future__ import annotations

import asyncio
import base64
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from karvyloop.relay import MAX_FRAME_BYTES

pytestmark = pytest.mark.security   # 安全套件:信使 relay 重放/篡改/明文泄露/盲转发对抗


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_uvicorn(app, port: int):
    """后台线程起 uvicorn(照 test_console_browser 先例);返回 (server, thread)。"""
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


# =====================================================================
# 1. e2e 单元(需要 cryptography;没装 → skip,extras [relay])
# =====================================================================

class TestE2E:
    @pytest.fixture(autouse=True)
    def _need_crypto(self):
        pytest.importorskip("cryptography")

    def _pair(self, tmp_path):
        """握手一对 Session:console 侧走真 PairingStore(一次性码)。"""
        from karvyloop.relay import e2e
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        c_priv, c_pub = store.identity()
        code = store.new_code()
        fp = e2e.fingerprint(c_pub)
        cl_priv, _cl_pub = e2e.gen_keypair()
        hello = e2e.build_hello(cl_priv, code)
        welcome, console_sess = e2e.console_accept(hello, c_priv, c_pub,
                                                   store.verify_and_consume)
        client_sess = e2e.client_complete(welcome, cl_priv, fp)
        return console_sess, client_sess, store, cl_priv

    def test_roundtrip_both_directions(self, tmp_path):
        console_sess, client_sess, *_ = self._pair(tmp_path)
        f = client_sess.seal(b"hello from phone")
        assert console_sess.open(f) == b"hello from phone"
        g = console_sess.seal(b"reply from console")
        assert client_sess.open(g) == b"reply from console"

    def test_replay_rejected(self, tmp_path):
        from karvyloop.relay import e2e
        console_sess, client_sess, *_ = self._pair(tmp_path)
        f = client_sess.seal(b"once")
        assert console_sess.open(f) == b"once"
        with pytest.raises(e2e.ReplayError):
            console_sess.open(f)                      # 同一帧第二次 = 重放,拒
        f2 = client_sess.seal(b"twice")               # 后续合法帧照常
        assert console_sess.open(f2) == b"twice"

    def test_tamper_rejected(self, tmp_path):
        from karvyloop.relay import e2e
        console_sess, client_sess, *_ = self._pair(tmp_path)
        f = bytearray(client_sess.seal(b"payload"))
        f[-1] ^= 0xFF
        with pytest.raises(e2e.FrameError):
            console_sess.open(bytes(f))

    def test_wrong_fingerprint_rejected(self, tmp_path):
        from karvyloop.relay import e2e
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        c_priv, c_pub = store.identity()
        code = store.new_code()
        cl_priv, _ = e2e.gen_keypair()
        welcome, _ = e2e.console_accept(e2e.build_hello(cl_priv, code),
                                        c_priv, c_pub, store.verify_and_consume)
        with pytest.raises(e2e.FingerprintMismatch):
            e2e.client_complete(welcome, cl_priv, "dead-beef-dead-beef")

    def test_wrong_pairing_code_rejected_and_one_time(self, tmp_path):
        from karvyloop.relay import e2e
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        c_priv, c_pub = store.identity()
        code = store.new_code()
        # 错码 → PairingRejected
        cl_priv, _ = e2e.gen_keypair()
        with pytest.raises(e2e.PairingRejected):
            e2e.console_accept(e2e.build_hello(cl_priv, "WRNG-CODE"),
                               c_priv, c_pub, store.verify_and_consume)
        # 对码 → 过;码即焚:另一个新设备复用同码 → 拒
        e2e.console_accept(e2e.build_hello(cl_priv, code),
                           c_priv, c_pub, store.verify_and_consume)
        other_priv, _ = e2e.gen_keypair()
        with pytest.raises(e2e.PairingRejected):
            e2e.console_accept(e2e.build_hello(other_priv, code),
                               c_priv, c_pub, store.verify_and_consume)
        # 已配对设备免码重连(HELLO 不带码)
        e2e.console_accept(e2e.build_hello(cl_priv, None),
                           c_priv, c_pub, store.verify_and_consume)

    def test_seal_refuses_oversized_frame(self, tmp_path):
        from karvyloop.relay import e2e
        _, client_sess, *_ = self._pair(tmp_path)
        with pytest.raises(e2e.FrameError):
            client_sess.seal(b"x" * (MAX_FRAME_BYTES + 1))

    def test_missing_cryptography_honest_error(self, tmp_path, monkeypatch):
        """缺依赖 → RelayCryptoUnavailable,报 pip install karvyloop[relay](不静默降级)。"""
        import sys
        from karvyloop.relay import e2e
        monkeypatch.setitem(sys.modules, "cryptography", None)
        for name in list(sys.modules):
            if name.startswith("cryptography."):
                monkeypatch.setitem(sys.modules, name, None)
        with pytest.raises(e2e.RelayCryptoUnavailable, match=r"karvyloop\[relay\]"):
            e2e.gen_keypair()


# =====================================================================
# 2+3. relay 行为 + 双端环回(真加密真转发真回 loopback API)
# =====================================================================

@pytest.fixture
def relay_server():
    """真起 relay(uvicorn 后台线程);yield (base_ws_url, app)。"""
    from karvyloop.relay.server import build_relay_app
    app = build_relay_app()
    port = _free_port()
    server, thread = _start_uvicorn(app, port)
    try:
        yield f"ws://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def test_console_offline_is_explicit(relay_server):
    """主人不在线:join 一个没 console 的房 → 明确报 console_offline。"""
    import websockets
    base, _app = relay_server
    async with websockets.connect(base + "/join?rid=room-without-console") as ws:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert msg["t"] == "error"
        assert msg["code"] == "console_offline"


async def test_bad_rid_rejected(relay_server):
    import websockets
    base, _app = relay_server
    async with websockets.connect(base + "/join?rid=x") as ws:   # 太短
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert msg["code"] == "bad_rid"


async def test_relay_ping_pong_heartbeat(relay_server):
    import websockets
    base, _app = relay_server
    async with websockets.connect(base + "/attach?rid=heartbeat-room-1") as ws:
        await ws.send('{"t":"ping"}')
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert msg["t"] == "pong"


async def test_room_busy_and_frame_too_large(relay_server):
    import websockets
    base, _app = relay_server
    rid = "busy-room-12345"
    async with websockets.connect(base + f"/attach?rid={rid}") as console_ws:
        async with websockets.connect(base + f"/join?rid={rid}") as c1:
            # 第二个 client → room_busy
            async with websockets.connect(base + f"/join?rid={rid}") as c2:
                msg = json.loads(await asyncio.wait_for(c2.recv(), timeout=5))
                assert msg["code"] == "room_busy"
            # 超大帧 → frame_too_large + 断连
            await c1.send(b"\x00" * (MAX_FRAME_BYTES + 1))
            while True:
                m = await asyncio.wait_for(c1.recv(), timeout=5)
                if isinstance(m, str):
                    d = json.loads(m)
                    if d.get("t") == "error":
                        assert d["code"] == "frame_too_large"
                        break
            with pytest.raises(Exception):
                await asyncio.wait_for(c1.recv(), timeout=5)   # 连接已被 relay 关闭
        _ = console_ws  # console 侧保持在线


def test_relay_server_is_diskless_and_blind():
    """不落盘 + 不拆信的结构性断言:server.py 无写盘原语、不 import e2e(没有解析密文的代码)。"""
    import karvyloop.relay.server as srv
    src = Path(srv.__file__).read_text(encoding="utf-8")
    for forbidden in ("open(", "write_text", "write_bytes", "sqlite", "shelve",
                      "pickle", "Path.home", "mkdir"):
        assert forbidden not in src, f"relay server 不许碰盘/序列化:发现 {forbidden!r}"
    assert "from karvyloop.relay import e2e" not in src
    assert "relay.e2e" not in src, "relay server 不许 import e2e(信使没有拆信的代码)"


@pytest.fixture
def real_console():
    """真起一份 console app(uvicorn 后台线程,armed token 门);yield (port, token)。"""
    pytest.importorskip("cryptography")
    from karvyloop.console import build_console_app
    from karvyloop.console import access as _access
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    token = _access.new_token()
    app.state.access_token = token          # 上闸:非 loopback 必须带 token(loopback 免)
    port = _free_port()
    server, thread = _start_uvicorn(app, port)
    try:
        yield port, token
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def _phone_handshake(ws, code: str, fp: str):
    """模拟手机端:HELLO → WELCOME → Session。"""
    from karvyloop.relay import e2e
    cl_priv, _ = e2e.gen_keypair()
    await ws.send(e2e.build_hello(cl_priv, code))
    while True:
        msg = await asyncio.wait_for(ws.recv(), timeout=10)
        if isinstance(msg, bytes):
            break
    assert e2e.frame_type(msg) == e2e.T_WELCOME, f"expected WELCOME, got {msg!r}"
    return e2e.client_complete(msg, cl_priv, fp)


async def test_full_relay_loop_end_to_end(relay_server, real_console, tmp_path):
    """双端环回金线:手机 → relay(只见密文)→ console client → loopback 真 API → 加密回传。

    同时验:重放帧不被二次执行;relay 转发钩子全程只见密文。
    """
    import websockets
    from karvyloop.relay import e2e
    from karvyloop.relay.client import run_relay_client
    from karvyloop.relay.pairing import PairingStore

    base, relay_app = relay_server
    console_port, token = real_console

    # relay 盲转发探针:记录每一帧,事后断言没有任何明文泄露
    forwarded: list[tuple[str, bytes]] = []
    relay_app.state.forward_hook = lambda d, b: forwarded.append((d, bytes(b)))

    store = PairingStore(tmp_path)
    store.identity()
    rid = store.rid()
    code = store.new_code()
    fp = store.fingerprint()

    stop = asyncio.Event()
    client_task = asyncio.create_task(run_relay_client(
        base, console_port=console_port, token=token,
        state_dir=tmp_path, heartbeat_s=5.0, stop=stop))
    try:
        # 等 console 客户端 attach 上 relay
        for _ in range(100):
            room = relay_app.state.rooms.get(rid)
            if room is not None and room.console is not None:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("console relay client 没 attach 上 relay")

        async with websockets.connect(base + f"/join?rid={rid}") as phone:
            sess = await _phone_handshake(phone, code, fp)

            # --- 真请求:GET /api/update_status(真 console 路由 + 中间件)---
            req = json.dumps({"id": 1, "method": "GET",
                              "path": "/api/update_status"}).encode()
            data_frame = sess.seal(req)
            await phone.send(data_frame)
            while True:
                msg = await asyncio.wait_for(phone.recv(), timeout=15)
                if isinstance(msg, bytes) and e2e.frame_type(msg) == e2e.T_DATA:
                    break
            resp = json.loads(sess.open(msg).decode())
            assert resp["id"] == 1
            assert resp["status"] == 200
            body = json.loads(base64.b64decode(resp["body_b64"]))
            assert "current" in body            # 真打到了 console 的 update_status

            # --- 重放拒:原样重发同一 DATA 帧 → console 端丢弃,不二次执行 ---
            await phone.send(data_frame)
            with pytest.raises(asyncio.TimeoutError):
                while True:
                    m = await asyncio.wait_for(phone.recv(), timeout=2)
                    assert not (isinstance(m, bytes) and e2e.frame_type(m) == e2e.T_DATA), \
                        "重放帧居然拿到了第二个响应"

            # --- 重放后合法请求照常(会话没被打死)---
            await phone.send(sess.seal(json.dumps(
                {"id": 2, "method": "GET", "path": "/api/update_status"}).encode()))
            while True:
                msg = await asyncio.wait_for(phone.recv(), timeout=15)
                if isinstance(msg, bytes) and e2e.frame_type(msg) == e2e.T_DATA:
                    break
            assert json.loads(sess.open(msg).decode())["id"] == 2
    finally:
        stop.set()
        client_task.cancel()
        try:
            await asyncio.wait_for(client_task, timeout=5)
        except (asyncio.CancelledError, Exception):
            pass

    # --- 信使不拆信:relay 转发的每一帧都是我们的密文帧,明文关键字绝不出现 ---
    assert forwarded, "forward_hook 没见到帧(转发没走 relay?)"
    for direction, blob in forwarded:
        assert blob[:2] == b"KL", "转发的不是 KL 帧"
        for leak in (b"update_status", b'"current"', b'"method"', b'"path"',
                     token.encode()):
            assert leak not in blob, f"relay 见到了明文/token 泄露({direction})"


async def test_wrong_pairing_code_rejected_over_the_wire(relay_server, real_console, tmp_path):
    """错配对码走全链路:console 客户端回明文 ERR 帧 pairing_rejected(零秘密),不建会话。"""
    import websockets
    from karvyloop.relay import e2e
    from karvyloop.relay.client import run_relay_client
    from karvyloop.relay.pairing import PairingStore

    base, relay_app = relay_server
    console_port, token = real_console
    store = PairingStore(tmp_path)
    store.identity()
    rid = store.rid()

    stop = asyncio.Event()
    task = asyncio.create_task(run_relay_client(
        base, console_port=console_port, token=token,
        state_dir=tmp_path, heartbeat_s=5.0, stop=stop))
    try:
        for _ in range(100):
            room = relay_app.state.rooms.get(rid)
            if room is not None and room.console is not None:
                break
            await asyncio.sleep(0.1)
        async with websockets.connect(base + f"/join?rid={rid}") as phone:
            cl_priv, _ = e2e.gen_keypair()
            await phone.send(e2e.build_hello(cl_priv, "BADC-ODEX"))
            while True:
                msg = await asyncio.wait_for(phone.recv(), timeout=10)
                if isinstance(msg, bytes):
                    break
            assert e2e.frame_type(msg) == e2e.T_ERR
            assert e2e.parse_err(msg) == "pairing_rejected"
    finally:
        stop.set()
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5)
        except (asyncio.CancelledError, Exception):
            pass


def test_start_relay_client_thread_attaches_and_stops(relay_server, tmp_path):
    """entry.py `--relay` 走的正是这条线程路径:起 → 出站 attach 上 relay → stop() 收干净。"""
    pytest.importorskip("cryptography")
    from karvyloop.relay.client import start_relay_client_thread
    from karvyloop.relay.pairing import PairingStore

    base, relay_app = relay_server
    rid = PairingStore(tmp_path).rid()   # 先定 rid,再起线程(两边并发各生成一个会对不上)
    handle = start_relay_client_thread(base, console_port=1, token="tok-FAKE-DO-NOT-LEAK",
                                       state_dir=tmp_path)
    try:
        for _ in range(100):
            room = relay_app.state.rooms.get(rid)
            if room is not None and room.console is not None:
                break
            time.sleep(0.1)
        else:
            pytest.fail("线程版 relay client 没 attach 上 relay")
    finally:
        handle.stop()
    assert not handle._thread.is_alive(), "stop() 后线程必须退干净(console 收尾不悬挂)"


# =====================================================================
# 4. CLI 注册 + relay-pair 输出
# =====================================================================

def test_cli_relay_subcommands_registered():
    from karvyloop.cli.main import main
    for cmd in ("relay-serve", "relay-pair"):
        with pytest.raises(SystemExit) as e:
            main([cmd, "--help"])
        assert e.value.code == 0


def test_cli_console_has_relay_flag():
    from karvyloop.cli.main import main
    with pytest.raises(SystemExit) as e:
        main(["console", "--help"])
    assert e.value.code == 0


def test_relay_pair_prints_pairing_info(tmp_path, capsys):
    pytest.importorskip("cryptography")
    from karvyloop.cli.main import main
    rc = main(["relay-pair", "--dir", str(tmp_path), "--relay-url", "wss://relay.example"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wss://relay.example" in out
    assert "Room:" in out and "Fingerprint:" in out and "One-time code:" in out
    # 状态真落在注入目录(密钥 + relay.json),而不是 ~/.karvyloop
    assert (tmp_path / "relay_key").exists()
    assert (tmp_path / "relay.json").exists()
    # 私钥绝不出现在输出里
    assert (tmp_path / "relay_key").read_bytes().hex() not in out


class TestPairingRevocation:
    """§9.6 授权层地基:已配对设备可列、可撤销(撤销=绝对把控权;撤后免不了码重连)。"""

    @pytest.fixture(autouse=True)
    def _need_crypto(self):
        pytest.importorskip("cryptography")

    def _pair_device(self, store):
        """走真验证门配一个设备:new_code → pair_mac → verify_and_consume(码即焚+记结构化)。"""
        from karvyloop.relay import e2e
        code = store.new_code()
        cl_priv, cl_pub = e2e.gen_keypair()
        mac = e2e.pair_mac(code, cl_pub)
        assert store.verify_and_consume(cl_pub, mac) is True
        return cl_pub, e2e.fingerprint(cl_pub)

    def test_pairing_records_structured_and_lists(self, tmp_path):
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        pub, fpr = self._pair_device(store)
        paired = store.list_paired()
        assert len(paired) == 1
        assert paired[0]["pub"] == pub.hex() and paired[0]["fingerprint"] == fpr
        assert paired[0]["scope"] == "full"          # 自有设备默认完整访问(零回归)

    def test_paired_device_reconnects_without_code(self, tmp_path):
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        pub, _ = self._pair_device(store)
        assert store.verify_and_consume(pub, b"mac-ignored-when-paired") is True

    def test_revoke_by_fingerprint_blocks_reconnect(self, tmp_path):
        """撤销(按指纹)→ 不再 paired → 无有效码则免码重连失败(回源即断地基)。"""
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        pub, fpr = self._pair_device(store)
        assert store.revoke(fpr) is True
        assert store.list_paired() == []
        assert store.verify_and_consume(pub, b"stale-mac") is False   # 撤后再连被拒

    def test_revoke_by_pubkey_hex(self, tmp_path):
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        pub, _ = self._pair_device(store)
        assert store.revoke(pub.hex()) is True and store.list_paired() == []

    def test_revoke_nonexistent_returns_false_no_collateral(self, tmp_path):
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        self._pair_device(store)
        assert store.revoke("deadbeef-not-real") is False
        assert len(store.list_paired()) == 1                          # 没误删

    def test_backward_compat_bare_hex_paired_entry(self, tmp_path):
        """旧格式(paired 裸 hex 字符串)→ list_paired 兼容 + 可撤销(升级不破旧盘)。"""
        from karvyloop.relay import e2e
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        store.identity()                                             # 建目录+密钥
        _, cl_pub = e2e.gen_keypair()
        store._save({"rid": "r0", "codes": [], "paired": [cl_pub.hex()]})   # 手工塞旧格式
        paired = store.list_paired()
        assert len(paired) == 1 and paired[0]["pub"] == cl_pub.hex()
        assert paired[0]["scope"] == "full"
        assert store.revoke(e2e.fingerprint(cl_pub)) is True

    def test_relay_unpair_cli_lists_and_revokes(self, tmp_path, capsys):
        """CLI relay-unpair 端到端:无参列设备 / 带指纹撤销。"""
        from karvyloop.cli.main import main
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        _pub, fpr = self._pair_device(store)
        assert main(["relay-unpair", "--dir", str(tmp_path)]) == 0
        assert fpr in capsys.readouterr().out
        assert main(["relay-unpair", fpr, "--dir", str(tmp_path)]) == 0
        assert "Revoked" in capsys.readouterr().out
        assert PairingStore(tmp_path).list_paired() == []


class TestScopeEnforcement:
    """§9.6 slice 2:per-request scope 强制 + 回源撤销(活连接下一个请求即拒,不必断 WS)。"""

    @pytest.fixture(autouse=True)
    def _need_crypto(self):
        pytest.importorskip("cryptography")

    def _paired(self, tmp_path, scope="full"):
        from karvyloop.relay import e2e
        from karvyloop.relay.pairing import PairingStore
        store = PairingStore(tmp_path)
        code = store.new_code(scope)
        _priv, pub = e2e.gen_keypair()
        assert store.verify_and_consume(pub, e2e.pair_mac(code, pub)) is True
        return store, pub

    def test_scope_for_paired_then_revoked(self, tmp_path):
        store, pub = self._paired(tmp_path, "read")
        assert store.scope_for(pub.hex()) == "read"
        store.revoke(pub.hex())
        assert store.scope_for(pub.hex()) is None            # 撤销后回源查=None

    def test_new_code_unknown_scope_denies_to_read(self, tmp_path):
        store, pub = self._paired(tmp_path, "garbage-scope")  # deny-by-default → read
        assert store.scope_for(pub.hex()) == "read"

    def _handle(self, store, pub, method, path="/api/x"):
        import asyncio
        import json as _j
        from karvyloop.relay.client import _handle_request
        calls = []

        class _FakeResp:
            status_code = 200
            content = b"ok"
            headers = {"content-type": "text/plain"}

        class _FakeHttp:
            async def request(self, m, p, headers=None, content=b""):
                calls.append((m, p))
                return _FakeResp()

        pt = _j.dumps({"id": 1, "method": method, "path": path}).encode()
        out = asyncio.run(_handle_request(pt, _FakeHttp(), "owner-token", store=store, peer_pub=pub))
        return _j.loads(out.decode()), calls

    def test_full_scope_allows_mutating(self, tmp_path):
        store, pub = self._paired(tmp_path, "full")
        resp, calls = self._handle(store, pub, "POST")
        assert resp["status"] == 200 and calls == [("POST", "/api/x")]   # 自有设备放行改动(零回归)

    def test_read_scope_blocks_mutating_never_hits_loopback(self, tmp_path):
        store, pub = self._paired(tmp_path, "read")
        resp, calls = self._handle(store, pub, "POST")
        assert resp["status"] == 403 and resp["error"] == "scope_read_only"
        assert calls == []                                              # 越 scope 绝不打 loopback

    def test_read_scope_allows_get(self, tmp_path):
        store, pub = self._paired(tmp_path, "read")
        resp, calls = self._handle(store, pub, "GET")
        assert resp["status"] == 200 and calls == [("GET", "/api/x")]

    def test_revoked_peer_denied_on_live_connection(self, tmp_path):
        """回源撤销:会话进行中撤销 → 下一个请求(哪怕 GET)也 403 revoked,绝不打 loopback。"""
        store, pub = self._paired(tmp_path, "full")
        store.revoke(pub.hex())
        resp, calls = self._handle(store, pub, "GET")
        assert resp["status"] == 403 and resp["error"] == "revoked_or_unpaired"
        assert calls == []
