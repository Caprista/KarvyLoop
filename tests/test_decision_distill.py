"""test_decision_distill — 显式陈述信号源(piggyback auto_distill,docs/02 §11 P1b)。

同一次 LLM 调用既抽"关于你的事实/偏好"(进记忆)又抽"你怎么决策"(进决策结晶),不加 token 成本;
两者分桶不混(一般偏好→记忆 Belief;拍板规则→决策偏好)。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.auto_distill import (  # noqa: E402
    distill_turns_with_decisions,
    parse_combined,
)
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.console.decision_wire import crystallize_candidates  # noqa: E402
from karvyloop.crystallize.decision_pref import is_decision_pref  # noqa: E402
from karvyloop.karvy.proposal_registry import PendingProposalRegistry  # noqa: E402

_COMBINED = ('{"facts":[{"content":"喜欢简洁","kind":"preference"}],'
             '"decisions":[{"content":"碰生产先写测试","kind":"constraint","explicit":true,"scope":"global"}]}')


class _StubGateway:
    def __init__(self, text):
        self._text = text

    def resolve_model(self, scope):
        return "stub/model"

    async def complete(self, messages, tools, ref, *, system=None):
        class TextDelta:
            def __init__(self, t):
                self.text = t
        yield TextDelta(self._text)


class _Turn:
    def __init__(self, u, a=""):
        self.user_intent = u
        self.agent_response = a


class _State:
    pass


class _App:
    def __init__(self, mem):
        self.state = _State()
        self.state.memory = mem
        self.state.ws_clients = set()
        self.state.proposal_registry = PendingProposalRegistry()


# ---- parse_combined ----


def test_parse_combined_splits_buckets():
    facts, dec = parse_combined(_COMBINED)
    assert len(facts) == 1 and facts[0]["content"] == "喜欢简洁"
    assert len(dec) == 1 and dec[0]["kind"] == "constraint" and dec[0]["explicit"] is True


def test_parse_combined_garbage_empty():
    assert parse_combined('{"facts": 坏掉') == ([], [])
    assert parse_combined("[1,2,3]") == ([], [])      # 数组(非对象)→ 空
    assert parse_combined("just prose") == ([], [])


def test_parse_combined_missing_keys():
    facts, dec = parse_combined('{"facts":[{"content":"x","kind":"fact"}]}')   # 无 decisions
    assert len(facts) == 1 and dec == []


# ---- distill_turns_with_decisions:一次调用,facts 写记忆 + 返回 decisions ----


@pytest.mark.asyncio
async def test_distill_writes_facts_returns_decisions():
    mem = MemoryManager()
    res, decisions = await distill_turns_with_decisions(
        [_Turn("我要上线", "好的")], gateway=_StubGateway(_COMBINED), mem=mem, now=1.0)
    assert res.written == 1                                   # fact 写进了记忆
    facts = [b for b in mem.index.all("personal") if not is_decision_pref(b)]
    assert any(b.content == "喜欢简洁" for b in facts)
    assert len(decisions) == 1 and decisions[0]["content"] == "碰生产先写测试"


@pytest.mark.asyncio
async def test_distill_empty_turns():
    mem = MemoryManager()
    res, decisions = await distill_turns_with_decisions(
        [], gateway=_StubGateway(_COMBINED), mem=mem)
    assert res.written == 0 and decisions == []


# ---- 端到端 piggyback:facts→记忆 / decisions→决策偏好,二者不混 ----


@pytest.mark.asyncio
async def test_distilled_decisions_crystallize_separately_from_facts():
    mem = MemoryManager()
    res, decisions = await distill_turns_with_decisions(
        [_Turn("上线", "ok")], gateway=_StubGateway(_COMBINED), mem=mem, now=1.0)
    await crystallize_candidates(_App(mem), decisions, now=2.0)
    prefs = [b for b in mem.index.all("personal") if is_decision_pref(b)]
    facts = [b for b in mem.index.all("personal") if not is_decision_pref(b)]
    assert len(prefs) == 1 and prefs[0].content == "碰生产先写测试"   # 决策规则 → 决策偏好
    assert any(b.content == "喜欢简洁" for b in facts)                # 一般偏好 → 记忆,不混
    assert prefs[0].provenance["status"] == "provisional"
