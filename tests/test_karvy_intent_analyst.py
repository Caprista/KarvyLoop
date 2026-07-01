"""IntentAnalyst 测试(M3+ 拍 9.0c)。

设计:docs/20 §3.3.5 + docs/25 + 用户原话 2026-06-17。

**覆盖矩阵**(CLAUDE.md Q2 CI shape test):
- AC1-AC4: TraceChunk + Proposal frozen dataclass + 字段
- AC5-AC8: can_propose 快脑门控(空 / signal kind / 非 signal kind / 多 signal)
- AC9-AC12: analyze 慢脑(can_propose fail → None / LLM 返 [] → None / 强度 < threshold → None / 强度 >= threshold → Proposal)
- AC13-AC15: 三种 trigger(event / boot / daily)— 包装 + 错误 source 拒
- AC16-AC17: model_ref 解析(per-agent 覆盖 + 默认兜底)
- AC18: graceful degradation — LLM 抛 NotImplementedError → 返 None
- AC19-AC22: 灵魂铁律 K1 / K5 / K7 / K8(K7 不参与 A2A + K8 不调 LLM)
- AC23-AC24: 集成测试(写 trace_index → IntentAnalyst 走 boot_poll → Proposal)
- AC25: 小卡私有域纪律(IntentAnalyst 不可走短路径 import)
"""
from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path
from typing import Sequence

import pytest

from karvyloop.karvy.atoms import (
    IntentAnalyst,
    Proposal,
    TRIGGER_BOOT,
    TRIGGER_DAILY,
    TRIGGER_EVENT,
    TraceChunk,
)
from karvyloop.karvy.fastbrain.trace_habit import (
    DEFAULT_FALLBACK_MODEL,
    Habit,
    HabitStore,
    ModelRef,
    resolve_model_ref,
)
from karvyloop.karvy.fastbrain.trace_index import TraceIndex, TraceRecord


# ---- fixture ----


def _trace_record(seq: int, kind: str, **extra) -> TraceRecord:
    """构造一条 TraceRecord(用于喂 IntentAnalyst)。"""
    return TraceRecord(
        seq=seq,
        ts=1700000000.0 + seq,
        payload={"kind": kind, **extra},
        size_bytes=100,
    )


def _habit(
    pattern: str = "用户偏好 X",
    strength: float = 0.8,
    evidence: tuple = (1, 2, 3),
    model_ref: str = "anthropic/claude-sonnet-4-6",
    id: int = 1,
) -> Habit:
    return Habit(
        id=id,
        pattern=pattern,
        strength=strength,
        evidence_count=len(evidence),
        evidence_refs=evidence,
        first_seen=1700000000.0,
        last_reinforced=1700000000.0,
        model_ref=model_ref,
    )


class _FakeBehaviorAnalyzer:
    """可控 BehaviorPatternAnalyzer(duck type — 不接真 LLM)。"""

    def __init__(self, habits_to_return: list[Habit] | None = None) -> None:
        self.habits = habits_to_return or []
        self.call_count = 0
        self.last_summaries: Sequence[TraceRecord] = ()
        self.last_model_ref: object = None

    def analyze(self, summaries, model_ref) -> list[Habit]:
        self.call_count += 1
        self.last_summaries = summaries
        self.last_model_ref = model_ref
        return self.habits


@pytest.fixture
def trace_index(tmp_path: Path) -> TraceIndex:
    return TraceIndex(
        tmp_path / "trace.db",
        raw_capacity=1024 * 1024,
        summary_capacity=5 * 1024 * 1024,
    )


@pytest.fixture
def habit_store(tmp_path: Path) -> HabitStore:
    return HabitStore(tmp_path / "habits.db")


@pytest.fixture
def workbench():  # workbench 不被 IntentAnalyst 直接用,只为了构造签名
    from karvyloop.karvy.observer import WorkbenchObserver
    return WorkbenchObserver()


# ---- AC1: TraceChunk dataclass ----


