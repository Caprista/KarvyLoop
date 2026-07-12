"""platform/linux/egress_proxy.py —— 按域名 egress(出网)allowlist 的用户态强制门(no root)。

**问题**:沙箱网络门原是二元(--unshare-net 全关 / 不加则全开)。外部子进程(external_runtime
成员化)是我们**不控其执行**的 opaque 执行体,唯一能约束其网络行为的确定性抓手 = 只放行
allowlist 里的域名、其余拒。这需要**域名级**强制,而二元 netns 做不到。

**Linux 最干净的免 root 确定性路**(目标架构,两个部件):
  (1) **用户态 allowlist 代理**(本模块 AllowlistProxy)—— **已焊、已测**:
      HTTP CONNECT + SOCKS5;命中 allowlist 放行、否则**确定性拒**(连 socket 都不给建);
      IP 字面量拒(无域名可判 = 防用 IP / DNS rebind 绕过)。
  (2) **让子进程唯一 egress 强制走该代理**:子进程放进无外部路由的 netns(--unshare-net,
      内核隔离,免 root)→ 该 netns 唯一 egress 走用户态 slirp 栈(pasta/slirp4netns,免 root)
      → slirp 只把流量转到代理端口。内核 netns 隔离 = 子进程**无法绕过**代理(没别的路由)。
      **此装配尚未真机验证**(见 bubblewrap.py 2b:P1)。

**诚实边界(硬要求,安全是地基,宁 fail-closed 不假安全)**:
  - 部件(2)未真机验证前,bubblewrap 对"allowlist 非空"**一律 fail-closed 拒网**;
    **绝不**退回"仅 *_PROXY env"(HTTP_PROXY 可被子进程无视 = 假放行,违背地基纪律)。
  - domain_egress_enforceable() 表达"本机能否**真焊出**免 root 域名级强制",两个硬前置都验:
    (a)有免 root 用户态网络栈(pasta/slirp4netns);(b)**免 root 能真建 user+net 命名空间**
    ——现代发行版(如 Ubuntu 24.04)常把非特权 userns 禁掉(AppArmor),禁了则装了(a)也建不出
    隔离 netns。只探(a)会 false-green,故(b)真跑一次 `unshare --user --net` 探。是给 P1 装配
    用的探针;**不**代表"现在就在做域名级强制"(现在恒 fail-closed)。
  - 代理(部件(1))**默认拒**:未在 allowlist 的 host、格式坏的请求、IP 字面量 → 一律拒。

代理协议:同时支持 **HTTP CONNECT**(https 隧道 / 通用 TCP)与 **SOCKS5**(no-auth,
CONNECT 命令)—— 覆盖绝大多数 CLI/HTTP 客户端。纯 stdlib(socket/threading),无新依赖。

纯逻辑(host 匹配判定)平台无关可单测;netns/slirp 装配仅 Linux 有意义。
"""

from __future__ import annotations

import shutil
import socket
import struct
import subprocess
import threading
from typing import Optional


# ---------------------------------------------------------------------------
# 纯逻辑:host allowlist 匹配(平台无关,可单测)
# ---------------------------------------------------------------------------

def host_allowed(host: str, allowlist: tuple[str, ...]) -> bool:
    """host 是否被 allowlist 放行(域名大小写无关 + 子域后缀匹配)。

    规则(fail-closed):
      - allowlist 为空 → **False**(本代理只在"已限制"语境用;空=不该用代理,调用方决定)。
      - 精确相等 → 放行。
      - allowlist 项 `example.com` 也放行其子域 `api.example.com`(后缀 + 点边界),
        但**不**放行 `notexample.com`(必须落在点边界上)。
      - host 为空 / None → False。

    纯函数,平台无关。**判定按域名字符串**(代理在 CONNECT/SOCKS 阶段拿到的就是域名,
    在解析前判 = 不被 DNS rebind 绕过)。
    """
    if not host or not allowlist:
        return False
    h = host.strip().lower().rstrip(".")
    if not h:
        return False
    for entry in allowlist:
        e = (entry or "").strip().lower().rstrip(".")
        if not e:
            continue
        if h == e or h.endswith("." + e):
            return True
    return False


