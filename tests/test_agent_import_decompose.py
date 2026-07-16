"""test_agent_import_decompose — M3 LLM 拆解:外部 agent → role + 可复用 atom(docs/14 §10).

Hardy 2026-06-26 拍定的头号缺陷修复:导入 agent 不该是扁平拷成 skill,该是 LLM 拆解
(agent → role + atom + 识别 skill)。这台子用**假 gateway**(吐一段 JSON)把整条接线走通,
CI 可跑(不需真 key);真模型那刀在 test_e2e_pressure.py 的 J6。

验收锚(Hardy 三选三):导一个 agent 后 (a) 出现 role、(b) 出现 ≥1 atom、(c) 拆解走了 LLM
(本测用假 gateway 证接线;token 计费由 gateway.complete 自动入账,真模型测验)。
"""
from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest

from karvyloop.adapter.bootstrap import (
    AtomProposal, DecompositionResult, bootstrap_decompose, parse_decomposition)
from karvyloop.adapter.source import ExternalManifest


# ---- 假 gateway:吐一段拆解 JSON(不调真模型)----
# 类名必须正好是 TextDelta:bootstrap 用 `type(ev).__name__ == "TextDelta"` 收流。
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
        # 一次性吐完整 JSON(真 gateway 是流式 TextDelta,这里两片模拟流)
        mid = len(self._payload) // 2
        yield TextDelta(self._payload[:mid])
        yield TextDelta(self._payload[mid:])


_GOOD_JSON = (
    '{"identity":"研究分析师:先查证再下判断",'
    '"soul":"诚实\\n查证优先\\n把判断权留给用户",'
    '"atoms":[{"id":"web_search","kind":"task","purpose":"查资料","tools":["http_get"],"reuse_existing":false},'
    '{"id":"summarize","kind":"task","purpose":"摘要长文","tools":[],"reuse_existing":false}],'
    '"skills":["fact-check"]}'
)


def _manifest() -> ExternalManifest:
    return ExternalManifest(
        source_id="generic-json", source_path="<test>",
        system_prompt="You are a careful research analyst. Verify before concluding.",
        tools=({"name": "web_search"}, {"name": "summarize"}), agent_name="analyst")


# ---- 1. 拆解函数:假 gateway → DecompositionResult ----
def test_bootstrap_decompose_with_fake_gateway():
    gw = FakeGateway(_GOOD_JSON)
    r = asyncio.run(bootstrap_decompose(_manifest(), existing_atom_ids=[], gateway=gw, model_ref=""))
    assert gw.calls == 1, "没真去调 gateway(没耗 token)"
    assert r is not None and r.is_valid()
    assert [a.id for a in r.atoms] == ["web_search", "summarize"]
    assert r.identity.startswith("研究分析师")
    assert r.skills == ("fact-check",)


def test_bootstrap_decompose_none_gateway_returns_none():
    assert asyncio.run(bootstrap_decompose(_manifest(), existing_atom_ids=[], gateway=None)) is None


class _FlakyGateway:
    """第一次吐坏 JSON,第二次吐好的(模拟并发偶发截断)。"""
    def __init__(self):
        self.calls = 0

    def resolve_model(self, scope):  # noqa: ANN001
        return "fake"

    async def complete(self, messages, tools, ref, system=None):  # noqa: ANN001
        self.calls += 1
        yield TextDelta("这不是JSON只是一段话" if self.calls == 1 else _GOOD_JSON)


def test_bootstrap_retries_once_on_bad_json():
    gw = _FlakyGateway()
    r = asyncio.run(bootstrap_decompose(_manifest(), existing_atom_ids=[], gateway=gw))
    assert gw.calls == 2 and r is not None and r.is_valid(), f"没重试或没救回: calls={gw.calls}"


def test_bootstrap_gives_up_after_retry():
    """两次都坏 → None(不无限烧 token)。"""
    class _AlwaysBad:
        def __init__(self): self.calls = 0
        def resolve_model(self, scope): return "fake"  # noqa: ANN001
        async def complete(self, messages, tools, ref, system=None):  # noqa: ANN001
            self.calls += 1
            yield TextDelta("永远坏")
    gw = _AlwaysBad()
    assert asyncio.run(bootstrap_decompose(_manifest(), existing_atom_ids=[], gateway=gw)) is None
    assert gw.calls == 2, f"重试次数不对: {gw.calls}"