def test_trace_chunk_is_frozen() -> None:
    chunk = TraceChunk(summaries=(), source=TRIGGER_EVENT, ts=1.0)
    with pytest.raises(Exception):  # FrozenInstanceError
        chunk.source = TRIGGER_BOOT  # type: ignore[misc]


def test_trace_chunk_validates_source() -> None:
    with pytest.raises(ValueError, match="source must be one of"):
        TraceChunk(summaries=(), source="invalid", ts=1.0)


def test_trace_chunk_accepts_all_three_sources() -> None:
    for s in (TRIGGER_EVENT, TRIGGER_BOOT, TRIGGER_DAILY):
        chunk = TraceChunk(summaries=(), source=s, ts=1.0)
        assert chunk.source == s


# ---- AC2: Proposal dataclass ----


def test_proposal_is_frozen() -> None:
    p = Proposal(
        summary="X", options=("ACCEPT",), strength=0.8,
        evidence_refs=(1,), habit_id=0, model_ref="x", ts=1.0,
    )
    with pytest.raises(Exception):
        p.strength = 0.5  # type: ignore[misc]


def test_proposal_to_dict_shape() -> None:
    p = Proposal(
        summary="用户可能想试这件衣服",
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.85,
        evidence_refs=(1, 2, 3),
        habit_id=42,
        model_ref="anthropic/claude-sonnet-4-6",
        ts=1700000000.0,
    )
    d = p.to_dict()
    assert d["summary"] == "用户可能想试这件衣服"
    assert d["options"] == ["ACCEPT", "DEFER", "REJECT"]
    assert d["strength"] == 0.85
    assert d["evidence_refs"] == [1, 2, 3]
    assert d["habit_id"] == 42
    assert d["model_ref"] == "anthropic/claude-sonnet-4-6"
    assert d["ts"] == 1700000000.0


# ---- AC3: IntentAnalyst 构造 ----


def test_intent_analyst_default_strength_threshold(workbench, trace_index, habit_store) -> None:
    """默认 strength_threshold = 0.7。"""
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=_FakeBehaviorAnalyzer(),
    )
    assert analyzer.strength_threshold == 0.7
    assert analyzer.agent_name == "intent_analyst"


# ---- AC5-AC8: can_propose 快脑门控 ----


def test_can_propose_empty_summaries_returns_false(workbench, trace_index, habit_store) -> None:
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=_FakeBehaviorAnalyzer(),
    )
    chunk = TraceChunk(summaries=(), source=TRIGGER_EVENT, ts=1.0)
    assert analyzer.can_propose(chunk) is False


def test_can_propose_with_intent_kind_returns_true(workbench, trace_index, habit_store) -> None:
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=_FakeBehaviorAnalyzer(),
    )
    chunk = TraceChunk(
        summaries=(_trace_record(1, "intent", text="hello"),),
        source=TRIGGER_EVENT, ts=1.0,
    )
    assert analyzer.can_propose(chunk) is True


@pytest.mark.parametrize("kind", ["intent", "task", "drive", "user_action"])
def test_can_propose_signal_kinds_all_pass(workbench, trace_index, habit_store, kind: str) -> None:
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=_FakeBehaviorAnalyzer(),
    )
    chunk = TraceChunk(
        summaries=(_trace_record(1, kind),),
        source=TRIGGER_EVENT, ts=1.0,
    )
    assert analyzer.can_propose(chunk) is True


def test_can_propose_non_signal_kind_returns_false(workbench, trace_index, habit_store) -> None:
    """非信号 kind(background events)→ False。"""
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=_FakeBehaviorAnalyzer(),
    )
    chunk = TraceChunk(
        summaries=(_trace_record(1, "background_log"),),
        source=TRIGGER_EVENT, ts=1.0,
    )
    assert analyzer.can_propose(chunk) is False


