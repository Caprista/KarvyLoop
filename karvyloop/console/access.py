"""console/access.py — 本地访问令牌(跨设备访问 console 的鉴权门)。

**为什么**:一旦你为了从手机/另一台电脑访问而把 console 绑到 `0.0.0.0` 或内网 IP,同一局域网里
任何知道 `IP:端口` 的人**无需口令**就能跟小卡聊天(=在你机器上跑代码、用你的 key)、读删你的数据。
"绑 localhost/LAN 即安全边界"是错的:LAN 不是边界。

**方案**:本机(loopback)请求**免 token**(本地零摩擦);**非本机**请求**必须带 token**。token 每次
启动**新生成**(重启即刷新),写进 `~/.karvyloop/console.runtime.json`(0600),`karvyloop url` 可取当前
带 token 的链接。这是"安全是地基不是招牌":内建在请求路径,绕不过;不对外当卖点。
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import time
from pathlib import Path
from typing import Any, Optional

COOKIE = "karvy_token"
HEADER = "x-karvy-token"


def runtime_path() -> Path:
    return Path.home() / ".karvyloop" / "console.runtime.json"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def is_loopback(host: str) -> bool:
    """本机(免 token)判定。"""
    h = (host or "").strip().lower()
    if h in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"):
        return True
    return h.startswith("127.")


def _lan_ip() -> str:
    """尽力探测本机 LAN IP(绑 0.0.0.0 时拼出别的设备能访问的链接)。探不到 → 空(不真发包,只选路由)。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def access_urls(host: str, port: int, token: str) -> dict:
    """{local, remote}:local=本机免 token 链接;remote=带 token 的跨设备链接(绑非 loopback 时才有)。"""
    local = f"http://localhost:{port}/"
    remote = ""
    if host in ("0.0.0.0", "::", ""):
        ip = _lan_ip()
        if ip:
            remote = f"http://{ip}:{port}/?token={token}"
    elif not is_loopback(host):
        remote = f"http://{host}:{port}/?token={token}"
    return {"local": local, "remote": remote, "host": host, "port": port}


def write_runtime(token: str, host: str, port: int) -> None:
    p = runtime_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"token": token, "host": host, "port": port, "pid": os.getpid(), "started_at": time.time()}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(p, 0o600)   # 只有你自己能读 token(POSIX;Windows 忽略)
    except Exception:
        pass


def read_runtime() -> Optional[dict]:
    p = runtime_path()
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("token") else None
    except Exception:
        return None


def token_from_request(request: Any) -> str:
    """从 query ?token= / cookie / header 三处任取(link 首次带 query → 之后落 cookie 免带)。"""
    try:
        q = request.query_params.get("token")
        if q:
            return q
    except Exception:
        pass
    try:
        c = request.cookies.get(COOKIE)
        if c:
            return c
    except Exception:
        pass
    try:
        return request.headers.get(HEADER) or ""
    except Exception:
        return ""


def token_ok(supplied: str, expected: str) -> bool:
    if not expected or not supplied:
        return False
    return secrets.compare_digest(str(supplied), str(expected))


__all__ = [
    "COOKIE", "HEADER", "runtime_path", "new_token", "is_loopback", "access_urls",
    "write_runtime", "read_runtime", "token_from_request", "token_ok",
]
