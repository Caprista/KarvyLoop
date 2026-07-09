"""test_karvy_conversational_tools — 小卡对话里能直接调的能力工具(审计 2026-07-08)。

背景:小卡聊天 drive 此前只拿最小工具集,定时任务/记忆全 REST-only。这里补三件工具,
按 make_instantiate_template_tool 的已验证工厂+注入模式接。锁四层不变量:

① 工具工厂合规:经 build_tool(HR-1)、policy 表下限对(不是默认 FULL 被拒)、schema 有必填字段。
② 工具真跑:create_schedule 真解析 NL→cron + 真写 SchedulerStore;remember_fact 真写 Belief;
   recall_memory 真走 recall_block(grep+overlap,无向量)召回。
③ 诚实边界:缺参/未接 LLM/解析不出/落盘失败 → ok=False 或 warning,不炸不乱建。
④ drive_in_tui 挂载门:小卡人格 + 对应 registry/store → 工具并进 extra_tools;
   业务角色 persona / 没传 store → 不挂(0 回归)。
⑤ 只读闸/catalog:recall_memory 在 deontic 只读豁免 + 三工具名进 BUILTIN_TOOL_NAMES。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.karvy.tools import (  # noqa: E402
    make_create_schedule_tool, make_recall_memory_tool, make_remember_fact_tool)


def _mem():
    from karvyloop.cognition.memory import MemoryManager
    return MemoryManager()


# ---- ① 工厂合规:build_tool + policy 下限 + schema ----

def test_all_three_tools_factory_built_and_policied():
    from karvyloop.capability import Mode
    from karvyloop.capability.policy import required_mode
    from karvyloop.registry.tool import is_factory_built
    from karvyloop.karvy.scheduler import SchedulerStore

    sched = make_create_schedule_tool(scheduler_store=SchedulerStore())
    rem = make_remember_fact_tool(memory=_mem())
    rec = make_recall_memory_tool(memory=_mem())
    for t in (sched, rem, rec):
        assert is_factory_built(t), t.name          # HR-1

    assert sched.name == "create_schedule"
    assert rem.name == "remember_fact"
    assert rec.name == "recall_memory"

    # policy 下限:非默认 FULL,否则一调即 capability_denied
    assert required_mode("create_schedule") == Mode.WORKSPACE_WRITE
    assert required_mode("remember_fact") == Mode.WORKSPACE_WRITE
    assert required_mode("recall_memory") == Mode.READ_ONLY
    assert sched.required_mode == Mode.WORKSPACE_WRITE
    assert rem.required_mode == Mode.WORKSPACE_WRITE
    assert rec.required_mode == Mode.READ_ONLY

    # schema 必填字段
    assert "description" in sched.input_schema["properties"]
    assert sched.input_schema["required"] == ["description"]
    assert "content" in rem.input_schema["properties"]
    assert "query" in rec.input_schema["properties"]


# ---- ② create_schedule 真跑:NL→cron + 真写 store ----

def _fake_parser(description, now_str=""):
    """假 NL→cron 解析器:命中"每天"关键词 → 固定 cron;否则 None(不懂时间)。"""
    if "每天" in description or "daily" in description.lower():
        return {"cron": "0 8 * * *", "intent": "汇总昨天进展", "title": "每日汇总", "target_role": ""}
    return None


def test_create_schedule_parses_and_creates():
    from karvyloop.karvy.scheduler import SchedulerStore
    store = SchedulerStore()
    tool = make_create_schedule_tool(scheduler_store=store, schedule_parser=_fake_parser)
    res = asyncio.run(tool.call({"description": "每天早上8点把昨天进展汇总给我"}, None, None))
    assert res["ok"], res
    assert res["cron"] == "0 8 * * *"
    # 真进了 store(全系统唯一审计面)
    all_tasks = store.all()
    assert len(all_tasks) == 1
    assert all_tasks[0].cron == "0 8 * * *"
    assert all_tasks[0].id == res["id"]


def test_create_schedule_action_overrides_parsed_intent():
    from karvyloop.karvy.scheduler import SchedulerStore
    store = SchedulerStore()
    tool = make_create_schedule_tool(scheduler_store=store, schedule_parser=_fake_parser)
    res = asyncio.run(tool.call(
        {"description": "每天早上8点", "action": "提醒我喝水"}, None, None))
    assert res["ok"] and res["intent"] == "提醒我喝水"
    assert store.all()[0].intent == "提醒我喝水"


def test_create_schedule_resolves_target_role():
    from karvyloop.karvy.scheduler import SchedulerStore
    store = SchedulerStore()
    resolver = lambda rn: ("dom1", "analyst", "analyst#1", "理财所/分析师")  # noqa: E731
    tool = make_create_schedule_tool(scheduler_store=store, schedule_parser=_fake_parser,
                                     target_resolver=resolver)
    res = asyncio.run(tool.call(
        {"description": "每天8点", "target_role": "分析师"}, None, None))
    assert res["ok"], res
    t = store.all()[0]
    assert t.target_domain == "dom1" and t.target_role == "analyst" and t.target_agent_id == "analyst#1"


def test_create_schedule_honest_failures():
    from karvyloop.karvy.scheduler import SchedulerStore
    store = SchedulerStore()
    # 缺 description
    tool = make_create_schedule_tool(scheduler_store=store, schedule_parser=_fake_parser)
    r1 = asyncio.run(tool.call({}, None, None))
    assert not r1["ok"] and "description" in r1["reason"]
    # 解析不出明确时间 → 拒(不瞎编)
    r2 = asyncio.run(tool.call({"description": "有空的时候弄一下"}, None, None))
    assert not r2["ok"] and r2["reason"]
    assert len(store.all()) == 0          # 什么都没建
    # 没接 LLM(parser=None)→ 诚实回,不炸
    tool_nolm = make_create_schedule_tool(scheduler_store=store, schedule_parser=None)
    r3 = asyncio.run(tool_nolm.call({"description": "每天8点"}, None, None))
    assert not r3["ok"] and "LLM" in r3["reason"]


# ---- ② remember_fact 真写 Belief ----

def test_remember_fact_writes_belief():
    mem = _mem()
    tool = make_remember_fact_tool(memory=mem)
    res = asyncio.run(tool.call({"content": "用户讨厌开早会", "title": "偏好"}, None, None))
    assert res["ok"] and res["persisted"] is True
    # 真进了个人库,且能被召回(证明 provenance/freshness 合法写入)
    beliefs = list(mem.index.all("personal"))
    assert any(b.content == "用户讨厌开早会" for b in beliefs)
    assert beliefs[0].provenance["source"] == "karvy_chat"


def test_remember_fact_honest_failures():
    mem = _mem()
    tool = make_remember_fact_tool(memory=mem)
    r1 = asyncio.run(tool.call({"content": "   "}, None, None))
    assert not r1["ok"] and "content" in r1["reason"]
    # memory 未接 → 诚实回,不炸
    tool_no = make_remember_fact_tool(memory=None)
    r2 = asyncio.run(tool_no.call({"content": "x"}, None, None))
    assert not r2["ok"] and "memory" in r2["reason"]


def test_remember_fact_reports_persist_failure_honestly():
    """落盘失败(write 返 False)→ ok=True 但 persisted=False + warning(fail-loud,不假装存上了)。"""
    class _FailMem:
        persist_error = "OSError: disk full"
        def write(self, belief, *, pinned=False):
            return False
    tool = make_remember_fact_tool(memory=_FailMem())
    res = asyncio.run(tool.call({"content": "x"}, None, None))
    assert res["ok"] and res["persisted"] is False and "落盘" in res["warning"]


# ---- ② recall_memory 真走 recall_block(无向量)----

def test_recall_memory_finds_written_fact():
    mem = _mem()
    asyncio.run(make_remember_fact_tool(memory=mem).call(
        {"content": "用户的预算上限是每月5000元"}, None, None))
    rec = make_recall_memory_tool(memory=mem)
    res = asyncio.run(rec.call({"query": "预算"}, None, None))
    assert res["ok"] and res["found"] is True
    assert "5000" in res["memory"]


def test_recall_memory_empty_when_nothing():
    rec = make_recall_memory_tool(memory=_mem())
    res = asyncio.run(rec.call({"query": "根本没记过的东西xyz"}, None, None))
    assert res["ok"] and res["found"] is False
    # 缺 query / 未接 memory
    assert not asyncio.run(rec.call({}, None, None))["ok"]
    assert not asyncio.run(make_recall_memory_tool(memory=None).call({"query": "x"}, None, None))["ok"]


# ---- ⑤ 只读闸 + catalog ----

def test_recall_memory_read_only_exempt_and_catalog():
    from karvyloop.capability.deontic_gate import _READ_ONLY_TOOLS
    from karvyloop.atoms.tool_catalog import BUILTIN_TOOL_NAMES
    assert "recall_memory" in _READ_ONLY_TOOLS
    for n in ("create_schedule", "remember_fact", "recall_memory"):
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
        "帮我建个每天的提醒", _ML(), token=1, sandbox=2, gateway=3, workspace_root="/tmp",
        persona=persona, **kw))
    return captured.get("mcp_tools") or {}


def test_drive_mounts_karvy_tools_for_karvy_persona(monkeypatch):
    from karvyloop.coding.persona import build_karvy_persona_prompt
    from karvyloop.karvy.scheduler import SchedulerStore
    tools = _drive(monkeypatch, persona=build_karvy_persona_prompt(cwd="/w"),
                   scheduler_store=SchedulerStore(), schedule_parser=_fake_parser, memory=_mem())
    assert {"create_schedule", "remember_fact", "recall_memory"} <= set(tools)


def test_drive_mounts_only_available_stores(monkeypatch):
    """只传 memory(没 scheduler)→ 只挂记忆两件;都不传 → 一件不挂(0 回归)。"""
    from karvyloop.coding.persona import build_karvy_persona_prompt
    tools = _drive(monkeypatch, persona=build_karvy_persona_prompt(cwd="/w"), memory=_mem())
    assert set(tools) == {"remember_fact", "recall_memory"}
    tools2 = _drive(monkeypatch, persona=build_karvy_persona_prompt(cwd="/w"))
    assert not ({"create_schedule", "remember_fact", "recall_memory"} & set(tools2))


def test_drive_skips_karvy_tools_for_business_role(monkeypatch):
    """业务角色 persona(无 karvy_self)→ 定时/记忆工具一件不挂(收口在小卡)。"""
    from karvyloop.coding.persona import build_role_persona_prompt
    from karvyloop.karvy.scheduler import SchedulerStore
    tools = _drive(monkeypatch, persona=build_role_persona_prompt("设计师"),
                   scheduler_store=SchedulerStore(), schedule_parser=_fake_parser, memory=_mem())
    assert not ({"create_schedule", "remember_fact", "recall_memory"} & set(tools))


def test_drive_coexists_with_mcp_and_instantiate(monkeypatch):
    """与既有 MCP 工具 + instantiate_domain_template(建 agent 意图)共存不覆盖。"""
    import karvyloop.workbench.main_loop_bridge as bridge
    from karvyloop.coding.persona import build_karvy_persona_prompt
    from karvyloop.karvy.scheduler import SchedulerStore
    captured = {}

    def fake_factory(**fk):
        captured.update(fk)
        return lambda i, *, ctx=None: ("x", None)

    monkeypatch.setattr(bridge, "forge_slow_brain_factory", fake_factory)
    intent = "我要做个帮我盯行业新闻的agent"   # 命中建 agent 意图门 → instantiate 也挂
    asyncio.run(bridge.drive_in_tui(
        intent, _ML(), token=1, sandbox=2, gateway=3, workspace_root="/tmp",
        persona=build_karvy_persona_prompt(cwd="/w", intent=intent),
        mcp_tools={"mcp_x": object()},
        domain_registry=object(), scheduler_store=SchedulerStore(),
        schedule_parser=_fake_parser, memory=_mem()))
    tools = captured.get("mcp_tools") or {}
    assert {"mcp_x", "instantiate_domain_template", "create_schedule",
            "remember_fact", "recall_memory"} <= set(tools)
