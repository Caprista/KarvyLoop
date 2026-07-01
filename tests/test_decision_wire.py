"""test_decision_wire — 决策接口结晶接线(docs/02 §11 console 侧)。

AC:
- observe 攒样本进缓冲
- maybe_crystallize:攒够批量 → 抽候选 → 双关门 → 写认知库(Belief);未够批不动
- 隐式候选跨批复现 <K 不写、≥K 才写;显式 1 次即写
- 去重:已有同偏好不重复写
- prealign_governance:从认知库召回决策偏好 → 预对齐块(忽略普通 Belief)
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.crystallize.decision_pref import (  # noqa: E402
    DecisionSample,
    is_decision_pref,
    make_decision_pref_belief,
)
from karvyloop.console.decision_wire import (  # noqa: E402
    DECISION_BATCH,
    crystallize_candidates,
    maybe_crystallize_decisions,
    observe_decision,
    prealign_governance,
)
from karvyloop.console.decision_log import RevocationStore  # noqa: E402


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


def _decisions(n):
    return [DecisionSample(decision="REJECT", context=f"提案{i}", reason="没测试", ts=float(i))
            for i in range(n)]


def _count_prefs(mem):
    return sum(1 for b in mem.index.all("personal") if is_decision_pref(b))


# ---- observe + batch gate ----


def test_observe_accumulates():
    app = _FakeApp()
    observe_decision(app, _decisions(1)[0])
    observe_decision(app, _decisions(1)[0])
    assert len(app.state.decision_samples) == 2


@pytest.mark.asyncio
async def test_under_batch_does_nothing():
    mem = MemoryManager()
    app = _FakeApp(gateway=_StubGateway("[]"), mem=mem)
    for s in _decisions(DECISION_BATCH - 1):
        observe_decision(app, s)
    written = await maybe_crystallize_decisions(app)
    assert written == 0
    assert len(app.state.decision_samples) == DECISION_BATCH - 1   # 没够批,缓冲不清


@pytest.mark.asyncio
async def test_explicit_candidate_crystallizes_provisional():
    mem = MemoryManager()
    gw = _StubGateway('[{"content":"碰生产先写测试","kind":"constraint","explicit":true}]')
    app = _FakeApp(gateway=gw, mem=mem)
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    written = await maybe_crystallize_decisions(app)
    assert written == 1
    assert _count_prefs(mem) == 1
    pref = next(b for b in mem.index.all("personal") if is_decision_pref(b))
    assert pref.provenance["status"] == "provisional"
    assert len(app.state.decision_samples) == 0   # 批已消费


@pytest.mark.asyncio
async def test_implicit_needs_recurrence_K():
    mem = MemoryManager()
    gw = _StubGateway('[{"content":"输出默认用表格","kind":"taste","explicit":false}]')
    app = _FakeApp(gateway=gw, mem=mem)
    # 第 1 批:隐式候选首次出现 → 复现计数=1 < K(2) → 不写
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    assert await maybe_crystallize_decisions(app) == 0
    assert _count_prefs(mem) == 0
    # 第 2 批:同候选再现 → 计数=2 ≥ K → 写
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    assert await maybe_crystallize_decisions(app) == 1
    assert _count_prefs(mem) == 1


@pytest.mark.asyncio
async def test_dedup_existing_pref_not_rewritten():
    mem = MemoryManager()
    gw = _StubGateway('[{"content":"碰生产先写测试","kind":"constraint","explicit":true}]')
    app = _FakeApp(gateway=gw, mem=mem)
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    await maybe_crystallize_decisions(app)
    # 再来一批同样的候选 → 已有该偏好,不重复写
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    await maybe_crystallize_decisions(app)
    assert _count_prefs(mem) == 1


# ---- 撤回墓碑:撤过的偏好冷却窗内别自动学回来(让"撤回"有牙) ----


def test_revocation_store_mark_suppress_clear():
    rs = RevocationStore(cooldown_days=14.0)
    assert not rs.is_suppressed("用表格", now=100.0)       # 没撤过
    rs.mark("用表格", now=100.0)
    assert rs.is_suppressed("用表格", now=100.0)            # 刚撤,窗口内
    assert rs.is_suppressed("用表格", now=100.0 + 13 * 86400)  # 13 天仍抑制
    assert not rs.is_suppressed("用表格", now=100.0 + 15 * 86400)  # 15 天 > 14 天窗口 → 放行
    rs.clear("用表格")
    assert not rs.is_suppressed("用表格", now=100.0)        # 解除


def test_revocation_store_persists(tmp_path):
    p = tmp_path / "revoked.json"
    RevocationStore(path=p).mark("用表格", now=100.0)
    assert RevocationStore(path=p).is_suppressed("用表格", now=100.0)   # 重开仍记得(跨重启)


@pytest.mark.asyncio
async def test_revoked_pref_not_relearned_in_cooldown():
    """核心:撤回后,**同一条**就算被显式重述(support=1 本会立刻结晶)也不复活——窗口内。"""
    mem = MemoryManager()
    app = _FakeApp(mem=mem)
    NOW = 1_000_000.0
    app.state.decision_revocations = RevocationStore(cooldown_days=14.0)
    app.state.decision_revocations.mark("输出默认用表格", now=NOW)     # 你刚撤过
    cand = [{"content": "输出默认用表格", "kind": "taste", "explicit": True}]
    written, _ = await crystallize_candidates(app, cand, now=NOW + 86400)  # 1 天后又这么拍
    assert written == 0 and _count_prefs(mem) == 0                    # 被墓碑挡住,没学回来


@pytest.mark.asyncio
async def test_revoked_pref_relearns_after_cooldown():
    """对称:窗口过后你仍持续这么做 → 照常重学(撤回不是永久封杀,也守'不固化你')。"""
    mem = MemoryManager()
    app = _FakeApp(mem=mem)
    NOW = 1_000_000.0
    app.state.decision_revocations = RevocationStore(cooldown_days=14.0)
    app.state.decision_revocations.mark("输出默认用表格", now=NOW)
    cand = [{"content": "输出默认用表格", "kind": "taste", "explicit": True}]
    written, _ = await crystallize_candidates(app, cand, now=NOW + 20 * 86400)  # 20 天后
    assert written == 1 and _count_prefs(mem) == 1                    # 窗口过了,能重学


@pytest.mark.asyncio
async def test_no_gateway_or_mem_is_noop():
    app = _FakeApp(gateway=None, mem=MemoryManager())
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    assert await maybe_crystallize_decisions(app) == 0


# ---- prealign ----


@pytest.mark.asyncio
async def test_prealign_governance_injects_prefs():
    mem = MemoryManager()
    gw = _StubGateway('[{"content":"碰生产先写测试","kind":"constraint","explicit":true}]')
    app = _FakeApp(gateway=gw, mem=mem)
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    await maybe_crystallize_decisions(app)
    block = prealign_governance(app, mem)
    assert "你的决策偏好" in block
    assert "碰生产先写测试" in block


def test_prealign_empty_when_no_prefs():
    mem = MemoryManager()
    app = _FakeApp(mem=mem)
    assert prealign_governance(app, mem) == ""
    assert prealign_governance(app, None) == ""   # mem None 安全


def test_prealign_ignores_plain_belief():
    from karvyloop.schemas.cognition import Belief
    mem = MemoryManager()
    mem.write(Belief(content="普通事实", provenance={"source": "conversation"},
                     freshness_ts=1.0, scope="personal"))
    app = _FakeApp(mem=mem)
    assert prealign_governance(app, mem) == ""   # 普通 Belief 不进预对齐


# ---- P1:加固 / 翻转 / 撤销(不固化你) ----


def _prefs_in(mem):
    return [b for b in mem.index.all("personal") if is_decision_pref(b)]


@pytest.mark.asyncio
async def test_reinforcement_bumps_existing():
    mem = MemoryManager()
    mem.write(make_decision_pref_belief("输出默认用表格", "taste", strength=0.5,
                                        status="provisional", now=1.0))
    gw = _StubGateway('[{"content":"输出默认用表格","kind":"taste","explicit":false}]')
    app = _FakeApp(gateway=gw, mem=mem)
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    await maybe_crystallize_decisions(app)
    prefs = _prefs_in(mem)
    assert len(prefs) == 1                                   # 不新增,加固原条
    assert prefs[0].provenance["strength"] == pytest.approx(0.6)   # 0.5+0.1


@pytest.mark.asyncio
async def test_contradiction_revokes_provisional():
    mem = MemoryManager()
    # provisional + 低 strength:相反决策削弱后跌破 floor → 撤销
    mem.write(make_decision_pref_belief("旧偏好X", "taste", strength=0.4,
                                        status="provisional", now=1.0))
    gw = _StubGateway('{"new":[],"contradicts":[1]}')
    app = _FakeApp(gateway=gw, mem=mem)
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    await maybe_crystallize_decisions(app)
    assert len(_prefs_in(mem)) == 0   # 0.4-0.3=0.1 < 0.25 → 撤销(今天的你推翻了昨天)


@pytest.mark.asyncio
async def test_domain_scoped_pref_when_llm_marks_domain():
    # 全批同一非 l0 域 + LLM 标 scope=domain → 偏好 applies 限定该域
    mem = MemoryManager()
    gw = _StubGateway('[{"content":"本域先审计","kind":"standing","explicit":true,"scope":"domain"}]')
    app = _FakeApp(gateway=gw, mem=mem)
    for i in range(DECISION_BATCH):
        observe_decision(app, DecisionSample(decision="REJECT", context=f"x{i}", reason="r",
                                             domain="legal", role="审计师", ts=float(i)))
    await maybe_crystallize_decisions(app)
    prefs = _prefs_in(mem)
    assert len(prefs) == 1
    assert prefs[0].provenance["applies"] == {"domain": "legal", "role": "审计师"}


@pytest.mark.asyncio
async def test_global_scope_stays_global_even_in_domain():
    # LLM 标 scope=global → 即便在某域决策,偏好仍全局(applies 空)
    mem = MemoryManager()
    gw = _StubGateway('[{"content":"输出用表格","kind":"taste","explicit":true,"scope":"global"}]')
    app = _FakeApp(gateway=gw, mem=mem)
    for i in range(DECISION_BATCH):
        observe_decision(app, DecisionSample(decision="EDIT", context=f"x{i}",
                                             domain="legal", role="审计师", ts=float(i)))
    await maybe_crystallize_decisions(app)
    prefs = _prefs_in(mem)
    assert len(prefs) == 1
    assert prefs[0].provenance["applies"] == {"domain": "", "role": ""}


@pytest.mark.asyncio
async def test_contradiction_keeps_confirmed_downgraded():
    mem = MemoryManager()
    # confirmed:你拍过板的,相反决策只降影响、不静默删
    mem.write(make_decision_pref_belief("旧偏好Y", "constraint", strength=0.8,
                                        status="confirmed", now=1.0))
    gw = _StubGateway('{"new":[],"contradicts":[1]}')
    app = _FakeApp(gateway=gw, mem=mem)
    for s in _decisions(DECISION_BATCH):
        observe_decision(app, s)
    await maybe_crystallize_decisions(app)
    prefs = _prefs_in(mem)
    assert len(prefs) == 1                                   # confirmed 不删
    assert prefs[0].provenance["strength"] == pytest.approx(0.5)   # 0.8-0.3 降但留
