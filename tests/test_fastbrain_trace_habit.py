"""trace_habit.py 测试(M3+ 拍 9.0b)。

设计:docs/25 §5 + 用户原话 2026-06-17。

**覆盖矩阵**(CLAUDE.md Q2 CI shape test):
- AC1-AC5: HabitStore upsert 新建 + dedup 合并 + evidence 去重 + strength 取 max + last_reinforced 更新
- AC6-AC8: HabitStore list/get/count + 排序(strength DESC + last_reinforced DESC)
- AC9-AC10: HabitStore 跨进程持久化 + close 幂等 + context manager
- AC11-AC15: ModelRef + resolve_model_ref(per-agent 覆盖 / 全局默认 / 硬编码兜底)
- AC16-AC17: BehaviorPatternAnalyzer 骨架(无 LLM client 返空 / 有 LLM client 抛 NotImplementedError — 9.0c 实做)
- AC18: FB-5 不变量(trace_habit 不 import karvy.atoms)
- AC19: 边界(empty pattern / strength 越界 / 0 evidence)
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Sequence

import pytest

from karvyloop.karvy.fastbrain.trace_habit import (
    BehaviorPatternAnalyzer,
    DEFAULT_FALLBACK_MODEL,
    Habit,
    HabitStore,
    LlmClientProtocol,
    ModelRef,
    resolve_model_ref,
)
from karvyloop.karvy.fastbrain.trace_index import TraceRecord


# ---- fixture ----


@pytest.fixture
def tmp_store(tmp_path: Path) -> HabitStore:
    return HabitStore(tmp_path / "habits.db")


@pytest.fixture
def fixed_clock():
    """固定时间,便于断言 last_reinforced 更新。"""
    base = [1_700_000_000.0]

    def clock() -> float:
        return base[0]

    def advance(seconds: float) -> None:
        base[0] += seconds

    return clock, advance


# ---- AC1: upsert 新建 ----


def test_upsert_creates_new_habit(tmp_store: HabitStore) -> None:
    h = tmp_store.upsert("用户每天早上看天气", strength=0.8, evidence_refs=(1, 2, 3))
    assert h.id > 0
    assert h.pattern == "用户每天早上看天气"
    assert h.strength == 0.8
    assert h.evidence_count == 3
    assert h.evidence_refs == (1, 2, 3)
    assert h.first_seen == h.last_reinforced  # 新建时 first == last


def test_upsert_persists_to_disk(tmp_store: HabitStore) -> None:
    """新建的 habit 立即可读(同进程)。"""
    h1 = tmp_store.upsert("用户晚上常读 README", strength=0.6)
    items = tmp_store.list_habits()
    assert len(items) == 1
    assert items[0].id == h1.id
    assert items[0].pattern == h1.pattern


# ---- AC2: dedup 合并(same pattern)----


def test_upsert_dedup_merges_evidence(tmp_store: HabitStore) -> None:
    """同 pattern 二次 upsert → evidence 合并去重。"""
    h1 = tmp_store.upsert("用户习惯 A", strength=0.5, evidence_refs=(1, 2, 3))
    h2 = tmp_store.upsert("用户习惯 A", strength=0.6, evidence_refs=(3, 4, 5))
    assert h1.id == h2.id  # 同 id
    assert h2.evidence_count == 5  # 1,2,3,4,5
    assert h2.evidence_refs == (1, 2, 3, 4, 5)
    # 仍只 1 行
    assert tmp_store.count() == 1


def test_upsert_dedup_takes_max_strength(tmp_store: HabitStore) -> None:
    """同 pattern 二次 upsert → strength 取 max。"""
    tmp_store.upsert("用户习惯 B", strength=0.5)
    h2 = tmp_store.upsert("用户习惯 B", strength=0.9)
    assert h2.strength == 0.9  # max
    h3 = tmp_store.upsert("用户习惯 B", strength=0.3)
    assert h3.strength == 0.9  # 仍 max,不被新低值覆盖


def test_upsert_dedup_updates_last_reinforced(
    tmp_store: HabitStore, fixed_clock
) -> None:
    """同 pattern 二次 upsert → last_reinforced 更新,first_seen 不变。"""
    clock, advance = fixed_clock
    # 替换 clock 到 store(通过构造时已注入,这里靠 tmp_store 接受)
    # tmp_store 用默认 time.time,改不了;改用直接构造一个用 fixed_clock 的 store
    db = tmp_store._path  # type: ignore[attr-defined]
    custom_store = HabitStore(db, clock=clock)
    try:
        h1 = custom_store.upsert("X", strength=0.5)
        first = h1.first_seen
        advance(100)
        h2 = custom_store.upsert("X", strength=0.6)
        assert h2.first_seen == first  # 不变
        assert h2.last_reinforced == first + 100  # 更新
    finally:
        custom_store.close()


def test_upsert_dedup_preserves_first_seen(
    tmp_store: HabitStore, fixed_clock
) -> None:
    db = tmp_store._path  # type: ignore[attr-defined]
    clock, advance = fixed_clock
    custom_store = HabitStore(db, clock=clock)
    try:
        h1 = custom_store.upsert("Y", strength=0.5)
        original_first = h1.first_seen
        advance(50)
        custom_store.upsert("Y", strength=0.6)
        items = custom_store.list_habits()
        assert items[0].first_seen == original_first
    finally:
        custom_store.close()


# ---- AC3: 边界 ----


def test_upsert_empty_pattern_rejected(tmp_store: HabitStore) -> None:
    with pytest.raises(ValueError, match="pattern must be non-empty"):
        tmp_store.upsert("", strength=0.5)


def test_upsert_strength_out_of_range_rejected(tmp_store: HabitStore) -> None:
    with pytest.raises(ValueError, match="strength must be 0-1"):
        tmp_store.upsert("X", strength=1.5)
    with pytest.raises(ValueError, match="strength must be 0-1"):
        tmp_store.upsert("X", strength=-0.1)


def test_upsert_zero_evidence_ok(tmp_store: HabitStore) -> None:
    """evidence_refs 为空是合法的(模式初现,无具体证据)。"""
    h = tmp_store.upsert("用户偶尔 Y", strength=0.3)
    assert h.evidence_count == 0
    assert h.evidence_refs == ()


def test_upsert_strength_boundary_0_and_1(tmp_store: HabitStore) -> None:
    """strength = 0.0 和 1.0 都合法(边界含)。"""
    h0 = tmp_store.upsert("Z0", strength=0.0)
    h1 = tmp_store.upsert("Z1", strength=1.0)
    assert h0.strength == 0.0
    assert h1.strength == 1.0


# ---- AC6: list 排序 + limit ----


def test_list_habits_orders_by_strength_desc(
    tmp_store: HabitStore, fixed_clock
) -> None:
    clock, advance = fixed_clock
    db = tmp_store._path  # type: ignore[attr-defined]
    custom = HabitStore(db, clock=clock)
    try:
        advance(1)
        custom.upsert("weak", strength=0.3)
        advance(1)
        custom.upsert("strong", strength=0.9)
        advance(1)
        custom.upsert("medium", strength=0.6)
        items = custom.list_habits()
        patterns = [h.pattern for h in items]
        assert patterns == ["strong", "medium", "weak"]
    finally:
        custom.close()


def test_list_habits_secondary_order_by_last_reinforced(
    tmp_store: HabitStore, fixed_clock
) -> None:
    """同 strength 时,last_reinforced 新的在前。"""
    clock, advance = fixed_clock
    db = tmp_store._path  # type: ignore[attr-defined]
    custom = HabitStore(db, clock=clock)
    try:
        advance(1)
        old = custom.upsert("old strong", strength=0.9)
        advance(100)
        new = custom.upsert("new strong", strength=0.9)  # 同样 strength,但更晚
        items = custom.list_habits()
        # 同样 strength,后 reinforced 的排前
        assert items[0].pattern == "new strong"
        assert items[1].pattern == "old strong"
    finally:
        custom.close()


def test_list_habits_limit_caps(
    tmp_store: HabitStore,
) -> None:
    for i in range(10):
        tmp_store.upsert(f"h{i}", strength=0.5 + i * 0.05)
    assert len(tmp_store.list_habits(limit=3)) == 3


def test_list_habits_empty(tmp_store: HabitStore) -> None:
    assert tmp_store.list_habits() == []
    assert tmp_store.count() == 0


# ---- AC7: get_habit ----


def test_get_habit_returns_existing(tmp_store: HabitStore) -> None:
    h1 = tmp_store.upsert("X", strength=0.7)
    h2 = tmp_store.get_habit(h1.id)
    assert h2 is not None
    assert h2.pattern == "X"
    assert h2.strength == 0.7


def test_get_habit_returns_none_for_missing(tmp_store: HabitStore) -> None:
    assert tmp_store.get_habit(999) is None


# ---- AC9: 跨进程持久化 ----


def test_persistence_across_close_and_reopen(tmp_path: Path) -> None:
    db = tmp_path / "habits.db"
    s1 = HabitStore(db)
    s1.upsert("P1", strength=0.5, evidence_refs=(1, 2))
    s1.upsert("P2", strength=0.8)
    s1.close()

    s2 = HabitStore(db)
    items = s2.list_habits()
    assert len(items) == 2
    patterns = {h.pattern for h in items}
    assert patterns == {"P1", "P2"}
    s2.close()


def test_persistence_across_real_subprocess(tmp_path: Path) -> None:
    """真子进程读写同一 sqlite — 跨进程边界。"""
    db = tmp_path / "habits.db"
    writer = textwrap.dedent(
        f"""
        from pathlib import Path
        from karvyloop.karvy.fastbrain.trace_habit import HabitStore
        s = HabitStore(Path({str(db)!r}))
        s.upsert("S1", strength=0.4)
        s.upsert("S2", strength=0.7, evidence_refs=(10, 20, 30))
        s.close()
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", writer],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"writer failed: {result.stderr}"
    assert "ok" in result.stdout

    s = HabitStore(db)
    try:
        items = s.list_habits()
        assert len(items) == 2
    finally:
        s.close()


