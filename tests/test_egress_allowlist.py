"""test_egress_allowlist —— 按域名 egress(出网)allowlist 的确定性安全地基(三平台).

外部子进程(external_runtime 成员化)是我们不控其执行的 opaque 执行体;唯一能约束其网络
行为的确定性抓手 = 只放行 allowlist 域名、其余拒。网络门从二元(net 全开/全关)升级成
域名级 allowlist。本套件锁三件事:

  1) 契约:CapabilityToken.net_allowlist 字段 + mounts.net_allowlist_of 归一化。
  2) 三平台粒度(诚实分层):
       - Linux:域名级**真强制**(用户态 allowlist 代理,真 socket 验放行/拒;
         焊不出免 root 用户态网络栈 → fail-closed 拒网)。
       - macOS:SBPL 表达不出域名粒度 → **fail-closed 拒网**(profile 出 deny network*)。
       - Windows:域名级需 admin WFP → **fail-closed 拒跑**(restricted / degraded 都 raise)。
     共同纪律:allowlist 非空但焊不出域名级强制 → **拒网别假放行**(安全是地基)。
  3) bridge.start 的**非破坏** egress_allowlist 透传(默认空零回归)。

全平台可跑(纯逻辑 + 真 socket 代理不依赖内核);平台强制的真机对抗见各平台沙箱测试。
"""
from __future__ import annotations

import asyncio
import socket
import struct
import threading
import http.server
import socketserver

import pytest

from karvyloop.schemas import Capability, CapabilityToken
from karvyloop.sandbox.mounts import has_net, net_allowlist_of
from karvyloop.platform.linux.egress_proxy import (
    AllowlistProxy,
    domain_egress_enforceable,
    host_allowed,
)

pytestmark = pytest.mark.security   # 安全套件:按域名 egress allowlist / fail-closed 降级


def _tok(*, net=False, allowlist=()):
    grants = []
    if net:
        grants.append(Capability(resource="net:host", ops=["connect"]))
    return CapabilityToken(task_id="t", grants=grants, expiry=9_999_999_999.0,
                           net_allowlist=tuple(allowlist))


# ============================================================================
# 1) 契约:CapabilityToken.net_allowlist + net_allowlist_of 归一化
# ============================================================================

def test_token_default_allowlist_is_empty_binary_preserved():
    """默认空 tuple = 保持二元:net 关拒网、net 开全放(零回归)。"""
    t = _tok(net=False)
    assert t.net_allowlist == ()
    assert net_allowlist_of(t) == ()
    assert has_net(t) is False
    assert has_net(_tok(net=True)) is True


def test_net_allowlist_of_normalizes():
    """去空白/空串、小写、去重保序。"""
    t = _tok(allowlist=(" Example.COM ", "", "api.foo.io", "example.com"))
    assert net_allowlist_of(t) == ("example.com", "api.foo.io")


def test_net_allowlist_of_failsafe_on_bad_type():
    """非可迭代/损坏的 net_allowlist → 空 tuple(fail-safe:当二元,不误当'已限制'假放行)。"""
    class _Bad:
        net_allowlist = 12345
    assert net_allowlist_of(_Bad()) == ()
    # 单串误传 → 当单元素(不 fail-open 成空)
    class _Solo:
        net_allowlist = "solo.com"
    assert net_allowlist_of(_Solo()) == ("solo.com",)


# ============================================================================
# 2a) host_allowed 纯逻辑(平台无关)
# ============================================================================

@pytest.mark.parametrize("host,allow,expected", [
    ("example.com", ("example.com",), True),          # 精确
    ("api.example.com", ("example.com",), True),       # 子域后缀
    ("example.com.", ("example.com",), True),          # 尾点归一
    ("notexample.com", ("example.com",), False),       # 非点边界 → 拒
    ("evil.com", ("example.com",), False),             # 无关 → 拒
    ("EXAMPLE.COM", ("example.com",), True),           # 大小写无关
    ("example.com", (), False),                        # 空 allowlist → 拒(代理语境)
    ("", ("example.com",), False),                     # 空 host → 拒
    ("a.b.example.com", ("example.com",), True),       # 多级子域
    ("example.com", ("foo.com", "example.com"), True), # 多项命中
])
def test_host_allowed(host, allow, expected):
    assert host_allowed(host, allow) is expected


