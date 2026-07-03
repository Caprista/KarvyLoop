"""docs/44 断⑭ + 断⑧ — 结晶信任包验收。

断⑭(验证门空心):独立验收 verdict 回流 VerifyStore/eval_fact;结晶闸门从"自报成功
N 次"升级为"跑成且没被打差评";无独立验据 → 照样结晶但 SKILL.md 诚实标
`verified: false`,recall 排序在前两键打平时吃这个标;自报与独立验据按 note 前缀分开存。

断⑧(evict 误杀):guided/dynamic 重跑早返回补 usage 记账(带去抖);evict 判据认
recall_count;回归:天天重跑的技能 30 天不被 evict,真没人用的照常归档。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from karvyloop.cli.pursuit_loop import pursue
from karvyloop.coding.checker import Verdict
from karvyloop.crystallize import (
    EVAL_FACT_KIND,
    INDEPENDENT_NOTE,
    SELF_REPORT_NOTE,
    InMemoryUsageStore,
    VerifyStore,
    build_skill_md,
    evaluate_pending,
    evict_stale,
    mark_skill_verified,
    maybe_promote,
    recall,
    write_skill_md,
)
from karvyloop.crystallize.crystallize import DecisionKind
from karvyloop.runtime.main_loop import Brain, MainLoop
from karvyloop.schemas import AtomRun, UsageStats


# ---- 工具(同 test_main_loop 的可控时钟/慢脑桩,独立拷贝避免测试间耦合)----

class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, sec: float = 100.0) -> None:
        self.t += sec


def make_slow_brain(*, success: bool = True) -> Callable[[str], tuple[str, AtomRun]]:
    n = [0]

    def slow_brain(intent: str) -> tuple[str, AtomRun]:
        n[0] += 1
        run = AtomRun(
            atom_id=f"run-{n[0]}",
            input={"intent": intent, "x": n[0]},
            output={"text": f"ok-{n[0]}"},
            success=success,
            tool_calls=[{"name": "run_command"}],
            trace_ref=f"trace-{n[0]}",
            ts=0.0,
        )
        return f"ok-{n[0]}", run

    return slow_brain


def crystallized_loop(tmp_path: Path, clk: Clock, intent: str = "summarize weekly report"):
    """3 次慢脑触发结晶,返回 (ml, sig, slow_brain)。默认 dynamic(命中重跑)。"""
    ml = MainLoop(skills_dir=tmp_path / "skills", clock=clk)
    ml.bootstrap()
    sb = make_slow_brain()
    sig = ""
    for _ in range(3):
        clk.tick()
        r = ml.drive(intent, slow_brain=sb)
        sig = r.sig
    assert r.crystallized is True, "前置:3 次同 intent 该结晶"
    return ml, sig, sb


_RK_CHECK = {"token": object(), "sandbox": object(), "gateway": object(),
             "workspace_root": "/", "model_ref": "m"}


def _patch_check(monkeypatch, verdicts):
    seq = list(verdicts)
    n = {"i": 0}

    async def _stub(goal, text, **kw):
        v = seq[min(n["i"], len(seq) - 1)]
        n["i"] += 1
        return v

    monkeypatch.setattr("karvyloop.cli.pursuit_loop.independent_check", _stub)


# ============ 断⑭-1:unverified 诚实落盘(无独立验据仍结晶,标 verified: false) ============

def test_crystallize_without_independent_evidence_marks_verified_false(tmp_path: Path):
    clk = Clock()
    ml, sig, _sb = crystallized_loop(tmp_path, clk)
    name = ml.skill_index.name_for_sig(sig)
    assert name, "结晶技能该进索引"
    text = (ml.skills_dir / name / "SKILL.md").read_text(encoding="utf-8")
    assert "verified: false" in text, "无独立验据 → 照样结晶,但 frontmatter 诚实标 false"
    # 自报验据在,且 note 是自报常量(与独立验据分开)
    assert ml.verify.has_gate(sig) is True
    assert ml.verify.has_independent(sig) is False
    assert all(p.note == SELF_REPORT_NOTE for p in ml.verify.proofs(sig))


# ============ 断⑭-2:pursue 真路径 —— checker PASS 回流(VerifyStore + eval_fact + 翻标) ============

def test_pursue_pass_verdict_flows_back(tmp_path: Path, monkeypatch):
    clk = Clock()
    intent = "summarize weekly report"
    ml, sig, sb = crystallized_loop(tmp_path, clk, intent)
    _patch_check(monkeypatch, [Verdict(passed=True, feedback="核验过,产物齐")])

    clk.tick()
    out = pursue(intent, ml=ml, slow_brain=sb, rk=_RK_CHECK)
    assert out.checked.verdict.passed is True
    # ① 独立验据入 VerifyStore(note 前缀区分自报)
    assert ml.verify.has_independent(sig) is True
    ind = [p for p in ml.verify.proofs(sig) if p.note.startswith(INDEPENDENT_NOTE)]
    assert len(ind) == 1 and ind[0].passed is True
    # ② SKILL.md 的诚实标跟着事实走:false → true
    name = ml.skill_index.name_for_sig(sig)
    text = (ml.skills_dir / name / "SKILL.md").read_text(encoding="utf-8")
    assert "verified: true" in text and "verified: false" not in text
    # ③ eval_fact 进 Trace(评价器零改动可消费),标 checker_verdict
    facts = []
    for tid in ml.trace.all_tasks():
        facts += [e for e in ml.trace.query(tid, kind=EVAL_FACT_KIND)
                  if e.payload.get("checker_verdict")]
    assert len(facts) == 1
    assert facts[0].payload["sig"] == sig and facts[0].payload["success"] is True
    # ④ 异步评价器消费后成满意度样本(跑评分离路径不改)
    n = evaluate_pending(ml.trace, ml.satisfaction, clock=clk)
    assert n >= 1
    refs = [s.trace_ref for s in ml.satisfaction.samples(sig)]
    assert any(r.startswith("verdict://") for r in refs)


# ============ 断⑭-3:checker FAIL = 差评 → 闸门"被打差评的不晋升" ============

def test_fail_verdicts_block_promotion_via_satisfaction_gate(tmp_path: Path):
    clk = Clock()
    ml, sig, _sb = crystallized_loop(tmp_path, clk)
    # 3 条独立验收 FAIL 回流(差评样本;record_verdict FAIL 不写 VerifyStore,只写 eval_fact)
    for i in range(3):
        clk.tick()
        assert ml.record_verdict(sig, passed=False, feedback=f"结果不对 {i}",
                                 task_id=f"t-fail-{i}") is True
    assert ml.verify.has_independent(sig) is False  # FAIL 不算验据
    evaluate_pending(ml.trace, ml.satisfaction, clock=clk)
    assert len(ml.satisfaction.samples(sig)) >= 3
    # 闸门:usage 侧本来 READY(3 次成功/泛化/score 够;now 取最后使用时刻,免半衰期
    # 微衰减把 score 压到 3.0 之下干扰本测),满意度差评压下来 → NOT_YET
    now = ml.store.get(sig).last_used_at
    d = maybe_promote(sig, ml.store, ml.verify, now=now, satisfaction=ml.satisfaction)
    assert d.kind is DecisionKind.NOT_YET
    assert "satisfaction" in d.reason
    # 影响面对照:不传 satisfaction = 旧行为,照旧 READY(0 回归面)
    d_old = maybe_promote(sig, ml.store, ml.verify, now=now)
    assert d_old.kind is DecisionKind.READY


def test_pure_success_history_never_blocked_by_satisfaction_gate(tmp_path: Path):
    """保守标定:纯成功历史(含未核验成功 0.5 样本)永不被满意度关误拦。"""
    clk = Clock()
    ml, sig, _sb = crystallized_loop(tmp_path, clk)
    # 灌 3 条"成功但未核验"(overall=0.5)+ 1 条核验成功 —— 全无差评
    from karvyloop.crystallize import record_facts
    for i in range(3):
        record_facts(ml.satisfaction, sig, success=True, verified=False,
                     steps=1, trace_ref=f"r-unv-{i}", clock=clk)
    record_facts(ml.satisfaction, sig, success=True, verified=True,
                 steps=1, trace_ref="r-v", clock=clk)
    now = ml.store.get(sig).last_used_at
    d = maybe_promote(sig, ml.store, ml.verify, now=now, satisfaction=ml.satisfaction)
    assert d.kind is DecisionKind.READY, f"纯成功历史被误拦: {d.reason}"


# ============ 断⑭-4:recall 排序吃 verified 标(只在前两键打平时破平) ============

def _write_skill(skills_dir: Path, name: str, sig: str, *, verified) -> None:
    text = build_skill_md(
        name=name, description="draw sales chart", body="## Goal\ndraw sales chart",
        signature=sig, verify_proof={"passed_at": 1, "verifier": "auto", "note": "x"},
        trace_refs=["t"], when_to_use="draw sales chart", verified=verified,
    )
    write_skill_md(skills_dir / name, text)


def test_recall_prefers_independently_verified_on_tie(tmp_path: Path):
    skills = tmp_path / "skills"
    # 名字让 verified 的排 glob **后**面:若赢只能是靠 verified 键(先出现者打平不被换)
    _write_skill(skills, "aaa_chart", "sig-aaa-0000000", verified=False)
    _write_skill(skills, "zzz_chart", "sig-zzz-0000000", verified=True)
    hit = recall("draw sales chart", skills_dir=skills, scope="user")
    assert hit is not None and hit.name == "zzz_chart"


def test_recall_tie_without_verified_keeps_first(tmp_path: Path):
    """对照:都没独立验据(false/缺标同级)→ 行为同旧(先到先得),证明第三键只破平不搅局。"""
    skills = tmp_path / "skills"
    _write_skill(skills, "aaa_chart", "sig-aaa-0000000", verified=False)
    _write_skill(skills, "zzz_chart", "sig-zzz-0000000", verified=False)
    hit = recall("draw sales chart", skills_dir=skills, scope="user")
    assert hit is not None and hit.name == "aaa_chart"


# ============ 断⑭-5:mark_skill_verified 幂等/边界 ============

def test_mark_skill_verified_flip_and_idempotent(tmp_path: Path):
    _write_skill(tmp_path / "s", "k", "sig-k", verified=False)
    p = tmp_path / "s" / "k" / "SKILL.md"
    assert mark_skill_verified(p) is True
    assert "verified: true" in p.read_text(encoding="utf-8")
    assert mark_skill_verified(p) is True  # 幂等
    # 正文里的同形行不被误改(只动 frontmatter)
    body_trap = build_skill_md(
        name="trap", description="d", body="verified: false\n正文里这行不许动",
        signature="sig-trap", verify_proof={"passed_at": 1, "verifier": "auto"},
        trace_refs=[], verified=False,
    )
    write_skill_md(tmp_path / "trap", body_trap)
    p2 = tmp_path / "trap" / "SKILL.md"
    assert mark_skill_verified(p2) is True
    t2 = p2.read_text(encoding="utf-8")
    assert t2.count("verified: true") == 1 and "verified: false\n正文里这行不许动" in t2
    # 没有 verified 行(老技能)→ 不补写、返 False
    old = build_skill_md(
        name="old", description="d", body="b", signature="sig-old",
        verify_proof={"passed_at": 1, "verifier": "auto"}, trace_refs=[],
    )
    write_skill_md(tmp_path / "old", old)
    assert mark_skill_verified(tmp_path / "old" / "SKILL.md") is False


# ============ 断⑧-1:dynamic 重跑补 usage 记账(带去抖) ============

def test_dynamic_rerun_accounts_usage(tmp_path: Path):
    clk = Clock()
    ml, sig, sb = crystallized_loop(tmp_path, clk)
    before = ml.store.get(sig)
    clk.tick()  # +100s > 60s 去抖
    r = ml.drive("summarize weekly report", slow_brain=sb)
    assert r.brain == Brain.SLOW and r.skill_name  # dynamic 命中重跑
    after = ml.store.get(sig)
    assert after.usage_count == before.usage_count + 1
    assert after.success_count == before.success_count + 1
    assert after.last_used_at == clk.t
    # 去抖:紧接着(+10s)再跑,计数不再 +1,但 last_used_at 照刷
    clk.tick(10.0)
    ml.drive("summarize weekly report", slow_brain=sb)
    debounced = ml.store.get(sig)
    assert debounced.usage_count == after.usage_count
    assert debounced.last_used_at == clk.t


# ============ 断⑧-2:回归 —— 天天重跑 30 天不被 evict;真没人用的照常归档 ============

def test_daily_rerun_skill_survives_30_days_unused_one_archived(tmp_path: Path):
    clk = Clock()
    ml, sig, sb = crystallized_loop(tmp_path, clk)
    # 对照组:一个真没人用的 sig(结晶时刻后再没动过)
    dead_sig = "sig-dead-000000"
    ml.store.put(dead_sig, UsageStats(usage_count=1, success_count=1, last_used_at=clk.t))
    # 35 天,天天重跑同 intent(dynamic 命中 → 早返回路径)
    for _day in range(35):
        clk.tick(86400.0)
        r = ml.drive("summarize weekly report", slow_brain=sb)
        assert r.skill_name, "该天天命中已结晶技能"
    ml.background_review()
    assert ml.store.is_archived(sig) is False, "天天重跑的技能不许被归档(断⑧)"
    assert ml.store.is_archived(dead_sig) is True, "真没人用的照常归档"


def test_evict_stale_counts_recall_activity(tmp_path: Path):
    """单元:usage 冻结但 recall_count 高的技能不被归档;recall_count=0 行为同旧公式。"""
    now = 1_700_000_000.0
    store = InMemoryUsageStore()
    store.put("hot", UsageStats(usage_count=1, success_count=1, recall_count=100,
                                last_used_at=now - 40 * 86400))
    store.put("cold", UsageStats(usage_count=1, success_count=1, recall_count=0,
                                 last_used_at=now - 40 * 86400))
    archived = evict_stale(store, now=now)
    assert "cold" in archived and "hot" not in archived


# ============ record_verdict 边界 ============

def test_record_verdict_rejects_empty_sig_and_survives_stub_stores(tmp_path: Path):
    clk = Clock()
    ml = MainLoop(skills_dir=tmp_path / "skills", clock=clk)
    assert ml.record_verdict("", passed=True) is False
    # 未结晶的 sig 也能回流(先有验据后有技能是合法时序)
    assert ml.record_verdict("sig-early", passed=True, feedback="ok", task_id="t1") is True
    assert ml.verify.has_independent("sig-early") is True
