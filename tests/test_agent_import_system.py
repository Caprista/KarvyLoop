"""test_agent_import_system — docs/84 #3:多 agent 系统导入(读谱→翻译→人拍板→落地)。

覆盖(表驱动、零 LLM 为主;LLM 只用假 gateway 走接线):
- bundle 解析:agents 字段检测 / 封顶 / 无人设项丢 / topology 原样透传
- IR 解析:封顶(agents≤24/edges≤64/teams≤8)/ 悬空引用丢 / 编造 agent 丢 / 坏 JSON→None
- 翻译器八案:流水线 / 并行汇聚 / 条件 / 群聊→圆桌种子 / 嵌套→子域 / 循环→降级 /
  汇报链→降级(绝不造 role→role 问责链)/ 黑板→降级;外加 executor 折边、动态路由静态化
- apply:域+子域+角色+模板落地 / 同名拒 / 中途失败回滚不留孤儿 / 模板 when/on_fail 存取往返
- plan 端点零写盘 / TRIAGE 坏 JSON 降级 per_agent / 无 LLM 降级 / mock LLM 的 TRIAGE 路由
- 载体补强:api_workflow_crystallize 透传 when/inputs/on_fail(不再丢回线性 DAG)
"""
from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

from karvyloop.adapter.source import (
    ManifestError, SystemBundle, is_system_bundle, parse_system_bundle)
from karvyloop.adapter.system_import import (
    IRAgent, IREdge, IRTeam, SystemApplyError, SystemIR,
    apply_system_plan, parse_system_ir, system_triage, translate_to_plan)


# ---- 假 gateway(同 test_agent_import_decompose 的约定:类名必须正好是 TextDelta)----
class TextDelta:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeGateway:
    def __init__(self, payload: str) -> None:
        self._payload = payload
        self.calls = 0

    def resolve_model(self, scope):  # noqa: ANN001
        return "fake-model"

    async def complete(self, messages, tools, ref, system=None):  # noqa: ANN001
        self.calls += 1
        mid = len(self._payload) // 2
        yield TextDelta(self._payload[:mid])
        yield TextDelta(self._payload[mid:])


# =========================================================================
# 1. bundle 解析(source.py)
# =========================================================================

def test_is_system_bundle_detection():
    assert is_system_bundle({"agents": [{"name": "a", "system_prompt": "x"}]})
    assert not is_system_bundle({"agents": []})          # 空列表不算
    assert not is_system_bundle({"system_prompt": "x"})  # 单 agent 清单没有 agents
    assert not is_system_bundle({"agents": "not-a-list"})
    assert not is_system_bundle("not-a-dict")


def test_parse_system_bundle_basic_and_topology_verbatim():
    topo = {"graph": [["a", "b"]], "whatever": {"nested": 1}}
    b = parse_system_bundle({"name": "sys", "agents": [
        {"name": "a", "system_prompt": "persona a"},
        {"name": "b", "role": "writer", "goal": "write", "backstory": "old hand"},
    ], "topology": topo})
    assert isinstance(b, SystemBundle) and b.name == "sys"
    assert [m.agent_name for m in b.agents] == ["a", "b"]
    # 无标准 system_prompt → 按字段名标注拼接(原文不改写)
    assert "role: writer" in b.agents[1].system_prompt
    assert "goal: write" in b.agents[1].system_prompt
    assert b.topology == topo                              # 源格式原样透传,不定 schema
    assert b.agents_total == 2 and b.agents_dropped == ()


def test_parse_system_bundle_caps_drops_and_dedup():
    items = [{"name": f"a{i}", "system_prompt": "p"} for i in range(30)]
    items[3] = {"name": "hollow"}                          # 连一句人设都没有 → 丢并如实报
    items[5] = {"name": "a1", "system_prompt": "dup"}      # 同名 → 加序号去重
    b = parse_system_bundle({"agents": items})
    assert b.agents_total == 30
    assert len(b.agents) <= 24                             # 封顶
    assert "hollow" in b.agents_dropped
    names = [m.agent_name for m in b.agents]
    assert len(names) == len(set(names))                   # 名字唯一(IR 引用键)
    assert "a1-2" in names


def test_parse_system_bundle_rejects_unusable():
    with pytest.raises(ManifestError):
        parse_system_bundle({"agents": [{"name": "x"}, {"name": "y"}]})  # 全都没人设
    with pytest.raises(ManifestError):
        parse_system_bundle({"no_agents": True})


# =========================================================================
# 2. IR 解析(parse_system_ir:宁空勿毒 + 引用级把关 + 封顶)
# =========================================================================

_NAMES = {"研究员", "评审员", "转换器"}