# ---- 2. 整条接线:api_agent_import 拆解路径 → 落原子 + 建角色 ----
def _app_with(tmp: Path, gateway):  # noqa: ANN001
    from karvyloop.atoms.registry import AtomRegistry, AtomStore
    from karvyloop.roles.registry import RoleRegistry
    atom_reg = AtomRegistry(store=AtomStore(tmp / "atoms.json"))
    role_reg = RoleRegistry(tmp / "roles", atom_registry=atom_reg)
    state = types.SimpleNamespace(
        role_registry=role_reg, atom_registry=atom_reg,
        runtime_kwargs={"gateway": gateway, "model_ref": ""})
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state)), atom_reg, role_reg


def test_api_agent_import_decomposes_to_role_and_atoms(tmp_path):
    from karvyloop.console.routes import api_agent_import
    req = types.SimpleNamespace(role_id="analyst", source_type="generic-json",
                                system_prompt="careful research analyst, verify before concluding",
                                tools=["web_search", "summarize"])
    request, atom_reg, role_reg = _app_with(tmp_path, FakeGateway(_GOOD_JSON))

    out = asyncio.run(api_agent_import(req, request))
    # (a) role 出现
    assert out["ok"] and out["decomposed"] is True, out
    assert (role_reg.root / "analyst").exists(), "角色目录没物化"
    # (b) ≥1 atom 落进公共池
    assert atom_reg.get("web_search") is not None and atom_reg.get("summarize") is not None
    assert set(out["atoms"]) == {"web_search", "summarize"}
    assert set(out["atoms_created"]) == {"web_search", "summarize"}
    # (c) COMPOSITION 引的是原子(不是死字符串 tool 名)
    comp = (role_reg.root / "analyst" / "COMPOSITION.yaml").read_text(encoding="utf-8")
    assert "atom: web_search" in comp and "atom: summarize" in comp, comp
    # IDENTITY 是 LLM 提炼的真人设,不是模板占位
    ident = (role_reg.root / "analyst" / "IDENTITY.md").read_text(encoding="utf-8")
    assert "研究分析师" in ident
    # skill 识别到了但没强塞(库里没有 → 只报不绑)
    assert out["skills_recognized"] == ["fact-check"] and out["skills_bound"] == []
    # docs/14 §11.1 诚实披露:两个原子的 tools(http_get / 空)都不是真实工具 → 标 advisory
    assert "atoms_advisory" in out and set(out["atoms_advisory"]) == {"web_search", "summarize"}
    assert out["atoms_executable"] == []
    # docs/14 §11.3 agent-vs-skill:全 advisory → 标 advisory_persona + 引导"给它一个 skill"(补能力正路)
    assert out["import_kind"] == "advisory_persona" and "skill" in out["note"]


def test_api_agent_import_tool_agent_kind(tmp_path):
    """有真实工具(web_search)→ import_kind=tool_agent、note 空(它真能干活)。"""
    from karvyloop.console.routes import api_agent_import
    tool_json = ('{"identity":"研究员","soul":"查证","atoms":[{"id":"do_search","kind":"task",'
                 '"purpose":"搜","tools":["web_search"],"reuse_existing":false}],"skills":[]}')
    request, atom_reg, role_reg = _app_with(tmp_path, FakeGateway(tool_json))
    req = types.SimpleNamespace(role_id="researcher", source_type="generic-json",
                                system_prompt="careful researcher", tools=["web_search"])
    out = asyncio.run(api_agent_import(req, request))
    assert out["decomposed"] is True and out["import_kind"] == "tool_agent" and out["note"] == ""
    assert out["atoms_executable"] == ["do_search"] and out["atoms_advisory"] == []


def test_api_agent_import_reuses_existing_atom(tmp_path):
    """已在公共池的同名原子 → 复用不重建(甲:用不拥有)。"""
    from karvyloop.console.routes import api_agent_import
    request, atom_reg, role_reg = _app_with(tmp_path, FakeGateway(_GOOD_JSON))
    atom_reg.create("web_search", "task", "已存在的搜索原子")  # 预置
    req = types.SimpleNamespace(role_id="analyst2", source_type="generic-json",
                                system_prompt="x", tools=["web_search", "summarize"])
    out = asyncio.run(api_agent_import(req, request))
    assert out["decomposed"] is True
    assert "web_search" not in out["atoms_created"], "已存在的原子被重建了(没复用)"
    assert "summarize" in out["atoms_created"]
    assert set(out["atoms"]) == {"web_search", "summarize"}  # 两个都进 COMPOSITION


