"""relay/server.py — Karvy 信使 relay 本体(docs/43 第二级:「信使不拆信」)。

单文件 FastAPI,**无状态、无盘、只配对 + 盲转发**:
- console(家)出站长连 ``/attach?rid=<房间号>``;手机/客户端连 ``/join?rid=<房间号>``。
- **binary 帧 = 端到端密文,原样转发、绝不解析**(本模块结构性不 import e2e ——
  没有解析代码 = 没有拆信能力;测试里有静态断言锁这两条)。
- text 帧 = relay 控制面(仅 ping/pong 心跳 + 错误/对端上下线通知),JSON 一行。
- 无 console 在线 → 明确报 ``console_offline``(「主人不在线」),不挂着干等。
- 上限:帧 ≤ MAX_FRAME_BYTES;房间数 ≤ MAX_ROOMS;每房 1 console + 1 client
  (console 重连顶掉旧连接;第二个 client → ``room_busy``)。

威胁模型(docs/43):恶意 relay 最多拒绝服务 + 看流量元数据,读/改/伪造都过不了帧内 AEAD。
纪律:不落盘、不记 payload、不 log 房间号内容;托管同一份代码不破本地优先承诺。

自部署一条命令:``karvyloop relay-serve``(或 ``uvicorn`` 直接挂 ``build_relay_app()``)。
"""
from __future__ import annotations

import json
import logging
import re
import sys
from typing import Dict, Optional

# fastapi 是基础依赖(console 同款)。**必须模块级 import**:
# `from __future__ import annotations` 下注解是字符串,FastAPI 用模块 globals 解析
# `ws: WebSocket` —— 函数内局部 import 会让它把 ws 当 query 参数,握手直接 403(实测)。
from fastapi import FastAPI, WebSocket

from karvyloop.relay import MAX_FRAME_BYTES

logger = logging.getLogger(__name__)

MAX_ROOMS = 256
_RID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# 控制面错误码(text 帧 {"t":"error","code":...})
ERR_CONSOLE_OFFLINE = "console_offline"   # 主人不在线
ERR_ROOM_BUSY = "room_busy"               # 已有一个 client 在房里
ERR_TOO_MANY_ROOMS = "too_many_rooms"
ERR_BAD_RID = "bad_rid"
ERR_FRAME_TOO_LARGE = "frame_too_large"
ERR_PEER_OFFLINE = "peer_offline"         # 非致命:对端暂时不在,帧被丢弃


class _Room:
    """一个 rendezvous 房间:至多 1 个 console + 1 个 client。纯内存,空了即删。"""
    __slots__ = ("console", "client")

    def __init__(self) -> None:
        self.console = None   # WebSocket | None
        self.client = None    # WebSocket | None


def _ctl(t: str, **kw) -> str:
    return json.dumps({"t": t, **kw}, ensure_ascii=False)


async def _say(ws, t: str, **kw) -> None:
    """尽力发一条控制帧(对端可能正在关,失败无所谓)。"""
    try:
        await ws.send_text(_ctl(t, **kw))
    except Exception:
        pass