_GOOD_IR = {
    "system": {"name": "研究所", "mission": "把问题查透", "forbid": ["无来源下结论"], "oblige": ["结论标来源"]},
    "pattern": "pipeline",
    "agents": [
        {"name": "研究员", "kind": "hybrid", "identity": "研究员:先查证再下判断", "soul": "较真",
         "title": "研究员", "task": "查证并写初稿", "team": "t1",
         "atoms": [{"id": "verify_sources", "kind": "task", "purpose": "交叉验证",
                    "tools": ["web_search"], "tags": ["research"]}], "skills": []},
        {"name": "评审员", "kind": "decision", "identity": "评审员:专挑毛病", "soul": "怀疑一切",
         "title": "评审员", "task": "评审结论", "team": "t1", "atoms": [], "skills": []},
        {"name": "转换器", "kind": "executor", "identity": "", "soul": "", "title": "", "task": "",
         "team": "", "atoms": [{"id": "md_to_html", "kind": "task", "purpose": "转格式",
                                "tools": ["run_command"], "tags": ["convert"]}], "skills": []},
    ],
    "teams": [{"id": "t1", "name": "研究组", "mission": "查透", "parent": "", "members": ["研究员", "评审员"]}],
    "edges": [{"from": "研究员", "to": "评审员", "type": "sequence"}],
    "schedules": [],
    "blackboard": "",
}


def test_parse_ir_good():
    ir = parse_system_ir(json.dumps(_GOOD_IR, ensure_ascii=False), _NAMES)
    assert ir is not None
    assert ir.system_name == "研究所" and ir.pattern == "pipeline"
    assert [a.name for a in ir.agents] == ["研究员", "评审员", "转换器"]
    assert ir.agents[0].atoms[0].id == "verify_sources"
    assert ir.teams[0].members == ("研究员", "评审员")
    assert ir.edges == (IREdge(src="研究员", dst="评审员", type="sequence"),)
    assert ir.forbid == ("无来源下结论",)


def test_parse_ir_drops_dangling_and_fabricated():
    bad = json.loads(json.dumps(_GOOD_IR, ensure_ascii=False))
    bad["agents"].append({"name": "编造的", "kind": "decision", "identity": "x"})   # bundle 里没有
    bad["edges"].append({"from": "研究员", "to": "不存在", "type": "sequence"})      # 悬空
    bad["edges"].append({"from": "研究员", "to": "研究员", "type": "sequence"})      # 自指
    bad["teams"].append({"id": "t9", "name": "鬼队", "members": ["不存在"]})          # 全悬空成员
    bad["teams"][0]["parent"] = "t404"                                              # 悬空 parent
    ir = parse_system_ir(json.dumps(bad, ensure_ascii=False), _NAMES)
    assert ir is not None
    assert all(a.name in _NAMES for a in ir.agents), "编造 agent 没被丢"
    assert len(ir.edges) == 1, "悬空/自指边没被丢"
    assert [t.id for t in ir.teams] == ["t1"], "全悬空成员的队没被丢"
    assert ir.teams[0].parent == "", "悬空 parent 没清空"


def test_parse_ir_caps():
    names = {f"a{i}" for i in range(30)}
    obj = {
        "pattern": "mixed",
        "agents": [{"name": f"a{i}", "kind": "hybrid", "identity": "x"} for i in range(30)],
        "teams": [{"id": f"t{i}", "name": f"t{i}", "members": [f"a{i}"]} for i in range(12)],
        "edges": [{"from": f"a{i}", "to": f"a{(i + 1) % 24}", "type": "sequence"} for i in range(24)] +
                 [{"from": f"a{i}", "to": f"a{(i + 2) % 24}", "type": "parallel"} for i in range(24)] +
                 [{"from": f"a{i}", "to": f"a{(i + 3) % 24}", "type": "handoff"} for i in range(24)],
        "schedules": [],
    }
    ir = parse_system_ir(json.dumps(obj), names)
    assert ir is not None
    assert len(ir.agents) <= 24 and len(ir.edges) <= 64 and len(ir.teams) <= 8


def test_parse_ir_kind_whitelist_and_garbage():
    obj = {"agents": [{"name": "研究员", "kind": "robot", "identity": "x"}]}
    ir = parse_system_ir(json.dumps(obj, ensure_ascii=False), _NAMES)
    assert ir is not None and ir.agents[0].kind == "hybrid"   # 白名单外 → hybrid(宁保守勿毒)
    assert parse_system_ir("这不是JSON", _NAMES) is None
    assert parse_system_ir('{"agents": [broken', _NAMES) is None
    assert parse_system_ir('["not","a","dict"]', _NAMES) is None
    # 一个真 agent 都没有(全编造)→ None
    assert parse_system_ir('{"agents":[{"name":"编造","identity":"x"}]}', _NAMES) is None


