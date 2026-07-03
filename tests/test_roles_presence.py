"""test_roles_presence — P1.5 灵魂后端口①②:工位区聚合 API + role_presence WS 事件。

契约(前端并行开发,形状冻结):
- GET /api/roles/presence → {"roles":[{"role_id","display","domain_id",
  "status":"busy|idle","running","last_activity_ts","last_task":{"id","intent"}|null}]}
- WS `role_presence`:任务 start/done/error 在既有 task_status 广播点顺势推
  该角色**单行** presence(同上单个 role 形状),不新开轮询。

AC:
- AC1 纯聚合:真 TaskRegistry 数据 → busy/idle、running 计数、last_task 截断 80 字
- AC2 归属:role 命中注册角色 / who 命中花名 / l0 无 role → 小卡;group 不归属
- AC3 API:角色库全角色 + 小卡各一行(没任务 = idle),domain_id 来自域成员解析
- AC4 WS:start(running)→ busy 单行推送;finish → idle 单行推送;形状同 API
- AC5 group 任务不推 role_presence(归不了属,诚实跳过)
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from karvyloop.console import build_console_app
from karvyloop.console.task_events import (
    WS_TYPE_ROLE_PRESENCE,
    make_task_change_sink,
    presence_row_for_task,
    roles_for_presence,
)
from karvyloop.console.tasks import (
    KARVY_ROLE_ID,
    TaskRegistry,
    aggregate_presence,
    match_task_role,
    presence_row,
)
from karvyloop.karvy.observer import WorkbenchObserver

PRESENCE_KEYS = {"role_id", "display", "domain_id", "status", "running",
                 "last_activity_ts", "last_task"}


# ---- AC1/AC2: 纯聚合(零 IO) ----

def test_presence_row_busy_and_intent_truncation():
    reg = TaskRegistry()
    tid = reg.start(who="小宏(宏观分析师)", domain_id="dom-x", role="analyst",
                    intent="宏" * 200)
    row = presence_row("analyst", "小宏(宏观分析师)", "dom-x", reg.list())
    assert set(row) == PRESENCE_KEYS
    assert row["status"] == "busy" and row["running"] == 1
    assert row["last_task"]["id"] == tid
    assert len(row["last_task"]["intent"]) == 80   # 截断 80 字
    assert row["last_activity_ts"] is not None


def test_presence_row_idle_after_finish_and_empty():
    reg = TaskRegistry()
    tid = reg.start(who="小宏", domain_id="dom-x", role="analyst", intent="分析")
    reg.finish(tid, result="done")
    row = presence_row("analyst", "小宏", "dom-x", reg.list())
    assert row["status"] == "idle" and row["running"] == 0
    assert row["last_task"]["id"] == tid          # idle 也带最近任务(工位可回看)
    empty = presence_row("designer", "小美", "dom-x", [])
    assert empty["status"] == "idle" and empty["last_task"] is None
    assert empty["last_activity_ts"] is None


def test_match_task_role_attribution():
    role_ids = {"analyst", KARVY_ROLE_ID}
    disp = {"小宏(宏观分析师)": "analyst", "小卡": KARVY_ROLE_ID}
    assert match_task_role({"role": "analyst", "domain_id": "d"}, role_ids, disp) == "analyst"
    # @ 命中路径:who 写的是花名 → 反查归属
    assert match_task_role({"role": "agent", "who": "小宏(宏观分析师)", "domain_id": "d"},
                           role_ids, disp) == "analyst"
    # l0 无 role → 小卡
    assert match_task_role({"role": "", "who": "小卡", "domain_id": "l0"},
                           role_ids, disp) == KARVY_ROLE_ID
    # group(圆桌/工作流)/ 未知角色 → 不硬塞
    assert match_task_role({"role": "group", "domain_id": "d"}, role_ids, disp) is None
    assert match_task_role({"role": "stranger", "who": "??", "domain_id": "d"},
                           role_ids, disp) is None


def test_aggregate_presence_every_role_gets_a_row():
    reg = TaskRegistry()
    reg.start(who="小卡", domain_id="l0", role="", intent="聊天")
    t2 = reg.start(who="小宏", domain_id="dom-x", role="analyst", intent="盯宏观")
    reg.finish(t2, result="ok")
    reg.start(who="🎡 圆桌", domain_id="dom-x", role="group", intent="圆桌")  # 不归属
    roles = [
        {"role_id": KARVY_ROLE_ID, "display": "小卡", "domain_id": "l0"},
        {"role_id": "analyst", "display": "小宏", "domain_id": "dom-x"},
        {"role_id": "designer", "display": "小美", "domain_id": "dom-x"},
    ]
    rows = {r["role_id"]: r for r in aggregate_presence(roles, reg.list())}
    assert set(rows) == {KARVY_ROLE_ID, "analyst", "designer"}   # 全角色都在场
    assert rows[KARVY_ROLE_ID]["status"] == "busy" and rows[KARVY_ROLE_ID]["running"] == 1
    assert rows["analyst"]["status"] == "idle" and rows["analyst"]["last_task"]["intent"] == "盯宏观"
    assert rows["designer"]["status"] == "idle" and rows["designer"]["last_task"] is None


# ---- AC3: GET /api/roles/presence(真 registries) ----

@pytest.fixture
def presence_client(tmp_path):
    from karvyloop.atoms.registry import AtomRegistry
    from karvyloop.roles.registry import RoleRegistry

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.atom_registry = AtomRegistry()
    app.state.role_registry = RoleRegistry(tmp_path / "roles",
                                           atom_registry=app.state.atom_registry)
    from karvyloop.domain.registry import BusinessDomainRegistry
    app.state.domain_registry = BusinessDomainRegistry()
    app.state.task_registry = TaskRegistry()
    return TestClient(app)


def test_api_roles_presence_contract_shape(presence_client):
    c = presence_client
    c.app.state.role_registry.create("analyst", identity="宏观分析",
                                     nickname="小宏", title="宏观分析师")
    r = c.post("/api/domain/create",
               json={"name": "投研", "value_md": "", "agent": "analyst"})
    dom_id = r.json().get("id", "")
    treg = c.app.state.task_registry
    treg.start(who="小宏(宏观分析师)", domain_id=dom_id or "dom-x",
               role="analyst", intent="盯美联储议息" + "x" * 100)
    treg.start(who="小卡", domain_id="l0", role="", intent="闲聊")

    body = c.get("/api/roles/presence").json()
    rows = {r["role_id"]: r for r in body["roles"]}
    # 小卡(l0)也算一行
    assert KARVY_ROLE_ID in rows and rows[KARVY_ROLE_ID]["domain_id"] == "l0"
    assert rows[KARVY_ROLE_ID]["status"] == "busy"
    a = rows["analyst"]
    assert set(a) == PRESENCE_KEYS
    assert a["display"] == "小宏(宏观分析师)"      # display_name():花名(职务)
    if dom_id:
        assert a["domain_id"] == dom_id            # 域成员解析出所属域
    assert a["status"] == "busy" and a["running"] == 1
    assert len(a["last_task"]["intent"]) <= 80


def test_api_roles_presence_no_registries_still_karvy_row():
    """未接 role/task registry(--no-llm 类)→ 至少小卡一行,idle,不崩。"""
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    c = TestClient(app)
    body = c.get("/api/roles/presence").json()
    rows = {r["role_id"]: r for r in body["roles"]}
    assert KARVY_ROLE_ID in rows
    assert rows[KARVY_ROLE_ID]["status"] == "idle"
    assert rows[KARVY_ROLE_ID]["last_task"] is None


# ---- AC4/AC5: WS role_presence(折叠进既有 task_status 广播点) ----

class _FakeWs:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_json(self, obj) -> None:
        self.sent.append(obj)


class _FakeApp:
    def __init__(self) -> None:
        class _State:
            pass
        self.state = _State()
        self.state.ws_clients = set()


@pytest.mark.asyncio
async def test_ws_role_presence_on_start_and_finish():
    app = _FakeApp()
    c1 = _FakeWs()
    app.state.ws_clients = {c1}
    reg = TaskRegistry(on_change=make_task_change_sink(app, None))
    app.state.task_registry = reg   # presence 聚合读它(与 API 同口径)

    tid = reg.start(who="小卡", domain_id="l0", role="", intent="写周报")
    await asyncio.sleep(0); await asyncio.sleep(0)
    pres = [m["payload"] for m in c1.sent if m["type"] == WS_TYPE_ROLE_PRESENCE]
    assert pres, "start 必须顺势推该角色单行 presence"
    assert set(pres[-1]) == PRESENCE_KEYS          # 单行形状 = API 单个 role 形状
    assert pres[-1]["role_id"] == KARVY_ROLE_ID
    assert pres[-1]["status"] == "busy" and pres[-1]["running"] == 1

    reg.finish(tid, result="写完了")
    await asyncio.sleep(0); await asyncio.sleep(0)
    pres = [m["payload"] for m in c1.sent if m["type"] == WS_TYPE_ROLE_PRESENCE]
    assert pres[-1]["status"] == "idle" and pres[-1]["running"] == 0
    assert pres[-1]["last_task"]["id"] == tid


@pytest.mark.asyncio
async def test_ws_step_event_does_not_emit_role_presence():
    """中途 step/blocked 不改 busy/idle → 不推 presence(契约:只在 start/done/error)。"""
    app = _FakeApp()
    c1 = _FakeWs()
    app.state.ws_clients = {c1}
    reg = TaskRegistry(on_change=make_task_change_sink(app, None))
    app.state.task_registry = reg
    tid = reg.start(who="小卡", domain_id="l0", role="", intent="干活")
    await asyncio.sleep(0); await asyncio.sleep(0)
    n_after_start = len([m for m in c1.sent if m["type"] == WS_TYPE_ROLE_PRESENCE])
    assert n_after_start == 1
    reg.add_event(tid, "step", "第一步完成")
    reg.add_event(tid, "blocked", "卡住了")
    await asyncio.sleep(0); await asyncio.sleep(0)
    assert len([m for m in c1.sent if m["type"] == WS_TYPE_ROLE_PRESENCE]) == n_after_start
    reg.finish(tid, error="挂了")
    await asyncio.sleep(0); await asyncio.sleep(0)
    pres = [m["payload"] for m in c1.sent if m["type"] == WS_TYPE_ROLE_PRESENCE]
    assert len(pres) == 2 and pres[-1]["status"] == "idle"   # error 终态照推


@pytest.mark.asyncio
async def test_ws_group_task_does_not_emit_role_presence():
    app = _FakeApp()
    c1 = _FakeWs()
    app.state.ws_clients = {c1}
    reg = TaskRegistry(on_change=make_task_change_sink(app, None))
    app.state.task_registry = reg
    reg.start(who="🎡 圆桌", domain_id="dom-x", role="group", intent="圆桌")
    await asyncio.sleep(0); await asyncio.sleep(0)
    assert not [m for m in c1.sent if m["type"] == WS_TYPE_ROLE_PRESENCE]
    # task_status 照旧推(0 回归)
    assert [m for m in c1.sent if m["type"] == "task_status"]


def test_presence_row_for_task_and_roles_snapshot_fail_soft():
    """无 role_registry/domain_registry 的 app(--no-llm / 测试桩)→ 只剩小卡行,不崩。"""
    app = _FakeApp()
    roles = roles_for_presence(app)
    assert roles[0]["role_id"] == KARVY_ROLE_ID
    row = presence_row_for_task(app, {"role": "", "who": "小卡", "domain_id": "l0",
                                      "id": "t1", "intent": "x", "status": "running",
                                      "started": 1.0})
    assert row is not None and row["role_id"] == KARVY_ROLE_ID
    assert presence_row_for_task(app, {"role": "group", "domain_id": "d"}) is None
