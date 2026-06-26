"""test_role_domain_cascade — 角色×域 生命周期接线(docs/00 §2.6 ④⑤ + L4 子域)。

- 子域:domain/create 带 parent_id → create_child(继承父域)。
- 删域:domain/archive 软删 + purge 该域私有认知(共享层/别域不动)。
- 删角色引用守护:被某域 member_query 引用 → 拦(blocked);force 才真删。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.conversation import ConversationManager, ConversationStore  # noqa: E402
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.domain.registry import BusinessDomainRegistry  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.roles.registry import RoleRegistry  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402


@pytest.fixture
def app(tmp_path):
    reg = BusinessDomainRegistry()
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"), domain_registry=reg)
    mgr.start()
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    a.state.conversation_manager = mgr
    a.state.domain_registry = reg
    a.state.memory = MemoryManager()
    a.state.role_registry = RoleRegistry(tmp_path / "roles")
    return a


# ---- L4 子域 ----


def test_create_subdomain_inherits_parent(app):
    c = TestClient(app)
    parent = c.post("/api/domain/create", json={"name": "产品部", "value_md": "用户第一"}).json()
    assert parent["ok"]
    child = c.post("/api/domain/create", json={"name": "增长组", "parent_id": parent["id"]}).json()
    assert child["ok"]
    cd = app.state.domain_registry.get(child["id"])
    assert cd.parent_id == parent["id"]                 # 真的是子域
    assert "用户第一" in cd.value_md.text                # 继承父域 value.md


def test_create_top_level_when_no_parent(app):
    c = TestClient(app)
    d = c.post("/api/domain/create", json={"name": "顶级域"}).json()
    assert app.state.domain_registry.get(d["id"]).parent_id is None


# ---- 删域:软删 + 清域私有认知 ----


def test_archive_domain_purges_private_cognition(app):
    c = TestClient(app)
    did = c.post("/api/domain/create", json={"name": "法务部"}).json()["id"]
    mem = app.state.memory
    mem.write(Belief(content="法务机密", provenance={"source": "t", "applies": {"domain": did}},
                     freshness_ts=1.0, scope="personal"))
    mem.write(Belief(content="共享事实", provenance={"source": "t"}, freshness_ts=1.0, scope="personal"))
    r = c.post("/api/domain/archive", json={"domain_id": did}).json()
    assert r["ok"] and r["purged_cognition"] == 1
    remaining = [b.content for b in mem.index.all("personal")]
    assert "共享事实" in remaining and "法务机密" not in remaining   # 共享留、域私有随域删


# ---- 删角色引用守护 ----


def test_role_remove_blocked_when_referenced(app):
    c = TestClient(app)
    # 建域时入职"设计师"→ member_query 含 agent:设计师 + 物化进角色库
    c.post("/api/domain/create", json={"name": "设计部", "agent": "设计师"})
    r = c.post("/api/role/remove", json={"role_id": "设计师"}).json()
    assert r["ok"] is False and r.get("blocked") is True
    assert any(d["name"] == "设计部" for d in r["referenced_by"])    # 告诉是哪些域


def test_role_remove_force_deletes(app):
    c = TestClient(app)
    c.post("/api/domain/create", json={"name": "设计部", "agent": "设计师"})
    r = c.post("/api/role/remove", json={"role_id": "设计师", "force": True}).json()
    assert r["ok"] is True                                           # force 才真删


def test_role_remove_unreferenced_ok(app):
    c = TestClient(app)
    app.state.role_registry.create("孤立角色", identity="x", atom_ids=[])
    r = c.post("/api/role/remove", json={"role_id": "孤立角色"}).json()
    assert r["ok"] is True and r["referenced_by"] == []             # 没被引用 → 直接删


# ---- P0 审计:编辑能力(此前建错只能删重建)----


def test_domain_update_value_and_members(app):
    c = TestClient(app)
    did = c.post("/api/domain/create", json={"name": "产品部", "value_md": "用户第一"}).json()["id"]
    r = c.post("/api/domain/update", json={"domain_id": did, "value_md": "用户至上;诚实",
                                           "member_query": "user:ch AND agent:pm"}).json()
    assert r["ok"]
    d = app.state.domain_registry.get(did)
    assert "用户至上" in d.value_md.text and "agent:pm" in d.member_query


def test_domain_archive_then_restore(app):
    c = TestClient(app)
    did = c.post("/api/domain/create", json={"name": "临时域"}).json()["id"]
    c.post("/api/domain/archive", json={"domain_id": did})
    assert app.state.domain_registry.get(did).lifecycle == "archived"
    assert c.post("/api/domain/restore", json={"domain_id": did}).json()["ok"]
    assert app.state.domain_registry.get(did).lifecycle == "active"


def test_domain_update_archived_rejected(app):
    c = TestClient(app)
    did = c.post("/api/domain/create", json={"name": "x"}).json()["id"]
    c.post("/api/domain/archive", json={"domain_id": did})
    r = c.post("/api/domain/update", json={"domain_id": did, "value_md": "改不动"}).json()
    assert r["ok"] is False                              # 归档域拒改(先恢复)


def test_domains_list_includes_archived(app):
    c = TestClient(app)
    a = c.post("/api/domain/create", json={"name": "活的"}).json()["id"]
    b = c.post("/api/domain/create", json={"name": "归档的"}).json()["id"]
    c.post("/api/domain/archive", json={"domain_id": b})
    doms = {d["id"]: d for d in c.get("/api/domains").json()["domains"]}
    assert doms[a]["lifecycle"] == "active" and doms[b]["lifecycle"] == "archived"


def test_role_update_identity_and_model(app):
    c = TestClient(app)
    app.state.role_registry.create("设计师", identity="老的人格", atom_ids=[])
    r = c.post("/api/role/update", json={"role_id": "设计师", "identity": "新的人格描述",
                                         "model": "anthropic/claude-sonnet-4-6"}).json()
    assert r["ok"]
    rv = app.state.role_registry.get("设计师")
    assert "新的人格描述" in rv.identity and rv.model == "anthropic/claude-sonnet-4-6"


def test_role_update_missing(app):
    c = TestClient(app)
    assert c.post("/api/role/update", json={"role_id": "不存在", "identity": "x"}).json()["ok"] is False