def test_parse_ir_team_parent_cycle_broken():
    obj = {"agents": [{"name": "研究员", "kind": "hybrid", "identity": "x"},
                      {"name": "评审员", "kind": "hybrid", "identity": "y"}],
           "teams": [{"id": "t1", "name": "A", "parent": "t2", "members": ["研究员"]},
                     {"id": "t2", "name": "B", "parent": "t1", "members": ["评审员"]}]}
    ir = parse_system_ir(json.dumps(obj, ensure_ascii=False), _NAMES)
    assert ir is not None
    parents = {t.id: t.parent for t in ir.teams}
    assert "" in parents.values(), f"parent 环没断:{parents}"


# =========================================================================
# 3. TRIAGE 接线(mock gateway:重试一次 / 放弃 / 无 LLM)
# =========================================================================

def _bundle() -> SystemBundle:
    return parse_system_bundle({"name": "研究所", "agents": [
        {"name": "研究员", "system_prompt": "verify before concluding"},
        {"name": "评审员", "system_prompt": "review everything"},
        {"name": "转换器", "system_prompt": "convert md to html"},
    ], "topology": {"order": ["研究员", "评审员"]}})


def test_system_triage_with_fake_gateway():
    gw = FakeGateway(json.dumps(_GOOD_IR, ensure_ascii=False))
    ir = asyncio.run(system_triage(_bundle(), existing_atom_ids=[], gateway=gw))
    assert gw.calls == 1 and ir is not None and ir.system_name == "研究所"


def test_system_triage_retries_once_then_gives_up():
    class _Flaky(FakeGateway):
        async def complete(self, messages, tools, ref, system=None):  # noqa: ANN001
            self.calls += 1
            yield TextDelta("坏的" if self.calls == 1 else json.dumps(_GOOD_IR, ensure_ascii=False))
    gw = _Flaky("")
    ir = asyncio.run(system_triage(_bundle(), existing_atom_ids=[], gateway=gw))
    assert gw.calls == 2 and ir is not None, "没重试或没救回"

    gw2 = FakeGateway("永远是垃圾")
    assert asyncio.run(system_triage(_bundle(), existing_atom_ids=[], gateway=gw2)) is None
    assert gw2.calls == 2, "重试次数不对"
    assert asyncio.run(system_triage(_bundle(), existing_atom_ids=[], gateway=None)) is None


# =========================================================================
# 4. 翻译器八案(纯函数,零 LLM,断言 plan + degradations 精确形状)
# =========================================================================

def _agent(name, kind="hybrid", identity=None, task=None, team="", atoms=(), skills=()):
    return IRAgent(name=name, kind=kind, identity=identity if identity is not None else f"{name}的人设",
                   soul="", title=name, task=task if task is not None else f"{name}的一步",
                   team=team, atoms=tuple(atoms), skills=tuple(skills))


def _ir(agents, edges=(), teams=(), pattern="mixed", mission="使命", forbid=(), oblige=(),
        schedules=(), blackboard=""):
    return SystemIR(system_name="测试系统", mission=mission, forbid=tuple(forbid),
                    oblige=tuple(oblige), pattern=pattern, agents=tuple(agents),
                    teams=tuple(teams), edges=tuple(edges), schedules=tuple(schedules),
                    blackboard=blackboard)


# 案 1:流水线 a→b→c → 一条模板,链式依赖,零降级
def test_translate_pipeline():
    ir = _ir([_agent("a"), _agent("b"), _agent("c")],
             edges=[IREdge("a", "b", "sequence"), IREdge("b", "c", "sequence")],
             pattern="pipeline")
    plan, deg = translate_to_plan(ir)
    assert deg == []
    assert plan["mode"] == "system" and plan["domain"]["name"] == "测试系统"
    assert len(plan["workflows"]) == 1
    steps = plan["workflows"][0]["steps"]
    assert [(s["id"], s["role_key"], s["depends_on"]) for s in steps] == [
        ("s1", "a", []), ("s2", "b", ["s1"]), ("s3", "c", ["s2"])]
    assert steps[0]["task"] == "a的一步"
    assert plan["workflows"][0]["goal"] == "使命"


# 案 2:并行汇聚 a→c、b→c → c 依赖两个上游且 inputs 全给
def test_translate_parallel_converge():
    ir = _ir([_agent("a"), _agent("b"), _agent("c")],
             edges=[IREdge("a", "c", "parallel"), IREdge("b", "c", "parallel")],
             pattern="parallel")
    plan, deg = translate_to_plan(ir)
    assert deg == []
    steps = {s["role_key"]: s for s in plan["workflows"][0]["steps"]}
    assert sorted(steps["c"]["depends_on"]) == ["s1", "s2"]
    assert sorted(steps["c"]["inputs"]) == ["s1", "s2"], "汇聚步没把全部上游产出标进 inputs"
    assert "inputs" not in steps["a"] and "inputs" not in steps["b"]


