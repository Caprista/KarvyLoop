"""test_karvy_role_domain_tools — 小卡从对话里建角色 / 开业务域(2026-07-09)。

背景:小卡聊天 drive 此前只拿定时/记忆几件工具,建角色/开业务域全 REST-only。这里按
已验证的 make_create_schedule_tool 工厂+注入模式补两件工具(create_role/create_domain),
锁四层不变量(与 test_karvy_conversational_tools 同款):

① 工具工厂合规:经 build_tool(HR-1)、policy 表下限 WORKSPACE_WRITE(不是默认 FULL 被拒)、
   schema 有必填字段。
② 工具真跑:create_role 真写 RoleRegistry(落 agent 目录);create_domain 真写
   BusinessDomainRegistry(create / parent→create_child),有 store 时真存盘。
③ 诚实边界:缺参/未接 registry/重名/父域不存在/落盘失败 → ok=False 或 warning,不炸不乱建。
④ drive_in_tui 挂载门:小卡人格 + role_registry/domain_registry → 工具并进 extra_tools;
   业务角色 persona / 没传 registry → 不挂(0 回归)。
⑤ catalog:两工具名进 BUILTIN_TOOL_NAMES(WORKSPACE_WRITE,不进只读豁免)。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.karvy.tools import (  # noqa: E402
    make_create_domain_tool, make_create_role_tool)


def _role_reg(tmp_path):
    from karvyloop.roles.registry import RoleRegistry
    return RoleRegistry(tmp_path / "roles")


def _dom_reg():
    from karvyloop.domain.registry import BusinessDomainRegistry
    return BusinessDomainRegistry()


# ---- ① 工厂合规:build_tool + policy 下限 + schema ----

def test_role_domain_tools_factory_built_and_policied(tmp_path):
    from karvyloop.capability import Mode
    from karvyloop.capability.policy import required_mode
    from karvyloop.registry.tool import is_factory_built

    role_t = make_create_role_tool(role_registry=_role_reg(tmp_path))
    dom_t = make_create_domain_tool(domain_registry=_dom_reg())
    for t in (role_t, dom_t):
        assert is_factory_built(t), t.name          # HR-1

    assert role_t.name == "create_role"
    assert dom_t.name == "create_domain"

    # policy 下限:WORKSPACE_WRITE(非默认 FULL,否则一调即 capability_denied)
    assert required_mode("create_role") == Mode.WORKSPACE_WRITE
    assert required_mode("create_domain") == Mode.WORKSPACE_WRITE
    assert role_t.required_mode == Mode.WORKSPACE_WRITE
    assert dom_t.required_mode == Mode.WORKSPACE_WRITE

    # schema 必填字段
    assert role_t.input_schema["required"] == ["role_id", "identity"]
    assert "identity" in role_t.input_schema["properties"]
    assert dom_t.input_schema["required"] == ["name"]
    assert "forbid" in dom_t.input_schema["properties"]


# ---- ② create_role 真写角色库 ----

def test_create_role_writes_role_dir(tmp_path):
    reg = _role_reg(tmp_path)
    tool = make_create_role_tool(role_registry=reg)
    res = asyncio.run(tool.call(
        {"role_id": "设计师", "identity": "负责视觉设计", "soul": "追求极简",
         "nickname": "小美", "title": "首席设计"}, None, None))
    assert res["ok"], res
    assert res["id"] == "设计师"
    # 真落了一个 agent 目录(7 文件 + COMPOSITION.yaml)
    v = reg.get("设计师")
    assert v is not None
    assert v.identity == "负责视觉设计"
    assert v.nickname == "小美" and v.title == "首席设计"
    # SOUL 真写进去了
    para = reg.read_paradigm("设计师")
    assert para is not None and "追求极简" in para["soul"]


def test_create_role_honest_failures(tmp_path):
    reg = _role_reg(tmp_path)
    tool = make_create_role_tool(role_registry=reg)
    # 缺 role_id
    r1 = asyncio.run(tool.call({"identity": "x"}, None, None))
    assert not r1["ok"] and "role_id" in r1["reason"]
    # 重名 → 拒(不盖旧角色)
    asyncio.run(tool.call({"role_id": "分析师", "identity": "a"}, None, None))
    r2 = asyncio.run(tool.call({"role_id": "分析师", "identity": "b"}, None, None))
    assert not r2["ok"] and r2["reason"]
    assert len(reg) == 1          # 只建了一个
    # 非法名(含空格)→ 拒
    r3 = asyncio.run(tool.call({"role_id": "有 空格", "identity": "x"}, None, None))
    assert not r3["ok"] and r3["reason"]
    # registry 未接 → 诚实回,不炸
    tool_no = make_create_role_tool(role_registry=None)
    r4 = asyncio.run(tool_no.call({"role_id": "x", "identity": "y"}, None, None))
    assert not r4["ok"] and "role_registry" in r4["reason"]


# ---- ② create_domain 真写业务域库 ----

def test_create_domain_creates(tmp_path):
    reg = _dom_reg()
    tool = make_create_domain_tool(domain_registry=reg, created_by_user="ch")
    res = asyncio.run(tool.call(
        {"name": "我的理财所", "value_md": "稳健第一",
         "forbid": ["未经确认直接下单"]}, None, None))
    assert res["ok"], res
    d = reg.get(res["id"])
    assert d is not None and d.name == "我的理财所"
    assert d.created_by == "user:ch"
    assert "user:ch" in d.member_query
    assert "稳健第一" in d.value_md.text
    assert "未经确认直接下单" in d.deontic.forbid


def test_create_domain_child_inherits(tmp_path):
    reg = _dom_reg()
    tool = make_create_domain_tool(domain_registry=reg, created_by_user="ch")
    parent = asyncio.run(tool.call(
        {"name": "投资集团", "value_md": "长期主义", "forbid": ["高杠杆"]}, None, None))
    assert parent["ok"], parent
    child = asyncio.run(tool.call(
        {"name": "股票子部门", "parent_id": parent["id"], "forbid": ["追涨杀跌"]}, None, None))
    assert child["ok"], child
    cd = reg.get(child["id"])
    assert cd.parent_id == parent["id"]
    # 继承父域 value.md + deontic(只能加不能删,D5)
    assert "长期主义" in cd.value_md.text
    assert "高杠杆" in cd.deontic.forbid and "追涨杀跌" in cd.deontic.forbid


def test_create_domain_persists_via_store(tmp_path):
    reg = _dom_reg()
    saved = {}

    class _Store:
        def save_all(self, domains):
            saved["n"] = len(list(domains))

    tool = make_create_domain_tool(domain_registry=reg, domain_store=_Store(),
                                   created_by_user="ch")
    res = asyncio.run(tool.call({"name": "工作室"}, None, None))
    assert res["ok"] and res["persisted"] is True
    assert saved.get("n") == 1


def test_create_domain_reports_persist_failure_honestly(tmp_path):
    """落盘失败 → ok=True 但 persisted=False + warning(fail-loud,不假装存上了)。"""
    reg = _dom_reg()

    class _FailStore:
        def save_all(self, domains):
            raise OSError("disk full")

    tool = make_create_domain_tool(domain_registry=reg, domain_store=_FailStore())
    res = asyncio.run(tool.call({"name": "会丢的域"}, None, None))
    assert res["ok"] and res["persisted"] is False and "落盘" in res["warning"]


def test_create_domain_honest_failures(tmp_path):
    reg = _dom_reg()
    tool = make_create_domain_tool(domain_registry=reg, created_by_user="ch")
    # 缺 name
    r1 = asyncio.run(tool.call({}, None, None))
    assert not r1["ok"] and "name" in r1["reason"]
    # 重名 active 域 → 拒
    asyncio.run(tool.call({"name": "唯一域"}, None, None))
    r2 = asyncio.run(tool.call({"name": "唯一域"}, None, None))
    assert not r2["ok"] and "同名" in r2["reason"]
    assert len(reg.list_active()) == 1
    # 父域不存在 → 拒(不炸)
    r3 = asyncio.run(tool.call({"name": "孤儿子域", "parent_id": "dom-nope"}, None, None))
    assert not r3["ok"] and r3["reason"]
    # registry 未接 → 诚实回,不炸
    tool_no = make_create_domain_tool(domain_registry=None)
    r4 = asyncio.run(tool_no.call({"name": "x"}, None, None))
    assert not r4["ok"] and "domain_registry" in r4["reason"]


# ---- ⑤ catalog ----

def test_role_domain_tools_in_catalog():
    from karvyloop.atoms.tool_catalog import BUILTIN_TOOL_NAMES
    for n in ("create_role", "create_domain"):
        assert n in BUILTIN_TOOL_NAMES, n


# ---- ④ drive_in_tui 挂载门 ----

def _res(text: str):
    return types.SimpleNamespace(
        brain=types.SimpleNamespace(value="slow"), text=text, skill_name="",
        fast_brain_hit=False, crystallized=False, task_id="t", ctx_dependent=False)


class _ML:
    def drive(self, intent, *, slow_brain=None, ctx=None, scope=None, fresh=False):
        return _res("ok")

    def background_review(self):
        pass


def _drive(monkeypatch, *, persona, **kw):
    import karvyloop.workbench.main_loop_bridge as bridge
    captured = {}

    def fake_factory(**fk):
        captured.update(fk)
        return lambda i, *, ctx=None: ("x", None)

    monkeypatch.setattr(bridge, "forge_slow_brain_factory", fake_factory)
    asyncio.run(bridge.drive_in_tui(
        "帮我建个角色", _ML(), token=1, sandbox=2, gateway=3, workspace_root="/tmp",
        persona=persona, **kw))
    return captured.get("mcp_tools") or {}


def test_drive_mounts_role_domain_tools_for_karvy(monkeypatch, tmp_path):
    from karvyloop.coding.persona import build_karvy_persona_prompt
    tools = _drive(monkeypatch, persona=build_karvy_persona_prompt(cwd="/w"),
                   role_registry=_role_reg(tmp_path), domain_registry=_dom_reg())
    assert {"create_role", "create_domain"} <= set(tools)


def test_drive_mounts_only_available_registries(monkeypatch, tmp_path):
    """只传 role_registry → 只挂 create_role;都不传 → 一件不挂(0 回归)。"""
    from karvyloop.coding.persona import build_karvy_persona_prompt
    tools = _drive(monkeypatch, persona=build_karvy_persona_prompt(cwd="/w"),
                   role_registry=_role_reg(tmp_path))
    assert "create_role" in tools and "create_domain" not in tools
    tools2 = _drive(monkeypatch, persona=build_karvy_persona_prompt(cwd="/w"))
    assert not ({"create_role", "create_domain"} & set(tools2))


def test_drive_skips_role_domain_tools_for_business_role(monkeypatch, tmp_path):
    """业务角色 persona(无 karvy_self)→ 建角色/建域工具一件不挂(收口在小卡)。"""
    from karvyloop.coding.persona import build_role_persona_prompt
    tools = _drive(monkeypatch, persona=build_role_persona_prompt("设计师"),
                   role_registry=_role_reg(tmp_path), domain_registry=_dom_reg())
    assert not ({"create_role", "create_domain"} & set(tools))