# ============================================================================
# 2b) Linux:用户态 allowlist 代理真强制(真 socket,全平台可跑)
# ============================================================================

@pytest.fixture()
def origin_server():
    """本机 HTTP origin(充当上游),返回 ORIGIN-OK。"""
    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ORIGIN-OK")
        def log_message(self, *a):
            pass
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port
    httpd.shutdown()


def test_proxy_http_connect_denies_non_allowlisted():
    """非 allowlist 域名经 HTTP CONNECT → 代理 403,**不建**上游连接(确定性拒)。"""
    with AllowlistProxy(allowlist=("allowed.test",)) as proxy:
        s = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
        s.sendall(b"CONNECT evil.test:443 HTTP/1.1\r\nHost: evil.test\r\n\r\n")
        resp = s.recv(200); s.close()
        assert b"403" in resp, resp
        assert "evil.test" in proxy.denied


def test_proxy_http_connect_tunnels_allowlisted(origin_server):
    """allowlist 域名(localhost)经 HTTP CONNECT → 200 隧道建立 + 真取到 origin 内容。"""
    with AllowlistProxy(allowlist=("localhost",)) as proxy:
        s = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
        s.sendall(f"CONNECT localhost:{origin_server} HTTP/1.1\r\nHost: localhost\r\n\r\n"
                  .encode())
        r = s.recv(100)
        assert b"200" in r, r
        s.sendall(f"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode())
        body = b""
        while True:
            d = s.recv(4096)
            if not d:
                break
            body += d
        s.close()
        assert b"ORIGIN-OK" in body
        assert "localhost" in proxy.allowed


def _socks5(proxy_port, host, port, atyp=0x03):
    s = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
    s.sendall(b"\x05\x01\x00")
    assert s.recv(2) == b"\x05\x00"
    if atyp == 0x03:
        hb = host.encode()
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + struct.pack("!H", port))
    elif atyp == 0x01:
        s.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(host) + struct.pack("!H", port))
    return s, s.recv(10)


def test_proxy_socks5_allows_and_denies(origin_server):
    """SOCKS5:allowlist 域名 → 成功(0x00)+ 隧道;非 allowlist → 拒(0x02)。"""
    with AllowlistProxy(allowlist=("localhost",)) as proxy:
        s, rep = _socks5(proxy.port, "localhost", origin_server)
        assert rep[1] == 0x00, rep
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        body = b""
        while True:
            d = s.recv(4096)
            if not d:
                break
            body += d
        s.close()
        assert b"ORIGIN-OK" in body

        s2, rep2 = _socks5(proxy.port, "evil.test", 443)
        s2.close()
        assert rep2[1] == 0x02, rep2       # connection not allowed


def test_proxy_socks5_ip_literal_denied(origin_server):
    """SOCKS5 IP 字面量(无域名)→ 拒(防用 IP 绕过域名 allowlist / DNS rebind)。"""
    with AllowlistProxy(allowlist=("localhost",)) as proxy:
        s, rep = _socks5(proxy.port, "127.0.0.1", origin_server, atyp=0x01)
        s.close()
        assert rep[1] == 0x02, rep