# ---- AC10: close 幂等 + context manager ----


def test_close_is_idempotent(tmp_store: HabitStore) -> None:
    tmp_store.close()
    tmp_store.close()


def test_context_manager_closes_on_exit(tmp_path: Path) -> None:
    db = tmp_path / "habits.db"
    with HabitStore(db) as s:
        s.upsert("ctx", strength=0.5)
    # 退出后再操作 sqlite 没问题(连接已关)
    with HabitStore(db) as s2:
        items = s2.list_habits()
        assert len(items) == 1


# ---- AC11-AC15: ModelRef + resolve_model_ref ----


def test_resolve_model_ref_per_agent_override() -> None:
    """per-agent 覆盖赢。"""
    cfg = {
        "model_refs": {"intent_analyst": "deepseek/deepseek-chat"},
        "default_model": "anthropic/claude-sonnet-4-6",
    }
    ref = resolve_model_ref("intent_analyst", cfg)
    assert ref.name == "deepseek/deepseek-chat"


def test_resolve_model_ref_falls_back_to_default() -> None:
    """per-agent 没配 → 全局 default。"""
    cfg = {
        "model_refs": {"karvy": "minimax/MiniMax-M3"},
        "default_model": "anthropic/claude-sonnet-4-6",
    }
    ref = resolve_model_ref("unknown_agent", cfg)
    assert ref.name == "anthropic/claude-sonnet-4-6"


