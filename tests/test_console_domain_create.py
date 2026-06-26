"""test_console_domain_create — 建业务域流(M3+ 拍 9.2c)。

设计:docs/26 §C + docs/18。用**真实** BusinessDomainRegistry 端到端验"真做业务域对话"。

AC 矩阵:
- AC1: /api/domain/create → 真 registry 建域
- AC2: 建的域出现在 /api/peers(入职 agent 可选)
- AC3: 切到它 → governance_text 含该域 value.md
- AC4: 端到端 — 建域 → 切场 → intent 注入该域 value.md 进慢脑
- AC5: value.md 自动补 `# 价值观` 前缀(D2)
- AC6: 无 registry → ok:False;AC7: 前端建域按钮
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.cognition.conversation import ConversationManager, ConversationStore  # noqa: E402
from karvyloop.domain.registry import BusinessDomainRegistry  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


@pytest.fixture
def registry() -> BusinessDomainRegistry:
    return BusinessDomainRegistry()  # 真 registry(进程内)


@pytest.fixture
def app(tmp_path, registry):
    mgr = ConversationManager(
        ConversationStore(tmp_path / "conv"), domain_registry=registry,
    )
    mgr.start()
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    a.state.conversation_manager = mgr
    a.state.domain_registry = registry
    return a


# ---- AC1: 建域 ----


def test_create_domain(app):
    client = TestClient(app)
    r = client.post("/api/domain/create", json={
        "name": "装修工作室", "value_md": "诚实第一;用户利益至上;不夸大", "agent": "设计师",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["id"]
    assert body["name"] == "装修工作室"


# ---- AC2: 建的域出现在 peers ----


def test_created_domain_appears_in_peers(app):
    client = TestClient(app)
    client.post("/api/domain/create", json={
        "name": "财务部", "value_md": "合规至上;数字精确", "agent": "会计",
    })
    peers = client.get("/api/peers").json()["peers"]
    biz = [p for p in peers if not p["is_private"] and not p.get("is_group")]
    assert len(biz) == 1
    assert biz[0]["domain_name"] == "财务部"
    assert biz[0]["agent_id"] == "会计"
    # ch4:新域也出一条 域群(群场)
    assert any(p.get("is_group") and p["domain_name"] == "财务部" for p in peers)


# ---- AC3: 切到它 → governance 含 value.md ----


def test_switch_to_created_domain_has_governance(app):
    client = TestClient(app)
    cid = client.post("/api/domain/create", json={
        "name": "法务部", "value_md": "依法依规;风险前置", "agent": "律师",
    }).json()["id"]
    r = client.post("/api/peer/switch", json={"domain_id": cid, "role": "agent", "agent_id": "律师"})
    assert r.json()["ok"] is True
    mgr = app.state.conversation_manager
    gov = mgr.governance_text()
    assert "法务部" in gov
    assert "依法依规" in gov


# ---- AC4: 端到端 — 建域 → 切场 → intent 注入 value.md ----


def test_end_to_end_create_switch_intent_injects_value_md(app, monkeypatch):
    from karvyloop.cli.main_loop import Brain
    import karvyloop.console.routes as routes_mod

    seen = {}

    async def fake_drive(intent, ml, *, ctx=None, governance="", **kw):
        from karvyloop.workbench.main_loop_bridge import DriveOutcome
        seen["governance"] = governance
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="ok",
                            skill_name="", fast_brain_hit=False, crystallized=False, task_id="t")

    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)
    # 需要 main_loop 非 None 才走 drive
    app.state.main_loop = object()
    client = TestClient(app)
    cid = client.post("/api/domain/create", json={
        "name": "营销组", "value_md": "真诚沟通;不骚扰用户", "agent": "文案",
    }).json()["id"]
    client.post("/api/peer/switch", json={"domain_id": cid, "role": "agent", "agent_id": "文案"})
    client.post("/api/intent", json={"intent": "写个推广文案"})
    assert "营销组" in seen["governance"]
    assert "真诚沟通" in seen["governance"]


# ---- AC5: value.md 自动补前缀 ----


def test_value_md_prefix_auto_added(app):
    client = TestClient(app)
    cid = client.post("/api/domain/create", json={
        "name": "X", "value_md": "原则甲;原则乙", "agent": "a",  # 未带 # 价值观
    }).json()["id"]
    reg = app.state.domain_registry
    d = reg.get(cid)
    assert d.value_md.text.startswith("# 价值观")
    assert "原则甲" in d.value_md.text


# ---- AC6: 无 registry → ok:False ----


def test_create_no_registry(tmp_path):
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    # 不设 domain_registry(默认 None)
    client = TestClient(a)
    r = client.post("/api/domain/create", json={"name": "x", "value_md": "y;z", "agent": "a"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


# ---- AC7: 前端建域按钮 ----


def test_static_has_domain_create_button():
    html = (ROOT / "karvyloop" / "console" / "static" / "index.html").read_text(encoding="utf-8")
    assert "domain-new-btn" in html
    js = (ROOT / "karvyloop" / "console" / "static" / "app.js").read_text(encoding="utf-8")
    assert "newDomain" in js
    assert "/api/domain/create" in js