def test_domain_egress_enforceable_requires_both_backend_and_rootless_netns(monkeypatch):
    """域名级可强制性 = **两个硬前置都满足**:用户态网络栈 + 免 root 真能建 netns。

    只探"pasta/slirp4netns 在不在 PATH"会 false-green —— 现代发行版禁非特权 userns 时,
    装了网络栈也建不出隔离 netns。回归锁:仅 backend 存在**不够**,userns 建不出 → False。
    """
    import karvyloop.platform.linux.egress_proxy as ep

    # (a) 用户态网络栈缺 → 不可强制(不管 netns 能不能建)
    monkeypatch.setattr(ep, "usernet_backend", lambda: None)
    monkeypatch.setattr(ep, "_rootless_netns_works", lambda: True)
    ep._ENFORCEABLE_CACHE = None
    assert ep.domain_egress_enforceable() is False

    # (b) 有网络栈但**免 root 建不出 netns**(userns 被禁)→ 仍不可强制(杀 false-green)
    monkeypatch.setattr(ep, "usernet_backend", lambda: "slirp4netns")
    monkeypatch.setattr(ep, "_rootless_netns_works", lambda: False)
    ep._ENFORCEABLE_CACHE = None
    assert ep.domain_egress_enforceable() is False

    # (c) 两者皆备 → 可强制
    monkeypatch.setattr(ep, "_rootless_netns_works", lambda: True)
    ep._ENFORCEABLE_CACHE = None
    assert ep.domain_egress_enforceable() is True

    ep._ENFORCEABLE_CACHE = None    # 复原缓存,不污染同套件后续用例


def test_rootless_netns_probe_is_failsafe_without_unshare(monkeypatch):
    """`unshare` 不在 PATH(证不出)→ _rootless_netns_works False(fail-safe,不假声称可强制)。"""
    import karvyloop.platform.linux.egress_proxy as ep
    monkeypatch.setattr(ep.shutil, "which", lambda name: None)
    assert ep._rootless_netns_works() is False


def test_linux_bwrap_allowlist_is_fail_closed(monkeypatch):
    """Linux bubblewrap:allowlist 非空 → **恒 --unshare-net 拒网**(fail-closed),
    即便同时带 net grant 也不放行(部件(2)不可绕过 netns 路由未真机验证前不假强制)。"""
    import asyncio
    from unittest import mock
    from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox

    captured = {}

    class _FakeProc:
        returncode = 0
        async def communicate(self, stdin=None):
            return (b"", b"")
        def kill(self):
            pass

    async def _fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        return _FakeProc()

    sb = BubblewrapSandbox()
    tok = CapabilityToken(
        task_id="t",
        grants=[Capability(resource="fs:/tmp/ws", ops=["write"]),
                Capability(resource="net:host", ops=["connect"])],
        expiry=9e9, net_allowlist=("example.com",))
    with mock.patch.object(BubblewrapSandbox, "available", staticmethod(lambda: True)), \
         mock.patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        asyncio.run(sb.exec(["echo", "hi"], token=tok, cwd="/tmp/ws"))
    assert "--unshare-net" in captured["cmd"]     # allowlist → 拒网(fail-closed)


def test_linux_bwrap_binary_net_preserved(monkeypatch):
    """控制组:net grant + **空** allowlist → net 保持 ON(不加 --unshare-net)= 零回归。"""
    import asyncio
    from unittest import mock
    from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox

    captured = {}

    class _FakeProc:
        returncode = 0
        async def communicate(self, stdin=None):
            return (b"", b"")
        def kill(self):
            pass

    async def _fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        return _FakeProc()

    sb = BubblewrapSandbox()
    tok = CapabilityToken(
        task_id="t",
        grants=[Capability(resource="fs:/tmp/ws", ops=["write"]),
                Capability(resource="net:host", ops=["connect"])],
        expiry=9e9)   # 空 allowlist
    with mock.patch.object(BubblewrapSandbox, "available", staticmethod(lambda: True)), \
         mock.patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        asyncio.run(sb.exec(["echo", "hi"], token=tok, cwd="/tmp/ws"))
    assert "--unshare-net" not in captured["cmd"]   # 二元:net grant → 放网,零回归


# ============================================================================
# 2c) macOS(seatbelt):allowlist 非空 → fail-closed(profile 拒网),不假放行
# ============================================================================

def test_seatbelt_allowlist_fail_closed():
    from karvyloop.platform.darwin.seatbelt import build_profile
    # 即便同时带 net grant,allowlist 非空也收紧为拒网(macOS 短板:SBPL 无域名粒度)
    tok = CapabilityToken(
        task_id="t",
        grants=[Capability(resource="net:host", ops=["connect"])],
        expiry=9e9, net_allowlist=("example.com",))
    p = build_profile(tok)
    assert "(deny network*)" in p
    assert "(allow network*)" not in p
    assert "egress allowlist requested" in p       # 审计痕:诚实标短板


