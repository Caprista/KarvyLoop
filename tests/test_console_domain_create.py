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


# ---- 多角色建域(Hardy:建域要能加多个角色)----


def test_create_domain_multiple_agents(app):
    client = TestClient(app)
    r = client.post("/api/domain/create", json={
        "name": "装修队", "value_md": "质量第一;按时交付", "agents": ["设计师", "监理", "项目经理"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["agents"] == ["设计师", "监理", "项目经理"]      # 多角色原样返回
    assert body["agent"] == "设计师"                            # back-compat:首个
    # 三个角色都真成为成员(member_query 每个角色一个 agent 子句 → resolve_members 逐个解析)
    reg = app.state.domain_registry
    members = reg.resolve_members(body["id"])
    agent_ids = {m.agent_id for m in members if m.role == "agent"}
    assert {"设计师", "监理", "项目经理"} <= agent_ids


def test_create_domain_dedup_and_blank_agents(app):
    """agents 去空、去重保序;与旧的单 agent 字段合并(agents 在前)。"""
    client = TestClient(app)
    r = client.post("/api/domain/create", json={
        "name": "测试域", "value_md": "x", "agents": ["a", "", "b", "a"], "agent": "b",
    })
    body = r.json()
    assert body["ok"] is True
    assert body["agents"] == ["a", "b"]   # 空丢掉、重复(a/b)只留一次、保序


# ---- 编辑域成员:角色多选 → 后端重建 member_query 且保留 user 子句(Hardy:不手编 DSL)----


def test_update_domain_members_via_agents_preserves_user(app):
    client = TestClient(app)
    cid = client.post("/api/domain/create", json={
        "name": "施工组", "value_md": "安全", "agents": ["设计师"], "created_by_user": "hardy",
    }).json()["id"]
    # 用 agents 改成员(加监理、去设计师)→ 后端重建,user:hardy 必须保留(用户没碰它)
    r = client.post("/api/domain/update", json={"domain_id": cid, "agents": ["监理", "项目经理"]})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    reg = app.state.domain_registry
    d = reg.get(cid)
    assert d.member_query.startswith("user:hardy"), d.member_query   # 域主子句保留
    assert "agent:监理" in d.member_query and "agent:项目经理" in d.member_query
    assert "agent:设计师" not in d.member_query                       # 去掉的真去掉
    # resolve_members 真把两个角色解析成成员
    agent_ids = {m.agent_id for m in reg.resolve_members(cid) if m.role == "agent"}
    assert {"监理", "项目经理"} <= agent_ids


def test_update_domain_value_only_leaves_members(app):
    """只改 value_md(不传 agents)→ 成员 member_query 不动。"""
    client = TestClient(app)
    cid = client.post("/api/domain/create", json={
        "name": "财务组", "value_md": "合规", "agents": ["会计"],
    }).json()["id"]
    before = app.state.domain_registry.get(cid).member_query
    client.post("/api/domain/update", json={"domain_id": cid, "value_md": "合规;精确"})
    assert app.state.domain_registry.get(cid).member_query == before   # agents=None → 成员不变


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
    from karvyloop.runtime.main_loop import Brain
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


# ---- AC7: 前端建域走「业务域」面板(冗余的 "🏢 建域" nav 按钮已移除,Hardy)----


def test_static_domain_create_via_panel():
    html = (ROOT / "karvyloop" / "console" / "static" / "index.html").read_text(encoding="utf-8")
    assert "domain-new-btn" not in html               # 冗余的历史遗产按钮已删
    assert 'data-i18n="nav.domains"' in html           # 入口 = 左导航「业务域」面板
    js = (ROOT / "karvyloop" / "console" / "static" / "app.js").read_text(encoding="utf-8")
    assert "openDomainsPanel" in js                    # 「业务域」面板(里面能新建业务域)
    assert "newDomain" in js and "/api/domain/create" in js   # 建域能力仍在
