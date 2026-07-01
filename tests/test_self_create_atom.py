"""docs/02 §15.5 — create_atom:role 自造原子(显式路径,化解空壳)。

不变量:① search-first(查池=消费路径+防重复)② 合成宁空勿毒(垃圾→failed,不投毒)
③ 合并闸近义复用(不无脑加)④ 出生 provisional/self_created ⑤ 沉淀:认可→进 role composition+留、拒→撤
⑥ 只动 self_created+provisional 的原子(不误碰正式/合并/导入)。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from karvyloop.atoms.registry import AtomRegistry
from karvyloop.atoms.self_create import (
    create_atom,
    make_self_create_tool,
    search_pool,
    sediment_self_created,
)
from karvyloop.registry.tool import is_factory_built


class TextDelta:  # 名字必须是 TextDelta(执行器/合成器按 type().__name__ 认)
    def __init__(self, text):
        self.text = text


class FakeGateway:
    def __init__(self, out: str):
        self.out = out

    def resolve_model(self, scope):
        return "m"

    async def complete(self, msgs, tools, model_ref, *, system=None):
        yield TextDelta(self.out)


_GOOD = '{"id": "summarize_pdf", "prompt": "读 PDF 并产出带页码的摘要", "tools": ["read_file"]}'


def _areg() -> AtomRegistry:
    return AtomRegistry()  # 纯内存


# ============ search-first ============

def test_search_pool_finds_match_and_respects_threshold():
    areg = _areg()
    areg.create("web_search", "task", "search the web for information")
    assert search_pool("I need to search the web", areg, threshold=0.4) == "web_search"
    assert search_pool("bake a chocolate cake", areg, threshold=0.4) is None


def test_search_prefers_formal_over_provisional():
    areg = _areg()
    areg.create("a_prov", "task", "search the web", provisional=True, origin="self_created")
    areg.create("a_formal", "task", "search the web")  # 正式
    # 同义两个,正式优先
    assert search_pool("search the web", areg, threshold=0.5) == "a_formal"


# ============ create_atom 主链 ============

def test_create_reuses_existing_pool_atom():
    """search-first:池里有能干的 → 复用(消费路径),不造新的。"""
    areg = _areg()
    areg.create("web_search", "task", "search the web for info")
    res = asyncio.run(create_atom("search the web for me", gateway=FakeGateway(_GOOD), atom_registry=areg))
    assert res["action"] == "reused" and res["atom_id"] == "web_search"


class _ScopeStrictGateway(FakeGateway):
    """resolve_model 像真 GatewayClient 那样读 scope.atom_model/role_model/domain_model ——
    锁 VM 真模型抓到的回归:旧 _DefaultScope 只有 atom_model → AttributeError → synth 静默 None。"""
    def resolve_model(self, scope):
        _ = (scope.atom_model, scope.role_model, scope.domain_model)  # 缺任一即崩
        return "m"


def test_synth_passes_real_resolve_scope_shape():
    """真模型回归:resolve_model 读 scope 的三个 model 字段都不崩(用真 ResolveScope,非手搓 stub)。"""
    areg = _areg()
    res = asyncio.run(create_atom("把 PDF 转成带页码摘要", gateway=_ScopeStrictGateway(_GOOD),
                                  atom_registry=areg))
    assert res["action"] == "created"  # resolve_model 没崩 → 真的合成出来了


def test_create_synthesizes_provisional_when_none():
    """池里没有 → LLM 合成 → 出生 provisional/self_created。"""
    areg = _areg()
    res = asyncio.run(create_atom("把 PDF 转成带页码摘要", gateway=FakeGateway(_GOOD), atom_registry=areg))
    assert res["action"] == "created"
    a = areg.get(res["atom_id"])
    assert a is not None and a.provisional is True and a.origin == "self_created"


def test_create_garbage_synthesis_fails_not_poisons():
    """合成出垃圾(非 JSON)→ failed,绝不把垃圾塞进池(宁空勿毒)。"""
    areg = _areg()
    n0 = len(areg.list_all())
    res = asyncio.run(create_atom("某种从没见过的能力xyz", gateway=FakeGateway("我觉得可以这样做哦~"), atom_registry=areg))
    assert res["action"] == "failed"
    assert len(areg.list_all()) == n0  # 池没被污染


_GOOD_EN = '{"id": "web_fetcher", "prompt": "fetch a web page and extract its text", "tools": ["web_fetch"]}'


def test_create_merge_gate_reuses_near_dup():
    """合成出的用途和池里近义(≥0.7,共享 ≥2 token)→ 复用,不新增(合并闸防自我污染)。"""
    areg = _areg()
    areg.create("page_fetcher", "task", "fetch a web page and extract text")  # 和 _GOOD_EN 近义
    # desc 本身不撞 search-first(不同词),但合成出的 prompt 撞 → 合并闸拦
    res = asyncio.run(create_atom("need some capability foobarbaz quux", gateway=FakeGateway(_GOOD_EN),
                                  atom_registry=areg))
    assert res["action"] == "reused" and res["atom_id"] == "page_fetcher"


_GOOD_TAGGED = ('{"id":"translate_x","prompt":"将材料译成英语","tools":[],'
                '"tags":["translate","english","localize"]}')


def test_parse_spec_extracts_tags():
    from karvyloop.atoms.self_create import _parse_spec
    assert _parse_spec(_GOOD_TAGGED)["tags"] == ["translate", "english", "localize"]


def test_tag_overlap_merge_gate_reuses_paraphrase():
    """合并闸标签层:lexical(含 CJK bigram)抓不到的深层改写,靠 LLM 标签重叠(≥2)认出同义→复用。"""
    areg = _areg()
    areg.create("zh_en", "task", "把文档从中文转为英文", tags=["translate", "chinese", "english"])
    # desc 与现有 atom 字面/bigram 都不重叠(过 search-first)→ 合成 → 合成 tags 与 atom tags 共享 {translate,english}≥2
    res = asyncio.run(create_atom("处理这份材料materialXYZ", gateway=FakeGateway(_GOOD_TAGGED), atom_registry=areg))
    assert res["action"] == "reused" and res["atom_id"] == "zh_en"


def test_search_pool_matches_via_tags():
    """search-first 也认 atom 的 tags(标签是可匹配的归一化 token)。"""
    areg = _areg()
    areg.create("inv", "task", "some unrelated prompt", tags=["invoice", "export", "pdf"])
    assert search_pool("export invoice", areg, threshold=0.5) == "inv"


def test_created_atom_carries_tags():
    areg = _areg()
    res = asyncio.run(create_atom("把某种没见过的东西foobarbaz处理一下", gateway=FakeGateway(_GOOD_TAGGED),
                                  atom_registry=areg))
    assert res["action"] == "created"
    assert areg.get(res["atom_id"]).tags == ["translate", "english", "localize"]


def test_cjk_bigram_lexical_reuses_surface_near_dup():
    """#3 零模型:CJK 字符 bigram 让 lexical 对中文表面近义也能去重(换措辞),不用 embedding。"""
    areg = _areg()
    areg.create("zh_summarize", "task", "把中文文章总结成要点")
    # "中文文章总结" 与 "把中文文章总结成要点" 共享 bigram {中文,文文,文章,章总,总结} ≥2 → 复用
    assert search_pool("帮我对这篇中文文章总结一下", areg, threshold=0.4) == "zh_summarize"