def test_seatbelt_empty_allowlist_preserves_binary():
    from karvyloop.platform.darwin.seatbelt import build_profile
    tok = CapabilityToken(
        task_id="t",
        grants=[Capability(resource="net:host", ops=["connect"])],
        expiry=9e9)   # 空 allowlist
    p = build_profile(tok)
    assert "(allow network*)" in p                  # 二元:net grant → 放网,零回归


# ============================================================================
# 2d) Windows(restricted / degraded):allowlist 非空 → fail-closed 拒跑
# ============================================================================

def test_win_restricted_allowlist_fail_closed():
    from karvyloop.platform.win.restricted import RestrictedTokenSandbox
    sb = RestrictedTokenSandbox()
    tok = _tok(allowlist=("example.com",))
    with pytest.raises(PermissionError) as ei:
        asyncio.run(sb.exec(["cmd", "/c", "echo hi"], token=tok, cwd="."))
    msg = str(ei.value).lower()
    assert "egress" in msg or "allowlist" in msg or "域名" in str(ei.value)


def test_win_degraded_allowlist_fail_closed():
    from karvyloop.platform.win.degraded import DegradedWindowsSandbox
    sb = DegradedWindowsSandbox()
    tok = _tok(allowlist=("example.com",))
    with pytest.raises(PermissionError):
        asyncio.run(sb.exec(["cmd", "/c", "echo hi"], token=tok, cwd="."))


# ============================================================================
# 3) bridge.start 非破坏 egress_allowlist 透传(契约 2)
# ============================================================================

def _fake_recipe(bin_path="echo"):
    from karvyloop.external_runtime.recipe import builtin_recipe
    import dataclasses
    return dataclasses.replace(builtin_recipe("generic_cli"), bin_path=bin_path)


class _FakeProc:
    returncode = 0
    stdout = '{"role":"assistant","content":"hi"}'
    stderr = ""


def test_bridge_builds_and_passes_egress_token():
    """allowlist 非空 → 构造 net_allowlist 非空的 CapabilityToken 传给沙箱后端 runner。"""
    from karvyloop.external_runtime.bridge import SubprocessBridge
    captured = {}

    def runner(argv, *, env, timeout, cwd, egress_token=None):
        captured["token"] = egress_token
        return _FakeProc()

    b = SubprocessBridge(_fake_recipe(), runner=runner, env_base={"PATH": "/usr/bin"})
    res = b.start("hi", egress_allowlist=("example.com", "api.foo.io"))
    tok = captured["token"]
    assert tok is not None
    assert tok.net_allowlist == ("example.com", "api.foo.io")
    assert res.ok


def test_bridge_default_no_allowlist_zero_regression():
    """默认调用(无 egress_allowlist）→ 不构造 token(egress_token=None)= C1 零回归。"""
    from karvyloop.external_runtime.bridge import SubprocessBridge
    captured = {}

    def runner(argv, *, env, timeout, cwd, egress_token=None):
        captured["token"] = egress_token
        return _FakeProc()

    b = SubprocessBridge(_fake_recipe(), runner=runner, env_base={"PATH": "/usr/bin"})
    b.start("hi")   # C1 默认调用方式
    assert captured["token"] is None


def test_bridge_legacy_runner_still_works():
    """未升级的 runner(不接受 egress_token kwarg)→ 旧形态调用,非破坏。"""
    from karvyloop.external_runtime.bridge import SubprocessBridge
    called = {}

    def legacy_runner(argv, *, env, timeout, cwd):    # 无 egress_token
        called["yes"] = True
        return _FakeProc()

    b = SubprocessBridge(_fake_recipe(), runner=legacy_runner, env_base={"PATH": "/usr/bin"})
    res = b.start("hi", egress_allowlist=("example.com",))   # 即便请求 allowlist 也不炸
    assert called.get("yes") is True
    assert res.ok
