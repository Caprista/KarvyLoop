"""test_mcp_hotload — MCP 热加载验收(docs/96 刀1:消灭「装完要重启」)。

真桩 server(本机子进程,不打外网;沿用 test_mcp_client/test_mcp_remote 先例):

- H1: reconnect 连真 stdio 桩 server → 工具真注入 runtime_kwargs["mcp_tools"],能调
- H2: 重连幂等 + 换组塞**新 dict**(老 dict 原样 —— 在跑的 drive 握着老引用不受影响)
- H3: 按 server fail-loud:坏 server 明说(name+原因),好 server 照常装
- H4: **正在跑的 tool call 不被重连打断**(worker 线程跨循环调用中途 reconnect,
      调用照常跑完;旧组排水后才关)
- H5: REST 门到门(带 lifespan 的 TestClient):/api/mcp/server/add 贴 URL →
      响应 connected=True + 工具已注入(无需重启);/api/mcp/reconnect 幂等;
      连不上的 server 端点如实报 connected=False+原因
- H6: shutdown 收干净(无僵尸子进程)
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import psutil
import pytest
import yaml

pytest.importorskip("mcp")

from karvyloop.console.mcp_manager import McpConnectionManager  # noqa: E402

FAKE_TOKEN = "sk-FAKE-DO-NOT-LEAK-hotload-0123456789"


# ---------- helpers:真 stdio 桩 server(先例:tests/test_mcp_client.py)----------

def _write_server(td: Path, *, name: str, tool_defs: str) -> Path:
    server_src = (
        "from mcp.server.fastmcp import FastMCP\n"
        f'mcp = FastMCP("test-{name}")\n'
        "\n"
        f"{tool_defs}\n"
        "\n"
        'if __name__ == "__main__":\n'
        '    mcp.run(transport="stdio")\n'
    )
    p = td / f"{name}_server.py"
    p.write_text(server_src, encoding="utf-8")
    return p


def _echo_tools() -> str:
    return textwrap.dedent("""\
        @mcp.tool()
        def echo(text: str) -> str:
            \"\"\"echo back the text verbatim\"\"\"
            return f"echo: {text}"
    """)


def _slow_tools() -> str:
    return textwrap.dedent("""\
        @mcp.tool()
        def slow_echo(text: str) -> str:
            \"\"\"echo after a 2s nap (in-flight window for reconnect tests)\"\"\"
            import time
            time.sleep(2.0)
            return f"slow: {text}"
    """)


def _write_cfg(td: Path, servers: list[dict]) -> str:
    p = td / "config.yaml"
    p.write_text(yaml.safe_dump({"mcp": {"servers": servers}}, allow_unicode=True),
                 encoding="utf-8")
    return str(p)


def _stdio_entry(name: str, script: Path) -> dict:
    return {"name": name, "command": sys.executable, "args": [str(script)]}


def _mgr(**kw) -> McpConnectionManager:
    """测试用小常数:排水静默 0.3s、硬上限 15s(生产默认 180s/30min)。"""
    kw.setdefault("connect_timeout_s", 60.0)
    kw.setdefault("drain_quiet_s", 0.3)
    kw.setdefault("drain_max_s", 15.0)
    kw.setdefault("drain_poll_s", 0.05)
    return McpConnectionManager(**kw)


def _count_children() -> int:
    return len(psutil.Process(os.getpid()).children(recursive=True))


# ============ H1:reconnect 连真桩 → 工具注入 rk,能调 ============

async def test_h1_reconnect_injects_tools_no_restart(tmp_path: Path):
    script = _write_server(tmp_path, name="hot1", tool_defs=_echo_tools())
    cfgp = _write_cfg(tmp_path, [_stdio_entry("hot1", script)])
    rk: dict = {}
    mgr = _mgr()
    try:
        res = await mgr.reconnect(cfgp, runtime_kwargs=rk)
        assert res["ok"] is True
        assert [c["name"] for c in res["connected"]] == ["hot1"]
        assert "mcp_hot1_echo" in rk["mcp_tools"]          # 注入真发生,不用重启
        r = await rk["mcp_tools"]["mcp_hot1_echo"]({"text": "live"})
        assert r.ok is True and "echo: live" in r.payload  # 装上即用
        st = mgr.status()
        assert st["connected"] == {"hot1": ["mcp_hot1_echo"]}
    finally:
        await mgr.shutdown()


# ============ H2:重连幂等 + 换组=新 dict(老 dict 原样) ============

async def test_h2_reconnect_idempotent_and_swaps_new_dict(tmp_path: Path):
    script = _write_server(tmp_path, name="hot2", tool_defs=_echo_tools())
    cfgp = _write_cfg(tmp_path, [_stdio_entry("hot2", script)])
    rk: dict = {}
    mgr = _mgr()
    try:
        await mgr.reconnect(cfgp, runtime_kwargs=rk)
        old_dict = rk["mcp_tools"]
        old_tool = old_dict["mcp_hot2_echo"]
        res2 = await mgr.reconnect(cfgp, runtime_kwargs=rk)    # 幂等:同 config 再连
        assert res2["ok"] is True and res2["tool_names"] == ["mcp_hot2_echo"]
        # 换组塞的是**新 dict**:老 dict 对象没被原地改(在跑的 drive 握着它)
        assert rk["mcp_tools"] is not old_dict
        assert set(old_dict) == {"mcp_hot2_echo"}
        assert old_dict["mcp_hot2_echo"] is old_tool
        # 新组工具是新的会话(旧组已退休)
        assert rk["mcp_tools"]["mcp_hot2_echo"] is not old_tool
        r = await rk["mcp_tools"]["mcp_hot2_echo"]({"text": "again"})
        assert r.ok is True and "echo: again" in r.payload
    finally:
        await mgr.shutdown()


# ============ H3:按 server fail-loud(坏的明说,好的照常) ============

async def test_h3_per_server_fail_loud(tmp_path: Path):
    good = _write_server(tmp_path, name="good", tool_defs=_echo_tools())
    bad = tmp_path / "bad_server.py"
    bad.write_text("import sys; sys.exit(3)\n", encoding="utf-8")   # 起来就死,连不上
    cfgp = _write_cfg(tmp_path, [_stdio_entry("good", good), _stdio_entry("badsrv", bad)])
    rk: dict = {}
    mgr = _mgr(connect_timeout_s=30.0)
    try:
        res = await mgr.reconnect(cfgp, runtime_kwargs=rk)
        assert res["ok"] is False                               # 有失败就不算全 ok
        assert [c["name"] for c in res["connected"]] == ["good"]
        (f,) = res["failed"]
        assert f["name"] == "badsrv" and f["reason"].strip()    # 连不上哪个明说+原因
        assert "mcp_good_echo" in rk["mcp_tools"]               # 好 server 照常装上
        assert not any(k.startswith("mcp_badsrv_") for k in rk["mcp_tools"])
        assert mgr.status()["failed"] == [f]                    # 状态灯数据源同口径
    finally:
        await mgr.shutdown()


# ============ H4:正在跑的 tool call 不被重连打断 ============

async def test_h4_inflight_call_survives_reconnect(tmp_path: Path):
    script = _write_server(tmp_path, name="slowsrv", tool_defs=_slow_tools())
    cfgp = _write_cfg(tmp_path, [_stdio_entry("slowsrv", script)])
    rk: dict = {}
    mgr = _mgr()
    try:
        await mgr.reconnect(cfgp, runtime_kwargs=rk)
        old_dict = rk["mcp_tools"]
        tool = old_dict["mcp_slowsrv_slow_echo"]
        # 真实形态:agent 在 worker 线程的另一个 asyncio.run 循环里调用,跨循环桥回主循环
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, lambda: asyncio.run(tool({"text": "inflight"})))
        await asyncio.sleep(0.5)                                # 确认调用已在途(server 睡 2s)
        cfgp2 = _write_cfg(tmp_path, [])                        # 换组:新 config 清空
        res2 = await mgr.reconnect(cfgp2, runtime_kwargs=rk)
        assert res2["ok"] is True and rk["mcp_tools"] == {}     # 新 drive 拿到新(空)组
        r = await fut                                           # 在途调用照常跑完,没被掐
        assert r.ok is True and "slow: inflight" in r.payload
        # 旧组排水后自行关断(quiet 0.3s):等 retiring 归零
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and mgr.status()["retiring"]:
            await asyncio.sleep(0.1)
        assert mgr.status()["retiring"] == 0                    # 排水完成,不用等 shutdown
    finally:
        await mgr.shutdown()


# ============ H5:REST 门到门(带 lifespan;remote 桩 server) ============

def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


_HTTP_SERVER_SRC = '''
import sys
from mcp.server.fastmcp import FastMCP

port = int(sys.argv[1])
mcp = FastMCP("hotload-remote", host="127.0.0.1", port=port)

@mcp.tool()
def remote_echo(text: str) -> str:
    """echo back with a remote marker"""
    return "remote: " + text

mcp.run(transport="streamable-http")
'''


@pytest.fixture()
def _bypass_system_proxy(monkeypatch):
    """连的是本机桩 server,绕开系统代理(先例:tests/test_mcp_remote.py)。"""
    import httpx._utils as _hu
    monkeypatch.setattr(_hu, "getproxies", lambda: {}, raising=True)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def http_stub_server(tmp_path):
    port = _free_port()
    script = tmp_path / "http_server.py"
    script.write_text(_HTTP_SERVER_SRC, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(script), str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    import socket
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"桩 server 提前退出 rc={proc.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    else:
        proc.kill()
        raise RuntimeError("桩 server 没起来")
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_h5_rest_add_hotloads_and_reconnect_idempotent(tmp_path, _bypass_system_proxy,
                                                       http_stub_server):
    """门到门:贴 URL 加 server → 响应 connected=True + 工具已注入(无需重启);
    /api/mcp/reconnect 幂等;GET /mcp/presets 带状态灯数据(mcp_status)。"""
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("lang: en\n", encoding="utf-8")
    app.state.config_path = str(cfg)
    with TestClient(app) as c:      # with = 跑 lifespan → mcp_manager 在,主循环常驻
        body = c.post("/api/mcp/server/add",
                      json={"url": http_stub_server, "name": "hotrest"}).json()
        assert body["ok"] is True and body["connected"] is True
        assert body["requires_restart"] is False                 # 装上即用,不再要求重启
        assert body["tools"] == ["mcp_hotrest_remote_echo"]
        rk = app.state.runtime_kwargs
        assert "mcp_hotrest_remote_echo" in (rk.get("mcp_tools") or {})   # 真注入
        # GET:状态灯数据源
        got = c.get("/api/mcp/presets").json()
        assert got["hot_reload"] is True and got["requires_restart"] is False
        assert got["mcp_status"]["connected"] == {"hotrest": ["mcp_hotrest_remote_echo"]}
        # 手动重连:幂等
        rec = c.post("/api/mcp/reconnect").json()
        assert rec["ok"] is True and rec["requires_restart"] is False
        assert [x["name"] for x in rec["connected"]] == ["hotrest"]
        assert rec["tool_names"] == ["mcp_hotrest_remote_echo"]


def test_h5b_rest_add_unreachable_fails_loud(tmp_path, _bypass_system_proxy):
    """连不上的 server:端点如实报 connected=False + 原因(config 已保存,不假装成功)。"""
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("lang: en\n", encoding="utf-8")
    app.state.config_path = str(cfg)
    dead_port = _free_port()        # 没人听的端口 → 连接被拒,fail fast
    with TestClient(app) as c:
        body = c.post("/api/mcp/server/add",
                      json={"url": f"http://127.0.0.1:{dead_port}/mcp",
                            "name": "deadsrv"}).json()
        assert body["ok"] is False and body["connected"] is False
        assert body["saved"] is True                     # 配置已落盘(诚实分开报)
        assert body["reason"].strip()                    # 连不上明说原因
        assert FAKE_TOKEN not in str(body)
        got = c.get("/api/mcp/presets").json()
        assert [f["name"] for f in got["mcp_status"]["failed"]] == ["deadsrv"]


# ============ H6:shutdown 收干净(无僵尸子进程) ============

async def test_h6_shutdown_no_zombie_subprocess(tmp_path: Path):
    before = _count_children()
    script = _write_server(tmp_path, name="hot6", tool_defs=_echo_tools())
    cfgp = _write_cfg(tmp_path, [_stdio_entry("hot6", script)])
    mgr = _mgr()
    rk: dict = {}
    await mgr.reconnect(cfgp, runtime_kwargs=rk)
    assert _count_children() > before                    # 子进程真起了
    await mgr.shutdown()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _count_children() > before:
        await asyncio.sleep(0.2)
    assert _count_children() <= before                   # 收干净,无僵尸
