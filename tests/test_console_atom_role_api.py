"""原子库 + 角色库管理面 API 验收(P1,拍 9.5 #3)。

端点:/api/atoms /api/atom/create /api/atom/remove /api/roles /api/role/create /api/role/remove。
甲:角色 create 引的原子必须在公共原子库(就地买糖 = 先 atom/create)。
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


def test_task_board_and_detail(client):
    """P2/P3:任务看板列表(摘要)+ 结果文档详情(完整)。"""
    reg = client.app.state.task_registry
    tid = reg.start(who="小卡", domain_id="l0", intent="写个文件")
    reg.finish(tid, result="full result " * 50)
    lst = client.get("/api/tasks").json()["tasks"]
    assert lst and lst[0]["id"] == tid and "result_full" not in lst[0]
    detail = client.get(f"/api/task/{tid}").json()
    assert detail["ok"] is True and detail["task"]["result_full"].startswith("full result")
    assert client.get("/api/task/nope").json()["ok"] is False


def test_domain_create_empty_role_ok(client):
    """9.5 P4:业务域角色可空(先想干啥再定角色)。"""
    r = client.post("/api/domain/create", json={"name": "嘻嘻", "value_md": "", "agent": ""})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_domain_create_with_role(client):
    r = client.post("/api/domain/create", json={"name": "装修", "value_md": "诚信", "agent": "设计师"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["agent"] == "设计师"


def test_domain_create_rejects_duplicate_name(client):
    """建域查重:同名 active 域不让重复建(防注册表被同名域灌满 → 左栏/组织架构变脏)。"""
    r1 = client.post("/api/domain/create", json={"name": "数据组", "value_md": "", "agent": ""})
    assert r1.json()["ok"] is True
    r2 = client.post("/api/domain/create", json={"name": "数据组", "value_md": "", "agent": ""})
    assert r2.status_code == 200 and r2.json()["ok"] is False
    assert "同名" in r2.json()["reason"]
    # 大小写/空格不敏感也算重名
    r3 = client.post("/api/domain/create", json={"name": "  数据组 ", "value_md": "", "agent": ""})
    assert r3.json()["ok"] is False


def test_domain_create_materializes_role_in_library(client):
    """9.5 loop-step1(修 checker 发现的 CRITICAL):入职角色被**物化进角色库**,
    否则 value.md→per-role 编译器永远 fall back。且中文角色名(Unicode role id)也得行。"""
    client.post("/api/domain/create", json={"name": "嘻嘻", "value_md": "诚信为本", "agent": "设计师"})
    roles = client.get("/api/roles").json()["roles"]
    assert any(v["id"] == "设计师" for v in roles)  # 中文角色名进了库 → 编译器能查到
    # 物化的角色目录能被 paradigm 编译器吃(端到端:库里的角色 → 编译 prompt)
    from karvyloop.coding.paradigm_prompt import build_role_paradigm_prompt
    reg = client.app.state.role_registry
    rv = reg.get("设计师")
    cp = build_role_paradigm_prompt(rv, None, intent="x", cwd="/w")
    assert cp is not None and "设计师" in cp.to_text()


def test_atom_create_and_list(client):
    r = client.post("/api/atom/create", json={"atom_id": "web_search", "kind": "task",
                                              "prompt": "搜网", "tools": ["run_command"]})
    assert r.status_code == 200 and r.json()["ok"] is True
    atoms = client.get("/api/atoms").json()["atoms"]
    assert any(a["id"] == "web_search" and a["tools"] == ["run_command"] for a in atoms)


def test_atom_update_edits_prompt_kind_tools(client):
    """#1 原子可编辑(此前只能删了重建):/api/atom/update 改 prompt/kind/tools;id 是引用键不改。"""
    client.post("/api/atom/create", json={"atom_id": "searcher", "kind": "task",
                                          "prompt": "旧提示", "tools": ["run_command"]})
    r = client.post("/api/atom/update", json={"atom_id": "searcher", "prompt": "新提示",
                                              "kind": "daemon", "tools": ["read_file", "web_search"]})
    assert r.status_code == 200 and r.json()["ok"] is True
    a = next(x for x in client.get("/api/atoms").json()["atoms"] if x["id"] == "searcher")
    assert a["prompt"] == "新提示" and a["kind"] == "daemon" and a["tools"] == ["read_file", "web_search"]
    # 只改 prompt(kind/tools 不传 → 不动)
    client.post("/api/atom/update", json={"atom_id": "searcher", "prompt": "再改"})
    a2 = next(x for x in client.get("/api/atoms").json()["atoms"] if x["id"] == "searcher")
    assert a2["prompt"] == "再改" and a2["kind"] == "daemon"   # kind 保留


