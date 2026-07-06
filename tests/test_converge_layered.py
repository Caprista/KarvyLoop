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
    converge_and_propose,
    parse_candidates,
)


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
