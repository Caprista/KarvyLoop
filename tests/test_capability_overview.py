"""test_capability_overview — 能力合一清单(P3-d).

病根(全盘 review):两套能力系统各说各话 —— 工具走 capability 决策链(模式下限),
技能走 grants(信任/联网/完整性锁),审计"谁能干什么"要拼两处。
不变量:① /api/capability/overview 一次给全(tools×required_mode + skills×trust/net/lock)
② 未在策略表的工具标 full(fail-closed 语义如实呈现)③ untrusted 技能带锁状态
④ 前端技能面板真渲染这张表(不是 backend self-hype)。
"""
from __future__ import annotations

import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def test_overview_shape_and_mode_floors():
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    client = TestClient(app)
    r = client.get("/api/capability/overview")
    assert r.status_code == 200
    body = r.json()
    assert "tools" in body and "skills" in body
    tools = {t["name"]: t for t in body["tools"]}
    # 内建工具在场,且模式下限如实(read=只读,bash/write=工作区写)
    assert tools["read_file"]["required_mode"] == "read_only"
    assert tools["write_file"]["required_mode"] == "workspace_write"
    assert tools["run_command"]["required_mode"] == "workspace_write"
    assert tools["web_search"]["required_mode"] == "read_only"
    # no_llm 下技能列表为空但字段仍在(前端不炸)
    assert isinstance(body["skills"], list)


def test_frontend_renders_overview():
    """前端真接线(不是 backend self-hype):skills 面板带能力总览卡 + i18n en/zh 齐。"""
    src = (ROOT / "karvyloop" / "console" / "frontend" / "src" / "skills_panel.ts").read_text(encoding="utf-8")
    assert "/api/capability/overview" in src
    assert "_openCapabilityOverview" in src and "_renderCapabilityOverviewCard" in src
    built = (ROOT / "karvyloop" / "console" / "static" / "skills_panel.js").read_text(encoding="utf-8")
    assert "/api/capability/overview" in built, "构建产物没带总览(没 npm run build?)"
    i18n = (ROOT / "karvyloop" / "console" / "static" / "i18n.js").read_text(encoding="utf-8")
    for k in ("capov.name", "capov.tools_title", "capov.lock_mismatch", "capov.trust_untrusted"):
        assert i18n.count(f'"{k}"') == 2, f"i18n {k} 不是 en+zh 各一份"
