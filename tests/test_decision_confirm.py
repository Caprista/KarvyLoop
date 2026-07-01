"""test_decision_confirm — 决策偏好 H2A 确认闭环(docs/02 §11 P1)。

高价值 provisional 偏好 → 弹 H2A 建议(confirm_decision_pref) → ACCEPT → 升 confirmed。
"你画像的结晶本身要你拍板"的三位一体闭环。

AC:
- handler:升 provisional→confirmed(按内容回查,Belief 无 id)/幂等(已 confirmed)/缺失诚实失败
- factory:proposal_for_confirm_decision 形状(kind/payload/summary)
- 结晶流:高价值新偏好 → 自动弹确认建议(经 broadcast_proposal 注册进 registry);每条只弹一次
- 元循环防护:确认建议本身不应被当成新决策样本(在 ws 层跳过,见 ws.py;此处验 handler 不自生样本)
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.console.decision_wire import (  # noqa: E402
    DECISION_BATCH,
    maybe_crystallize_decisions,
    observe_decision,
    proposal_for_confirm_decision,
)
from karvyloop.console.proposal_handlers import build_proposal_handlers  # noqa: E402
from karvyloop.crystallize.decision_pref import (  # noqa: E402
    DecisionSample,
    is_decision_pref,
    make_decision_pref_belief,
)
from karvyloop.karvy.proposal_registry import (  # noqa: E402
    KIND_CONFIRM_DECISION_PREF,
    PendingProposalRegistry,
)


class _StubGateway:
    def __init__(self, text: str) -> None:
        self._text = text

    def resolve_model(self, scope):
        return "stub/model"

    async def complete(self, messages, tools, ref, *, system=None):
        class TextDelta:
            def __init__(self, text):
                self.text = text
        yield TextDelta(self._text)


class _State:
    pass


class _FakeApp:
    def __init__(self, gateway=None, mem=None) -> None:
        self.state = _State()
        self.state.runtime_kwargs = {"gateway": gateway, "model_ref": ""}
        self.state.memory = mem
        self.state.ws_clients = set()
        self.state.proposal_registry = PendingProposalRegistry()


class _Prop:
    def __init__(self, content):
        self.payload = {"content": content}
        self.summary = "记成你的默认偏好吗?"
        self.kind = KIND_CONFIRM_DECISION_PREF


def _prefs(mem):
    return [b for b in mem.index.all("personal") if is_decision_pref(b)]


def _decisions(n):
    return [DecisionSample(decision="REJECT", context=f"提案{i}", reason="没测试", ts=float(i))
            for i in range(n)]


# ---- handler ----


def test_confirm_handler_upgrades_provisional():
    mem = MemoryManager()
    mem.write(make_decision_pref_belief("先写测试", "constraint", strength=0.7,
                                        status="provisional", now=1.0))
    app = _FakeApp(mem=mem)
    handler = build_proposal_handlers(app)[KIND_CONFIRM_DECISION_PREF]
    ok, detail = handler(_Prop("先写测试"))
    assert ok
    prefs = _prefs(mem)
    assert len(prefs) == 1 and prefs[0].provenance["status"] == "confirmed"


def test_confirm_handler_idempotent_when_already_confirmed():
    mem = MemoryManager()
    mem.write(make_decision_pref_belief("已确认X", "taste", status="confirmed", now=1.0))
    app = _FakeApp(mem=mem)
    handler = build_proposal_handlers(app)[KIND_CONFIRM_DECISION_PREF]
    ok, _ = handler(_Prop("已确认X"))
    assert ok                       # 幂等成功(已是默认)
    assert len(_prefs(mem)) == 1    # 不重复写


def test_confirm_handler_missing_pref_fails_honestly():
    mem = MemoryManager()
    app = _FakeApp(mem=mem)
    handler = build_proposal_handlers(app)[KIND_CONFIRM_DECISION_PREF]
    ok, detail = handler(_Prop("压根没有这条"))
    assert not ok                   # 不假装成功(可能被后来的决策推翻撤销了)


# ---- factory ----


def test_proposal_for_confirm_shape():
    b = make_decision_pref_belief("输出默认用表格", "taste", strength=0.7, now=1.0)
    p = proposal_for_confirm_decision(b, now=5.0)
    assert p.kind == KIND_CONFIRM_DECISION_PREF
    assert p.payload["content"] == "输出默认用表格"
    assert "记成你的默认偏好" in p.summary
    assert p.proposal_id   # __post_init__ 派生稳定 id


# ---- 结晶流自动弹确认 ----


@pytest.mark.asyncio
async def test_high_value_crystallize_triggers_confirm_proposal():
    mem = MemoryManager()
    gw = _StubGateway('[{"content":"碰生产先写测试","kind":"constraint","explicit":true}]')
    app = _FakeApp(gateway=gw, mem=mem)
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    await maybe_crystallize_decisions(app)
    pend = app.state.proposal_registry.pending()
    assert any(getattr(p, "kind", "") == KIND_CONFIRM_DECISION_PREF for p in pend)


@pytest.mark.asyncio
async def test_confirm_proposed_once_only():
    mem = MemoryManager()
    gw = _StubGateway('[{"content":"碰生产先写测试","kind":"constraint","explicit":true}]')
    app = _FakeApp(gateway=gw, mem=mem)
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    await maybe_crystallize_decisions(app)
    # 再来一批同偏好(会走加固,不新结晶)→ 不应再弹第二条确认
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    await maybe_crystallize_decisions(app)
    confirms = [p for p in app.state.proposal_registry.pending()
                if getattr(p, "kind", "") == KIND_CONFIRM_DECISION_PREF]
    assert len(confirms) == 1   # 每条偏好只弹一次(守"按钮越来越少")


@pytest.mark.asyncio
async def test_low_value_implicit_does_not_trigger_confirm():
    # 隐式偏好(非高价值)即便结晶也不弹确认(只静默暂记)
    mem = MemoryManager()
    gw = _StubGateway('[{"content":"小偏好","kind":"taste","explicit":false}]')
    app = _FakeApp(gateway=gw, mem=mem)
    # 两批让隐式达到 K 而结晶
    for _ in range(2):
        for s in _decisions(DECISION_BATCH):
            observe_decision(app, s)
        await maybe_crystallize_decisions(app)
    assert len(_prefs(mem)) == 1                       # 已结晶
    confirms = [p for p in app.state.proposal_registry.pending()
                if getattr(p, "kind", "") == KIND_CONFIRM_DECISION_PREF]
    assert confirms == []                              # 但非高价值 → 不弹确认
