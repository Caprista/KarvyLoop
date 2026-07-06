"""收敛式分层认知提议(converge.py)的确定性回归 —— 不用 key,假 gateway。

守 docs/66 §D:颗粒度由理解关分层(经历/推理/原则/校正/涌现)、宁空勿毒、绝不猜时间、
产候选不写库。converge_and_propose 只产出、不碰 mem。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from karvyloop.cognition.converge import (
    DEPTH_BY_LAYER,
    LAYERS,
    CognitionCandidate,
    _parse_when,
    converge_and_propose,
    parse_candidates,
    sediment_confirmed,
    build_sediment_card,
    apply_confirmation,
    SedimentTracker,
)


class _FakeMem:
    def __init__(self) -> None:
        self.written: list = []
        self.concept_cache = None

    def write(self, b, *, pinned: bool = False) -> bool:  # noqa: ANN001
        self.written.append(b)
        return True


class _FakeTrace:
    def __init__(self) -> None:
        self.entries: list = []

    def append(self, entry) -> str:  # noqa: ANN001
        self.entries.append(entry)
        return "tid"


# ---- 假 gateway:complete 必须 yield 名为 "TextDelta" 的事件(代码按 type().__name__ 收) ----
class TextDelta:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeGateway:
    def __init__(self, out: str, *, chunks: int = 3, raise_on_complete: bool = False) -> None:
        self._out = out
        self._chunks = chunks
        self._raise = raise_on_complete

    def resolve_model(self, scope):  # noqa: ANN001
        return "fake-model"

    async def complete(self, messages, tools, ref, system=None):  # noqa: ANN001
        if self._raise:
            raise RuntimeError("boom")
        # 分块 yield,顺便验证累加
        n = max(1, len(self._out) // self._chunks)
        for i in range(0, len(self._out), n):
            yield TextDelta(self._out[i:i + n])


def _turns():
    return [SimpleNamespace(user_intent="我从 React 换到了 Vue", agent_response="为什么切换?")]


# ---------------------------------------------------------------- parse_candidates
def test_parse_happy_all_layers():
    payload = [
        {"content": "从 React 换到了 Vue", "layer": "experience", "why": "做过=能力", "when": None},
        {"content": "因为团队协作更顺", "layer": "reasoning", "why": "那次的推理", "when": "上个月"},
        {"content": "别为半年后模型会有的功能提前建", "layer": "principle", "why": "约束决策", "when": None},
        {"content": "不做≠不好", "layer": "corrective", "why": "校正别的推理", "when": None},
        {"content": "每个决策都藏着隐含假设", "layer": "emergent", "why": "聊才涌现", "when": None},
    ]
    cands = parse_candidates(json.dumps(payload, ensure_ascii=False))
    assert [c.layer for c in cands] == list(LAYERS)
    assert [c.depth for c in cands] == [1, 2, 3, 4, 5]      # 深度递增
    # 绝不猜时间:只有明说"上个月"的那条带 when_hint
    assert cands[1].when_hint == "上个月"
    assert all(c.when_hint is None for c in cands if c.layer != "reasoning")
    assert cands[0].id and len(cands[0].id) == 12           # 内容哈希 id


def test_parse_skips_unknown_layer_and_empty_content():
    payload = [
        {"content": "好的一条", "layer": "experience", "why": ""},
        {"content": "未知层丢掉", "layer": "made_up_layer", "why": ""},   # 未知层 → 跳
        {"content": "", "layer": "principle", "why": ""},                # 空内容 → 跳
        {"content": "   ", "layer": "principle"},                        # 纯空白 → 跳
    ]
    cands = parse_candidates(json.dumps(payload, ensure_ascii=False))
    assert len(cands) == 1
    assert cands[0].content == "好的一条"


def test_parse_dedup_by_content():
    payload = [
        {"content": "同一条", "layer": "experience"},
        {"content": "同一条", "layer": "emergent"},   # 内容重复 → 去重(保第一条)
    ]
    cands = parse_candidates(json.dumps(payload, ensure_ascii=False))
    assert len(cands) == 1
    assert cands[0].layer == "experience"


def test_parse_refuses_garbage_ningkong_wudu():
    # 宁空勿毒:各种非严格 JSON 数组 → []
    assert parse_candidates("") == []
    assert parse_candidates("这是一段散文,不是 JSON") == []
    assert parse_candidates(json.dumps({"content": "对象不是数组"})) == []   # dict 非 list
    assert parse_candidates("42") == []                                      # 数字
    assert parse_candidates("[") == []                                       # 半截


def test_parse_strips_code_fence():
    payload = [{"content": "带栅栏", "layer": "principle"}]
    fenced = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    cands = parse_candidates(fenced)
    assert len(cands) == 1 and cands[0].content == "带栅栏"


def test_layers_taxonomy_stable():
    assert LAYERS == ("experience", "reasoning", "principle", "corrective", "emergent")
    assert DEPTH_BY_LAYER["emergent"] == 5 and DEPTH_BY_LAYER["experience"] == 1
    c = CognitionCandidate(content="x", layer="emergent")
    assert c.depth == 5 and c.id                              # __post_init__ 自动补 id


# ---------------------------------------------------------------- converge_and_propose
def test_converge_happy_returns_candidates():
    out = json.dumps([
        {"content": "从 React 换到了 Vue", "layer": "experience", "why": "", "when": None},
        {"content": "每个决策都藏着隐含假设", "layer": "emergent", "why": "聊才涌现", "when": None},
    ], ensure_ascii=False)
    cands = asyncio.run(converge_and_propose(_turns(), gateway=_FakeGateway(out), model_ref="m"))
    assert len(cands) == 2
    assert cands[-1].layer == "emergent" and cands[-1].depth == 5


def test_converge_empty_turns_no_llm_call():
    # 空对话:不调 gateway,直接 []
    empty = [SimpleNamespace(user_intent="", agent_response="")]
    cands = asyncio.run(converge_and_propose(empty, gateway=_FakeGateway("[]"), model_ref="m"))
    assert cands == []


def test_converge_llm_error_returns_empty():
    gw = _FakeGateway("", raise_on_complete=True)
    cands = asyncio.run(converge_and_propose(_turns(), gateway=gw, model_ref="m"))
    assert cands == []                                        # 调用异常 → 宁空,不炸调用方


def test_converge_garbage_output_returns_empty():
    gw = _FakeGateway("模型今天不听话,吐了一段散文")
    cands = asyncio.run(converge_and_propose(_turns(), gateway=gw, model_ref="m"))
    assert cands == []                                        # 宁空勿毒:坏输出不进候选


# ---------------------------------------------------------------- _parse_when(绝不猜时间)
def test_parse_when_absolute_only():
    assert _parse_when("2026") is not None
    assert _parse_when("2026-03") is not None
    assert _parse_when("2026-03-15") is not None
    assert _parse_when("上个月") is None            # 相对 → 不猜
    assert _parse_when("Vue 之前") is None           # 模糊 → 不猜
    assert _parse_when(None) is None
    assert _parse_when("") is None
    assert _parse_when("2026-13-99") is None         # 非法日期 → None,不炸


# ---------------------------------------------------------------- sediment_confirmed(只沉确认的)
def test_sediment_writes_user_explicit_and_layers():
    mem, trace = _FakeMem(), _FakeTrace()
    cands = [
        CognitionCandidate(content="从 React 换到了 Vue", layer="experience"),
        CognitionCandidate(content="每个决策都藏着隐含假设", layer="emergent", why="聊才涌现"),
        CognitionCandidate(content="   ", layer="principle"),   # 空内容 → 跳
    ]
    res = asyncio.run(sediment_confirmed(cands, mem=mem, gateway=None, now=1000.0,
                                         trace=trace, learned_via="conv#42"))
    assert res["written"] == 2 and len(mem.written) == 2
    for b in mem.written:
        assert b.provenance["source"] == "user_explicit"      # 最高档
        assert "provisional" not in b.provenance              # 不是 auto 蒸的低置信
        assert b.provenance["learned_via"] == "conv#42"       # 理解出处
        assert b.scope == "personal" and b.freshness_ts == 1000.0
    assert {b.provenance["layer"] for b in mem.written} == {"experience", "emergent"}
    # Trace 只记确认沉淀的一条
    assert len(trace.entries) == 1 and trace.entries[0].kind == "belief_sedimented"
    assert trace.entries[0].payload["n"] == 2


def test_sediment_valid_from_absolute_vs_relative():
    mem = _FakeMem()
    cands = [
        CognitionCandidate(content="绝对时间", layer="experience", when_hint="2026-03"),
        CognitionCandidate(content="相对时间", layer="experience", when_hint="上个月"),
        CognitionCandidate(content="没时间", layer="experience"),
    ]
    asyncio.run(sediment_confirmed(cands, mem=mem, gateway=None, now=1000.0))
    by = {b.content: b.provenance for b in mem.written}
    assert isinstance(by["绝对时间"].get("valid_from"), float)          # 绝对日期 → float
    assert "valid_from" not in by["相对时间"]                            # 相对 → 不填 float
    assert by["相对时间"]["valid_from_hint"] == "上个月"                 # 留原话字符串
    assert "valid_from" not in by["没时间"] and "valid_from_hint" not in by["没时间"]


def test_sediment_empty_list_noop():
    mem = _FakeMem()
    res = asyncio.run(sediment_confirmed([], mem=mem, gateway=None, now=1000.0))
    assert res["written"] == 0 and mem.written == []


# ---------------------------------------------------------------- ② 确认卡 + 防盲拍
def _five_candidates():
    return [
        CognitionCandidate(content="每个决策都藏着隐含假设", layer="emergent", why="聊才涌现"),
        CognitionCandidate(content="从 React 换到了 Vue", layer="experience"),
        CognitionCandidate(content="不做≠不好", layer="corrective"),
        CognitionCandidate(content="因为团队协作更顺", layer="reasoning"),
        CognitionCandidate(content="别为半年后模型会有的功能提前建", layer="principle"),
    ]


def test_build_card_sorted_by_depth_and_flags_deep():
    card = build_sediment_card(_five_candidates(), conversation_ref="conv#7")
    assert card["kind"] == "sediment" and card["conversation_ref"] == "conv#7"
    assert card["n"] == 5 and card["max_depth"] == 5
    depths = [it["depth"] for it in card["items"]]
    assert depths == sorted(depths) == [1, 2, 3, 4, 5]        # 浅→深(经历在前,涌现殿后)
    # depth≥4(校正/涌现)= 模型替你立的观点 → needs_attention
    assert [it["needs_attention"] for it in card["items"]] == [False, False, False, True, True]


def test_apply_confirmation_accept_edit_drop_and_default_drop():
    cands = _five_candidates()
    by_layer = {c.layer: c for c in cands}
    decisions = {
        by_layer["experience"].id: {"action": "accept"},
        by_layer["reasoning"].id: {"action": "edit", "content": "换 Vue 是因为团队都熟"},
        by_layer["principle"].id: {"action": "drop"},
        # corrective / emergent 不在 decisions 里 → 未确认 = 不沉
    }
    accepted, engaged = apply_confirmation(cands, decisions)
    assert engaged is True                                     # 有 edit/drop = 真判断过
    contents = {c.content for c in accepted}
    assert contents == {"从 React 换到了 Vue", "换 Vue 是因为团队都熟"}
    edited = next(c for c in accepted if c.layer == "reasoning")
    assert edited.id == CognitionCandidate(content="换 Vue 是因为团队都熟", layer="reasoning").id  # id 重算


def test_apply_confirmation_blind_accept_all_not_engaged():
    cands = _five_candidates()
    decisions = {c.id: {"action": "accept"} for c in cands}
    accepted, engaged = apply_confirmation(cands, decisions)
    assert len(accepted) == 5 and engaged is False             # 全收零改零删 = 盲拍


def test_apply_confirmation_edit_to_empty_is_drop():
    cands = [_five_candidates()[1]]
    accepted, engaged = apply_confirmation(cands, {cands[0].id: {"action": "edit", "content": "  "}})
    assert accepted == [] and engaged is True


def test_sediment_tracker_deep_blind_counts_double():
    t = SedimentTracker()                                      # threshold=3
    t.record(accepted_any=True, engaged=False, max_depth=5)    # 盲拍含涌现 → +2
    assert not t.needs_recheck()
    t.record(accepted_any=True, engaged=False, max_depth=1)    # 盲拍浅层 → +1 → 3
    assert t.needs_recheck()
    t.record(accepted_any=True, engaged=True, max_depth=5)     # 真判断过 → 归零
    assert t.score == 0 and not t.needs_recheck()


def test_full_chain_card_to_sediment():
    """整链串测:候选 → 卡 → 用户逐条确认 → 只沉确认的(user_explicit)。"""
    mem, trace = _FakeMem(), _FakeTrace()
    cands = _five_candidates()
    card = build_sediment_card(cands)
    assert card["n"] == 5
    # 用户:收经历+涌现(涌现改了措辞),删原则,其余没动(=不沉)
    by_layer = {c.layer: c for c in cands}
    decisions = {
        by_layer["experience"].id: {"action": "accept"},
        by_layer["emergent"].id: {"action": "edit", "content": "跨域套用认知前,先刨出它的隐含假设"},
        by_layer["principle"].id: {"action": "drop"},
    }
    accepted, engaged = apply_confirmation(cands, decisions)
    assert engaged is True and len(accepted) == 2
    res = asyncio.run(sediment_confirmed(accepted, mem=mem, gateway=None, now=1000.0,
                                         trace=trace, learned_via="conv#7"))
    assert res["written"] == 2
    assert all(b.provenance["source"] == "user_explicit" for b in mem.written)
    assert {b.provenance["layer"] for b in mem.written} == {"experience", "emergent"}
    edited = next(b for b in mem.written if b.provenance["layer"] == "emergent")
    assert edited.content == "跨域套用认知前,先刨出它的隐含假设"   # 沉的是你改后的话
    assert trace.entries[0].payload["n"] == 2                      # Trace 只记确认沉淀的


def test_parse_salvages_truncated_array():
    """真机实拍:思考型模型烧掉 max_tokens,数组尾被截 → 打捞完整项,别整包丢弃误报"没什么可沉"。"""
    full = json.dumps([
        {"content": "完整的第一条", "layer": "experience", "why": "", "when": None},
        {"content": "完整的第二条", "layer": "principle", "why": "", "when": None},
    ], ensure_ascii=False)
    truncated = full[:-1] + ', {"content": "被截断的第三'   # 合法尾巴被截
    cands = parse_candidates(truncated)
    assert [c.content for c in cands] == ["完整的第一条", "完整的第二条"]


def test_parse_extracts_array_wrapped_in_prose():
    """真模型偶发把数组裹在散文里 → 提取数组本体,不整包判废。"""
    payload = json.dumps([{"content": "裹在散文里", "layer": "emergent", "why": "", "when": None}],
                         ensure_ascii=False)
    wrapped = "好的,我把这段对话收敛成以下认知候选:\n" + payload + "\n以上,请逐条确认。"
    cands = parse_candidates(wrapped)
    assert len(cands) == 1 and cands[0].content == "裹在散文里" and cands[0].layer == "emergent"