def test_api_agent_import_falls_back_to_v0_when_no_llm(tmp_path):
    """无 gateway(--no-llm)→ 降级 v0 确定性 adapter:建角色但 decomposed=False、不出原子。"""
    from karvyloop.console.routes import api_agent_import
    request, atom_reg, role_reg = _app_with(tmp_path, None)  # gateway=None
    req = types.SimpleNamespace(role_id="analyst3", source_type="generic-json",
                                system_prompt="careful analyst", tools=["web_search"])
    out = asyncio.run(api_agent_import(req, request))
    assert out["ok"] and out["decomposed"] is False, out
    assert (role_reg.root / "analyst3").exists()           # v0 仍物化 7 文件
    assert len(atom_reg) == 0, "v0 不该造原子"


def test_api_agent_import_garbage_decomp_falls_back(tmp_path):
    """LLM 吐 prose 垃圾(宁空勿毒返 None)→ 不写坏原子,降级 v0。"""
    from karvyloop.console.routes import api_agent_import
    request, atom_reg, role_reg = _app_with(tmp_path, FakeGateway("这个 agent 我建议拆成两个原子哦"))
    req = types.SimpleNamespace(role_id="analyst4", source_type="generic-json",
                                system_prompt="x", tools=["web_search"])
    out = asyncio.run(api_agent_import(req, request))
    assert out["decomposed"] is False, "垃圾拆解没降级"
    assert len(atom_reg) == 0, "垃圾被当原子写进公共池了(投毒)"


# ---- 独立对抗验收(2026-06-27)逮到的 2 个真缺陷,锁回归 ----
def test_parse_rejects_overlong_atom_id():
    """Defect 1:atom id 无界长度 → 超长垃圾写进公共池。id 必须封顶。"""
    huge = '{"atoms":[{"id":"' + "a" * 10000 + '","kind":"task"},{"id":"ok_one","kind":"task"}]}'
    r = parse_decomposition(huge)
    assert r is not None and [a.id for a in r.atoms] == ["ok_one"], "超长 id 没被丢"


def test_parse_caps_all_llm_controlled_collections():
    """独立对抗验收 round2:tools/原子个数/skills 也是 LLM 控制、直落 atoms.json,必须封顶
    (id 封了顶但兄弟集合还能灌爆盘:9.59MB/单次导入)。"""
    import json as _j
    payload = {"atoms": [{"id": f"atom_{i}", "kind": "task",
                          "tools": ["t" * 2000] * 500} for i in range(100)],
               "skills": ["s" * 2000] * 100}
    r = parse_decomposition(_j.dumps(payload))
    assert r is not None
    assert len(r.atoms) <= 32, "原子总数没封顶(能灌爆公共池)"
    for a in r.atoms:
        assert len(a.tools) <= 16, "单原子 tools 条数没封顶"
        assert all(len(t) <= 64 for t in a.tools), "tool 串长度没封顶(灌爆 atoms.json)"
    assert len(r.skills) <= 16 and all(len(s) <= 64 for s in r.skills), "skills 没封顶"


def test_import_bad_role_id_charset_leaves_no_orphan_atoms(tmp_path):
    """Defect 2:role_id 字符集非法(如 a.b)→ 前置拒掉,绝不先建原子再崩留孤儿。"""
    import pytest as _pt
    from fastapi import HTTPException
    from karvyloop.console.routes import api_agent_import
    request, atom_reg, role_reg = _app_with(tmp_path, FakeGateway(_GOOD_JSON))
    req = types.SimpleNamespace(role_id="a.b", source_type="generic-json",
                                system_prompt="x", tools=["web_search"])
    with _pt.raises(HTTPException) as ei:
        asyncio.run(api_agent_import(req, request))
    assert ei.value.status_code == 422
    assert len(atom_reg) == 0, "role_id 非法但原子已落公共池 = 孤儿(没前置拦)"


# ==== docs/84 #2:判型分流(agent_kind)+ 在场 bug 修(纯人设空 atoms 不再降级)====

_PURE_PERSONA_JSON = (
    '{"identity":"资深谈判顾问:替你权衡出价与让步的分寸",'
    '"soul":"先听后说\\n不替你拍板,只把取舍摆清楚",'
    '"atoms":[],"skills":[]}'
)

_EXECUTOR_JSON = (
    '{"agent_kind":"executor","identity":"","soul":"",'
    '"atoms":[{"id":"pdf_to_text","kind":"task","purpose":"PDF 转文本","tools":["run_command"],"reuse_existing":false},'
    '{"id":"csv_dedupe","kind":"task","purpose":"CSV 去重","tools":["run_command"],"reuse_existing":false}],'
    '"skills":[]}'
)