def test_resolve_model_ref_falls_back_to_hardcoded() -> None:
    """啥都没配 → DEFAULT_FALLBACK_MODEL。"""
    ref = resolve_model_ref("any_agent", None)
    assert ref.name == DEFAULT_FALLBACK_MODEL
    assert ref.name == "anthropic/claude-sonnet-4-6"


def test_resolve_model_ref_falls_back_when_cfg_empty() -> None:
    """空 cfg → 兜底。"""
    ref = resolve_model_ref("any_agent", {})
    assert ref.name == DEFAULT_FALLBACK_MODEL


def test_resolve_model_ref_ignores_empty_string_override() -> None:
    """per-agent 配空串 → 跳过,走 default。"""
    cfg = {
        "model_refs": {"karvy": ""},
        "default_model": "deepseek/deepseek-chat",
    }
    ref = resolve_model_ref("karvy", cfg)
    assert ref.name == "deepseek/deepseek-chat"


def test_resolve_model_ref_handles_non_dict_gracefully() -> None:
    """cfg 字段类型异常 → 不崩,走兜底。"""
    cfg = {"model_refs": "not a dict", "default_model": 42}  # type: ignore[dict-item]
    ref = resolve_model_ref("any", cfg)
    assert ref.name == DEFAULT_FALLBACK_MODEL


def test_model_ref_fallback_chain() -> None:
    """ModelRef.fallback 链可构造。"""
    primary = ModelRef(name="anthropic/claude-sonnet-4-6")
    fallback = ModelRef(name="ollama/llama3")
    chained = ModelRef(name="anthropic/claude-opus-4-8", fallback=fallback)
    assert chained.name == "anthropic/claude-opus-4-8"
    assert chained.fallback is not None
    assert chained.fallback.name == "ollama/llama3"
    assert chained.fallback.fallback is None