def build_relay_app() -> FastAPI:
    """构造 relay FastAPI app(每个 app 自己的房间表;无全局态,可并排起多份)。"""
    app = FastAPI(title="karvyloop-relay", docs_url=None, redoc_url=None, openapi_url=None)
    rooms: Dict[str, _Room] = {}
    app.state.rooms = rooms
    # 测试探针:fn(direction:"c2s"|"s2c", data:bytes) —— 生产恒 None;
    # 双端环回测试用它断言 relay 转发的每一帧都是密文(信使真的没拆信)。
    app.state.forward_hook = None

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "rooms": len(rooms)}

    async def _accept_checked(ws: WebSocket) -> Optional[str]:
        """accept + 校验 rid;不合法 → 报错关闭返 None。"""
        await ws.accept()
        rid = ws.query_params.get("rid", "")
        if not _RID_RE.match(rid):
            await _say(ws, "error", code=ERR_BAD_RID)
            await ws.close(code=1008)
            return None
        return rid

    async def _pump(ws: WebSocket, room: _Room, *, is_console: bool) -> None:
        """收帧循环:text=控制面(ping/pong);bytes=盲转发到对端。"""
        direction = "s2c" if is_console else "c2s"   # console 发出的帧流向 client
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            text = msg.get("text")
            if text is not None:
                # 控制面:只认 ping;别的 text 一律忽略(不解析、不转发 —— 数据面只走 binary)
                try:
                    if json.loads(text).get("t") == "ping":
                        await _say(ws, "pong")
                except Exception:
                    pass
                continue
            data = msg.get("bytes")
            if data is None:
                continue
            if len(data) > MAX_FRAME_BYTES:
                await _say(ws, "error", code=ERR_FRAME_TOO_LARGE, limit=MAX_FRAME_BYTES)
                await ws.close(code=1009)
                return
            peer = room.client if is_console else room.console
            if peer is None:
                await _say(ws, "error", code=ERR_PEER_OFFLINE)
                continue
            hook = app.state.forward_hook
            if hook is not None:
                try:
                    hook(direction, data)
                except Exception:
                    pass
            # 盲转发:原样 bytes,一个字节都不看;对端正巧在关 → 报 peer_offline,不拖垮本端
            try:
                await peer.send_bytes(data)
            except Exception:
                await _say(ws, "error", code=ERR_PEER_OFFLINE)

    @app.websocket("/attach")
    async def attach(ws: WebSocket) -> None:
        """console 出站长连挂进房间(重连顶掉旧连接)。"""
        rid = await _accept_checked(ws)
        if rid is None:
            return
        if rid not in rooms and len(rooms) >= MAX_ROOMS:
            await _say(ws, "error", code=ERR_TOO_MANY_ROOMS)
            await ws.close(code=1013)
            return
        room = rooms.setdefault(rid, _Room())
        if room.console is not None:      # 心跳断了重连进来 → 新连接接管
            old, room.console = room.console, None
            try:
                await old.close(code=1012)
            except Exception:
                pass
        room.console = ws
        if room.client is not None:
            await _say(ws, "peer", online=True)
        try:
            await _pump(ws, room, is_console=True)
        except Exception:
            pass
        finally:
            if room.console is ws:
                room.console = None
                if room.client is not None:   # 主人掉线 → 明确告诉 client,让它重连再试
                    await _say(room.client, "error", code=ERR_CONSOLE_OFFLINE)
                    try:
                        await room.client.close(code=1013)
                    except Exception:
                        pass
            if room.console is None and room.client is None:
                rooms.pop(rid, None)

    @app.websocket("/join")
    async def join(ws: WebSocket) -> None:
        """client(手机等)入房;主人不在线 → 明确报 console_offline。"""
        rid = await _accept_checked(ws)
        if rid is None:
            return
        room = rooms.get(rid)
        if room is None or room.console is None:
            await _say(ws, "error", code=ERR_CONSOLE_OFFLINE,
                       msg="owner offline — start `karvyloop console --relay ...` first")
            await ws.close(code=1013)
            return
        if room.client is not None:
            await _say(ws, "error", code=ERR_ROOM_BUSY)
            await ws.close(code=1013)
            return
        room.client = ws
        if room.console is not None:
            await _say(room.console, "peer", online=True)
        try:
            await _pump(ws, room, is_console=False)
        except Exception:
            pass
        finally:
            if room.client is ws:
                room.client = None
                if room.console is not None:
                    await _say(room.console, "peer", online=False)
            if room.console is None and room.client is None:
                rooms.pop(rid, None)

    return app


def cmd_relay_serve(host: str = "0.0.0.0", port: int = 8767) -> int:
    """`karvyloop relay-serve`:一条命令自部署(VPS 上直接跑;只见密文,公网可绑)。"""
    try:
        import uvicorn
    except ImportError as e:  # pragma: no cover
        sys.stderr.write(f"relay-serve needs uvicorn (base dependency missing?): {e}\n")
        return 1
    sys.stderr.write(
        f"[karvyloop relay] stateless blind-forwarding relay on {host}:{port} — "
        "endpoints: /attach (console, outbound) /join (client); "
        "frames are end-to-end encrypted, this relay only ever sees ciphertext\n")
    sys.stderr.flush()
    try:
        uvicorn.run(build_relay_app(), host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        pass
    except OSError as e:
        sys.stderr.write(f"[karvyloop relay] bind failed: {e}\n")
        return 1
    return 0


__all__ = ["build_relay_app", "cmd_relay_serve", "MAX_ROOMS",
           "ERR_CONSOLE_OFFLINE", "ERR_ROOM_BUSY", "ERR_TOO_MANY_ROOMS",
           "ERR_BAD_RID", "ERR_FRAME_TOO_LARGE", "ERR_PEER_OFFLINE"]