_SKILL_JSON = (
    '{"agent_kind":"skill","identity":"一套周报生成流程","soul":"",'
    '"atoms":[],"skills":["weekly-report-sop"]}'
)

_DECISION_JSON = (
    '{"agent_kind":"decision","identity":"投资把关人:替你判断该不该进场","soul":"证据优先",'
    '"atoms":[],"skills":[]}'
)


# ---- 判型白名单(非法/缺失 → hybrid,宁保守勿毒)----
def test_parse_agent_kind_whitelist():
    for k in ("decision", "executor", "hybrid", "skill"):
        r = parse_decomposition('{"agent_kind":"%s","identity":"某人","atoms":[],'
                                '"skills":["s"]}' % k)
        assert r is not None and r.agent_kind == k, f"合法 kind {k} 没被解析"
    # 大小写归一
    r = parse_decomposition('{"agent_kind":"Decision","identity":"某人","atoms":[],"skills":[]}')
    assert r is not None and r.agent_kind == "decision"
    # 非法值 / 缺失 → hybrid(不 None、不投毒)
    for payload in ('{"agent_kind":"robot","identity":"某人","atoms":[],"skills":[]}',
                    '{"identity":"某人","atoms":[],"skills":[]}'):
        r = parse_decomposition(payload)
        assert r is not None and r.agent_kind == "hybrid", payload


def test_parse_garbage_still_none():
    """宁空勿毒不松动:坏 JSON / prose / 全空产出照样 None。"""
    assert parse_decomposition("这不是JSON") is None
    assert parse_decomposition('{"atoms": [broken') is None
    assert parse_decomposition('["not","a","dict"]') is None
    # atoms/identity/skills 全空 = 啥也没拆出来 → None(bug 修后唯一合法的"空"判据)
    assert parse_decomposition('{"agent_kind":"decision","identity":"","atoms":[],"skills":[]}') is None


# ---- 四型 is_valid 矩阵 ----
def test_is_valid_matrix_by_kind():
    def _mk(kind, identity="", atoms=(), skills=()):
        return DecompositionResult(identity=identity, soul="", atoms=tuple(atoms),
                                   skills=tuple(skills), agent_kind=kind)
    atom = AtomProposal(id="a1", kind="task", purpose="p", tools=("run_command",),
                        tags=(), reuse_existing=False)
    # decision / hybrid:要 identity;atoms 可 0
    assert _mk("decision", identity="谁").is_valid()
    assert _mk("hybrid", identity="谁").is_valid()
    assert not _mk("decision").is_valid()
    assert not _mk("hybrid", atoms=[atom]).is_valid(), "hybrid 无 identity 不该过"
    # executor:要 atoms≥1;identity 不作数
    assert _mk("executor", atoms=[atom]).is_valid()
    assert not _mk("executor", identity="谁").is_valid(), "executor 零原子不该过"
    # skill:要 skills≥1
    assert _mk("skill", skills=["s"]).is_valid()
    assert not _mk("skill", identity="谁").is_valid(), "skill 零技能不该过"


# ---- 回归锁(docs/84 在场 bug):纯人设(identity 有 / atoms 空)不再被 parse 丢弃降级 ----
def test_pure_persona_empty_atoms_not_dropped_by_parse():
    """提示词 :81 明说纯人设 atoms 留空数组合法;parse 原先 `if not atoms: return None`
    把它丢弃 → 烧了 token 却降级 v0 扁平拷。锁死:parse 必须返回结果且 is_valid。"""
    r = parse_decomposition(_PURE_PERSONA_JSON)
    assert r is not None, "纯人设空 atoms 又被 parse 丢弃了(bug 回归)"
    assert r.agent_kind == "hybrid" and r.is_valid()
    assert r.atoms == () and r.identity.startswith("资深谈判顾问")


def test_api_pure_persona_imports_as_role_not_v0(tmp_path):
    """回归锁(整条接线):纯人设 agent → decomposed=True 建成零原子顾问角色,不再降级 v0。"""
    from karvyloop.console.routes import api_agent_import
    request, atom_reg, role_reg = _app_with(tmp_path, FakeGateway(_PURE_PERSONA_JSON))
    req = types.SimpleNamespace(role_id="negotiator", source_type="generic-json",
                                system_prompt="You are a seasoned negotiation advisor.", tools=[])
    out = asyncio.run(api_agent_import(req, request))
    assert out["ok"] and out["decomposed"] is True, f"纯人设又降级 v0 了: {out}"
    assert out["import_kind"] == "advisory_persona" and out["atoms"] == []
    assert (role_reg.root / "negotiator").exists(), "角色没建成"
    assert len(atom_reg) == 0
    ident = (role_reg.root / "negotiator" / "IDENTITY.md").read_text(encoding="utf-8")
    assert "资深谈判顾问" in ident, "identity 没落进角色(还是 v0 模板占位?)"