# 案 3:单条件路由 + 失败策略 → when / on_fail 进模板步
def test_translate_condition_and_on_fail():
    ir = _ir([_agent("a"), _agent("b"), _agent("c")],
             edges=[IREdge("a", "b", "condition", condition="通过"),
                    IREdge("a", "c", "condition", condition="", on_fail="retry")],
             pattern="conditional")
    plan, deg = translate_to_plan(ir)
    assert deg == []
    steps = {s["role_key"]: s for s in plan["workflows"][0]["steps"]}
    assert steps["b"]["when"] == {"step": "s1", "contains": "通过"}
    assert steps["c"]["when"] == {"step": "s1", "status": "done"}   # 无条件词 → 上游 done 门
    assert steps["c"]["on_fail"] == "retry" and steps["c"]["max_retries"] == 2


# 案 4:群聊辩论 → 圆桌种子(seed_intents),不硬造 workflow
def test_translate_groupchat_to_roundtable_seed():
    ir = _ir([_agent("a", kind="decision"), _agent("b", kind="decision"),
              _agent("c", kind="executor", identity="", task="")],
             edges=[], pattern="groupchat", mission="辩个明白")
    plan, deg = translate_to_plan(ir)
    assert deg == []
    assert plan["workflows"] == []
    assert plan["seed_intents"] == [{"topic": "辩个明白", "participants": ["a", "b"]}]  # executor 不进圆桌


# 案 5:嵌套团队 → 子域(父先子后,成员=会落成角色的)
def test_translate_nested_teams_to_subdomains():
    ir = _ir([_agent("a", team="t1"), _agent("b", team="t2"), _agent("x", kind="executor",
                                                                     identity="", task="", team="t2")],
             teams=[IRTeam(id="t2", name="子组", mission="", parent="t1", members=("b", "x")),
                    IRTeam(id="t1", name="母组", mission="查透", parent="", members=("a",))],
             pattern="nested")
    plan, deg = translate_to_plan(ir)
    assert deg == []
    subs = plan["subdomains"]
    assert [s["team_id"] for s in subs] == ["t1", "t2"], "子域没按父先子后排"
    assert subs[0]["parent_team_id"] == "" and subs[1]["parent_team_id"] == "t1"
    assert subs[1]["members"] == ["b"], "executor 不该进子域成员(不落角色)"


# 案 6:循环 → 丢边 + 诚实降级(workflow 无循环 = 诚实 P1)
def test_translate_loop_degrades():
    ir = _ir([_agent("a"), _agent("b")],
             edges=[IREdge("a", "b", "sequence"), IREdge("b", "a", "loop")])
    plan, deg = translate_to_plan(ir)
    assert [d["element"] for d in deg] == ["loop:b→a"]
    assert all(k in deg[0] for k in ("element", "why", "fallback"))
    steps = plan["workflows"][0]["steps"]
    assert [(s["id"], s["depends_on"]) for s in steps] == [("s1", []), ("s2", ["s1"])]  # 环没进模板


# 案 7:agent→agent 汇报链 → 降级(绝不造 role→role 问责链)+ 评审步 + 职务写进 IDENTITY
def test_translate_report_chain_degrades_to_review():
    ir = _ir([_agent("a"), _agent("sup", kind="decision")],
             edges=[IREdge("a", "sup", "report")])
    plan, deg = translate_to_plan(ir)
    assert [d["element"] for d in deg] == ["report_chain:a→sup"]
    sup = next(r for r in plan["roles"] if r["role_id"] == "sup")
    assert sup["identity"].startswith("sup的人设") and len(sup["identity"]) > len("sup的人设"), \
        "汇报职务没追加进 IDENTITY(问责重接说明丢了)"
    # report 边不是 workflow 边:只有评审步时 steps<2 → 不出模板;配上真流水线则出评审步
    ir2 = _ir([_agent("a"), _agent("b"), _agent("sup", kind="decision")],
              edges=[IREdge("a", "b", "sequence"), IREdge("a", "sup", "report")])
    plan2, deg2 = translate_to_plan(ir2)
    assert [d["element"] for d in deg2] == ["report_chain:a→sup"]
    steps = plan2["workflows"][0]["steps"]
    review = [s for s in steps if s["role_key"] == "sup"]
    assert len(review) == 1 and review[0]["depends_on"] == ["s1"], "评审步没落在被评审者产出之后"


# 案 8:共享黑板 / 定时常驻 → 诚实降级(不静默吞)
def test_translate_blackboard_and_schedule_degrade():
    ir = _ir([_agent("a"), _agent("b")], edges=[IREdge("a", "b", "sequence")],
             blackboard="共享 scratchpad", schedules=[{"agent": "a", "when": "每天早8点"}])
    plan, deg = translate_to_plan(ir)
    els = [d["element"] for d in deg]
    assert "blackboard" in els and "schedule:a" in els
    for d in deg:
        assert d["why"] and d["fallback"], f"降级条目不完整:{d}"


