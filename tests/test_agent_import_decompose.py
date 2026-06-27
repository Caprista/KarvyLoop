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