# ---- executor 路由:只落公共原子库,不建 role ----
def test_api_executor_lands_atoms_without_role(tmp_path):
    from karvyloop.console.routes import api_agent_import
    request, atom_reg, role_reg = _app_with(tmp_path, FakeGateway(_EXECUTOR_JSON))
    req = types.SimpleNamespace(role_id="converter", source_type="generic-json",
                                system_prompt="convert pdf and dedupe csv", tools=["pdf", "csv"])
    out = asyncio.run(api_agent_import(req, request))
    assert out["ok"] and out["decomposed"] is True
    assert out["agent_kind"] == "executor" and out["import_kind"] == "pure_executor"
    # 不建 role(纯执行体不担责,不给决策席)
    assert not (role_reg.root / "converter").exists(), "executor 不该建 role"
    # 原子真落进公共库,任何角色可组合
    assert atom_reg.get("pdf_to_text") is not None and atom_reg.get("csv_dedupe") is not None
    assert set(out["atoms"]) == {"pdf_to_text", "csv_dedupe"} == set(out["atoms_created"])
    # role_id 降级为 provenance(origin 标来源)
    assert atom_reg.get("pdf_to_text").origin == "agent-import:converter"
    # note 如实(i18n,默认 en):落了 N 个原子 + 指路自建 role
    assert out["note"] and str(len(out["atoms"])) in out["note"]
    assert out["skills_bound"] == []


# ---- skill 路由:零写盘,指路技能库 ----
def test_api_skill_like_writes_nothing(tmp_path):
    from karvyloop.console.routes import api_agent_import
    request, atom_reg, role_reg = _app_with(tmp_path, FakeGateway(_SKILL_JSON))
    req = types.SimpleNamespace(role_id="weekly_sop", source_type="generic-json",
                                system_prompt="step by step weekly report SOP", tools=[])
    out = asyncio.run(api_agent_import(req, request))
    assert out["ok"] and out["decomposed"] is True
    assert out["agent_kind"] == "skill" and out["import_kind"] == "skill_like"
    assert len(atom_reg) == 0, "skill 型写了原子(该零写盘)"
    assert not (role_reg.root / "weekly_sop").exists(), "skill 型建了 role(该零写盘)"
    assert out["skills_recognized"] == ["weekly-report-sop"]
    assert out["note"], "skill_like 必须给指路 note"


# ---- decision 路由:零原子角色照样建成 ----
def test_api_decision_zero_atom_role_created(tmp_path):
    from karvyloop.console.routes import api_agent_import
    request, atom_reg, role_reg = _app_with(tmp_path, FakeGateway(_DECISION_JSON))
    req = types.SimpleNamespace(role_id="gatekeeper", source_type="generic-json",
                                system_prompt="you judge whether to enter the market", tools=[])
    out = asyncio.run(api_agent_import(req, request))
    assert out["ok"] and out["decomposed"] is True and out["agent_kind"] == "decision"
    assert (role_reg.root / "gatekeeper").exists(), "decision 零原子角色没建成"
    assert out["atoms"] == [] and len(atom_reg) == 0


# ---- 降级链照旧:is_valid 按型不过(executor 零原子)→ v0,如实标 decomposed=False ----
def test_api_executor_without_atoms_degrades_to_v0(tmp_path):
    from karvyloop.console.routes import api_agent_import
    bad = '{"agent_kind":"executor","identity":"有人设但没原子","atoms":[],"skills":[]}'
    request, atom_reg, role_reg = _app_with(tmp_path, FakeGateway(bad))
    req = types.SimpleNamespace(role_id="hollow_exec", source_type="generic-json",
                                system_prompt="x", tools=["t"])
    out = asyncio.run(api_agent_import(req, request))
    assert out["ok"] and out["decomposed"] is False, "executor 零原子该走 v0 降级"
    assert (role_reg.root / "hollow_exec").exists()   # v0 仍物化 7 文件(降级链不变)
    assert len(atom_reg) == 0