# 附案:executor 折边(a→e→b 桥成 a→b)+ 动态路由静态化
def test_translate_executor_folded_and_dynamic_route():
    ir = _ir([_agent("a"), _agent("e", kind="executor", identity="", task=""), _agent("b")],
             edges=[IREdge("a", "e", "sequence"), IREdge("e", "b", "sequence"),
                    IREdge("a", "b", "dynamic")])
    plan, deg = translate_to_plan(ir)
    assert [d["element"] for d in deg] == ["dynamic_route:a→b"]
    steps = plan["workflows"][0]["steps"]
    assert [(s["role_key"], s["depends_on"]) for s in steps] == [("a", []), ("b", ["s1"])]
    assert any("“e”" in n or "「e」" in n for n in plan["notes"]), "executor 折边没如实注记"
    e_row = next(r for r in plan["roles"] if r["role_id"] == "e")
    assert e_row["agent_kind"] == "executor"


# skill 型 agent:识别进 skills_recognized,不进步骤/圆桌
def test_translate_skill_agent_recognized():
    ir = _ir([_agent("a"), _agent("sop", kind="skill", skills=("weekly-report",))])
    plan, deg = translate_to_plan(ir)
    assert plan["skills_recognized"] == ["weekly-report"]
    assert any("sop" in n for n in plan["notes"])


# supervisor 分派 → 路由权上移(移位报告,不是降级)
def test_translate_supervisor_relocates_routing():
    ir = _ir([_agent("boss", kind="decision"), _agent("a")],
             edges=[IREdge("boss", "a", "handoff")], pattern="supervisor")
    plan, deg = translate_to_plan(ir)
    assert deg == []
    assert len(plan["relocations"]) == 1 and plan["relocations"][0]["element"] == "supervisor_dispatch"


# =========================================================================
# 5. apply(确定性落地:域+子域+角色+模板 / 同名拒 / 回滚 / 往返)
# =========================================================================

def _stack(tmp: Path):
    from karvyloop.atoms.registry import AtomRegistry, AtomStore
    from karvyloop.domain.registry import BusinessDomainRegistry
    from karvyloop.karvy.workflow_store import WorkflowStore
    from karvyloop.roles.registry import RoleRegistry
    atom_reg = AtomRegistry(store=AtomStore(tmp / "atoms.json"))
    role_reg = RoleRegistry(tmp / "roles", atom_registry=atom_reg)
    dom_reg = BusinessDomainRegistry()
    wf = WorkflowStore(tmp / "wf.json")
    return atom_reg, role_reg, dom_reg, wf


class FakeProposals:
    def __init__(self) -> None:
        self.items: list = []

    def register(self, p, **kw):  # noqa: ANN001
        self.items.append(p)
        return getattr(p, "proposal_id", "pid")


def _full_plan() -> dict:
    ir = _ir(
        [_agent("研究员", team="t1",
                atoms=[types.SimpleNamespace(id="verify_sources", kind="task", purpose="查证",
                                             tools=("web_search",), tags=("research",),
                                             reuse_existing=False)]),
         _agent("评审员", kind="decision", team="t1"),
         _agent("转换器", kind="executor", identity="", task="",
                atoms=[types.SimpleNamespace(id="md_to_html", kind="task", purpose="转格式",
                                             tools=("run_command",), tags=("convert",),
                                             reuse_existing=False)])],
        edges=[IREdge("研究员", "评审员", "condition", condition="初稿完成", on_fail="skip")],
        teams=[IRTeam(id="t1", name="研究组", mission="查透", parent="", members=("研究员", "评审员"))],
        pattern="pipeline", mission="把问题查透", forbid=("无来源下结论",), oblige=("结论标来源",))
    plan, _deg = translate_to_plan(ir)
    plan["seed_intents"] = [{"topic": "开题圆桌", "participants": ["研究员", "评审员"]}]
    return plan


