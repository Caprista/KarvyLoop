"""test_workflow_console — ch4 workflow 模式:@多人→规划 DAG→执行(上游喂下游)。

AC:
- AC1 /workflow/plan:@多人 → 返 plan,每步补齐角色身份(display/agent_id/domain_id)
- AC2 /workflow/plan:<2 角色 → 拒
- AC3 /workflow/run:执行 DAG(monkeypatch drive)→ 各步 done + 记进对话(data.workflow)+ 同步 task
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
from karvyloop.domain.registry import Address, BusinessDomainRegistry  # noqa: E402
from karvyloop.console.tasks import TaskRegistry  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.karvy.workflow_store import WorkflowStore  # noqa: E402


@pytest.fixture
def app(tmp_path):
    reg = BusinessDomainRegistry()
    d1 = reg.create(name="产品", created_by="user:ch", value_md_raw="", member_query="user:ch AND agent:产品经理")
    d2 = reg.create(name="设计", created_by="user:ch", value_md_raw="", member_query="user:ch AND agent:设计师")
    mgr = ConversationManager(ConversationStore(tmp_path / "c"), domain_registry=reg)
    mgr.start()
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    a.state.conversation_manager = mgr
    a.state.domain_registry = reg
    a.state.task_registry = TaskRegistry()
    a.state.workflow_store = WorkflowStore(tmp_path / "wf.json")
    a.state.main_loop = object()
    a.state.runtime_kwargs = {"gateway": object(), "model_ref": "x", "workspace_root": "/"}
    mgr.set_peer(Address(domain_id="l0", role="group", agent_id=""))   # 大群
    return a, mgr, reg, d1, d2


def _ms(d1, d2):
    return [{"agent_id": "产品经理", "domain_id": d1.id}, {"agent_id": "设计师", "domain_id": d2.id}]


# ---- AC1: plan 补齐角色身份 ----
def test_workflow_plan(app):
    a, mgr, reg, d1, d2 = app
    body = TestClient(a).post("/api/workflow/plan",
                              json={"intent": "做个登录页", "mentions": _ms(d1, d2)}).json()
    assert body["ok"] is True
    steps = body["plan"]["steps"]
    assert len(steps) >= 2
    aids = {s["agent_id"] for s in steps}
    assert "产品经理" in aids and "设计师" in aids
    for s in steps:                       # 每步补齐身份,能驱动
        assert s["agent_id"] and s["domain_id"] and s["display"] and "id" in s


# ---- AC2: <2 角色拒 ----
def test_workflow_plan_needs_two(app):
    a, mgr, reg, d1, d2 = app
    body = TestClient(a).post("/api/workflow/plan",
                              json={"intent": "x", "mentions": [{"agent_id": "产品经理", "domain_id": d1.id}]}).json()
    assert body["ok"] is False


# ---- AC3: run 执行 DAG + 记录 ----
def test_workflow_run(app, monkeypatch):
    a, mgr, reg, d1, d2 = app
    import karvyloop.console.routes as routes_mod
    from karvyloop.cli.main_loop import Brain
    from karvyloop.workbench.main_loop_bridge import DriveOutcome

    async def fake_drive(intent, ml, *, persona=None, **kw):
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="这一步的产出",
                            skill_name="", fast_brain_hit=False, crystallized=False, task_id="t")
    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)

    plan = {"goal": "做个登录页", "steps": [
        {"id": "s1", "display": "产品经理", "agent_id": "产品经理", "domain_id": d1.id, "task": "写需求", "depends_on": []},
        {"id": "s2", "display": "设计师", "agent_id": "设计师", "domain_id": d2.id, "task": "出设计", "depends_on": ["s1"]},
    ]}
    body = TestClient(a).post("/api/workflow/run", json={"plan": plan}).json()
    assert body["ok"] is True
    st = {s["id"]: s["status"] for s in body["workflow"]["steps"]}
    assert st == {"s1": "done", "s2": "done"}
    assert body["conversation_id"]
    # 记进对话(data.workflow)→ 重开渲染
    cur = mgr.current()
    assert any(t.data and t.data.get("workflow") for t in cur.turns)
    # 同步首页 task
    assert any("工作流" in (tk.get("who") or "") for tk in a.state.task_registry.list())
    # 2a:落成一条**独立「工作流」会话线**(role=workflow),左栏出卡 + 可重开追问
    rl = body["run_line"]
    assert rl and rl["role"] == "workflow" and rl["kind"] == "workflow"
    assert rl["title"] == "做个登录页" and rl["origin_group"]   # 主题 + 发起群名
    assert body["conversation_id"] == rl["conversation_id"]     # 追问跳工作流线,不是群线
    # 这条线可被 set_peer 重开(追问 = 切到它,上下文齐)
    from karvyloop.domain import Address
    reopened = mgr.set_peer(Address(domain_id=rl["domain_id"], role="workflow", agent_id=rl["agent_id"]))
    assert reopened.id == rl["conversation_id"]
    assert any(t.data and t.data.get("workflow") for t in reopened.turns)   # 全文+结构在工作流线里
    # 2d:/api/lines 把它列进「工作流」区(主题 + 发起群)
    c = TestClient(a)
    lines = c.get("/api/lines").json()
    wfs = lines["workflows"]
    assert any(w["agent_id"] == rl["agent_id"] and w["title"] == "做个登录页" for w in wfs)
    # 2e:/api/line/open 重开这条工作流线(上下文齐)
    op = c.post("/api/line/open", json={"role": "workflow", "domain_id": rl["domain_id"],
                                        "agent_id": rl["agent_id"]}).json()
    assert op["ok"] and op["conversation_id"] == rl["conversation_id"]
    assert any(t.get("data") and t["data"].get("workflow") for t in op["turns"])
    # 2c:X 掉 → 从 /api/lines 消失;恢复 → 回来
    c.post("/api/line/hide", json={"domain_id": rl["domain_id"], "role": "workflow", "agent_id": rl["agent_id"]})
    assert not any(w["agent_id"] == rl["agent_id"] for w in c.get("/api/lines").json()["workflows"])
    # 重开自动恢复显示
    c.post("/api/line/open", json={"role": "workflow", "domain_id": rl["domain_id"], "agent_id": rl["agent_id"]})
    assert any(w["agent_id"] == rl["agent_id"] for w in c.get("/api/lines").json()["workflows"])
    # 2e:料里追问 = 按 conv_id 定位真 peer 再开(此前切群+resume 找不到工作流线 → 没上下文)
    ob = c.post("/api/line/open_by_conv", json={"conversation_id": rl["conversation_id"]}).json()
    assert ob["ok"] and ob["is_run_line"] and ob["kind"] == "workflow"
    assert ob["role"] == "workflow" and ob["conversation_id"] == rl["conversation_id"]
    assert any(t.get("data") and t["data"].get("workflow") for t in ob["turns"])   # 上下文齐


# ---- 沉淀:结晶 → 快脑匹配复用 ----
def test_crystallize_then_match_reuses(app):
    a, mgr, reg, d1, d2 = app
    c = TestClient(a)
    plan = {"goal": "做个登录页", "steps": [
        {"id": "s1", "display": "产品经理", "agent_id": "产品经理", "domain_id": d1.id, "task": "写需求", "depends_on": []},
        {"id": "s2", "display": "设计师", "agent_id": "设计师", "domain_id": d2.id, "task": "出设计", "depends_on": ["s1"]},
    ]}
    cr = c.post("/api/workflow/crystallize", json={"plan": plan, "name": "登录页流程"}).json()
    assert cr["ok"] is True and cr["template"]["id"]
    # 下次 @ 同类角色做类似事 → 快脑匹配上,提议复用(repoint 到当前角色)
    body = c.post("/api/workflow/plan", json={"intent": "做一个登录页面", "mentions": _ms(d1, d2)}).json()
    assert body["ok"] is True
    # 命中只作为**可选**附带项(matched.plan),默认 plan 是**针对新意图现设计**的 —— 不沿用旧目标
    assert body.get("matched") and body["matched"]["name"] == "登录页流程"
    assert body["matched"]["plan"] and body["matched"]["plan"]["goal"] == "做一个登录页面"  # 套用也用新目标
    assert body["plan"]["goal"] != "做个登录页"   # 默认计划不是旧模板目标(#2:防沿用上一轮)
    aids = {s["agent_id"] for s in body["plan"]["steps"]}
    assert "产品经理" in aids and "设计师" in aids                          # 重指到当前角色


def test_no_match_falls_through_to_fresh(app):
    a, mgr, reg, d1, d2 = app
    # 库里只有"登录页"模板;问个不相干的 → 不匹配 → 走现设计(假 gw → fallback 线性)
    a.state.workflow_store.save(goal="做个登录页", role_keys=["产品经理", "设计师"],
                                steps=[{"id": "s1", "role_key": "产品经理", "task": "x", "depends_on": []}])
    body = TestClient(a).post("/api/workflow/plan",
                              json={"intent": "搞一场年会策划", "mentions": _ms(d1, d2)}).json()
    assert body["ok"] is True and not body.get("matched")   # 没匹配 → 现设计