def test_default_fallback_constant_locked() -> None:
    """Q2 锁 public surface。"""
    assert DEFAULT_FALLBACK_MODEL == "anthropic/claude-sonnet-4-6"


# ---- AC16-AC17: BehaviorPatternAnalyzer 优雅退化 ----


def test_analyzer_no_llm_returns_empty() -> None:
    """无 LLM client → 返空 list(优雅退化)。"""
    analyzer = BehaviorPatternAnalyzer(llm_client=None)
    assert analyzer.has_llm is False
    # 喂假 summaries,仍应返 []
    summaries = [
        TraceRecord(seq=1, ts=1.0, payload={"kind": "intent"}, size_bytes=20),
    ]
    result = analyzer.analyze(summaries, ModelRef(name="anthropic/claude-sonnet-4-6"))
    assert result == []


def test_analyzer_empty_summaries_returns_empty() -> None:
    """summaries 为空 → 早返 [](即便有 LLM client 也不调)。"""
    class _FakeLlm:
        called = False

        def chat(self, model, messages, *, temperature=0.3):
            self.called = True
            return "[]"

    fake = _FakeLlm()
    analyzer = BehaviorPatternAnalyzer(llm_client=fake)
    result = analyzer.analyze([], ModelRef(name="x"))
    assert result == []
    assert fake.called is False  # 空摘要不调 LLM


def test_llm_client_protocol_defined() -> None:
    """LlmClientProtocol 暴露(Q2 锁 public surface)。"""
    assert hasattr(LlmClientProtocol, "chat")
    annotations = LlmClientProtocol.chat.__annotations__
    assert "model" in annotations
    assert "messages" in annotations


# ---- 9.0b-补: BehaviorPatternAnalyzer.analyze 真实做(LLM + JSON parse)----


class _RecordingLlm:
    """记录调用 + 返预设文本的假 LLM client。"""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.last_model = None
        self.last_messages = None
        self.last_temperature = None
        self.calls = 0

    def chat(self, model, messages, *, temperature=0.3):
        self.calls += 1
        self.last_model = model
        self.last_messages = messages
        self.last_temperature = temperature
        return self.reply


def _summaries(n: int = 3):
    return [
        TraceRecord(seq=i, ts=1.0 + i, payload={"kind": "intent", "text": f"act{i}"}, size_bytes=30)
        for i in range(1, n + 1)
    ]


def test_analyze_parses_clean_json_array() -> None:
    """干净 JSON 数组 → Habit 列表。"""
    reply = '[{"pattern": "用户常查 git 状态", "strength": 0.85}]'
    llm = _RecordingLlm(reply)
    analyzer = BehaviorPatternAnalyzer(llm_client=llm)
    habits = analyzer.analyze(_summaries(3), ModelRef(name="anthropic/claude-sonnet-4-6"))
    assert len(habits) == 1
    h = habits[0]
    assert h.pattern == "用户常查 git 状态"
    assert h.strength == 0.85
    assert h.id == 0  # 未持久化
    assert h.evidence_refs == (1, 2, 3)  # 所有摘要 seq
    assert h.evidence_count == 3
    assert h.model_ref == "anthropic/claude-sonnet-4-6"


def test_analyze_passes_model_name_and_temperature() -> None:
    """analyze 用 model_ref.name + temperature=0.3 调 LLM。"""
    llm = _RecordingLlm("[]")
    analyzer = BehaviorPatternAnalyzer(llm_client=llm)
    analyzer.analyze(_summaries(2), ModelRef(name="deepseek/deepseek-chat"))
    assert llm.calls == 1
    assert llm.last_model == "deepseek/deepseek-chat"
    assert llm.last_temperature == 0.3
    # prompt 含摘要行
    content = llm.last_messages[0]["content"]
    assert "#1" in content and "#2" in content
    assert "intent" in content


def test_analyze_strips_markdown_code_fences() -> None:
    """LLM 违反指令套了 ```json 围栏 → 仍能 parse。"""
    reply = '```json\n[{"pattern": "用户偏好暗色主题", "strength": 0.7}]\n```'
    llm = _RecordingLlm(reply)
    analyzer = BehaviorPatternAnalyzer(llm_client=llm)
    habits = analyzer.analyze(_summaries(1), ModelRef(name="x"))
    assert len(habits) == 1
    assert habits[0].pattern == "用户偏好暗色主题"