# ---------------------------------------------------------------------------
# 用户态 allowlist 代理(HTTP CONNECT + SOCKS5,纯 stdlib)
# ---------------------------------------------------------------------------

class AllowlistProxy:
    """只放行 allowlist 域名的用户态 TCP 代理(HTTP CONNECT + SOCKS5 no-auth)。

    非 allowlist 的 host → 确定性拒(HTTP 403 / SOCKS 拒答),不建上游连接。
    线程模型:一个 accept 线程 + 每连接一个转发线程。start()/stop() 幂等。
    """

    def __init__(self, allowlist: tuple[str, ...], *, bind_host: str = "127.0.0.1",
                 bind_port: int = 0) -> None:
        self.allowlist = tuple(allowlist)
        self._bind_host = bind_host
        self._bind_port = bind_port
        self._srv: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.port: int = 0
        #: 审计:被拒的 host(测试/可观测用)
        self.denied: list[str] = []
        self.allowed: list[str] = []

    def start(self) -> int:
        """起代理,返回真实监听端口。"""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self._bind_host, self._bind_port))
        srv.listen(64)
        srv.settimeout(0.5)
        self._srv = srv
        self.port = srv.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        self._stop.set()
        s = self._srv
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
        t = self._thread
        if t is not None:
            t.join(timeout=3)

    def __enter__(self) -> "AllowlistProxy":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def _serve(self) -> None:
        srv = self._srv
        assert srv is not None
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30)
            first = conn.recv(1, socket.MSG_PEEK)
            if not first:
                return
            if first[0] == 0x05:          # SOCKS5 版本字节
                self._handle_socks5(conn)
            else:                          # 否则当 HTTP CONNECT
                self._handle_http_connect(conn)
        except (OSError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    # ---- HTTP CONNECT ----
    def _handle_http_connect(self, conn: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 8192:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
        line = data.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            return
        hostport = parts[1]
        host, _, port_s = hostport.rpartition(":")
        if not host:  # 无端口的形式
            host, port_s = hostport, "443"
        try:
            port = int(port_s)
        except ValueError:
            port = 443
        if not host_allowed(host, self.allowlist):
            self.denied.append(host)
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n"
                         b"X-Karvy-Egress: denied (not in allowlist)\r\n\r\n")
            return
        upstream = self._dial(host, port)
        if upstream is None:
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        self.allowed.append(host)
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        self._pump(conn, upstream)

    # ---- SOCKS5 (no-auth, CONNECT) ----
    def _handle_socks5(self, conn: socket.socket) -> None:
        # 握手:VER NMETHODS METHODS...
        hdr = self._recvn(conn, 2)
        if not hdr or hdr[0] != 0x05:
            return
        nmethods = hdr[1]
        self._recvn(conn, nmethods)   # 丢弃 methods,我们只支持 no-auth
        conn.sendall(b"\x05\x00")     # 选 no-auth
        # 请求:VER CMD RSV ATYP DST.ADDR DST.PORT
        req = self._recvn(conn, 4)
        if not req or req[1] != 0x01:  # 只支持 CONNECT
            conn.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")  # cmd not supported
            return
        atyp = req[3]
        if atyp == 0x03:              # 域名(我们要的:拿到域名再判)
            ln = self._recvn(conn, 1)
            # 域名在 SOCKS5 里是 ASCII/punycode 线格式;按 latin-1/替换解,交给
            # host_allowed 归一化判定(idna 编解码不支持 replace,会抛 → 别用)。
            host = self._recvn(conn, ln[0]).decode("latin-1", "replace") if ln else ""
        elif atyp == 0x01:            # IPv4 字面量(无域名 → 无法按域名放行 → 拒)
            host = socket.inet_ntoa(self._recvn(conn, 4))
        elif atyp == 0x04:            # IPv6 字面量 → 同样拒(无域名)
            host = socket.inet_ntop(socket.AF_INET6, self._recvn(conn, 16))
        else:
            conn.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")  # addr type ns
            return
        port_b = self._recvn(conn, 2)
        port = struct.unpack("!H", port_b)[0] if port_b else 0
        # IP 字面量(atyp 1/4)无域名 → 域名级 allowlist 无从判 → 确定性拒(防绕过 DNS)
        is_literal = atyp in (0x01, 0x04)
        if is_literal or not host_allowed(host, self.allowlist):
            self.denied.append(host)
            conn.sendall(b"\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00")  # connection not allowed
            return
        upstream = self._dial(host, port)
        if upstream is None:
            conn.sendall(b"\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00")  # host unreachable
            return
        self.allowed.append(host)
        conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")      # succeeded
        self._pump(conn, upstream)

    # ---- helpers ----
    @staticmethod
    def _recvn(sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    @staticmethod
    def _dial(host: str, port: int) -> Optional[socket.socket]:
        try:
            return socket.create_connection((host, port), timeout=15)
        except OSError:
            return None

    def _pump(self, a: socket.socket, b: socket.socket) -> None:
        """双向转发直到任一端 EOF。"""
        done = threading.Event()

        def one(src: socket.socket, dst: socket.socket) -> None:
            try:
                while not done.is_set():
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                done.set()
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        t1 = threading.Thread(target=one, args=(a, b), daemon=True)
        t2 = threading.Thread(target=one, args=(b, a), daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()
        try:
            b.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Linux netns egress 装配:pasta / slirp4netns 免 root 用户态网络栈探测
# ---------------------------------------------------------------------------

def usernet_backend() -> Optional[str]:
    """返回可用的免 root 用户态网络栈("pasta" / "slirp4netns"),都没有 → None。

    这两者都能把一个隔离 netns 的出站流量在用户态转出(免 root)。二者皆无 →
    Linux 上焊不出"netns 隔离 + 唯一 egress 走代理"的域名级强制 → 调用方 fail-closed 拒网。
    """
    for tool in ("pasta", "slirp4netns"):
        if shutil.which(tool):
            return tool
    return None


#: domain_egress_enforceable 结果缓存(None=未探;探要 spawn 一次 unshare,别每次重跑)。
#: 测试可置回 None 重探(见 tests/test_egress_allowlist.py)。
_ENFORCEABLE_CACHE: Optional[bool] = None


def _rootless_netns_works() -> bool:
    """真探:能否**免 root** 建一个 user+net 命名空间(域名级强制装配的硬前置)。

    只看 pasta/slirp4netns 在不在 PATH **不够** —— 现代发行版(如 Ubuntu 24.04)常把非特权
    user namespace 禁掉(AppArmor `kernel.apparmor_restrict_unprivileged_userns` / sysctl
    `kernel.unprivileged_userns_clone=0`),此时装了用户态网络栈也建不出隔离 netns(uid_map
    permission denied)。这里**真跑一次** `unshare --user --map-root-user --net -- true`:
    退 0 = 免 root netns 可建。无 unshare / 探不通 / 超时 → False
    (fail-safe:证不出就**不**声称可强制,不留 false-green)。
    """
    unshare = shutil.which("unshare")
    if not unshare:
        return False
    try:
        r = subprocess.run(
            [unshare, "--user", "--map-root-user", "--net", "--", "true"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def domain_egress_enforceable() -> bool:
    """本机 Linux 能否**真焊出**免 root 域名级 egress 强制。两个硬前置都要满足:

      (1) 有免 root 用户态网络栈(pasta/slirp4netns)—— 把隔离 netns 出站转出;
      (2) 免 root 能真建 user+net 命名空间(`_rootless_netns_works`)—— 禁了非特权 userns
          则即便有(1)也建不出隔离 netns,装了 slirp4netns 也白搭。

    仅探(1)会 false-green(旧实现的坑)。结果缓存(探一次 spawn 一次 unshare)。
    False → 调用方对"非空 allowlist"必须 fail-closed 拒网(不假放行)。
    """
    global _ENFORCEABLE_CACHE
    if _ENFORCEABLE_CACHE is None:
        _ENFORCEABLE_CACHE = usernet_backend() is not None and _rootless_netns_works()
    return _ENFORCEABLE_CACHE


__all__ = [
    "host_allowed", "AllowlistProxy",
    "usernet_backend", "domain_egress_enforceable",
]