def test_apply_full_landing(tmp_path):
    atom_reg, role_reg, dom_reg, wf = _stack(tmp_path)
    props = FakeProposals()
    report = apply_system_plan(_full_plan(), atom_registry=atom_reg, role_registry=role_reg,
                               domain_registry=dom_reg, workflow_store=wf,
                               proposal_registry=props, now=123.0)
    assert report["ok"] is True
    # 域:使命→value.md,禁令→deontic,成员=member_query(只挂落成角色的)
    root = dom_reg.get(report["domain_id"])
    assert root.name == "测试系统"
    assert "把问题查透" in root.value_md.text
    assert root.deontic.forbid == ("无来源下结论",) and root.deontic.oblige == ("结论标来源",)
    assert "agent:研究员" in root.member_query and "agent:评审员" in root.member_query
    assert "agent:转换器" not in root.member_query, "executor 进了决策席"
    # 子域:create_child 继承(value.md 同文),parent 指到根
    assert len(report["subdomains"]) == 1
    child = dom_reg.get(report["subdomains"][0]["id"])
    assert child.parent_id == root.id and child.name == "研究组"
    assert child.value_md.text == root.value_md.text
    # 角色:decision/hybrid 落库(自动 seed 尽责契约);executor 不建 role
    assert sorted(report["roles_created"]) == ["研究员", "评审员"]
    assert report["roles_skipped_executor"] == ["转换器"]
    assert not (role_reg.root / "转换器").exists()
    commitment = (role_reg.root / "研究员" / "COMMITMENT.md").read_text(encoding="utf-8")
    assert commitment.strip(), "尽责契约没 seed"
    # 原子:executor 的也落公共库,origin 记 provenance
    assert atom_reg.get("verify_sources") is not None and atom_reg.get("md_to_html") is not None
    assert atom_reg.get("md_to_html").origin == "system-import:测试系统"
    # 模板:provenance=import,when/on_fail 原样落
    tpls = wf.all()
    assert len(tpls) == 1 and tpls[0]["provenance"] == "import"
    s2 = next(s for s in tpls[0]["steps"] if s["role_key"] == "评审员")
    assert s2["when"] == {"step": "s1", "contains": "初稿完成"} and s2["on_fail"] == "skip"
    # 圆桌种子:H2A 提案卡(人拍了才开桌)
    assert report["roundtables_seeded"] == 1 and len(props.items) == 1
    p = props.items[0]
    assert getattr(p, "kind", "") == "roundtable"
    assert p.payload["participants"] == ["研究员", "评审员"]
    assert p.payload["group_domain_id"] == root.id


def test_apply_same_name_active_domain_rejected_zero_writes(tmp_path):
    atom_reg, role_reg, dom_reg, wf = _stack(tmp_path)
    dom_reg.create(name="测试系统", created_by="user:ch")
    with pytest.raises(SystemApplyError):
        apply_system_plan(_full_plan(), atom_registry=atom_reg, role_registry=role_reg,
                          domain_registry=dom_reg, workflow_store=wf)
    assert len(atom_reg) == 0 and not (role_reg.root / "研究员").exists()
    assert wf.all() == []


def test_apply_midway_role_failure_rolls_back_no_orphans(tmp_path):
    atom_reg, role_reg, dom_reg, wf = _stack(tmp_path)

    class _Boom:
        """第 2 个角色创建时炸(代理真实中途失败)。"""
        def __init__(self, real):
            self._real, self.n = real, 0

        def __getattr__(self, k):
            return getattr(self._real, k)

        def create(self, *a, **kw):
            self.n += 1
            if self.n >= 2:
                raise RuntimeError("disk full")
            return self._real.create(*a, **kw)

    with pytest.raises(SystemApplyError):
        apply_system_plan(_full_plan(), atom_registry=atom_reg, role_registry=_Boom(role_reg),
                          domain_registry=dom_reg, workflow_store=wf)
    assert len(atom_reg) == 0, "回滚后原子成孤儿"
    assert not (role_reg.root / "研究员").exists(), "回滚后第 1 个角色成孤儿"
    assert dom_reg.list_all() == (), "失败发生在建域前,不该有域"
    assert wf.all() == []


def test_apply_workflow_failure_rolls_back_domains_too(tmp_path):
    atom_reg, role_reg, dom_reg, _wf = _stack(tmp_path)

    class _BoomStore:
        def save(self, **kw):  # noqa: ANN003
            raise RuntimeError("store broken")

    with pytest.raises(SystemApplyError):
        apply_system_plan(_full_plan(), atom_registry=atom_reg, role_registry=role_reg,
                          domain_registry=dom_reg, workflow_store=_BoomStore())
    assert len(atom_reg) == 0 and not (role_reg.root / "研究员").exists()
    doms = dom_reg.list_all()
    assert doms and all(d.lifecycle == "archived" for d in doms), "失败后建出的域没归档回滚"


def test_workflow_store_optional_fields_roundtrip(tmp_path):
    from karvyloop.karvy.workflow_store import WorkflowStore
    wf = WorkflowStore(tmp_path / "wf.json")
    steps = [
        {"id": "s1", "role_key": "a", "task": "查", "depends_on": []},
        {"id": "s2", "role_key": "b", "task": "审", "depends_on": ["s1"],
         "inputs": ["s1"], "when": {"step": "s1", "contains": "OK"},
         "on_fail": "retry", "max_retries": 3, "垃圾字段": "丢我"},
    ]
    tpl = wf.save(goal="流程", role_keys=["a", "b"], steps=steps, provenance="import")
    got = wf.all()[0]
    s2 = got["steps"][1]
    assert s2["when"] == {"step": "s1", "contains": "OK"}
    assert s2["inputs"] == ["s1"] and s2["on_fail"] == "retry" and s2["max_retries"] == 3
    assert "垃圾字段" not in s2, "未知字段该在存储咽喉被丢"
    assert got["provenance"] == "import" and tpl["id"] == got["id"]
    # 旧调用(无进阶字段)向后兼容
    tpl2 = wf.save(goal="旧", role_keys=["a"], steps=[{"id": "s1", "role_key": "a", "task": "x",
                                                       "depends_on": []}])
    assert "provenance" not in tpl2 and "when" not in tpl2["steps"][0]


