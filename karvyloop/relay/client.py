"""relay/client.py — console 侧信使客户端(``karvyloop console --relay wss://…``)。

出站长连 relay ``/attach?rid=…``(家里发不了入站、发得了出站 —— docs/43 破局点),
心跳 + 断线指数退避重连;收到的 E2E 密文帧解密后变成 HTTP-over-frame 请求,
转发给**本机 loopback console**(复用全部既有中间件:同源门/token 门 —— 深度防御,
即使 loopback 免 token 也照带 ``x-karvy-token``),响应加密回传。

帧内明文协议(v1,JSON;e2e.Session 只管封/拆信封,这里定义信的内容):
    请求  {"id": <any>, "method": "GET|POST|...", "path": "/api/...",
           "headers": {"content-type": ...}, "body_b64": "<base64>"}
    响应  {"id": <同请求>, "status": <int>, "headers": {"content-type": ...},
           "body_b64": "<base64>"}
    错误  {"id": ..., "status": 502, "error": "<短错误码,不带敏感内容>"}

纪律:绝不 log 密钥/明文 body;path 必须以 "/" 开头(不许带 scheme,防 SSRF 出本机)。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
from typing import Optional

from karvyloop.relay import MAX_FRAME_BYTES
from karvyloop.relay import e2e

logger = logging.getLogger(__name__)

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_FWD_REQ_HEADERS = ("content-type", "accept", "accept-language")
# 明文预算:响应帧(头 12B + AEAD tag 16B)不得超 MAX_FRAME_BYTES;
# body 再经 base64 膨胀 4/3 → 预算打 0.7 留裕量。
_MAX_BODY_BYTES = int(MAX_FRAME_BYTES * 0.7)


def _response_json(rid, status: int, content_type: str, body: bytes) -> bytes:
    return json.dumps({
        "id": rid, "status": int(status),
        "headers": {"content-type": content_type},
        "body_b64": base64.b64encode(body).decode("ascii"),
    }, ensure_ascii=False).encode("utf-8")


def _error_json(rid, code: str, status: int = 502) -> bytes:
    return json.dumps({"id": rid, "status": status, "error": code}).encode("utf-8")


async def _handle_request(pt: bytes, http, token: str, *, store=None, peer_pub: bytes = b"") -> bytes:
    """解密后的请求明文 → 打本机 loopback console → 响应明文(待加密)。

    §9.6 slice 2 授权层:**每请求**按对端身份查 scope(回源在线校验)。
    - 对端已撤销/不在 paired(store.scope_for → None)→ 403 revoked_or_unpaired(撤销即断落到活连接)。
    - scope=read(分享只读)且方法是改动 → 403 scope_read_only。
    - scope=full(自有设备)或只读方法 → 照常打 loopback(带主人 token)。零回归:自有设备恒 full。
    """
    from karvyloop.relay.pairing import READ_ONLY_METHODS, SCOPE_FULL
    try:
        req = json.loads(pt.decode("utf-8"))
        rid = req.get("id")
        method = str(req.get("method", "GET")).upper()
        path = str(req.get("path", ""))
        if method not in _ALLOWED_METHODS:
            return _error_json(rid, "method_not_allowed", 405)
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            return _error_json(rid, "bad_path", 400)
        # 授权门(回源在线校验):撤销/越 scope 一律拒,绝不打 loopback。
        scope = store.scope_for(peer_pub.hex()) if (store is not None and peer_pub) else SCOPE_FULL
        if scope is None:
            return _error_json(rid, "revoked_or_unpaired", 403)
        if scope != SCOPE_FULL and method not in READ_ONLY_METHODS:
            return _error_json(rid, "scope_read_only", 403)
        headers = {"x-karvy-token": token} if token else {}
        # 特权分离标(docs/74 配对切片):经隧道的请求一律打此标,授权管理端点(/api/pair/*)
        # 见标即拒 —— 远程面(哪怕 full scope)永远够不到"颁发/吊销授权"这类管理权。
        # 防伪:此标由本函数(console 侧咽喉)注入;远端自带的头只透传 _FWD_REQ_HEADERS 三样,
        # 伪造不进来;LAN 侧攻击者自己加此标只会**降**自己的权(安全方向)。
        headers["x-karvy-via-relay"] = "1"
        # 受众标(docs/78 §4.3 / docs/73 §9.6):非 full scope = 分享给**别人**(不是我自己的设备)→
        # 打 external 标,console 侧召回据此走白名单刀(deny-by-default 只放被访角色兵法)+ 裸 dump
        # 端点见标即拒。防伪同 via-relay:此标由本咽喉注入,远端自带的被 _FWD_REQ_HEADERS 白名单挡在外。
        if scope != SCOPE_FULL:
            headers["x-karvy-audience"] = "external"
            # per-channel role 绑定(docs/78 §4.3 白名单刀的"放行名单"):分享码上绑了被访角色 →
            # 同一咽喉、同一防伪纪律注 x-karvy-audience-role(白名单不含它,远端伪造/剥除都不可能;
            # full scope 永不注——自有设备不被收窄)。百分号编码:role 名可含中文,裸塞会破 HTTP 头。
            # 没绑(role="")→ 不注,召回谓词③ deny-by-default 全拒,一条兵法也不漏。
            role = store.role_for(peer_pub.hex()) if (store is not None and peer_pub) else ""
            if role:
                from urllib.parse import quote
                headers["x-karvy-audience-role"] = quote(role, safe="")
        for k, v in (req.get("headers") or {}).items():
            if str(k).lower() in _FWD_REQ_HEADERS:
                headers[str(k).lower()] = str(v)
        body = base64.b64decode(req.get("body_b64") or "") if req.get("body_b64") else b""
        resp = await http.request(method, path, headers=headers, content=body)
        if len(resp.content) > _MAX_BODY_BYTES:
            return _error_json(rid, "response_too_large", 502)
        return _response_json(rid, resp.status_code,
                              resp.headers.get("content-type", ""), resp.content)
    except json.JSONDecodeError:
        return _error_json(None, "bad_request", 400)
    except Exception as exc:   # loopback 不通等 —— 只回短错误码,不带敏感内容
        logger.warning(f"[relay-client] loopback request failed: {type(exc).__name__}")
        return _error_json(None, "console_unreachable", 502)


async def _serve_connection(ws, store, priv: bytes, pub: bytes, *,
                            console_host: str, console_port: int, token: str,
                            heartbeat_s: float, stop: asyncio.Event) -> None:
    """一条 relay 连接的生命周期:握手(HELLO/WELCOME)+ DATA 循环。"""
    import httpx
    session: Optional[e2e.Session] = None
    send_lock = asyncio.Lock()          # seal(seq++) 与 ws.send 必须原子,否则乱序=对端拒帧
    pending: set = set()

    async def _sealed_send(plaintext: bytes) -> None:
        async with send_lock:
            if session is None:
                return
            await ws.send(session.seal(plaintext))

    async def _run_request(pt: bytes) -> None:
        # 对端身份来自握手后的 session(console 侧 = 连进来的设备公钥)→ per-request 查 scope/撤销。
        peer = session.peer_pub if session is not None else b""
        await _sealed_send(await _handle_request(pt, http, token, store=store, peer_pub=peer))

    async with httpx.AsyncClient(
            base_url=f"http://{console_host}:{console_port}", timeout=60.0) as http:
        try:
            while not stop.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=heartbeat_s)
                except TimeoutError:
                    await ws.send('{"t":"ping"}')     # 心跳:让 NAT/中间盒别掐这条出站长连
                    continue
                if isinstance(msg, str):              # relay 控制面(pong/peer/error)—— 不含数据
                    continue
                if len(msg) > MAX_FRAME_BYTES:
                    continue                          # relay 已限;双保险,静默丢
                ftype = e2e.frame_type(msg)
                if ftype == e2e.T_HELLO:
                    try:
                        welcome, session = e2e.console_accept(
                            msg, priv, pub, store.verify_and_consume)
                    except e2e.HandshakeError:
                        # 配对码错/设备未配对:明文错误帧(只有错误码,零秘密),不建会话
                        await ws.send(e2e.err_frame("pairing_rejected"))
                        continue
                    await ws.send(welcome)
                elif ftype == e2e.T_DATA and session is not None:
                    try:
                        pt = session.open(msg)
                    except e2e.ReplayError:
                        continue                      # 重放帧:丢弃,绝不二次执行
                    except e2e.FrameError:
                        continue                      # 坏帧/被改:丢弃
                    task = asyncio.create_task(_run_request(pt))
                    pending.add(task)
                    task.add_done_callback(pending.discard)
                # 其余帧型(未知/未握手的 DATA):静默丢弃
        finally:
            for t in pending:
                t.cancel()


async def run_relay_client(relay_url: str, *, console_port: int,
                           console_host: str = "127.0.0.1", token: str = "",
                           state_dir=None, heartbeat_s: float = 20.0,
                           max_backoff_s: float = 30.0,
                           stop: Optional[asyncio.Event] = None,
                           rid: Optional[str] = None) -> None:
    """console 信使客户端主循环:连 relay → 服务 → 断线退避重连,直到 stop。

    rid=None → store.rid()(主房,原行为);显式给 rid(如 store.mesh_rid())→ 挂那个房
    (docs/74:mesh 同步走第二房,不跟 away 浏览器抢主房唯一的 client 位)。
    """
    from karvyloop.relay.pairing import PairingStore
    import websockets

    stop = stop or asyncio.Event()
    store = PairingStore(state_dir)
    priv, pub = store.identity()        # 缺 cryptography 在这里诚实炸(RelayCryptoUnavailable)
    rid = rid or store.rid()
    url = relay_url.rstrip("/") + f"/attach?rid={rid}"
    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(url, max_size=MAX_FRAME_BYTES + 4096) as ws:
                logger.info("[relay-client] attached to relay (outbound)")
                backoff = 1.0
                # stop 要**立刻**生效(console 退出不能等 recv 超时)→ serve 与 stop.wait 赛跑
                serve = asyncio.create_task(_serve_connection(
                    ws, store, priv, pub,
                    console_host=console_host, console_port=console_port,
                    token=token, heartbeat_s=heartbeat_s, stop=stop))
                stopper = asyncio.create_task(stop.wait())
                done, pending = await asyncio.wait(
                    {serve, stopper}, return_when=asyncio.FIRST_COMPLETED)
                for p in pending:
                    p.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if serve in done:
                    serve.result()     # 连接层异常冒出来 → 走退避重连
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                f"[relay-client] connection lost ({type(exc).__name__}); "
                f"reconnecting in {backoff:.0f}s")
        if stop.is_set():
            break
        try:                            # 可被 stop 打断的退避睡眠
            await asyncio.wait_for(stop.wait(), timeout=backoff)
        except TimeoutError:
            pass
        backoff = min(backoff * 2, max_backoff_s)


class RelayClientHandle:
    """后台线程句柄(照 email_channel_task 先例:console 退出时 stop() 收干净)。"""

    def __init__(self, relay_url: str, *, console_port: int, token: str,
                 console_host: str = "127.0.0.1", state_dir=None,
                 rid: Optional[str] = None) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop: Optional[asyncio.Event] = None
        self._kw = dict(console_port=console_port, token=token,
                        console_host=console_host, state_dir=state_dir, rid=rid)
        self._url = relay_url
        self._thread = threading.Thread(
            target=self._run, name="karvyloop-relay-client", daemon=True)

    def _run(self) -> None:
        async def _main() -> None:
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
            await run_relay_client(self._url, stop=self._stop, **self._kw)
        try:
            asyncio.run(_main())
        except Exception as exc:   # 线程里不许无声死:留一行日志(不含密钥)
            logger.error(f"[relay-client] background thread exited: {type(exc).__name__}: {exc}")

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        loop, ev = self._loop, self._stop
        if loop is not None and ev is not None:
            try:
                loop.call_soon_threadsafe(ev.set)
            except Exception:
                pass
        self._thread.join(timeout=timeout)


def start_relay_client_thread(relay_url: str, *, console_port: int, token: str,
                              console_host: str = "127.0.0.1",
                              state_dir=None, rid: Optional[str] = None) -> RelayClientHandle:
    """entry.py 用:先**急切**验一遍身份(缺 cryptography 当场诚实报错,而不是线程里静默死),
    再起后台线程。返回句柄,console 退出时调 .stop()。rid=None=主房;显式给=挂那个房(mesh)。"""
    from karvyloop.relay.pairing import PairingStore
    PairingStore(state_dir).identity()   # RelayCryptoUnavailable → 直接抛给调用方
    h = RelayClientHandle(relay_url, console_port=console_port, token=token,
                          console_host=console_host, state_dir=state_dir, rid=rid)
    h.start()
    return h


__all__ = ["run_relay_client", "start_relay_client_thread", "RelayClientHandle"]