def test_atom_update_missing_atom(client):
    assert client.post("/api/atom/update", json={"atom_id": "ghost", "prompt": "x"}).json()["ok"] is False


def test_atom_create_bad_id_422(client):
    r = client.post("/api/atom/create", json={"atom_id": "web-search", "kind": "task"})
    assert r.status_code == 422  # 连字符不 COMPOSITION-safe


def test_atom_duplicate_422(client):
    client.post("/api/atom/create", json={"atom_id": "dup", "kind": "task"})
    r = client.post("/api/atom/create", json={"atom_id": "dup", "kind": "task"})
    assert r.status_code == 422


def test_role_create_with_atoms(client):
    client.post("/api/atom/create", json={"atom_id": "a1", "kind": "task"})
    client.post("/api/atom/create", json={"atom_id": "a2", "kind": "task"})
    r = client.post("/api/role/create", json={"role_id": "pm", "identity": "我是PM",
                                              "atom_ids": ["a1", "a2"]})
    assert r.status_code == 200 and r.json()["ok"] is True
    roles = client.get("/api/roles").json()["roles"]
    pm = next(x for x in roles if x["id"] == "pm")
    assert pm["identity"] == "我是PM" and set(pm["atom_ids"]) == {"a1", "a2"}


def test_role_create_unknown_atom_422(client):
    """甲:挑了不存在的原子 → 422(先买糖)。"""
    r = client.post("/api/role/create", json={"role_id": "pm", "atom_ids": ["ghost"]})
    assert r.status_code == 422


def test_agent_import_lands_in_role_library(client):
    """9.5:外部 agent 导入 → adapter 改造 → 落角色库(变成一个 role)。"""
    r = client.post("/api/agent/import", json={
        "role_id": "imported_pm", "source_type": "generic-json",
        "system_prompt": "You are a helpful product manager.",
        "tools": ["read_file", "run_command"],
    })
    assert r.status_code == 200 and r.json()["ok"] is True
    roles = client.get("/api/roles").json()["roles"]
    assert any(v["id"] == "imported_pm" for v in roles)


def test_agent_import_missing_prompt_422(client):
    r = client.post("/api/agent/import", json={"role_id": "x", "tools": []})
    assert r.status_code == 422


def test_role_remove(client):
    client.post("/api/role/create", json={"role_id": "tmp"})
    assert client.post("/api/role/remove", json={"role_id": "tmp"}).json()["ok"] is True
    assert client.post("/api/role/remove", json={"role_id": "tmp"}).json()["ok"] is False


def test_endpoints_graceful_without_registry():
    """没接 registry(裸 app)→ 不 500,返空/ok=False。"""
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    c = TestClient(app)
    assert c.get("/api/atoms").json() == {"atoms": []}
    assert c.get("/api/roles").json() == {"roles": []}


# ============ brick4:role-create API 接花名/职务 ============
def test_role_create_with_nickname_title(client):
    r = client.post("/api/role/create", json={
        "role_id": "designer", "identity": "设计师", "nickname": "张三", "title": "产品经理",
    })
    assert r.status_code == 200 and r.json()["ok"]
    role = r.json()["role"]
    assert role["nickname"] == "张三" and role["title"] == "产品经理"
    assert role["display_name"] == "张三(产品经理)"     # 哟吼/张三(产品经理) 那种
    # 列表里也带上 display_name
    roles = client.get("/api/roles").json()["roles"]
    got = [x for x in roles if x["id"] == "designer"][0]
    assert got["display_name"] == "张三(产品经理)"


def test_role_create_without_nickname_display_falls_back(client):
    client.post("/api/role/create", json={"role_id": "dba"})
    roles = client.get("/api/roles").json()["roles"]
    got = [x for x in roles if x["id"] == "dba"][0]
    assert got["display_name"] == "dba"                # 没花名 → role_id
