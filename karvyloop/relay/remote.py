"""relay/remote.py — 接入端客户端(``karvyloop remote``):从另一台机器/手机跨网访问你家的 console。

console(家)出站长连 relay ``/attach``;本模块是**接入端**——连 relay ``/join?rid=``,走 client 侧
E2E 握手(build_hello 带一次性码 + client_complete **验指纹防中间人**),再把 HTTP 请求经 E2E 会话
转给远程 console,响应解密回来。relay 全程只见密文(「信使不拆信」)。

- **slice 3a(本文件)**:一次性请求 —— `karvyloop remote --request "GET /api/..."` 打印响应,**证跨网真通**。
- **slice 3b(后续)**:本地反向代理 —— `--port`,浏览器开 `localhost:端口` = 你家 console 跨网。

接入端自己一把持久密钥(``remote_key``,首次用配对码、之后免码;与 console 的 ``relay_key`` 不同文件,
同机测试也不撞)。**纪律**:绝不 log 密钥/明文 body;path 必须以 "/" 开头(防 SSRF 出本机)。
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from karvyloop.relay import MAX_FRAME_BYTES, e2e

logger = logging.getLogger(__name__)
REMOTE_KEY_FILE = "remote_key"


def _remote_identity(state_dir=None) -> Tuple[bytes, bytes]:
    """接入端自己的持久 keypair(remote_key,0600);首次生成。缺 crypto → RelayCryptoUnavailable。"""
    d = Path(state_dir) if state_dir else (Path.home() / ".karvyloop")
    kp = d / REMOTE_KEY_FILE
    if kp.exists():
        priv = kp.read_bytes()
        if len(priv) != 32:
            raise ValueError(f"corrupt remote key file: {kp}")
        return priv, e2e.pub_from_priv(priv)
    priv, pub = e2e.gen_keypair()
    d.mkdir(parents=True, exist_ok=True)
    kp.write_bytes(priv)
    if os.name != "nt":
        try:
            os.chmod(kp, 0o600)
        except Exception:
            pass
    return priv, pub


class RemoteSession:
    """一条到远程 console 的接入会话(经 relay E2E)。**并发安全**:后台收帧循环按 id 派发到
    per-request future,`request()` 可并发多发(本地代理下浏览器多请求共用一条 ws)。

    纪律:`seal`(seq++)与 `ws.send` 必须原子(外挂 send_lock),否则乱序=对端拒帧。
    """

    def __init__(self, ws, session: "e2e.Session") -> None:
        import asyncio
        self._ws = ws
        self._sess = session
        self._next_id = 0
        self._pending: dict = {}                 # id -> Future(resp dict)
        self._send_lock = asyncio.Lock()
        self._recv_task = None
        self._closed = False

    def start(self) -> None:
        """启动后台收帧派发循环(open_remote_session 建完即调)。"""
        import asyncio
        if self._recv_task is None:
            self._recv_task = asyncio.create_task(self._recv_loop())

    async def _recv_loop(self) -> None:
        """唯一读 ws 的地方:解密 → 按 id 派发到等待的 future。ws 断 → 所有 pending 失败。"""
        try:
            while not self._closed:
                msg = await self._ws.recv()
                if not isinstance(msg, bytes) or e2e.frame_type(msg) != e2e.T_DATA:
                    continue
                try:
                    resp = json.loads(self._sess.open(msg).decode("utf-8"))
                except (e2e.ReplayError, e2e.FrameError):
                    continue                     # 重放/坏帧:丢弃,绝不二次派发
                fut = self._pending.pop(resp.get("id"), None)
                if fut is not None and not fut.done():
                    fut.set_result(resp)
        except Exception as exc:                 # noqa: BLE001 — ws 断/关:让所有 pending 醒来报错
            self._fail_all(exc)

    def _fail_all(self, exc: Exception) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(ConnectionError(f"relay connection lost: {type(exc).__name__}"))
        self._pending.clear()

    async def request(self, method: str, path: str, *, headers: Optional[dict] = None,
                      body: bytes = b"", timeout: float = 30.0) -> dict:
        """发一个请求经 E2E 会话 → {status, headers, body(bytes), error}。path 必须以 "/" 开头。"""
        import asyncio
        if not path.startswith("/") or "://" in path:
            raise ValueError("path must start with / and carry no scheme")
        if self._recv_task is None:              # 没启后台循环 → 单发也自启(CLI 一次性用)
            self.start()
        self._next_id += 1
        rid = self._next_id
        req: dict = {"id": rid, "method": method.upper(), "path": path}
        if headers:
            req["headers"] = headers
        if body:
            req["body_b64"] = base64.b64encode(body).decode("ascii")
        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        try:
            async with self._send_lock:          # seal(seq++)+send 原子
                await self._ws.send(self._sess.seal(json.dumps(req).encode("utf-8")))
            resp = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)
        b64 = resp.get("body_b64")
        return {"status": int(resp.get("status", 0)),
                "headers": resp.get("headers", {}),
                "error": resp.get("error", ""),
                "body": base64.b64decode(b64) if b64 else b""}

    async def close(self) -> None:
        self._closed = True
        if self._recv_task is not None:
            self._recv_task.cancel()
        await self._ws.close()


async def open_remote_session(relay_url: str, rid: str, *, fingerprint: str,
                              code: Optional[str] = None, state_dir=None):
    """连 relay ``/join?rid=`` + client 侧握手 → (ws, RemoteSession)。

    code 首次配对必给(一次性,来自 `relay-pair`);已配对设备可省(remote_key 免码重连)。
    **验指纹**(client_complete):relay 掉包公钥 → FingerprintMismatch,必须放弃(防中间人)。
    """
    import asyncio

    import websockets
    priv, _pub = _remote_identity(state_dir)
    url = relay_url.rstrip("/") + f"/join?rid={rid}"
    ws = await websockets.connect(url, max_size=MAX_FRAME_BYTES + 4096)
    try:
        await ws.send(e2e.build_hello(priv, code))
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=15)
            if isinstance(msg, bytes):
                break
        if e2e.frame_type(msg) == e2e.T_ERR:
            raise e2e.HandshakeError(f"rejected by relay/console: {e2e.parse_err(msg)}")
        session = e2e.client_complete(msg, priv, fingerprint)   # 验指纹,防中间人
        sess = RemoteSession(ws, session)
        sess.start()                                            # 启后台收帧派发循环
        return ws, sess
    except Exception:
        await ws.close()
        raise


async def run_remote_request(relay_url: str, rid: str, *, fingerprint: str, method: str,
                             path: str, code: Optional[str] = None, body: bytes = b"",
                             state_dir=None) -> dict:
    """一次性:连+握手+发一个请求+关连接。返回响应 dict(slice 3a 用)。"""
    ws, sess = await open_remote_session(relay_url, rid, fingerprint=fingerprint,
                                         code=code, state_dir=state_dir)
    try:
        return await sess.request(method, path, body=body)
    finally:
        await sess.close()


# 转发给远程 console 的请求头白名单(其余不透传;console 侧还会再过一遍)。
_FWD_HEADERS = ("content-type", "accept", "accept-language")


async def run_remote_proxy(relay_url: str, rid: str, *, fingerprint: str, local_port: int,
                           code: Optional[str] = None, local_host: str = "127.0.0.1",
                           state_dir=None, stop=None) -> None:
    """本地反向代理(slice 3b):``localhost:local_port`` 起 HTTP,每个请求经 RemoteSession 转给
    远程 console,响应回来。**浏览器开 http://localhost:local_port = 你家 console 跨网。**

    复用既有 Starlette+uvicorn(不引新依赖);单条持久 E2E 会话,浏览器并发请求走派发器按 id 关联。
    v1 不自动重连(ws 断则请求报 502,重启即可)——重连是后续 refinement。
    """
    import asyncio

    import uvicorn
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Route

    ws, sess = await open_remote_session(relay_url, rid, fingerprint=fingerprint,
                                         code=code, state_dir=state_dir)

    async def _proxy(request: "Request") -> "Response":
        body = await request.body()
        fwd = {k.lower(): v for k, v in request.headers.items() if k.lower() in _FWD_HEADERS}
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        try:
            r = await sess.request(request.method, path, headers=fwd, body=body)
        except Exception as exc:  # noqa: BLE001 — 会话断/超时:回 502,不泄敏感
            return Response(f"remote unreachable: {type(exc).__name__}", status_code=502)
        if r.get("error"):
            return Response(r["error"], status_code=r.get("status") or 502)
        ct = (r.get("headers") or {}).get("content-type", "application/octet-stream")
        return Response(content=r["body"], status_code=r["status"], media_type=ct)

    app = Starlette(routes=[Route("/{path:path}", _proxy,
                                  methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])])
    config = uvicorn.Config(app, host=local_host, port=local_port,
                            log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    if stop is not None:                     # 测试/程序化关停:stop 一置位就让 uvicorn 退
        async def _watch():
            await stop.wait()
            server.should_exit = True
        asyncio.create_task(_watch())
    try:
        await server.serve()
    finally:
        await sess.close()


def cmd_remote(relay_url: str, rid: str, fingerprint: str, request: Optional[str] = None,
               port: Optional[int] = None, code: Optional[str] = None, state_dir=None) -> int:
    """接入端(从另一台机器跨网访问家里 console):

    - ``--port N``  → **本地反向代理**:浏览器开 ``http://localhost:N`` = 你家 console(slice 3b)。
    - ``--request "GET /api/…"`` → 一次性请求,打印响应(slice 3a,证连通)。
    """
    import asyncio
    import sys
    if port:                                  # 代理模式:起本地反向代理,直到 Ctrl-C
        print(f"KarvyLoop remote proxy → open  http://localhost:{port}  in your browser "
              f"(Ctrl-C to stop). relay={relay_url} room={rid}")
        try:
            asyncio.run(run_remote_proxy(relay_url, rid, fingerprint=fingerprint,
                                         local_port=int(port), code=code, state_dir=state_dir))
            return 0
        except KeyboardInterrupt:
            return 0
        except e2e.RelayCryptoUnavailable as exc:
            sys.stderr.write(str(exc) + "\n")
            return 1
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"remote proxy failed: {type(exc).__name__}: {exc}\n")
            return 1
    parts = (request or "").strip().split(None, 1)
    if len(parts) != 2 or not parts[1].startswith("/"):
        sys.stderr.write('need --port N (browser proxy) or --request "GET /api/status"\n')
        return 2
    method, path = parts[0], parts[1]
    try:
        resp = asyncio.run(run_remote_request(
            relay_url, rid, fingerprint=fingerprint, method=method, path=path,
            code=code, state_dir=state_dir))
    except e2e.RelayCryptoUnavailable as exc:
        import sys
        sys.stderr.write(str(exc) + "\n")
        return 1
    except Exception as exc:  # noqa: BLE001 — 连不上/握手失败/超时:诚实短错误,不泄敏感
        import sys
        sys.stderr.write(f"remote request failed: {type(exc).__name__}: {exc}\n")
        return 1
    status = resp.get("status", 0)
    if resp.get("error"):
        print(f"[{status}] error: {resp['error']}")
        return 0 if status and status < 400 else 1
    body = resp.get("body", b"")
    print(f"[{status}]")
    try:
        print(body.decode("utf-8"))
    except UnicodeDecodeError:
        print(f"<{len(body)} bytes binary>")
    return 0 if status and status < 400 else 1


__all__ = ["RemoteSession", "open_remote_session", "run_remote_request",
           "cmd_remote", "REMOTE_KEY_FILE"]