def test_repoint_template_keeps_advanced_fields(tmp_path):
    """导入的模板被 /workflow/plan 快脑命中后,_repoint_template 带回 when/inputs/on_fail(接缝)。"""
    from karvyloop.console.routes import _repoint_template
    tpl = {"id": "t1", "goal": "流程", "steps": [
        {"id": "s1", "role_key": "a", "task": "查", "depends_on": []},
        {"id": "s2", "role_key": "b", "task": "审", "depends_on": ["s1"],
         "inputs": ["s1"], "when": {"step": "s1", "status": "done"}, "on_fail": "abort"}]}
    roles = [{"agent_id": "a", "role_id": "ra", "display": "甲", "domain_id": "d1"},
             {"agent_id": "b", "role_id": "rb", "display": "乙", "domain_id": "d1"}]
    out = _repoint_template(tpl, roles)
    assert out is not None
    s2 = out["steps"][1]
    assert s2["when"] == {"step": "s1", "status": "done"}
    assert s2["inputs"] == ["s1"] and s2["on_fail"] == "abort"


def test_crystallize_passes_through_advanced_fields(tmp_path):
    """载体补强:api_workflow_crystallize 不再丢 when/inputs/on_fail/max_retries。"""
    from karvyloop.console.routes import WorkflowCrystallizeRequest, api_workflow_crystallize
    from karvyloop.karvy.workflow_store import WorkflowStore
    state = types.SimpleNamespace(workflow_store=WorkflowStore(tmp_path / "wf.json"))
    request = types.SimpleNamespace(app=types.SimpleNamespace(state=state))
    plan = {"goal": "做登录页", "steps": [
        {"id": "s1", "agent_id": "产品经理", "task": "写需求", "depends_on": []},
        {"id": "s2", "agent_id": "设计师", "task": "出设计", "depends_on": ["s1"],
         "inputs": ["s1"], "when": {"step": "s1", "status": "done"},
         "on_fail": "retry", "max_retries": 2}]}
    out = api_workflow_crystallize(WorkflowCrystallizeRequest(plan=plan, name="登录页"), request)
    assert out["ok"] is True
    s2 = state.workflow_store.all()[0]["steps"][1]
    assert s2["when"] == {"step": "s1", "status": "done"}
    assert s2["inputs"] == ["s1"] and s2["on_fail"] == "retry" and s2["max_retries"] == 2


# =========================================================================
# 6. 两端点契约(plan 零写盘 / TRIAGE 降级 / apply 接线;mock LLM 走 TRIAGE 路由)
# =========================================================================

_BUNDLE_REQ = {
    "name": "研究所",
    "agents": [
        {"name": "研究员", "system_prompt": "verify before concluding"},
        {"name": "评审员", "system_prompt": "review everything"},
        {"name": "转换器", "system_prompt": "convert md to html"},
    ],
    "topology": {"pipeline": ["研究员", "评审员", "转换器"]},
}


def _app(tmp: Path, gateway):  # noqa: ANN001
    from karvyloop.karvy.workflow_store import WorkflowStore
    atom_reg, role_reg, dom_reg, _ = _stack(tmp)
    wf = WorkflowStore(tmp / "wf.json")
    state = types.SimpleNamespace(
        atom_registry=atom_reg, role_registry=role_reg, domain_registry=dom_reg,
        domain_store=None, workflow_store=wf, proposal_registry=FakeProposals(),
        runtime_kwargs={"gateway": gateway, "model_ref": ""})
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state)), atom_reg, role_reg, dom_reg, wf


def test_plan_endpoint_triage_routes_and_writes_nothing(tmp_path):
    from karvyloop.console.routes_roles import api_agent_import_system_plan
    request, atom_reg, role_reg, dom_reg, wf = _app(
        tmp_path, FakeGateway(json.dumps(_GOOD_IR, ensure_ascii=False)))
    req = types.SimpleNamespace(bundle=_BUNDLE_REQ, domain_name="")
    out = asyncio.run(api_agent_import_system_plan(req, request))
    assert out["ok"] and out["mode"] == "system" and out["triaged"] is True
    assert out["plan"]["domain"]["name"] == "研究所"
    kinds = {r["role_id"]: r["agent_kind"] for r in out["plan"]["roles"]}
    assert kinds == {"研究员": "hybrid", "评审员": "decision", "转换器": "executor"}
    # 零写盘:plan 阶段任何注册表/盘都不动
    assert len(atom_reg) == 0
    assert not role_reg.root.exists() or not any(role_reg.root.iterdir())
    assert dom_reg.list_all() == () and wf.all() == [] and not (tmp_path / "wf.json").exists()


