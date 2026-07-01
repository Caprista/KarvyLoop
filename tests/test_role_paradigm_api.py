"""范式可见可编(docs/00 §2.4):编辑页能看见+改完整七层范式,不再 write-once。

不变量:① GET /role/paradigm 暴露七层(含 ②a seeded 尽责契约)② POST 能改 SOUL/USER/COMMITMENT/VERIFY/
IDENTITY(原先只创建时能填)③ MEMORY(运行时)/COMPOSITION(走 atom/skill)不可在此改 ④ 编辑往返一致。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from karvyloop.console import build_console_app
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.atoms.registry import AtomRegistry
from karvyloop.roles.registry import RoleRegistry


@pytest.fixture
def client(tmp_path):
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.console.tasks import TaskRegistry
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.atom_registry = AtomRegistry()
    app.state.role_registry = RoleRegistry(tmp_path / "roles", atom_registry=app.state.atom_registry)
    app.state.domain_registry = BusinessDomainRegistry()
    app.state.task_registry = TaskRegistry()
    return TestClient(app)


def test_paradigm_exposes_all_seven_layers(client):
    """以前 GET /api/roles 只返 identity/atoms/skills，看不到灵魂层；现在全暴露。"""
    client.app.state.role_registry.create("analyst", identity="你是分析师", soul="严谨求证")
    r = client.get("/api/role/paradigm", params={"role_id": "analyst"}).json()
    assert r["ok"] is True
    pm = r["paradigm"]
    assert pm["identity"] == "你是分析师" and pm["soul"] == "严谨求证"
    for k in ("identity", "soul", "user", "memory", "commitment", "verify", "atom_ids", "skill_ids"):
        assert k in pm
    # COMMITMENT 里有 ②a seeded 的尽责契约
    assert "resourceful subordinate" in pm["commitment"]
    assert set(pm["editable_slots"]) == {"IDENTITY", "SOUL", "USER", "COMMITMENT", "VERIFY"}


def test_paradigm_edit_round_trip(client):
    """SOUL/COMMITMENT 原先 write-once，现在能改且读回一致。"""
    client.app.state.role_registry.create("analyst", identity="x")
    assert client.post("/api/role/paradigm/update",
                       json={"role_id": "analyst", "slot": "SOUL", "text": "新的性格原则"}).json()["ok"]
    assert client.post("/api/role/paradigm/update",
                       json={"role_id": "analyst", "slot": "COMMITMENT", "text": "我承诺把活干到底"}).json()["ok"]
    pm = client.get("/api/role/paradigm", params={"role_id": "analyst"}).json()["paradigm"]
    assert pm["soul"] == "新的性格原则" and pm["commitment"] == "我承诺把活干到底"


def test_paradigm_edit_rejects_readonly_and_unknown(client):
    """MEMORY(运行时)/COMPOSITION(走 atom/skill)/未知 slot → 改不了。"""
    client.app.state.role_registry.create("analyst", identity="x")
    for bad in ("MEMORY", "COMPOSITION", "NOPE", ""):
        assert client.post("/api/role/paradigm/update",
                           json={"role_id": "analyst", "slot": bad, "text": "y"}).json()["ok"] is False


def test_role_update_can_change_atoms(client):
    """全范式编辑器:/api/role/update 现在能改 atom_ids(此前只有 identity/model/skills,改不了可用原子)。"""
    areg = client.app.state.role_registry.atoms if hasattr(client.app.state.role_registry, "atoms") else client.app.state.atom_registry
    areg.create("web_search", kind="task", prompt="")
    areg.create("read_file", kind="task", prompt="")
    client.app.state.role_registry.create("analyst", identity="x", atom_ids=["web_search"])
    # 改成 [read_file] —— 换掉可用原子
    r = client.post("/api/role/update", json={"role_id": "analyst", "atom_ids": ["read_file"]}).json()
    assert r["ok"] is True
    pm = client.get("/api/role/paradigm", params={"role_id": "analyst"}).json()["paradigm"]
    assert pm["atom_ids"] == ["read_file"], pm["atom_ids"]


def test_role_in_domain_merges_paradigm_and_governance(client):
    """#4:域内角色只读合并视图 —— 原生范式 + 本域 value.md/deontic 一次拿到(此前 UI 看不到域治理)。"""
    client.app.state.role_registry.create("设计师", identity="你是设计师", soul="以用户为中心")
    cid = client.post("/api/domain/create", json={
        "name": "装修工作室", "value_md": "诚实第一;用户利益至上", "agents": ["设计师"]}).json()["id"]
    r = client.get("/api/role/in_domain", params={"role_id": "设计师", "domain_id": cid}).json()
    assert r["ok"] is True and r["domain_name"] == "装修工作室"
    assert r["paradigm"]["identity"] == "你是设计师"      # 原生范式(角色自己的)
    assert "诚实第一" in r["value_md"]                     # 本域继承的 value.md
    assert set(r["deontic"].keys()) == {"forbid", "oblige", "permit"}  # deontic 三段
    # 角色/域不存在 → ok=False
    assert client.get("/api/role/in_domain", params={"role_id": "ghost", "domain_id": cid}).json()["ok"] is False
    assert client.get("/api/role/in_domain", params={"role_id": "设计师", "domain_id": "nope"}).json()["ok"] is False


def test_paradigm_missing_role(client):
    assert client.get("/api/role/paradigm", params={"role_id": "ghost"}).json()["ok"] is False
    assert client.post("/api/role/paradigm/update",
                       json={"role_id": "ghost", "slot": "SOUL", "text": "y"}).json()["ok"] is False


def test_registry_read_paradigm_and_update_soul_units(tmp_path):
    """RoleRegistry 层单测:read_paradigm 读七层、update_soul 只动可编辑槽。"""
    areg = AtomRegistry()
    reg = RoleRegistry(tmp_path / "roles", atom_registry=areg)
    reg.create("r", identity="ident", soul="s", user_desc="u")
    pm = reg.read_paradigm("r")
    assert pm["identity"] == "ident" and pm["soul"] == "s" and pm["user"] == "u"
    assert reg.update_soul("r", "VERIFY", "验证:产出必须带出处") is True
    assert reg.read_paradigm("r")["verify"] == "验证:产出必须带出处"
    assert reg.update_soul("r", "MEMORY", "x") is False        # 运行时只读
    assert reg.update_soul("r", "COMPOSITION", "x") is False   # 走 atom/skill
    assert reg.update_soul("ghost", "SOUL", "x") is False      # 角色不存在
    assert reg.read_paradigm("ghost") is None
