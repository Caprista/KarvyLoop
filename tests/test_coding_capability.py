"""test_coding_capability — 内建「Coding」技能露在技能库里 + 外接 coder 可编辑(#1/#3)。

锁:① tools 反映**真实**内建工具(read/write/edit/run + web,不是硬编码假卡);
② 注入的 MCP 工具也露出;③ 实跑执行器**恒 Forge**(诚实:v1.0 不接入外接执行,external_active=False);
④ 外接命令可存/可清,落仓外 coding.json,且不影响 executor/sandboxed(不假装外接已生效)。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.coding import coding_config  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_coding_store(tmp_path, monkeypatch):
    """把 coding.json 重定向到 tmp + 清环境变量 —— 测试绝不碰真实 ~/.karvyloop。"""
    monkeypatch.delenv("CODING_EXTERNAL_EXECUTOR", raising=False)
    monkeypatch.setattr(coding_config, "_store_path", lambda: tmp_path / "coding.json")


def _client(rk=None):
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None, runtime_kwargs=rk or {})
    return TestClient(app)


def test_builtin_tools_reflected():
    r = _client().get("/api/coding/capability").json()
    assert r["name"] == "coding"
    names = {t["name"] for t in r["tools"]}
    assert {"read_file", "write_file", "edit_file", "run_command",
            "web_search", "web_fetch"} <= names
    assert all(t["kind"] == "builtin" for t in r["tools"])
    # 实跑恒 Forge,走沙箱;没配外接 coder
    assert r["executor"] == "forge" and r["sandboxed"] is True
    assert r["external_executor"] is None and r["external_active"] is False


def test_injected_mcp_tools_listed():
    class _FakeMcp:
        name = "mcp_minimax_web_search"
        description = "Search the web via MiniMax."

    rk = {"mcp_tools": {"mcp_minimax_web_search": _FakeMcp()}}
    r = _client(rk).get("/api/coding/capability").json()
    mcp = [t for t in r["tools"] if t["kind"] == "mcp"]
    assert len(mcp) == 1 and mcp[0]["name"] == "mcp_minimax_web_search"
    assert "MiniMax" in mcp[0]["description"]   # 描述真实带出,不是空串


def test_external_coder_editable_but_not_active():
    """#3:外接命令可存/可清;存了之后 capability 反映它,但 executor/sandboxed **不变**
    (诚实:v1.0 只存偏好,实跑还是 Forge —— 不假装外接已接入)。"""
    c = _client()
    # 存
    r = c.post("/api/coding/config", json={"external_executor": "claude -p"}).json()
    assert r["ok"] is True and r["external_executor"] == "claude -p" and r["external_active"] is False
    cap = c.get("/api/coding/capability").json()
    assert cap["external_executor"] == "claude -p"
    assert cap["executor"] == "forge" and cap["sandboxed"] is True   # 实跑没变,诚实
    # 清
    r2 = c.post("/api/coding/config", json={"external_executor": ""}).json()
    assert r2["ok"] is True and r2["external_executor"] is None
    assert c.get("/api/coding/capability").json()["external_executor"] is None