def test_plan_endpoint_bad_triage_falls_back_per_agent(tmp_path):
    from karvyloop.console.routes_roles import api_agent_import_system_plan
    request, atom_reg, role_reg, dom_reg, wf = _app(tmp_path, FakeGateway("这不是JSON"))
    req = types.SimpleNamespace(bundle=_BUNDLE_REQ, domain_name="")
    out = asyncio.run(api_agent_import_system_plan(req, request))
    assert out["ok"] and out["mode"] == "per_agent" and out["triaged"] is False
    assert [a["name"] for a in out["agents"]] == ["研究员", "评审员", "转换器"]
    assert out["degradations"][0]["element"] == "topology", "拓扑丢失没如实报"
    assert out["note"]
    assert len(atom_reg) == 0 and dom_reg.list_all() == ()


def test_plan_endpoint_no_llm_falls_back(tmp_path):
    from karvyloop.console.routes_roles import api_agent_import_system_plan
    request, *_ = _app(tmp_path, None)
    out = asyncio.run(api_agent_import_system_plan(
        types.SimpleNamespace(bundle=_BUNDLE_REQ, domain_name=""), request))
    assert out["mode"] == "per_agent" and out["degradations"][0]["element"] == "topology"


def test_plan_endpoint_rejects_non_bundle(tmp_path):
    from karvyloop.console.routes_roles import api_agent_import_system_plan
    request, *_ = _app(tmp_path, None)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(api_agent_import_system_plan(
            types.SimpleNamespace(bundle={"system_prompt": "单 agent"}, domain_name=""), request))
    assert ei.value.status_code == 422


def test_apply_endpoint_end_to_end(tmp_path):
    """plan(mock TRIAGE)→(人改判:转换器 executor 保持)→ apply → 资产落地 + 报告。"""
    from karvyloop.console.routes_roles import (
        api_agent_import_system_apply, api_agent_import_system_plan)
    request, atom_reg, role_reg, dom_reg, wf = _app(
        tmp_path, FakeGateway(json.dumps(_GOOD_IR, ensure_ascii=False)))
    plan_out = asyncio.run(api_agent_import_system_plan(
        types.SimpleNamespace(bundle=_BUNDLE_REQ, domain_name="我的研究所"), request))
    assert plan_out["plan"]["domain"]["name"] == "我的研究所"   # 域名覆盖生效
    out = asyncio.run(api_agent_import_system_apply(
        types.SimpleNamespace(plan=plan_out["plan"], created_by_user="ch"), request))
    assert out["ok"] is True and out["domain_name"] == "我的研究所"
    assert sorted(out["roles_created"]) == ["研究员", "评审员"]
    assert out["roles_skipped_executor"] == ["转换器"]
    assert atom_reg.get("verify_sources") is not None and atom_reg.get("md_to_html") is not None
    assert dom_reg.get(out["domain_id"]).name == "我的研究所"
    assert len(out["subdomains"]) == 1
    assert out["consolidation_suggestions"] == [] or isinstance(out["consolidation_suggestions"], list)
    assert len(wf.all()) == len(out["workflows_saved"])


def test_apply_endpoint_rejects_per_agent_plan(tmp_path):
    from karvyloop.console.routes_roles import api_agent_import_system_apply
    request, atom_reg, *_ = _app(tmp_path, None)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(api_agent_import_system_apply(
            types.SimpleNamespace(plan={"mode": "per_agent"}, created_by_user="ch"), request))
    assert ei.value.status_code == 422
    assert len(atom_reg) == 0


def test_apply_endpoint_same_name_422(tmp_path):
    from karvyloop.console.routes_roles import api_agent_import_system_apply
    request, _atom_reg, _role_reg, dom_reg, _wf = _app(tmp_path, None)
    dom_reg.create(name="测试系统", created_by="user:ch")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(api_agent_import_system_apply(
            types.SimpleNamespace(plan=_full_plan(), created_by_user="ch"), request))
    assert ei.value.status_code == 422


# 人改判生效:把 hybrid 改成 executor → 不建 role、只落原子(H2A 的"判型可改"是真的)
def test_apply_respects_human_rejudged_kind(tmp_path):
    atom_reg, role_reg, dom_reg, wf = _stack(tmp_path)
    plan = _full_plan()
    next(r for r in plan["roles"] if r["role_id"] == "研究员")["agent_kind"] = "executor"
    report = apply_system_plan(plan, atom_registry=atom_reg, role_registry=role_reg,
                               domain_registry=dom_reg, workflow_store=wf)
    assert "研究员" in report["roles_skipped_executor"]
    assert not (role_reg.root / "研究员").exists(), "人改判 executor 还是建了 role"
    assert atom_reg.get("verify_sources") is not None, "改判 executor 后原子也该照落"
    # 它在模板里的步骤落不了(role 没建)→ 步骤被丢并如实报;剩 1 步 < 2 → 模板不存
    assert wf.all() == [] and report["steps_dropped"], "没角色的步骤该被丢并如实报"