def test_cjk_bigram_no_false_reuse_unrelated():
    """不相干中文(共享 bigram <2)→ 不误判复用。"""
    areg = _areg()
    areg.create("zh_summarize", "task", "把中文文章总结成要点")
    assert search_pool("给猫咪做一份健康食谱", areg, threshold=0.4) is None


def test_single_shared_token_is_not_a_false_match():
    """#3:两个不相干描述只共享一个内嵌 ascii 词(如 PDF)→ **不**误判同义。"""
    areg = _areg()
    areg.create("resume_to_pdf", "task", "把用户的简历内容排版并导出为PDF")
    # "发票汇总导出PDF" 与 "简历排版导出PDF" 只共享 ascii token {pdf} → 不该复用 resume_to_pdf
    assert search_pool("把发票数据汇总并导出为PDF", areg, threshold=0.5) is None


def test_minted_collector_records_created_atoms():
    """make_self_create_tool 的 minted 收集器:新造的 atom_id 记进去(给收尾沉淀用);复用不记。"""
    areg = _areg()
    minted: list = []
    tool = make_self_create_tool(gateway=FakeGateway(_GOOD_EN), atom_registry=areg, minted=minted)
    out = asyncio.run(tool.call({"capability": "I need to grab a webpage and read it"}, None, None))
    assert out["action"] == "created" and minted == [out["atom_id"]]
    # 第二次描述与已造原子共享 ≥2 token → 复用,不再往 minted 加
    out2 = asyncio.run(tool.call({"capability": "fetch a web page and extract its text now"}, None, None))
    assert out2["action"] == "reused" and minted == [out["atom_id"]]  # 没变长


# ============ 沉淀 ============

def test_sediment_reject_reverts():
    areg = _areg()
    areg.create("tmp1", "task", "x", provisional=True, origin="self_created")
    res = sediment_self_created("tmp1", approved=False, atom_registry=areg)
    assert res["action"] == "reverted"
    assert areg.get("tmp1") is None


