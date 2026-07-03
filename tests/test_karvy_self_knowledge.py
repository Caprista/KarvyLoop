"""test_karvy_self_knowledge — 小卡的系统自我认知(建 agent 指导,Hardy 2026-07-02)。

锁四层不变量:
① 内容合同(tool-reality,防吹牛回归):知识块里提到的每个模板/工具/能力**真实存在**
   (模板对 TEMPLATES、工具对 capability policy 表),且诚实边界(observer/先确认后调用)在。
② 意图门:建 agent 类意图 → 注入知识;普通聊天 → 不注入(省 token,0 常驻)。
③ 落地工具:instantiate_domain_template 经 build_tool(HR-1)、policy 表 WORKSPACE_WRITE
   下限,真调 = 真开出域+角色(走真 registry,不造数据)。
④ drive_in_tui 挂载门:小卡人格 + 建 agent 意图 + domain_registry 三者齐 → 工具并进
   extra_tools;任一不齐(业务角色 persona / 普通意图 / 没传 registry)→ 不挂(0 回归)。
⑤ 执行器协议缝(真模型验证抓到):registry build_tool 的 Tool 不满足执行器 CodingTool
   协议(`await tool(inp)` / `.parameters`)→ 必须经 _merge_extra_tools 的适配层;
   锁"经真执行编排层(run_tools)调用真的开出域"——否则工具看得见调不动,每次 is_error。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.karvy.self_knowledge import (  # noqa: E402
    make_instantiate_template_tool, self_knowledge_block, wants_build_guidance)


# ---- ② 意图门 ----

def test_build_intents_hit_gate():
    for s in ("我要做个帮我盯行业新闻的agent", "帮我建个角色管报销", "想搭个班子写公众号",
              "开个理财研究所吧", "建个团队帮我求职", "做个助手盯竞品",
              "can you build an agent for me", "I want to create a role"):
        assert wants_build_guidance(s), s


def test_ordinary_chat_misses_gate():
    for s in ("今天天气怎么样", "你好呀", "帮我看下这段代码为什么报错", "写一首关于秋天的诗", ""):
        assert not wants_build_guidance(s), s


# ---- ① 内容合同(防吹牛):提到的能力必须真实存在 ----

def test_knowledge_templates_match_real_templates():
    """知识块的模板清单从真实 TEMPLATES 动态生成 —— 每个真实模板都在,不多不少不编造。"""
    from karvyloop.domain.templates import TEMPLATES
    block = self_knowledge_block()
    for t in TEMPLATES:
        assert f"template_id={t['id']}" in block, t["id"]
        assert t["name"] in block, t["id"]
    # 不编造:块里出现的 template_id= 数量 == 真实模板数
    assert block.count("template_id=") == len(TEMPLATES)


def test_knowledge_tools_exist_in_policy_table():
    """知识块点名的每个工具都在 capability policy 下限表里(不存在的工具=吹牛)。"""
    from karvyloop.capability.policy import DEFAULT_TOOL_REQUIREMENTS
    block = self_knowledge_block()
    for tool in ("read_file", "write_file", "edit_file", "run_command",
                 "web_search", "web_fetch", "create_atom", "instantiate_domain_template"):
        assert tool in block, f"知识块应提到 {tool}"
        assert tool in DEFAULT_TOOL_REQUIREMENTS, f"{tool} 不在 policy 表(默认 FULL=被拒)"


def test_knowledge_claims_are_grounded():
    """核心概念/边界句真实:instantiate 入口存在、结晶存方法不存答案、observer 边界、先确认后调用。"""
    from karvyloop.domain.templates import instantiate_template, list_templates  # 真入口存在
    assert callable(instantiate_template) and len(list_templates()) >= 5
    block = self_knowledge_block()
    assert "observer" in block                    # K1 边界诚实
    assert "先确认" in block or "必须先确认" in block   # 不擅自开域
    assert "存方法不存答案" in block or "用出来的" in block   # 技能结晶语义
    assert "没有" in block                         # 明令"系统没有的别编"
    # 别发明新词:核心概念只用既有术语
    for term in ("业务域", "角色", "原子", "技能", "工具"):
        assert term in block, term


# ---- ③ 落地工具:真走 instantiate 路径 ----

def test_instantiate_tool_is_factory_built_and_policied():
    from karvyloop.capability import Mode
    from karvyloop.capability.policy import required_mode
    from karvyloop.registry.tool import is_factory_built
    t = make_instantiate_template_tool(domain_registry=object(), role_registry=object())
    assert t.name == "instantiate_domain_template"
    assert is_factory_built(t)                                  # HR-1:经 build_tool
    assert t.required_mode == Mode.WORKSPACE_WRITE
    assert required_mode(t.name) == Mode.WORKSPACE_WRITE        # policy 表下限一致(非 FULL 被拒)
    assert "template_id" in t.input_schema["properties"]


def test_instantiate_tool_really_creates_domain_and_roles(tmp_path):
    """真调工具 = 真开出域+角色(真 registry,不 mock instantiate)。"""
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.roles.registry import RoleRegistry
    roles = RoleRegistry(tmp_path / "roles")
    domains = BusinessDomainRegistry()
    tool = make_instantiate_template_tool(domain_registry=domains, role_registry=roles)
    res = asyncio.run(tool.call({"template_id": "finance-research"}, None, None))
    assert res["ok"], res
    assert domains.get(res["domain_id"]).name == "理财研究所"
    assert roles.get("macro-analyst") is not None
    # 幂等语义透传:同名域再开被拒(如实转告,不静默重复)
    res2 = asyncio.run(tool.call({"template_id": "finance-research"}, None, None))
    assert not res2["ok"] and res2["reason"]


def test_instantiate_tool_refuses_empty_and_unknown():
    """宁空勿毒:无 template_id / 未知模板 → ok=False + reason,不炸不乱建。"""
    tool = make_instantiate_template_tool(domain_registry=object(), role_registry=object())
    res = asyncio.run(tool.call({}, None, None))
    assert not res["ok"] and "template_id" in res["reason"]
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.roles.registry import RoleRegistry
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tool2 = make_instantiate_template_tool(
            domain_registry=BusinessDomainRegistry(), role_registry=RoleRegistry(pathlib.Path(td)))
        res2 = asyncio.run(tool2.call({"template_id": "nope-nope"}, None, None))
        assert not res2["ok"] and res2["reason"]


# ---- ⑤ 执行器协议缝:registry Tool 必须经适配层才能被 agent 执行器调用 ----

def test_merge_adapts_registry_tool_to_agent_protocol(tmp_path):
    """_merge_extra_tools 后:registry Tool 可被 `await tool(inp)` 调、`parameters` 有 schema;
    已满足协议的(MCP 形状,callable)原样不包。这是真模型验证抓到的缝:没有适配层,
    模型每次调用都吃 TypeError('Tool' object is not callable) 的 is_error。"""
    from karvyloop.coding.forge import _merge_extra_tools
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.roles.registry import RoleRegistry
    domains = BusinessDomainRegistry()
    raw = make_instantiate_template_tool(domain_registry=domains,
                                         role_registry=RoleRegistry(tmp_path / "roles"))
    class _McpShaped:  # 已满足 CodingTool 协议 → 不该被包
        name = "mcp_x"
        async def __call__(self, inp):
            return "ok"
        def is_concurrency_safe(self, inp):
            return False
    mcp = _McpShaped()
    tools: dict = {}
    _merge_extra_tools(tools, {"instantiate_domain_template": raw, "mcp_x": mcp},
                       token=None, sandbox=None)
    adapted = tools["instantiate_domain_template"]
    assert callable(adapted), "registry Tool 没被适配成可调用 → 执行器必炸"
    assert tools["mcp_x"] is mcp                                   # 已合规的原样
    assert adapted.parameters.get("properties", {}).get("template_id"), \
        "schema 没透出 → 模型只能盲调"
    assert adapted.is_concurrency_safe({}) is False                # fail-closed 透传
    res = asyncio.run(adapted({"template_id": "personal-research"}))
    assert res["ok"], res                                          # 经适配层真开出域
    assert any(d.name == "个人研究所" for d in domains.list_all())


def test_registry_tool_via_run_tools_really_instantiates(tmp_path):
    """整条执行编排缝:模型发 tool_use → run_tools → 域真开出、不 is_error(J2 真模型曾在此挂)。"""
    from karvyloop.atoms.orchestration import ToolUseBlock, run_tools
    from karvyloop.coding.forge import _merge_extra_tools
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.roles.registry import RoleRegistry
    domains = BusinessDomainRegistry()
    roles = RoleRegistry(tmp_path / "roles")
    tools: dict = {}
    _merge_extra_tools(tools, {"instantiate_domain_template": make_instantiate_template_tool(
        domain_registry=domains, role_registry=roles)}, token=None, sandbox=None)
    blk = ToolUseBlock(id="tu1", name="instantiate_domain_template",
                       input={"template_id": "finance-research"})
    results = asyncio.run(run_tools([blk], tools, None))
    assert not results[0].is_error, results[0].error_reason
    assert results[0].content["ok"], results[0].content
    assert any(d.name == "理财研究所" for d in domains.list_all())
    assert roles.get("macro-analyst") is not None


# ---- 知识注入:persona 层 ----

def test_karvy_persona_injects_knowledge_on_build_intent():
    from karvyloop.coding.persona import build_karvy_persona_prompt
    p = build_karvy_persona_prompt(cwd="/w", intent="我要做个帮我盯行业新闻的agent")
    text = p.to_text()
    assert "自我认知" in text and "template_id=" in text
    assert "instantiate_domain_template" in text
    assert getattr(p, "karvy_self", False) is True


def test_karvy_persona_skips_knowledge_on_ordinary_chat():
    """普通聊天不注入(省 token);无 intent(旧调用方)= 0 回归;marker 始终在。"""
    from karvyloop.coding.persona import build_karvy_persona_prompt
    for kw in ({"intent": "今天天气怎么样"}, {}):
        p = build_karvy_persona_prompt(cwd="/w", **kw)
        text = p.to_text()
        assert "自我认知" not in text and "template_id=" not in text
        assert getattr(p, "karvy_self", False) is True


def test_role_persona_has_no_karvy_marker():
    """业务角色 persona 无 karvy_self 标记 → drive 层不会给它挂开域工具。"""
    from karvyloop.coding.persona import build_role_persona_prompt
    p = build_role_persona_prompt("设计师", domain_name="装修域")
    assert not getattr(p, "karvy_self", False)


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


def _drive(monkeypatch, *, intent, persona, domain_registry, mcp_tools=None):
    import karvyloop.workbench.main_loop_bridge as bridge
    captured = {}

    def fake_factory(**kw):
        captured.update(kw)
        return lambda i, *, ctx=None: ("x", None)

    monkeypatch.setattr(bridge, "forge_slow_brain_factory", fake_factory)
    asyncio.run(bridge.drive_in_tui(
        intent, _ML(), token=1, sandbox=2, gateway=3, workspace_root="/tmp",
        persona=persona, mcp_tools=mcp_tools, domain_registry=domain_registry))
    return captured


def test_drive_mounts_instantiate_tool_for_karvy_build_intent(monkeypatch):
    from karvyloop.coding.persona import build_karvy_persona_prompt
    intent = "我要做个帮我盯行业新闻的agent"
    cap = _drive(monkeypatch, intent=intent,
                 persona=build_karvy_persona_prompt(cwd="/w", intent=intent),
                 domain_registry=object())
    tools = cap.get("mcp_tools") or {}
    assert "instantiate_domain_template" in tools
    # 与既有 MCP 工具共存不覆盖
    cap2 = _drive(monkeypatch, intent=intent,
                  persona=build_karvy_persona_prompt(cwd="/w", intent=intent),
                  domain_registry=object(), mcp_tools={"mcp_x": object()})
    assert set(cap2["mcp_tools"]) >= {"mcp_x", "instantiate_domain_template"}


def test_drive_skips_tool_when_gate_not_met(monkeypatch):
    from karvyloop.coding.persona import (
        build_karvy_persona_prompt, build_role_persona_prompt)
    # 普通聊天 → 不挂
    cap = _drive(monkeypatch, intent="你好",
                 persona=build_karvy_persona_prompt(cwd="/w", intent="你好"),
                 domain_registry=object())
    assert "instantiate_domain_template" not in (cap.get("mcp_tools") or {})
    # 业务角色 persona(无 karvy_self)→ 不挂
    cap = _drive(monkeypatch, intent="我要做个agent",
                 persona=build_role_persona_prompt("设计师"), domain_registry=object())
    assert "instantiate_domain_template" not in (cap.get("mcp_tools") or {})
    # 没传 domain_registry(其它路径)→ 不挂,mcp_tools 原样(0 回归)
    cap = _drive(monkeypatch, intent="我要做个agent",
                 persona=build_karvy_persona_prompt(cwd="/w"), domain_registry=None)
    assert cap.get("mcp_tools") is None
