"""test_update_apply — 一键升级:localhost 安全门 + 升级 runner 的端口等待逻辑.

完整的"停→装→起"端到端在真机验(VM git clone);这里锁住能单测的:① 非本机来源被拒(且**不**触发
os._exit / 不启动升级器)② runner 的 _port_free/_wait_free 正确。
"""
from __future__ import annotations

import socket
import types

from karvyloop.console.upgrade_runner import _port_free, _wait_free


def _a_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


# ---- 安全门:CSRF 头 + localhost ----
def _fake_request(client_host: str, *, header: str = "1"):
    headers = {"x-karvyloop-upgrade": header} if header is not None else {}
    return types.SimpleNamespace(
        client=types.SimpleNamespace(host=client_host),
        headers=types.SimpleNamespace(get=lambda k, d=None: headers.get(k.lower(), d)),
        app=types.SimpleNamespace(state=types.SimpleNamespace(console_relaunch=None)))


def test_update_apply_rejects_missing_csrf_header():
    """没带升级标记头(防 CSRF)→ 拒,且不触发任何升级动作。"""
    from karvyloop.console.routes import api_update_apply
    out = api_update_apply(_fake_request("127.0.0.1", header=None))
    assert out["ok"] is False and "CSRF" in out["reason"]
    assert "started" not in out


def test_update_apply_rejects_non_localhost():
    from karvyloop.console.routes import api_update_apply
    for bad in ("192.168.1.50", "10.0.0.3", "203.0.113.7"):
        out = api_update_apply(_fake_request(bad))   # 带头但非本机
        assert out["ok"] is False and "localhost" in out["reason"], (bad, out)
    assert "started" not in api_update_apply(_fake_request("8.8.8.8"))


# ---- 并发锁 D3/D6 ----
def test_upgrade_lock_blocks_concurrent(tmp_path):
    from karvyloop.console.routes import _acquire_upgrade_lock
    lock = tmp_path / "_upgrade.lock"
    assert _acquire_upgrade_lock(lock) is True       # 第一次拿到
    assert _acquire_upgrade_lock(lock) is False      # 已持有 → 拒(防双 runner)
    # 陈旧锁(改 mtime 到 11 分钟前)→ 接管
    import os, time
    os.utime(lock, (time.time() - 700, time.time() - 700))
    assert _acquire_upgrade_lock(lock) is True


# ---- runner 端口逻辑 ----
def test_runner_port_free_and_wait():
    p = _a_free_port()
    assert _port_free("127.0.0.1", p) is True
    occ = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occ.bind(("127.0.0.1", p)); occ.listen()
    try:
        assert _port_free("127.0.0.1", p) is False
        assert _wait_free("127.0.0.1", p, secs=0.5) is False   # 一直被占 → 等不到
    finally:
        occ.close()
    assert _wait_free("127.0.0.1", p, secs=1) is True          # 放开后立刻空闲
