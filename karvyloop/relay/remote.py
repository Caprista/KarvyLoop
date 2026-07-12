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
    """一条到远程 console 的接入会话(经 relay E2E)。`request()` 发一个 HTTP-over-frame 请求。"""

    def __init__(self, ws, session: "e2e.Session") -> None:
        self._ws = ws
        self._sess = session
        self._next_id = 0

    async def request(self, method: str, path: str, *, headers: Optional[dict] = None,
                      body: bytes = b"", timeout: float = 30.0) -> dict:
        """发一个请求经 E2E 会话 → {status, headers, body(bytes), error}。path 必须以 "/" 开头。

        按 id 关联响应(收到别的 id / 非 DATA / 重放帧都跳过,直到本请求的响应)。
        """
        import asyncio
        if not path.startswith("/") or "://" in path:
            raise ValueError("path must start with / and carry no scheme")
        self._next_id += 1
        rid = self._next_id
        req: dict = {"id": rid, "method": method.upper(), "path": path}
        if headers:
            req["headers"] = headers
        if body:
            req["body_b64"] = base64.b64encode(body).decode("ascii")
        await self._ws.send(self._sess.seal(json.dumps(req).encode("utf-8")))
        while True:
            msg = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            if not isinstance(msg, bytes) or e2e.frame_type(msg) != e2e.T_DATA:
                continue
            try:
                resp = json.loads(self._sess.open(msg).decode("utf-8"))
            except (e2e.ReplayError, e2e.FrameError):
                continue
            if resp.get("id") != rid:
                continue
            b64 = resp.get("body_b64")
            return {"status": int(resp.get("status", 0)),
                    "headers": resp.get("headers", {}),
                    "error": resp.get("error", ""),
                    "body": base64.b64decode(b64) if b64 else b""}


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
        return ws, RemoteSession(ws, session)
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
        await ws.close()


def cmd_remote(relay_url: str, rid: str, fingerprint: str, request: str,
               code: Optional[str] = None, state_dir=None) -> int:
    """`karvyloop remote --relay … --room … --fingerprint … --request "GET /api/…"`.

    接入端一次性请求:证你能从这台机器跨网访问家里的 console。request = "METHOD /path"。
    """
    import asyncio
    parts = (request or "").strip().split(None, 1)
    if len(parts) != 2 or not parts[1].startswith("/"):
        import sys
        sys.stderr.write('--request must be like:  "GET /api/status"\n')
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