def test_can_propose_picks_up_at_least_one_signal_in_mixed_chunk(
    workbench, trace_index, habit_store
) -> None:
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=_FakeBehaviorAnalyzer(),
    )
    chunk = TraceChunk(
        summaries=(
            _trace_record(1, "background_log"),
            _trace_record(2, "crystallize"),
            _trace_record(3, "task"),  # 唯一一个 signal
        ),
        source=TRIGGER_DAILY, ts=1.0,
    )
    assert analyzer.can_propose(chunk) is True


# ---- AC9: analyze 慢脑主路径 ----


def test_analyze_can_propose_fail_returns_none(workbench, trace_index, habit_store) -> None:
    fake = _FakeBehaviorAnalyzer(habits_to_return=[_habit()])
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=fake,
    )
    chunk = TraceChunk(summaries=(), source=TRIGGER_EVENT, ts=1.0)
    assert analyzer.analyze(chunk) is None
    assert fake.call_count == 0  # 快脑 fail → 不调慢脑


def test_analyze_llm_returns_empty_returns_none(workbench, trace_index, habit_store) -> None:
    """LLM 返 [] → None(沉默)。"""
    fake = _FakeBehaviorAnalyzer(habits_to_return=[])
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=fake,
    )
    chunk = TraceChunk(
        summaries=(_trace_record(1, "intent"),),
        source=TRIGGER_EVENT, ts=1.0,
    )
    assert analyzer.analyze(chunk) is None
    assert fake.call_count == 1


def test_analyze_strength_below_threshold_returns_none(
    workbench, trace_index, habit_store
) -> None:
    """最强 habit 强度 < threshold → None(沉默)。"""
    weak = _habit(pattern="weak", strength=0.5)
    fake = _FakeBehaviorAnalyzer(habits_to_return=[weak])
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=fake,
        strength_threshold=0.7,
    )
    chunk = TraceChunk(
        summaries=(_trace_record(1, "intent"),),
        source=TRIGGER_EVENT, ts=1.0,
    )
    assert analyzer.analyze(chunk) is None


def test_analyze_strength_meets_threshold_returns_proposal(
    workbench, trace_index, habit_store
) -> None:
    """最强 habit 强度 >= threshold → Proposal。"""
    strong = _habit(pattern="用户偏好 A", strength=0.85, id=99)
    fake = _FakeBehaviorAnalyzer(habits_to_return=[strong])
    resolver = lambda agent: ModelRef(name="anthropic/claude-sonnet-4-6")  # noqa: E731
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=fake,
        model_ref_resolver=resolver,
        strength_threshold=0.7,
    )
    chunk = TraceChunk(
        summaries=(_trace_record(1, "intent"),),
        source=TRIGGER_EVENT, ts=1.0,
    )
    p = analyzer.analyze(chunk)
    assert p is not None
    assert isinstance(p, Proposal)
    assert p.summary == "用户偏好 A"
    assert p.strength == 0.85
    assert p.habit_id == 99
    assert p.evidence_refs == (1, 2, 3)
    assert p.options == ("ACCEPT", "DEFER", "REJECT")
    assert p.model_ref == "anthropic/claude-sonnet-4-6"


def test_analyze_picks_strongest_habit_when_multiple(
    workbench, trace_index, habit_store
) -> None:
    """多 habit → 选最强。"""
    weak = _habit(pattern="weak", strength=0.3, id=1)
    strong = _habit(pattern="strong", strength=0.9, id=2)
    medium = _habit(pattern="medium", strength=0.6, id=3)
    fake = _FakeBehaviorAnalyzer(habits_to_return=[weak, strong, medium])
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=fake,
    )
    chunk = TraceChunk(
        summaries=(_trace_record(1, "intent"),),
        source=TRIGGER_EVENT, ts=1.0,
    )
    p = analyzer.analyze(chunk)
    assert p is not None
    assert p.summary == "strong"
    assert p.habit_id == 2


# ---- AC18: graceful degradation on NotImplementedError ----


