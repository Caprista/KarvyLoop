"""crystallize 验收测试 — 逐条对应 docs/modules/crystallize.md §5。

9 条 AC:
  1.  N 次使用 → 候选;success_rate 来自 Trace
  2.  signature 归一化(同意图不同月份 → 同签;结构不同 → 不同签)
  3.  无 verify gate → 不结晶(关 1 挡)
  4.  promote 阈值 score≥3 / sr≥0.8 / (generalized OR high_freq) → Ready
  5.  结晶产物合法 SKILL.md(含 verify_proof + trace_refs)
  6.  7天半衰期公式 + 30天未用低分 → 归档(不删)
  7.  improve:每 5 轮写回纠正到 SKILL.md
  8.  recall:命中已结晶 → 快脑;未命中 → 慢脑(None)
  9.  evict 可逆:归档技能被召回 → restore
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import pytest

from karvyloop.crystallize import (
    DecisionKind,
    EVICT_SCORE,
    HALFLIFE_DAYS,
    HIGH_FREQ,
    InMemoryUsageStore,
    MIN_SUCCESS_RATE,
    PROMOTE_SCORE,
    STALE_DAYS,
    USAGE_DEBOUNCE_SEC,
    VerifyResult,
    VerifyStore,
    build_skill_md,
    compute_signature,
    crystallize,
    evict_stale,
    maybe_improve,
    maybe_promote,
    observe,
    recall,
    restore,
    same_signature,
    success_rate,
    usage_score,
    write_skill_md,
)
from karvyloop.schemas import AtomRun, UsageStats


# ---- 工具 ----

def make_run(
    *,
    intent: str = "summarize monthly report",
    input_: Optional[dict] = None,
    success: bool = True,
    tool_calls: Optional[list[dict]] = None,
    trace_ref: str = "trace-1",
    ts: float = 0.0,
) -> AtomRun:
    """构造一个 AtomRun。M1 测试简版:input 是 dict,tool_calls 列表。"""
    return AtomRun(
        atom_id="a1",
        input={"intent": intent, **(input_ or {})},
        output={"ok": True} if success else None,
        success=success,
        tool_calls=tool_calls or [],
        trace_ref=trace_ref,
        ts=ts,
    )


class FrozenClock:
    """测试用可控时钟。"""
    def __init__(self, t0: float = 1_700_000_000.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> None:
        self.t += sec


# ============ AC1: N 次使用 → 候选;success_rate 来自 Trace ============
def test_ac1_observe_creates_candidate_from_runs(tmp_path: Path):
    """同 sig 跑 5 次成功 4 次失败 1 次 → UsageStats 记录正确。"""
    store = InMemoryUsageStore()
    runs = []
    for i in range(5):
        runs.append(make_run(
            intent="weekly summary",
            input_={"week": f"2026-W{i+1:02d}"},
            success=(i != 0),  # 4 success + 1 fail
            tool_calls=[{"name": "read_file"}],
            trace_ref=f"t-{i}",
            ts=1_700_000_000.0 + i * 100,  # 拉开间距过 60s 去抖
        ))
    counts = observe(runs, store)
    assert len(counts) == 1
    sig = next(iter(counts))
    stats = store.get(sig)
    assert stats is not None
    assert stats.usage_count == 5
    assert stats.success_count == 4
    assert stats.failure_count == 1
    # success_rate 来自 success/usage(由 UsageStats 派生,非另埋点)
    assert success_rate(stats) == pytest.approx(4 / 5)


def test_ac1b_observe_respects_60s_debounce():
    """60s 内重复使用同 sig → 不重复计数 usage_count(只更新 last_used_at)。"""
    store = InMemoryUsageStore()
    clk = FrozenClock(1_700_000_000.0)
    runs1 = [make_run(intent="x", trace_ref="t1", ts=clk.t)]
    observe(runs1, store, clock=clk)
    # 30s 后再用 — 仍在 60s 内 → 去抖
    clk.advance(30.0)
    runs2 = [make_run(intent="x", trace_ref="t2", ts=clk.t)]
    observe(runs2, store, clock=clk)
    sig = compute_signature(runs1[0])
    stats = store.get(sig)
    assert stats.usage_count == 1, "60s 内重复使用不应增计数"
    # 再过 61s(累计 91s)→ 过 60s 阈值,应再计
    clk.advance(61.0)
    runs3 = [make_run(intent="x", trace_ref="t3", ts=clk.t)]
    observe(runs3, store, clock=clk)
    stats2 = store.get(sig)
    assert stats2.usage_count == 2, "过 60s 后应再计"


# ============ AC2: signature 归一化 ============
def test_ac2_signature_same_intent_different_month():
    """同意图不同月份参数 → 同签。"""
    r1 = make_run(intent="summarize for 2026-01", input_={"month": "2026-01"})
    r2 = make_run(intent="summarize for 2026-05", input_={"month": "2026-05"})
    s1 = compute_signature(r1)
    s2 = compute_signature(r2)
    assert s1 == s2
    assert same_signature(s1, s2)


def test_ac2b_signature_different_structure_different_sig():
    """不同 schema 形状 → 不同签(不漏并)。"""
    r1 = make_run(intent="summarize", input_={"month": "2026-01"})
    r2 = make_run(intent="summarize", input_={"day": "2026-01-15", "extra": 1})
    s1 = compute_signature(r1)
    s2 = compute_signature(r2)
    assert s1 != s2


def test_ac2c_signature_ignores_tool_set():
    """9.4 门1 修正(结晶宽松):工具集不同但意图+输入相同 → **同签**。

    原行为(工具集进签名)在真机抓到致命问题:同一任务每跑一次 LLM 工具路径都不同
    → 碎成 N 个签名各 usage=1 → 永不结晶。改为签名只看 意图聚类+输入形状,执行路径
    (用了哪些工具)不决定"是不是同一能力"。召回(intent overlap)不受影响,仍严格。
    """
    r1 = make_run(
        intent="fetch page",
        tool_calls=[{"name": "WebFetch"}],
    )
    r2 = make_run(
        intent="fetch page",
        tool_calls=[{"name": "WebFetch"}, {"name": "Bash"}],
    )
    # 同意图 + 同输入 → 同签(工具集不再进签名)
    assert compute_signature(r1) == compute_signature(r2)


# ============ AC3: 无 verify gate → 不结晶 ============
def test_ac3_no_verify_gate_blocks_promote():
    """关 1:没 verify gate → NOT_ELIGIBLE,即便用够也不行。"""
    store = InMemoryUsageStore()
    verify = VerifyStore()
    # 模拟 5 次成功
    clk = FrozenClock(1_700_000_000.0)
    runs = []
    for i in range(5):
        runs.append(make_run(
            intent="x", input_={"i": i},
            success=True, trace_ref=f"t{i}",
            ts=clk.t + i * 100,
        ))
    observe(runs, store, clock=clk)
    sig = compute_signature(runs[0])
    # 不调 verify.mark_verified
    decision = maybe_promote(sig, store, verify, now=clk.t)
    assert decision.kind is DecisionKind.NOT_ELIGIBLE
    assert "verify gate" in decision.reason


def test_ac3b_verify_gate_present_success_zero_still_blocked():
    """关 1:有 verify gate 但 0 成功 → 仍 NOT_ELIGIBLE。"""
    store = InMemoryUsageStore()
    verify = VerifyStore()
    clk = FrozenClock(1_700_000_000.0)
    # 5 次失败
    runs = [make_run(success=False, trace_ref=f"t{i}", ts=clk.t + i * 100)
            for i in range(5)]
    observe(runs, store, clock=clk)
    sig = compute_signature(runs[0])
    verify.mark_verified(sig, "trace-x", clock=clk)
    decision = maybe_promote(sig, store, verify, now=clk.t)
    assert decision.kind is DecisionKind.NOT_ELIGIBLE
    assert "never succeeded" in decision.reason


# ============ AC4: 两关都过 → Ready ============
def test_ac4_promote_threshold_ready_with_generalized():
    """score≥3 / sr≥0.8 / generalized → Ready(高使用频次,参数化)。"""
    store = InMemoryUsageStore()
    verify = VerifyStore()
    clk = FrozenClock(1_700_000_000.0)
    # 4 次成功,每次不同 month 参数(generalized)
    runs = [
        make_run(intent="monthly report", input_={"month": f"2026-{m:02d}"},
                 success=True, tool_calls=[{"name": "read_file"}],
                 trace_ref=f"t{m}", ts=clk.t + m * 100)
        for m in range(1, 5)
    ]
    observe(runs, store, clock=clk)
    sig = compute_signature(runs[0])
    verify.mark_verified(sig, "t1", clock=clk)
    decision = maybe_promote(sig, store, verify, now=clk.t)
    # score = 4 × 1.0 = 4 ≥ 3;sr = 4/4 = 1.0 ≥ 0.8;generalized=True(同 schema,4 种取值)
    assert decision.kind is DecisionKind.READY
    assert decision.score >= PROMOTE_SCORE
    assert decision.success_rate >= MIN_SUCCESS_RATE
    assert decision.generalized is True


def test_ac4b_promote_high_freq_even_without_generalized():
    """高频(usage_count ≥ HIGH_FREQ)即便不 generalized 也可 Ready。"""
    store = InMemoryUsageStore()
    verify = VerifyStore()
    clk = FrozenClock(1_700_000_000.0)
    # 5 次成功,所有同 month 参数(不 generalized)
    runs = [
        make_run(intent="x", input_={"month": "2026-01"},
                 success=True, trace_ref=f"t{i}", ts=clk.t + i * 100)
        for i in range(HIGH_FREQ)
    ]
    observe(runs, store, clock=clk)
    sig = compute_signature(runs[0])
    verify.mark_verified(sig, "t0", clock=clk)
    decision = maybe_promote(sig, store, verify, now=clk.t)
    assert decision.kind is DecisionKind.READY
    assert decision.high_freq is True
    assert decision.generalized is False


def test_ac4c_promote_not_yet_when_score_below_threshold():
    """score<3 → NOT_YET。"""
    store = InMemoryUsageStore()
    verify = VerifyStore()
    clk = FrozenClock(1_700_000_000.0)
    # 只 1 次成功(score = 1 × 1.0 = 1 < 3)
    runs = [make_run(intent="x", success=True, trace_ref="t0", ts=clk.t)]
    observe(runs, store, clock=clk)
    sig = compute_signature(runs[0])
    verify.mark_verified(sig, "t0", clock=clk)
    decision = maybe_promote(sig, store, verify, now=clk.t)
    assert decision.kind is DecisionKind.NOT_YET


def test_ac4d_promote_not_yet_when_success_rate_below_threshold():
    """成功率<0.8 → NOT_YET(即便用够)。"""
    store = InMemoryUsageStore()
    verify = VerifyStore()
    clk = FrozenClock(1_700_000_000.0)
    # 4 成功 + 1 失败 → sr = 4/5 = 0.8(边界;再调一次失败让 sr < 0.8)
    runs = [
        make_run(intent="x", input_={"i": i}, success=(i < 4),
                 trace_ref=f"t{i}", ts=clk.t + i * 100)
        for i in range(5)
    ]
    observe(runs, store, clock=clk)
    sig = compute_signature(runs[0])
    verify.mark_verified(sig, "t0", clock=clk)
    decision = maybe_promote(sig, store, verify, now=clk.t)
    # sr = 4/5 = 0.8 ≥ 0.8 → 应过(阈值是 ≥,不是 >)
    # 改成 3 成功 + 2 失败
    store2 = InMemoryUsageStore()
    runs2 = [
        make_run(intent="x", input_={"i": i + 1}, success=(i < 3),
                 trace_ref=f"t{i}", ts=clk.t + i * 100)
        for i in range(5)
    ]
    observe(runs2, store2, clock=clk)
    sig2 = compute_signature(runs2[0])
    verify2 = VerifyStore()
    verify2.mark_verified(sig2, "t0", clock=clk)
    decision2 = maybe_promote(sig2, store2, verify2, now=clk.t)
    assert decision2.kind is DecisionKind.NOT_YET
    assert "success_rate" in decision2.reason


# ============ AC5: 结晶产物是合法 SKILL.md ============
def test_ac5_crystallize_writes_legal_skill_md(tmp_path: Path):
    store = InMemoryUsageStore()
    verify = VerifyStore()
    clk = FrozenClock(1_700_000_000.0)
    runs = [
        make_run(intent="monthly report", input_={"month": f"2026-{m:02d}"},
                 success=True, tool_calls=[{"name": "read_file"}],
                 trace_ref=f"t{m}", ts=clk.t + m * 100)
        for m in range(1, 5)
    ]
    observe(runs, store, clock=clk)
    sig = compute_signature(runs[0])
    verify.mark_verified(sig, "t1", note="trace-based", clock=clk)
    skills_dir = tmp_path / "skills"
    skill = crystallize(
        sig,
        name="monthly-report",
        description="generate monthly report from month param",
        body="# Steps\n1. Read data\n2. Summarize",
        when_to_use="monthly report summary 2026",
        arguments=[{"name": "month", "type": "string", "required": True}],
        store=store, verify=verify, skills_dir=skills_dir,
        scope="user", now=clk.t + 400,
    )
    # SKILL.md 写盘
    path = skills_dir / "monthly-report" / "SKILL.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    # frontmatter 必含 name/description/when_to_use/verify_proof/trace_refs
    assert "name: monthly-report" in text
    assert "description: generate monthly report" in text
    assert "verify_proof:" in text
    assert "trace_refs:" in text
    assert "t1" in text
    # Skill 内存态
    assert skill.name == "monthly-report"
    assert skill.from_candidate == sig
    assert "t1" in skill.verify_proof.get("note", "") or skill.verify_proof.get("note") == "trace-based"


def test_ac5b_build_skill_md_format_is_parseable_by_registry(tmp_path: Path):
    """build_skill_md 产出的 SKILL.md 能被 registry.skills.parse_frontmatter 解析(不双轨)。"""
    text = build_skill_md(
        name="foo", description="bar", body="## steps",
        signature="abcdef0123456789",
        verify_proof={"passed_at": 1.0, "verifier": "auto", "note": "x"},
        trace_refs=["r1", "r2"],
        when_to_use="foo bar",
        arguments=[{"name": "x", "type": "string"}],
    )
    p = tmp_path / "SKILL.md"
    p.write_text(text, encoding="utf-8")
    from karvyloop.registry.skills import parse_frontmatter
    fm, body = parse_frontmatter(p)
    assert fm.name == "foo"
    assert fm.description == "bar"
    assert fm.when_to_use == "foo bar"
    assert any(a.get("name") == "x" for a in (fm.arguments or []))
    assert "## steps" in body


# ============ AC6: 7天半衰期 + 30天未用 → 归档 ============
def test_ac6_usage_score_halflife_formula():
    """7天半衰期:usage_count × 0.5^(days/7),保底 0.1。"""
    now = 1_700_000_000.0
    stats = UsageStats(usage_count=10, last_used_at=now, success_count=10)
    # 0 天 → recency 1.0 → 10
    assert usage_score(stats, now=now) == pytest.approx(10.0)
    # 7 天 → 5.0
    assert usage_score(stats, now=now + 7 * 86400) == pytest.approx(5.0)
    # 14 天 → 2.5
    assert usage_score(stats, now=now + 14 * 86400) == pytest.approx(2.5)
    # 28 天 → 1.25
    assert usage_score(stats, now=now + 28 * 86400) == pytest.approx(1.0)


def test_ac6b_usage_score_min_recency_floor():
    """极长时间不用 → recency 不低于 0.1(MIN_RECENCY_FACTOR),但 × usage_count 仍生效。"""
    now = 1_700_000_000.0
    stats = UsageStats(usage_count=4, last_used_at=now, success_count=4)
    # 365 天后
    s = usage_score(stats, now=now + 365 * 86400)
    # 0.5^(365/7) ≈ 极小 → 截到 0.1 → 4 * 0.1 = 0.4
    assert s == pytest.approx(0.4)


def test_ac6c_evict_archive_not_delete():
    """30 天未用 + score < EVICT_SCORE → 归档(不删 store 数据)。"""
    store = InMemoryUsageStore()
    now = 1_700_000_000.0
    # usage_count=1,last_used 35 天前 → score = 1 × 0.5^(35/7) ≈ 0.0306,保底 0.1 → 0.1
    # 0.1 < EVICT_SCORE(0.5),35 > STALE_DAYS(30) → 应归档
    stats = UsageStats(usage_count=1, last_used_at=now - 35 * 86400,
                        success_count=1)
    store.put("sig-a", stats)
    archived = evict_stale(store, now=now)
    assert "sig-a" in archived
    assert store.is_archived("sig-a") is True
    # UsageStats 数据仍在(可逆)
    assert store.get("sig-a") is not None


def test_ac6d_evict_skip_if_not_stale_yet():
    """30 天内 + 低分 → 不归档。"""
    store = InMemoryUsageStore()
    now = 1_700_000_000.0
    # score 低,但 没用超过 30 天 → 不动
    stats = UsageStats(usage_count=1, last_used_at=now - 5 * 86400,
                        success_count=1)
    store.put("sig-a", stats)
    archived = evict_stale(store, now=now)
    assert archived == []


def test_ac6e_evict_skip_high_score_even_if_old():
    """高分(刚用完)即便 30 天前用过也不归档(刚 last_used_at 是 0 天前,高 use_count)。"""
    store = InMemoryUsageStore()
    now = 1_700_000_000.0
    # 10 次成功,昨天用过 → score ≈ 10,远高于 EVICT_SCORE
    stats = UsageStats(usage_count=10, last_used_at=now - 86400,
                        success_count=10)
    store.put("sig-a", stats)
    archived = evict_stale(store, now=now)
    assert archived == []


# ============ AC7: improve 每 5 轮写回 ============
def test_ac7_improve_triggers_every_5_turns(tmp_path: Path):
    """turn_count % 5 == 0 且 UsageStats.steered_by_user 非空 → 写回 SKILL.md。"""
    store = InMemoryUsageStore()
    # 先准备一个已结晶的 skill(手工写 SKILL.md)
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "daily-report"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "---\nname: daily-report\ndescription: x\nwhen_to_use: y\n---\n# Body\n",
        encoding="utf-8",
    )
    # steered_by_user 有纠正
    stats = UsageStats(
        usage_count=6, last_used_at=1_700_000_000.0, success_count=6,
        steered_by_user=["记得加日期", "用 markdown 表格"],
    )
    store.put("sig-x", stats)
    # turn_count=3 → 不触发
    assert maybe_improve("daily-report", skills_dir=skills_dir, store=store,
                         sig="sig-x", turn_count=3) is False
    # turn_count=5 → 触发
    assert maybe_improve("daily-report", skills_dir=skills_dir, store=store,
                         sig="sig-x", turn_count=5) is True
    text = skill_dir.joinpath("SKILL.md").read_text(encoding="utf-8")
    # M1.5 起:纠正按 5 类分桶写,不再全塞 ## Corrections。
    # "记得加日期" → ## Add;"用 markdown 表格" → ## Preferences
    assert "## Add" in text
    assert "## Preferences" in text
    assert "记得加日期" in text
    assert "用 markdown 表格" in text
    # 类别前缀也写入了,便于审计
    assert "[add]" in text
    assert "[preference]" in text


def test_ac7b_improve_no_op_on_short_or_no_correction(tmp_path: Path):
    """turn_count 不是 5 的倍数,或没有纠正 → 不写。"""
    store = InMemoryUsageStore()
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "x"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "---\nname: x\ndescription: y\n---\n# B\n",
        encoding="utf-8",
    )
    # 没纠正
    store.put("s", UsageStats(usage_count=5, last_used_at=1.0, success_count=5))
    assert maybe_improve("x", skills_dir=skills_dir, store=store,
                         sig="s", turn_count=5) is False


# ============ AC8: recall 命中 / 未命中 ============
def test_ac8_recall_hit_returns_skill(tmp_path: Path):
    """意图与 skill.when_to_use/description 匹配 → 召回。"""
    skills_dir = tmp_path / "skills"
    sd = skills_dir / "monthly-report"
    sd.mkdir(parents=True)
    sd.joinpath("SKILL.md").write_text(
        "---\nname: monthly-report\ndescription: monthly summary\n"
        "when_to_use: monthly summary report\n---\n# Body\n",
        encoding="utf-8",
    )
    hit = recall("monthly summary please", skills_dir=skills_dir)
    assert hit is not None
    assert hit.name == "monthly-report"


def test_ac8b_recall_miss_returns_none(tmp_path: Path):
    """意图与已结晶 skill 无关 → None(走慢脑)。"""
    skills_dir = tmp_path / "skills"
    sd = skills_dir / "monthly-report"
    sd.mkdir(parents=True)
    sd.joinpath("SKILL.md").write_text(
        "---\nname: monthly-report\ndescription: monthly summary\n"
        "when_to_use: monthly summary report\n---\n# Body\n",
        encoding="utf-8",
    )
    hit = recall("translate chinese to english", skills_dir=skills_dir)
    assert hit is None


# ============ AC9: evict 可逆 ============
def test_ac9_archived_skill_can_be_restored():
    """归档后 restore → is_archived 变 False,数据仍可访问。"""
    store = InMemoryUsageStore()
    now = 1_700_000_000.0
    stats = UsageStats(usage_count=1, last_used_at=now - 35 * 86400,
                        success_count=1)
    store.put("sig-a", stats)
    evict_stale(store, now=now)
    assert store.is_archived("sig-a") is True
    # 召回命中 → 恢复(可逆)
    assert restore("sig-a", store) is True
    assert store.is_archived("sig-a") is False
    # 数据仍在
    assert store.get("sig-a") is not None


def test_ac9b_restore_noop_on_active():
    """活跃技能 restore → False(无操作)。"""
    store = InMemoryUsageStore()
    store.put("s", UsageStats(usage_count=2, last_used_at=1.0, success_count=2))
    assert restore("s", store) is False


# ============ 额外:observe 与 verify 接口协作 ============
def test_extra_observe_then_mark_verified_then_promote(tmp_path: Path):
    """端到端:observe → mark_verified → crystallize → recall。"""
    store = InMemoryUsageStore()
    verify = VerifyStore()
    clk = FrozenClock(1_700_000_000.0)
    runs = [
        make_run(intent="monthly report", input_={"month": f"2026-{m:02d}"},
                 success=True, tool_calls=[{"name": "read_file"}],
                 trace_ref=f"trace-{m}", ts=clk.t + m * 100)
        for m in range(1, 5)
    ]
    observe(runs, store, clock=clk)
    sig = compute_signature(runs[0])
    verify.mark_verified(sig, "trace-1", note="executor", clock=clk)
    skills_dir = tmp_path / "skills"
    skill = crystallize(
        sig, name="monthly-report", description="monthly summary",
        body="## steps", when_to_use="monthly summary report",
        arguments=[{"name": "month", "type": "string"}],
        store=store, verify=verify, skills_dir=skills_dir, scope="user", now=clk.t + 400,
    )
    assert skill.name == "monthly-report"
    hit = recall("monthly summary", skills_dir=skills_dir)
    assert hit is not None
    assert hit.name == "monthly-report"


# ============ 额外:regression - 7天半衰期精确数值 ============
def test_extra_halflife_at_7_days_is_exactly_half():
    """边界:7 天整正好是 1/2(spec 半衰期定义)。"""
    now = 1_700_000_000.0
    stats = UsageStats(usage_count=8, last_used_at=now, success_count=8)
    s7 = usage_score(stats, now=now + 7 * 86400)
    assert s7 == pytest.approx(4.0)


def test_extra_promote_blocked_when_score_decayed_below_threshold():
    """20 天没用(score 衰减)→ 即便累计用过 5 次,score<3 → NOT_YET。"""
    store = InMemoryUsageStore()
    verify = VerifyStore()
    now = 1_700_000_000.0
    # last_used 20 天前,usage_count=5 → score = 5 × 0.5^(20/7) ≈ 0.78
    stats = UsageStats(usage_count=5, last_used_at=now - 20 * 86400,
                        success_count=5)
    store.put("sig-decay", stats)
    verify.mark_verified("sig-decay", "trace-old", clock=lambda: now)
    decision = maybe_promote("sig-decay", store, verify, now=now)
    assert decision.kind is DecisionKind.NOT_YET


# ============ 额外:write_skill_md 工具 ============
def test_extra_write_skill_md_creates_file(tmp_path: Path):
    text = build_skill_md(
        name="x", description="y", body="z",
        signature="0000000000000000",
        verify_proof={"passed_at": 0, "verifier": "manual", "note": ""},
        trace_refs=["r1"],
    )
    p = write_skill_md(tmp_path, text)
    assert p.exists()
    assert p.read_text(encoding="utf-8") == text


# ============ 拍 9:recall_count_inc 死代码修复 ============
def test_recall_count_inc_actually_increments():
    """拍 9:UsageStore.recall_count_inc 必须真 +1,不能 no-op。

    回归保护:之前 3 处实现(抽象基类 / InMemory / Sqlite)都是 no-op,
    即使接了也什么都不干 —— 现在 schema 加了 recall_count 字段,实现真做。
    """
    store = InMemoryUsageStore()
    now = 1_700_000_000.0
    # 预存一条 stats
    store.put("sig-x", UsageStats(usage_count=3, last_used_at=now,
                                  success_count=3, failure_count=0))
    # 调 2 次
    store.recall_count_inc("sig-x")
    store.recall_count_inc("sig-x")
    cur = store.get("sig-x")
    assert cur is not None
    assert cur.recall_count == 2
    # 其他字段不变(没破坏其他)
    assert cur.usage_count == 3
    assert cur.success_count == 3


def test_recall_count_inc_noop_for_unknown_sig():
    """拍 9:recall 一个不存在的 sig 不报错(recall 路径上,可能先 hit 后 store.get 才见到)。

    行为约定:未知 sig → no-op,不动 store。
    """
    store = InMemoryUsageStore()
    # 调不应崩
    store.recall_count_inc("never-seen-sig")
    assert store.get("never-seen-sig") is None


def test_recall_calls_recall_count_inc_on_hit(tmp_path: Path):
    """拍 9:recall 命中已结晶技能 → store.recall_count_inc 自动 +1。

    之前 recall.py 命中后只 auto-restore,不调 recall_count_inc —— 接口预留了
    但 wiring 缺。今天接上:每次召回命中都 +1。
    """
    from karvyloop.crystallize import recall as recall_fn

    skills_dir = tmp_path / "skills"
    sd = skills_dir / "monthly-report"
    sd.mkdir(parents=True)
    sd.joinpath("SKILL.md").write_text(
        "---\nname: monthly-report\ndescription: monthly summary\n"
        "when_to_use: monthly summary report\n"
        "signature: 0123456789abcdef\n---\n# Body\n",
        encoding="utf-8",
    )
    store = InMemoryUsageStore()
    # 预存一条 stats(模拟结晶后写入),sig 对齐 SKILL.md 的 signature
    store.put("0123456789abcdef", UsageStats(
        usage_count=1, last_used_at=1_700_000_000.0, success_count=1,
    ))

    hit = recall_fn("monthly summary please", skills_dir=skills_dir, store=store)
    assert hit is not None
    assert hit.sig == "0123456789abcdef"

    # 核心断言:recall 命中后 recall_count 自动 +1
    cur = store.get("0123456789abcdef")
    assert cur is not None
    assert cur.recall_count == 1


def test_recall_no_inc_on_miss(tmp_path: Path):
    """拍 9:recall 未命中 → 不调 recall_count_inc(只命中才计)。

    防止误把"我没找到"也当成"用了一次"。
    """
    from karvyloop.crystallize import recall as recall_fn

    skills_dir = tmp_path / "skills"  # 空的
    store = InMemoryUsageStore()
    hit = recall_fn("translate chinese to english", skills_dir=skills_dir, store=store)
    assert hit is None
    # store 仍空,无任何 stats
    assert list(store.all()) == []


# ============ 夯实:结晶文件名/路径规范化(第二道防线) ============
# name 可能来自 LLM 产出/导入/用户输入 —— 含分隔符/`..`/非法字符/超长时也必须:
# 落在技能根之内、目录名合法、CJK 保留、幂等(同名恒同路径)。越界一律兜底重定位,
# 绝不 raise 炸结晶主流程(结晶是增益不是命脉;宁可名字丑,不写到库外)。

_DANGEROUS_FS_CHARS = '\\/:*?"<>|'


def _ready_sig(clk: FrozenClock, intent: str = "weird name crystallize"):
    """构造一个已过两关(READY)的 sig(镜像 AC5 的最小 setup)。"""
    store = InMemoryUsageStore()
    verify = VerifyStore()
    runs = [
        make_run(intent=intent, input_={"month": f"2026-{m:02d}"},
                 success=True, tool_calls=[{"name": "read_file"}],
                 trace_ref=f"t{m}", ts=clk.t + m * 100)
        for m in range(1, 5)
    ]
    observe(runs, store, clock=clk)
    sig = compute_signature(runs[0])
    verify.mark_verified(sig, "t1", note="trace-based", clock=clk)
    return store, verify, sig


def test_safe_skill_filename_normal_names_pass_through():
    """第一道防线产出的正常名(kebab / skill_<hash> / 中文)恒等通过 —— 两道防线咬合不冲突。"""
    from karvyloop.crystallize.crystallize import _safe_skill_filename

    assert _safe_skill_filename("monthly-report") == "monthly-report"
    assert _safe_skill_filename("skill_1a2b3c4d") == "skill_1a2b3c4d"
    assert _safe_skill_filename("整理发票流程") == "整理发票流程"


def test_safe_skill_filename_deterministic_fallback():
    """空/剥光/Windows 保留设备名 → 确定性兜底名(同输入恒同输出,不破幂等)。"""
    from karvyloop.crystallize.crystallize import _safe_skill_filename

    # 空串 → 兜底;两次调用结果一致(确定性)
    a, b = _safe_skill_filename(""), _safe_skill_filename("")
    assert a and a == b
    # 全被剥光(纯穿越/纯点)→ 兜底,不产出 "" / "." / ".."
    for raw in ("..", "...", "///", "  ", "-._"):
        got = _safe_skill_filename(raw)
        assert got not in ("", ".", "..")
        assert not any(c in got for c in _DANGEROUS_FS_CHARS)
    # Windows 保留设备名做目录名会写失败/写去设备 → 兜底
    assert _safe_skill_filename("con") not in ("con", "CON")
    # 显式 fallback(结晶路径挂 sig 前 8 位)生效
    assert _safe_skill_filename("", fallback="skill_deadbeef") == "skill_deadbeef"


@pytest.mark.parametrize("raw", [
    "../evil",           # 上跳穿越
    "a/b\\c",            # 两种路径分隔符混用(Windows/POSIX 都要处理)
    "con|foo?",          # Windows 危险字符
    "🦫🦫🦫",            # 纯 emoji(合法 Unicode 文件名,不抹)
    "",                  # 空串
    "x" * 200,           # 超长
    "整理发票流程",       # 正常中文(必须原样保留,见下面的专项断言)
])
def test_crystallize_weird_names_stay_inside_skills_root(tmp_path: Path, raw: str):
    """怪名结晶:全部落在技能根**直下一层**,目录名合法,写入名与查找名同源。"""
    from karvyloop.registry.skills import parse_frontmatter

    clk = FrozenClock(1_700_000_000.0)
    store, verify, sig = _ready_sig(clk)
    skills_dir = tmp_path / "skills"
    skill = crystallize(
        sig, name=raw, description="weird name case", body="## steps",
        when_to_use="weird name crystallize",
        arguments=None, store=store, verify=verify,
        skills_dir=skills_dir, scope="user", now=clk.t + 400,
    )
    p = Path(skill.manifest["path"]).resolve()
    root = skills_dir.resolve()
    # 落点:恰好 <root>/<skill>/SKILL.md 一层(*/SKILL.md 扫描发现得了,重启不失踪)
    assert p.name == "SKILL.md"
    assert p.parent.parent == root
    dirname = p.parent.name
    # 目录名合法:无危险字符 / 无穿越残留 / 不超长 / 非空
    assert dirname
    assert not any(c in dirname for c in _DANGEROUS_FS_CHARS)
    assert ".." not in dirname
    assert len(dirname) <= 64
    # 同源:落盘目录名 = 内存态 Skill.name = frontmatter name(重启 rebuild 用 frontmatter)
    assert skill.name == dirname
    fm, _body = parse_frontmatter(p)
    assert fm.name == dirname
    # 穿越名绝没写到根外(tmp_path 下只有 skills/ 一棵树)
    assert not (tmp_path / "evil").exists()
    # 中文保留专项:正常中文名原样通过(不被抹成兜底串)
    if raw == "整理发票流程":
        assert dirname == raw
    # 纯 emoji 是合法文件名 → 原样保留(只替危险字符哲学)
    if raw == "🦫🦫🦫":
        assert dirname == raw
    # 空名 → 兜底名挂 sig 前 8 位(与 sig 幂等键对齐)
    if raw == "":
        assert dirname == f"skill_{sig[:8]}"


def test_crystallize_weird_name_idempotent_same_path(tmp_path: Path):
    """幂等:同一 sig 同一怪名结晶两次 → 同一路径,技能根下只有一个技能目录。"""
    clk = FrozenClock(1_700_000_000.0)
    store, verify, sig = _ready_sig(clk)
    skills_dir = tmp_path / "skills"
    kw = dict(name="a/b\\c", description="idem", body="## steps",
              when_to_use="weird name crystallize", arguments=None,
              store=store, verify=verify, skills_dir=skills_dir,
              scope="user", now=clk.t + 400)
    s1 = crystallize(sig, **kw)
    s2 = crystallize(sig, **kw)
    assert s1.manifest["path"] == s2.manifest["path"]
    assert s1.name == s2.name == "a-b-c"
    assert len(list(skills_dir.glob("*/SKILL.md"))) == 1


def test_write_skill_md_containment_relocates_outside_root(tmp_path: Path):
    """路径包含校验:resolve 后不在技能根直下(逃逸/嵌套/根本身)→ 兜底重定位,不 raise。"""
    root = tmp_path / "skills"
    root.mkdir()
    text = build_skill_md(
        name="evil", description="escape case", body="z",
        signature="0000000000000000",
        verify_proof={"passed_at": 0, "verifier": "manual", "note": ""},
        trace_refs=["r1"],
    )
    # 上跳逃逸 → 拉回根内(根外一个字节不落)
    p = write_skill_md(root / ".." / "evil", text, skills_root=root)
    assert p.resolve().is_relative_to(root.resolve())
    assert p.resolve().parent.parent == root.resolve()
    assert not (tmp_path / "evil").exists()
    # 嵌套两层(*/SKILL.md 扫不到 = 静默失踪)→ 拉回直下一层
    p2 = write_skill_md(root / "a" / "b", text, skills_root=root)
    assert p2.resolve().parent.parent == root.resolve()
    # 不传 skills_root = 行为同旧(手工/测试直调不受影响)
    p3 = write_skill_md(tmp_path / "legacy", text)
    assert p3 == tmp_path / "legacy" / "SKILL.md"
    assert p3.exists()