def test_analyze_extracts_array_amid_prose() -> None:
    """LLM 加了散文前后缀 → 抽出中间数组。"""
    reply = '好的,我分析出以下习惯:\n[{"pattern": "用户每天写测试", "strength": 0.9}]\n希望有用!'
    llm = _RecordingLlm(reply)
    analyzer = BehaviorPatternAnalyzer(llm_client=llm)
    habits = analyzer.analyze(_summaries(1), ModelRef(name="x"))
    assert len(habits) == 1
    assert habits[0].pattern == "用户每天写测试"


def test_analyze_empty_array_returns_empty() -> None:
    """LLM 返空数组(凝不出)→ []。"""
    llm = _RecordingLlm("[]")
    analyzer = BehaviorPatternAnalyzer(llm_client=llm)
    assert analyzer.analyze(_summaries(2), ModelRef(name="x")) == []


def test_analyze_malformed_json_returns_empty() -> None:
    """LLM 返非法 JSON → 优雅返 [](不抛)。"""
    llm = _RecordingLlm("这不是 JSON,只是一段废话")
    analyzer = BehaviorPatternAnalyzer(llm_client=llm)
    assert analyzer.analyze(_summaries(2), ModelRef(name="x")) == []


def test_analyze_skips_bad_items_keeps_good() -> None:
    """坏项(缺 pattern / strength 越界 / 非 dict)跳过,好项保留。"""
    reply = json.dumps([
        {"pattern": "好习惯", "strength": 0.8},   # 好
        {"pattern": "", "strength": 0.9},          # 空 pattern → 跳
        {"pattern": "越界", "strength": 1.5},      # strength 越界 → 跳
        {"strength": 0.5},                          # 缺 pattern → 跳
        "not a dict",                               # 非 dict → 跳
        {"pattern": "另一个好习惯", "strength": 0.6},  # 好
    ])
    llm = _RecordingLlm(reply)
    analyzer = BehaviorPatternAnalyzer(llm_client=llm)
    habits = analyzer.analyze(_summaries(1), ModelRef(name="x"))
    assert [h.pattern for h in habits] == ["好习惯", "另一个好习惯"]


def test_analyze_respects_max_habits() -> None:
    """max_habits 截断。"""
    reply = json.dumps([{"pattern": f"h{i}", "strength": 0.8} for i in range(10)])
    llm = _RecordingLlm(reply)
    analyzer = BehaviorPatternAnalyzer(llm_client=llm, max_habits=3)
    habits = analyzer.analyze(_summaries(1), ModelRef(name="x"))
    assert len(habits) == 3


def test_analyze_llm_exception_returns_empty() -> None:
    """LLM chat 抛异常 → 优雅返 [](慢脑异常不打断主流程)。"""
    class _BoomLlm:
        def chat(self, model, messages, *, temperature=0.3):
            raise RuntimeError("network down")

    analyzer = BehaviorPatternAnalyzer(llm_client=_BoomLlm())
    assert analyzer.analyze(_summaries(2), ModelRef(name="x")) == []


def test_analyze_strength_boundary_0_and_1_accepted() -> None:
    """strength=0.0 和 1.0 是合法边界(含)。"""
    reply = json.dumps([
        {"pattern": "边界0", "strength": 0.0},
        {"pattern": "边界1", "strength": 1.0},
    ])
    llm = _RecordingLlm(reply)
    analyzer = BehaviorPatternAnalyzer(llm_client=llm)
    habits = analyzer.analyze(_summaries(1), ModelRef(name="x"))
    assert {h.pattern for h in habits} == {"边界0", "边界1"}


# ---- ProviderLlmClient 适配器 ----


def test_provider_llm_client_adapts_provider_chat() -> None:
    """ProviderLlmClient.chat → 构 ChatRequest → provider.chat → 返 .content。"""
    from karvyloop.karvy.fastbrain.trace_habit import ProviderLlmClient

    captured = {}

    class _FakeProvider:
        def chat(self, request):
            captured["model"] = request.model
            captured["messages"] = request.messages
            captured["max_tokens"] = request.max_tokens

            class _Resp:
                content = "provider 回复文本"

            return _Resp()

    client = ProviderLlmClient(_FakeProvider(), max_tokens=512)
    out = client.chat("anthropic/claude-sonnet-4-6", [{"role": "user", "content": "hi"}])
    assert out == "provider 回复文本"
    assert captured["model"] == "anthropic/claude-sonnet-4-6"
    assert captured["max_tokens"] == 512
    assert captured["messages"][0].role == "user"
    assert captured["messages"][0].content == "hi"