def test_analyze_handles_not_implemented_gracefully(
    workbench, trace_index, habit_store
) -> None:
    """behavior_analyzer.analyze 抛 NotImplementedError → 返 None(9.0b 骨架兼容)。"""

    class _StubAnalyzer:
        def analyze(self, summaries, model_ref):
            raise NotImplementedError("9.0b 骨架")

    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=_StubAnalyzer(),
    )
    chunk = TraceChunk(
        summaries=(_trace_record(1, "intent"),),
        source=TRIGGER_EVENT, ts=1.0,
    )
    assert analyzer.analyze(chunk) is None  # 不抛


# ---- AC13-AC15: 三种 trigger ----


def test_on_event_works(workbench, trace_index, habit_store) -> None:
    """on_event 走 analyze。"""
    strong = _habit(strength=0.8, id=5)
    fake = _FakeBehaviorAnalyzer(habits_to_return=[strong])
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=fake,
    )
    chunk = TraceChunk(
        summaries=(_trace_record(1, "intent"),),
        source=TRIGGER_EVENT, ts=1.0,
    )
    p = analyzer.on_event(chunk)
    assert p is not None
    assert p.habit_id == 5


def test_on_event_rejects_wrong_source(workbench, trace_index, habit_store) -> None:
    """on_event 只接 TRIGGER_EVENT source,其他拒。"""
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=_FakeBehaviorAnalyzer(),
    )
    chunk = TraceChunk(
        summaries=(_trace_record(1, "intent"),),
        source=TRIGGER_BOOT, ts=1.0,  # 错
    )
    with pytest.raises(ValueError, match="on_event 必须用 source=TRIGGER_EVENT"):
        analyzer.on_event(chunk)


def test_boot_poll_reads_recent_summaries(workbench, trace_index, habit_store) -> None:
    """boot_poll 从 trace_index 摘要层读最近 N 条 → 喂给 analyze。"""
    # 准备 5 条摘要,其中 1 条是 signal
    for i in range(1, 6):
        kind = "intent" if i == 3 else "background"
        trace_index.append_summary({"kind": kind, "i": i})

    strong = _habit(strength=0.8, id=10)
    fake = _FakeBehaviorAnalyzer(habits_to_return=[strong])
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=fake,
    )
    p = analyzer.boot_poll(recent_n=10)
    assert p is not None
    assert p.habit_id == 10
    # 喂给 LLM 的 summaries 应包含全部 5 条
    assert len(fake.last_summaries) == 5


def test_daily_poll_reads_recent_summaries(workbench, trace_index, habit_store) -> None:
    """daily_poll 同理。"""
    for i in range(1, 4):
        trace_index.append_summary({"kind": "task", "i": i})

    strong = _habit(strength=0.8, id=20)
    fake = _FakeBehaviorAnalyzer(habits_to_return=[strong])
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=fake,
    )
    p = analyzer.daily_poll(recent_n=5)
    assert p is not None
    assert p.habit_id == 20
    assert len(fake.last_summaries) == 3


def test_boot_poll_empty_trace_returns_none(workbench, trace_index, habit_store) -> None:
    """trace_index 摘要层空 + 无 signal → None。"""
    fake = _FakeBehaviorAnalyzer(habits_to_return=[_habit()])  # 强 habit
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=fake,
    )
    p = analyzer.boot_poll(recent_n=10)
    # 空 summaries → can_propose False → None
    assert p is None
    assert fake.call_count == 0


# ---- AC16-AC17: model_ref 解析 ----


def test_analyze_uses_resolver_with_agent_name(workbench, trace_index, habit_store) -> None:
    """analyze 调 resolver(agent_name)。"""
    captured: list[str] = []

    def resolver(agent: str) -> ModelRef:
        captured.append(agent)
        return ModelRef(name=f"per-agent/{agent}")

    strong = _habit(strength=0.8)
    fake = _FakeBehaviorAnalyzer(habits_to_return=[strong])
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=fake,
        model_ref_resolver=resolver,
        agent_name="my_intent_analyst",
    )
    chunk = TraceChunk(
        summaries=(_trace_record(1, "intent"),),
        source=TRIGGER_EVENT, ts=1.0,
    )
    p = analyzer.analyze(chunk)
    assert p is not None
    assert captured == ["my_intent_analyst"]
    # LLM 收到的是解析后的 ModelRef
    assert fake.last_model_ref is not None
    assert fake.last_model_ref.name == "per-agent/my_intent_analyst"


