"""test_fuzzy_dispatch — 模糊指令 LLM 拆解层(Hardy 2026-06-27).

"去产品研发域,找几个人,帮我分析一下竞品" 这类模糊话 → LLM 拆出 域+人+方式 → 既有 H2A 提案。
假 gateway 走通整条接线(CI 可跑);真模型那刀在 test_e2e_pressure 的 J7。
"""
from __future__ import annotations

import asyncio
import types

from karvyloop.karvy.fuzzy_dispatch import (
    build_roster, decompose_dispatch, parse_fuzzy_plan)


# 类名必须正好 TextDelta(bootstrap/dispatch 用 type(ev).__name__ 收流)
class TextDelta:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeGateway:
    def __init__(self, payload: str) -> None:
        self._payload = payload
        self.calls = 0

    def resolve_model(self, scope):  # noqa: ANN001
        return "fake"

    async def complete(self, messages, tools, ref, system=None):  # noqa: ANN001
        self.calls += 1
        yield TextDelta(self._payload)


class _M:
    def __init__(self, role, agent_id=""):
        self.role = role
        self.agent_id = agent_id


class _D:
    def __init__(self, id, name, members):
        self.id = id
        self.name = name
        self.lifecycle = "active"
        self._members = members


class FakeDomainRegistry:
    def __init__(self, domains):
        self._domains = domains

    def list_all(self):
        return self._domains

    def resolve_members(self, did):
        for d in self._domains:
            if d.id == did:
                return d._members
        return []


def _roster():
    reg = FakeDomainRegistry([
        _D("d1", "产品研发", [_M("产品经理"), _M("前端工程师"), _M("后端工程师")]),
        _D("d2", "数据分析", [_M("数据科学家"), _M("业务分析师")]),
    ])
    app = types.SimpleNamespace(state=types.SimpleNamespace(domain_registry=reg))
    return build_roster(app), app


# ---- build_roster ----
def test_build_roster_lists_domains_and_members():
    roster, _ = _roster()
    assert len(roster) == 2
    pr = next(d for d in roster if d["domain_name"] == "产品研发")
    assert [m["name"] for m in pr["members"]] == ["产品经理", "前端工程师", "后端工程师"]


# ---- parse 解析 + 对齐真实 registry(宁空勿毒)----
def test_parse_roundtable_resolves_real_members():
    roster, _ = _roster()
    p = parse_fuzzy_plan('{"action":"roundtable","domain":"产品研发",'
                         '"participants":["产品经理","前端工程师"],"topic":"分析竞品"}', roster)
    assert p.action == "roundtable" and p.is_actionable() and p.domain_id == "d1"
    assert set(p.participant_names) == {"产品经理", "前端工程师"}


def test_parse_rejects_fabricated_domain_and_members():
    roster, _ = _roster()
    assert parse_fuzzy_plan('{"action":"roundtable","domain":"火星域","participants":["x"],"topic":"t"}', roster) is None
    assert parse_fuzzy_plan('{"action":"roundtable","domain":"产品研发","participants":["幽灵角色"],"topic":"t"}', roster) is None


def test_parse_self_and_ops_and_delegate_cap():
    roster, _ = _roster()
    assert not parse_fuzzy_plan('{"action":"self","topic":"闲聊"}', roster).is_actionable()
    assert parse_fuzzy_plan('{"action":"ops","topic":"诊断"}', roster).is_actionable()
    d = parse_fuzzy_plan('{"action":"delegate","domain":"产品研发","participants":["产品经理","前端工程师"],"topic":"写方案"}', roster)
    assert d.action == "delegate" and len(d.participants) == 1


def test_parse_garbage_returns_none():
    roster, _ = _roster()
    assert parse_fuzzy_plan("我建议找产品经理和前端", roster) is None        # prose
    assert parse_fuzzy_plan('{"action":"weird","topic":"x"}', roster) is None  # bad action
    assert parse_fuzzy_plan('{"atoms":[]}', roster) is None                    # wrong shape


# ---- decompose_dispatch(假 gateway)----
def test_decompose_dispatch_with_fake_gateway():
    roster, _ = _roster()
    gw = FakeGateway('{"action":"roundtable","domain":"数据分析",'
                     '"participants":["数据科学家","业务分析师"],"topic":"分析留存"}')
    p = asyncio.run(decompose_dispatch("去数据那边找几个人看看留存为啥掉", roster=roster, gateway=gw))
    assert gw.calls == 1 and p.action == "roundtable" and p.domain_id == "d2"
    assert set(p.participant_names) == {"数据科学家", "业务分析师"}


def test_decompose_none_gateway_or_empty_roster():
    roster, _ = _roster()
    assert asyncio.run(decompose_dispatch("x", roster=roster, gateway=None)) is None
    assert asyncio.run(decompose_dispatch("x", roster=[], gateway=FakeGateway("{}"))) is None


# ---- 整条接线:maybe_route_to_role 模糊兜底 → roundtable 提案 ----
def test_maybe_route_fuzzy_fallback_registers_roundtable():
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.console.routes import maybe_route_to_role
    from karvyloop.karvy.proposal_registry import KIND_ROUNDTABLE, PendingProposalRegistry
    import tempfile
    from pathlib import Path

    _, app = _roster()
    app.state.proposal_registry = PendingProposalRegistry()
    app.state.runtime_kwargs = {"gateway": FakeGateway(
        '{"action":"roundtable","domain":"产品研发","participants":["产品经理","前端工程师"],"topic":"分析竞品"}'),
        "model_ref": ""}
    app.state.ws_clients = set()
    mgr = ConversationManager(ConversationStore(Path(tempfile.mkdtemp()) / "conv"))
    mgr.start()

    # 模糊指令:没点名角色、没说"圆桌",但有 "找"(should_route)+"几个人"
    out = asyncio.run(maybe_route_to_role(app, mgr, "去产品研发域找几个人帮我分析下竞品"))
    assert out is not None and out["routed"] is True, out
    pend = app.state.proposal_registry.pending()
    assert pend and pend[-1].kind == KIND_ROUNDTABLE, "模糊指令没拆成圆桌提案"
    assert set(pend[-1].payload["participant_names"]) == {"产品经理", "前端工程师"}


def test_looks_like_ops_detects_diagnose_intents():
    """运维意图确定性识别(让 ops 能从自然语言路由,绕过 should_route=execute 把它拦掉)。"""
    from karvyloop.console.routes import _looks_like_ops
    assert _looks_like_ops("帮我诊断下系统哪里有问题")
    assert _looks_like_ops("运维一下,排查报错")
    assert _looks_like_ops("run a health check")
    assert not _looks_like_ops("帮我写个登录功能")
    assert not _looks_like_ops("找几个人分析竞品")


def test_maybe_route_fuzzy_self_returns_none():
    """拆出 self(不是编排)→ None → 小卡自己干(不强行路由、不投毒提案)。"""
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.console.routes import maybe_route_to_role
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry
    import tempfile
    from pathlib import Path

    _, app = _roster()
    app.state.proposal_registry = PendingProposalRegistry()
    app.state.runtime_kwargs = {"gateway": FakeGateway('{"action":"self","topic":"闲聊"}'), "model_ref": ""}
    app.state.ws_clients = set()
    mgr = ConversationManager(ConversationStore(Path(tempfile.mkdtemp()) / "conv"))
    mgr.start()
    out = asyncio.run(maybe_route_to_role(app, mgr, "帮我找几个人随便聊聊天气"))
    assert out is None and not app.state.proposal_registry.pending(), "self 不该出提案"