def test_provider_llm_client_handles_missing_content() -> None:
    """provider 返无 content → 返空串(不崩)。"""
    from karvyloop.karvy.fastbrain.trace_habit import ProviderLlmClient

    class _NoContentProvider:
        def chat(self, request):
            class _Resp:
                pass

            return _Resp()

    client = ProviderLlmClient(_NoContentProvider())
    assert client.chat("x", [{"role": "user", "content": "hi"}]) == ""


def test_analyzer_with_provider_llm_client_end_to_end() -> None:
    """BehaviorPatternAnalyzer + ProviderLlmClient 端到端(假 provider 返 JSON)。"""
    from karvyloop.karvy.fastbrain.trace_habit import ProviderLlmClient

    class _JsonProvider:
        def chat(self, request):
            class _Resp:
                content = '[{"pattern": "用户常在晚上写代码", "strength": 0.75}]'

            return _Resp()

    client = ProviderLlmClient(_JsonProvider())
    analyzer = BehaviorPatternAnalyzer(llm_client=client)
    habits = analyzer.analyze(_summaries(3), ModelRef(name="anthropic/claude-sonnet-4-6"))
    assert len(habits) == 1
    assert habits[0].pattern == "用户常在晚上写代码"
    assert habits[0].strength == 0.75
    assert habits[0].evidence_refs == (1, 2, 3)


def test_analyzed_habit_persists_via_habit_store(tmp_store: HabitStore) -> None:
    """凝出的 Habit(id=0)经 HabitStore.upsert 落库 → 获真 id + 真 first_seen。"""
    reply = '[{"pattern": "用户偏好 vim", "strength": 0.8}]'
    analyzer = BehaviorPatternAnalyzer(llm_client=_RecordingLlm(reply))
    habits = analyzer.analyze(_summaries(2), ModelRef(name="anthropic/claude-sonnet-4-6"))
    assert habits[0].id == 0  # 未持久化
    assert habits[0].first_seen == 0.0
    # 落库
    stored = tmp_store.upsert(
        habits[0].pattern,
        strength=habits[0].strength,
        evidence_refs=habits[0].evidence_refs,
        model_ref=habits[0].model_ref,
    )
    assert stored.id > 0  # 获真 id
    assert stored.first_seen > 0.0  # HabitStore 赋真时间
    assert stored.pattern == "用户偏好 vim"


# ---- AC18: FB-5 不变量 ----


def test_trace_habit_does_not_depend_on_karvy_private() -> None:
    """FB-5 锁:trace_habit 模块源码不 import karvy.atoms(注释里提 IntentAnalyst 是允许的)。"""
    import karvyloop.karvy.fastbrain.trace_habit as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    import_lines = [
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    import_blob = "\n".join(import_lines)
    assert "karvy.atoms" not in import_blob, (
        f"FB-5 violation — trace_habit 禁 import karvy.atoms:\n{import_blob}"
    )
    assert "IntentAnalyst" not in import_blob


# ---- AC19: Habit frozen dataclass ----


def test_habit_is_frozen_dataclass(tmp_store: HabitStore) -> None:
    """Habit 不可变(Q5 借 TraceRecord 风格)。"""
    h = tmp_store.upsert("X", strength=0.5)
    with pytest.raises(Exception):  # FrozenInstanceError
        h.strength = 0.9  # type: ignore[misc]


# ---- 集成 smoke:upsert + list + get 完整流程 ----


def test_full_lifecycle(tmp_store: HabitStore) -> None:
    """完整生命周期:插 3 条 → 同 pattern 再插 → list → get → count。"""
    a = tmp_store.upsert("用户每天 8 点起床", strength=0.6, evidence_refs=(1,))
    b = tmp_store.upsert("用户偏好 Markdown", strength=0.9, evidence_refs=(2, 3))
    c = tmp_store.upsert("用户每天 8 点起床", strength=0.7, evidence_refs=(1, 4))  # merge a
    assert a.id == c.id  # dedup
    assert b.id != c.id
    assert c.evidence_count == 2  # 1 + 4(去重)
    assert c.strength == 0.7  # max(0.6, 0.7)

    items = tmp_store.list_habits()
    assert len(items) == 2
    # b(strength 0.9)在前
    assert items[0].id == b.id
    assert items[1].id == c.id

    fetched = tmp_store.get_habit(c.id)
    assert fetched is not None
    assert fetched.evidence_refs == (1, 4)

    assert tmp_store.count() == 2