def test_analyze_falls_back_to_resolver_default(workbench, trace_index, habit_store) -> None:
    """resolver 返 ModelRef(name="default") → proposal.model_ref 用它。"""
    strong = _habit(strength=0.8, model_ref="")  # habit 自带 model_ref 为空
    fake = _FakeBehaviorAnalyzer(habits_to_return=[strong])

    def resolver(agent: str) -> ModelRef:
        return ModelRef(name="global-default/claude-sonnet-4-6")

    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=fake,
        model_ref_resolver=resolver,
    )
    chunk = TraceChunk(
        summaries=(_trace_record(1, "intent"),),
        source=TRIGGER_EVENT, ts=1.0,
    )
    p = analyzer.analyze(chunk)
    assert p is not None
    assert p.model_ref == "global-default/claude-sonnet-4-6"


# ---- AC19-AC22: 灵魂铁律 ----


def test_k7_invariant_intent_analyst_does_not_import_courier() -> None:
    """K7:IntentAnalyst 不调 Courier(不参与 A2A)。"""
    from karvyloop.karvy import atoms as atoms_mod
    src = inspect.getsource(atoms_mod)
    # 严格:IntentAnalyst 类范围内不 import Courier
    # 简单做法:整文件无 Courier.send(给 K7 测试用)
    assert "Courier.send" not in src, "K7 违反 — IntentAnalyst 调了 Courier.send"


def test_k7_invariant_intent_analyst_no_a2a_routing() -> None:
    """K7:IntentAnalyst 不引 EnvelopeRouter。"""
    from karvyloop.karvy import atoms as atoms_mod
    src = inspect.getsource(atoms_mod)
    assert "EnvelopeRouter" not in src, "K7 违反 — IntentAnalyst 引了 EnvelopeRouter"


def test_k8_invariant_intent_analyst_no_llm_import() -> None:
    """K8:IntentAnalyst 不直接 import LLM(openai/anthropic/litellm)。"""
    from karvyloop.karvy import atoms as atoms_mod
    src = inspect.getsource(atoms_mod)
    for forbidden in ("import openai", "import anthropic", "import litellm"):
        assert forbidden not in src, f"K8 违反 — IntentAnalyst 引了 {forbidden}"


def test_k5_invariant_intent_analyst_returns_proposal_not_decision() -> None:
    """K5:IntentAnalyst 返 Proposal,**不**替用户决策(不返 H2ADecision / Envelope)。"""
    from karvyloop.karvy import atoms as atoms_mod
    src = inspect.getsource(atoms_mod)
    # 不返 H2ADecision / Envelope / decision_to_envelope
    for forbidden in ("H2ADecision", "decision_to_envelope", "H2A_ACCEPT", "H2A_REJECT"):
        assert forbidden not in src, f"K5 违反 — IntentAnalyst 引了 {forbidden}"


# ---- AC25: 小卡私有域纪律 ----


def test_intent_analyst_not_in_short_path() -> None:
    """小卡私有 — `from karvyloop.karvy import IntentAnalyst` 应 ImportError。"""
    with pytest.raises(ImportError):
        from karvyloop.karvy import IntentAnalyst  # noqa: F401


def test_intent_analyst_only_via_deep_path() -> None:
    """深路径 `from karvyloop.karvy.atoms import IntentAnalyst` 应 OK。"""
    from karvyloop.karvy.atoms import IntentAnalyst as _IA  # noqa: F401
    assert _IA is IntentAnalyst


# ---- AC23: 集成(写 trace_index → IntentAnalyst 走 boot_poll → Proposal)----