def test_sediment_approve_keeps_and_composes(tmp_path: Path):
    from karvyloop.roles.registry import RoleRegistry

    areg = _areg()
    areg.create("tmp2", "task", "x", provisional=True, origin="self_created")
    rreg = RoleRegistry(tmp_path / "roles")
    rreg.create("analyst", identity="分析师")
    res = sediment_self_created("tmp2", approved=True, atom_registry=areg,
                                role_registry=rreg, role_id="analyst")
    assert res["action"] == "kept" and res["composed_into_role"] is True
    assert areg.get("tmp2") is not None              # 留着(provisional,靠复用转正)
    assert "tmp2" in rreg.get("analyst").atom_ids     # 进了 composition = 被引用资产


def test_sediment_reject_wont_delete_referenced_atom(tmp_path: Path):
    """#2 dangling 守:approved=False 但该 atom 已被某 role composition 引用 → **不删**(防悬空)。"""
    from karvyloop.roles.registry import RoleRegistry

    areg = _areg()
    areg.create("refd", "task", "x", provisional=True, origin="self_created")
    rreg = RoleRegistry(tmp_path / "roles")
    rreg.create("r1", identity="r")
    rreg.add_atom("r1", "refd")  # 被引用
    res = sediment_self_created("refd", approved=False, atom_registry=areg,
                                role_registry=rreg, role_id="other")
    assert res["action"] == "kept_referenced"
    assert areg.get("refd") is not None              # 没被删 → 无悬空


def test_self_create_tool_required_mode_matches_policy():
    """#4:工具 required_mode 与 policy 表一致(WORKSPACE_WRITE),别留 build_tool 默认 FULL。"""
    from karvyloop.capability import Mode
    tool = make_self_create_tool(gateway=FakeGateway(_GOOD), atom_registry=_areg())
    assert tool.required_mode == Mode.WORKSPACE_WRITE


def test_sediment_only_touches_self_created_provisional():
    """不误碰正式原子 / 非 self_created 的 provisional(如合并出的)。"""
    areg = _areg()
    areg.create("formal", "task", "x")                                  # 正式
    areg.create("merged", "task", "y", provisional=True, origin="merge")  # provisional 但非 self_created
    assert sediment_self_created("formal", approved=False, atom_registry=areg)["action"] == "noop"
    assert sediment_self_created("merged", approved=False, atom_registry=areg)["action"] == "noop"
    assert areg.get("formal") is not None and areg.get("merged") is not None


# ============ Tool 包装 ============

def test_judge_atom_keep_parses_keep_and_drop():
    """role 综合裁:LLM 给 keep:true/false 都如实返回 + 带 reason。"""
    from karvyloop.atoms.self_create import judge_atom_keep
    areg = _areg()
    spec = areg.create("cand", "task", "do a reusable thing", provisional=True, origin="self_created")
    jk = asyncio.run(judge_atom_keep(spec, role_id="analyst", human_approved=True, contributed=True,
                                     verified=True, gateway=FakeGateway('{"keep": true, "reason": "通用且过验证"}')))
    assert jk["keep"] is True and "通用" in jk["reason"]
    jd = asyncio.run(judge_atom_keep(spec, role_id="analyst", human_approved=True, contributed=True,
                                     verified=False, gateway=FakeGateway('{"keep": false, "reason": "太窄,只为这次"}')))
    assert jd["keep"] is False


def test_judge_atom_keep_conservative_on_garbage():
    """宁空勿毒:判断输出是垃圾/非 JSON/无明确 keep → 保守 keep=False(不拿没把握的污染池)。"""
    from karvyloop.atoms.self_create import judge_atom_keep
    areg = _areg()
    spec = areg.create("c2", "task", "x", provisional=True, origin="self_created")
    for bad in ("我觉得可以留着哦", '{"reason": "没给 keep"}', '{"keep": "yes"}', "not json {"):
        jk = asyncio.run(judge_atom_keep(spec, role_id="r", human_approved=True, contributed=True,
                                         verified=True, gateway=FakeGateway(bad)))
        assert jk["keep"] is False


def test_make_tool_is_factory_built_and_callable():
    areg = _areg()
    tool = make_self_create_tool(gateway=FakeGateway(_GOOD), atom_registry=areg)
    assert tool.name == "create_atom"
    assert is_factory_built(tool) is True              # 走了 build_tool(HR-1)
    out = asyncio.run(tool.call({"capability": "把 PDF 转成带页码摘要"}, None, None))
    assert out["action"] == "created"
    # 空描述安全兜
    out2 = asyncio.run(tool.call({}, None, None))
    assert out2.get("ok") is False