def test_integration_write_trace_then_boot_poll_proposes(
    workbench, trace_index, habit_store
) -> None:
    """集成:模拟真实数据流 — 写 3 条摘要到 trace_index → 调 boot_poll → 返 Proposal。"""
    # 1. 模拟 24h 内用户做了几件事
    trace_index.append_summary({"kind": "intent", "text": "查 git status"})
    trace_index.append_summary({"kind": "task", "text": "编辑 README"})
    trace_index.append_summary({"kind": "background", "text": "心跳"})

    # 2. 准备假 LLM — 凝出"用户常查 git 状态"习惯
    habit = _habit(
        pattern="用户常在工作中查 git 状态",
        strength=0.85,
        evidence=(1, 2, 3),
        id=42,
    )
    fake = _FakeBehaviorAnalyzer(habits_to_return=[habit])

    # 3. 真实 IntentAnalyst + 真实 HabitStore / TraceIndex
    analyzer = IntentAnalyst(
        workbench=workbench,
        habit_store=habit_store,
        trace_index=trace_index,
        behavior_analyzer=fake,
    )

    # 4. 启动时跑一次
    proposal = analyzer.boot_poll(recent_n=10)
    assert proposal is not None
    assert proposal.summary == "用户常在工作中查 git 状态"
    assert proposal.strength == 0.85
    assert proposal.habit_id == 42
    assert proposal.evidence_refs == (1, 2, 3)

    # 5. 持久化到 HabitStore(9.0c 不自动写库,9.0d 由 propose_factory 决定)
    habit_store.upsert(
        proposal.summary,
        strength=proposal.strength,
        evidence_refs=proposal.evidence_refs,
        model_ref=proposal.model_ref,
    )
    assert habit_store.count() == 1


# ---- AC24: 灵魂铁律 grep 跨包 ----


def test_intent_analyst_does_not_depend_on_console_or_llm_registry() -> None:
    """FB-7 锁:IntentAnalyst 不依赖 console 推 push,也不依赖 llm registry 取真 client。

    (IntentAnalyst 通过 duck-type 接 behavior_analyzer;真 client 接线在 9.0d 由 propose_factory 负责)
    """
    from karvyloop.karvy import atoms as atoms_mod
    src = inspect.getsource(atoms_mod)
    # 不引 karvyloop.console
    assert "karvyloop.console" not in src, (
        f"FB-7 违反 — IntentAnalyst 引了 karvyloop.console:\n{src[:500]}"
    )
    # 不引 karvyloop.llm.registry
    assert "karvyloop.llm" not in src, (
        f"FB-7 违反 — IntentAnalyst 引了 karvyloop.llm:\n{src[:500]}"
    )


# ---- Proposal strength 边界 ----


def test_proposal_strength_must_be_0_to_1() -> None:
    """Proposal strength 范围 0-1(契约,9.0d console 显示用)。"""
    # 边界 0 和 1 都合法
    p0 = Proposal("x", (), 0.0, (), 0, "", 1.0)
    p1 = Proposal("x", (), 1.0, (), 0, "", 1.0)
    assert p0.strength == 0.0
    assert p1.strength == 1.0


# ---- default strength threshold 锁(Q2 锁 public surface)----


def test_default_strength_threshold_constant() -> None:
    """Q2:0.7 锁 public surface(防止后面悄悄改)。"""
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.karvy.fastbrain.trace_habit import HabitStore as _HS
    from karvyloop.karvy.fastbrain.trace_index import TraceIndex as _TI
    from karvyloop.karvy.atoms import IntentAnalyst as _IA
    wb = WorkbenchObserver()
    a = _IA(
        workbench=wb,
        habit_store=_HS(Path("/tmp/_dummy_h.db")),
        trace_index=_TI(Path("/tmp/_dummy_t.db"), raw_capacity=1024, summary_capacity=1024),
        behavior_analyzer=_FakeBehaviorAnalyzer(),
    )
    assert a.strength_threshold == 0.7
