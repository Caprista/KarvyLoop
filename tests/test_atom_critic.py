"""①-a 契约测试 — atom 层结晶裁判 = role 多维分级满意度(docs/02 §14)。

锁住四条设计不变量,任一被破即 fail:
  - 契约 #1:verify verdict **流进** atom 结晶信号(成功+过门→achievement 1.0;成功未核验→0.5)。
  - 先做对再做好:做对没站住(achievement=0)→ overall=0,做好维救不回(防质量分作弊)。
  - 多维分级,不是二极管:同样成功、步数不同 → 满意度不同。
  - 信用隔离:满意度只由"本 run + 本 sig"决定,与别的 sig / role 全局成败无关。
  - 零回归:observe 不传 verify/sat_store 时,行为与从前一字不差。
"""

from __future__ import annotations

import pytest

from karvyloop.crystallize import (
    AtomSatisfaction,
    InMemoryUsageStore,
    SatisfactionStore,
    compute_signature,
    observe,
    record_run,
    score_achievement,
    score_efficiency,
)
from karvyloop.crystallize.atom_critic import W_BASE
from karvyloop.schemas import AtomRun


def _run(intent: str, *, success: bool, n_tools: int, ts: float = 1000.0) -> AtomRun:
    return AtomRun(
        atom_id="forge",
        input={"intent": intent},
        output={"ok": True} if success else None,
        success=success,
        tool_calls=[{"name": "run_command", "input": {}} for _ in range(n_tools)],
        trace_ref=f"trace:{intent}:{ts}",
        ts=ts,
    )


# ---- 评分体系(确定性·分级)----

def test_achievement_uses_verify_verdict():
    # 契约 #1 的核:验证门是 achievement 满分的前提
    assert score_achievement(success=True, has_proof=True) == 1.0
    assert score_achievement(success=True, has_proof=False) == 0.5   # 成功但未核验 → 诚实打折
    assert score_achievement(success=False, has_proof=True) == 0.0   # 没做对就是 0


def test_efficiency_is_graded_relative_to_baseline():
    assert score_efficiency(steps=5, baseline_steps=None) == 1.0      # 无基线不罚
    assert score_efficiency(steps=4, baseline_steps=8) == 1.0         # 优于基线 → 满
    assert score_efficiency(steps=8, baseline_steps=4) == pytest.approx(0.5)  # 2× 基线 → 0.5
    assert score_efficiency(steps=100, baseline_steps=1) < 0.05       # 远超基线 → 趋 0


# ---- 先做对再做好(overall 的不变量)----

def test_doing_good_cannot_rescue_not_doing_right():
    # achievement=0 → 无论效率/质量多高,overall 必须是 0(防质量分作弊)
    s = AtomSatisfaction(sig="x", achievement=0.0, efficiency=1.0, quality=1.0)
    assert s.overall == 0.0


def test_right_but_not_good_gets_base_floor():
    # 做对了但不够好(效率 0)→ 拿到地基分 W_BASE,不是满分也不是 0
    s = AtomSatisfaction(sig="x", achievement=1.0, efficiency=0.0)
    assert s.overall == pytest.approx(W_BASE)
    # 做对又高效 → 满分
    assert AtomSatisfaction(sig="x", achievement=1.0, efficiency=1.0).overall == pytest.approx(1.0)


def test_quality_only_weighted_after_correctness():
    # 质量维并入"做好",但仍被 achievement 缩放(做对之后才采信)
    s = AtomSatisfaction(sig="x", achievement=1.0, efficiency=1.0, quality=1.0)
    assert s.overall == pytest.approx(1.0)
    half = AtomSatisfaction(sig="x", achievement=0.5, efficiency=1.0, quality=1.0)
    assert half.overall == pytest.approx(0.5)  # 达成只一半 → 整体腰斩


def test_satisfaction_is_graded_not_binary():
    # 同样成功、同样过门,步数不同 → 满意度不同(不是 pass/pass)
    store = SatisfactionStore()
    sig = "demo"
    record_run(store, _run("t", success=True, n_tools=2), sig, has_proof=True)  # 首次=基线
    record_run(store, _run("t", success=True, n_tools=2), sig, has_proof=True)  # 与基线持平
    lean = store.samples(sig)[-1].overall
    record_run(store, _run("t", success=True, n_tools=20), sig, has_proof=True)  # 远超基线
    fat = store.samples(sig)[-1].overall
    assert fat < lean                       # 啰嗦的那次满意度更低
    assert 0.0 < fat < 1.0 and 0.0 < lean   # 都是分级值,不是二极管


# ---- 效率基线抗污染(对抗验收 M2:中位数而非均值)----

def test_baseline_uses_median_resists_first_run_bloat():
    store = SatisfactionStore()
    sig = "m"
    # 一个特别贵的早跑(100 步)+ 几个正常跑(2 步)。均值会被 100 拉高 → 后续平庸跑全看着高效;
    # 中位数稳在 2 → 一个 10 步的跑会被如实判为低效。
    for steps in (100, 2, 2, 2):
        record_run(store, _run("m", success=True, n_tools=steps), sig, has_proof=True)
    assert store.baseline_steps(sig) == 2.0           # 中位数,不是均值(26.5)
    sat = record_run(store, _run("m", success=True, n_tools=10), sig, has_proof=True)
    assert sat.efficiency < 0.5                        # 10 步 vs 基线 2 → 如实低效,没被早跑洗白


# ---- 信用隔离 ----

def test_credit_isolation_other_sig_does_not_leak():
    store = SatisfactionStore()
    # sig A 全是烂 run(失败),sig B 一条好 run —— B 的满意度不该被 A 污染
    for _ in range(5):
        record_run(store, _run("a", success=False, n_tools=9), "A", has_proof=False)
    sat_b = record_run(store, _run("b", success=True, n_tools=1), "B", has_proof=True)
    assert sat_b.overall == pytest.approx(1.0)        # B 不受 A 的成败影响
    assert store.mean_overall("A") == pytest.approx(0.0)


# ---- 契约 #1:verify verdict 流进 atom 结晶信号(record_run 层)----

def test_record_run_verified_is_full_unverified_is_half():
    store = SatisfactionStore()
    # 同一条 run,被核验 vs 未核验 → achievement 1.0 vs 0.5(verify verdict 真起作用)
    v = record_run(store, _run("a", success=True, n_tools=3), "sigV", has_proof=True)
    u = record_run(store, _run("b", success=True, n_tools=3), "sigU", has_proof=False)
    assert v.achievement == 1.0
    assert u.achievement == 0.5
    # 失败的 run 即便谎称核验也 0(score_achievement 在 not success 上短路)
    f = record_run(store, _run("c", success=False, n_tools=3), "sigF", has_proof=True)
    assert f.achievement == 0.0


# ---- 集成:真 MainLoop.drive 在**首个被核验的跑**就记满分(锁 C1 时序修复 + C2 存活)----

def test_drive_records_satisfaction_on_first_verified_run(tmp_path):
    from karvyloop.cli.main_loop import MainLoop

    def _slow(text):
        def sb(intent, *, ctx=None):
            return text, AtomRun(atom_id="forge", input={"intent": intent},
                                 output={"text": text}, success=True,
                                 tool_calls=[{"name": "write_file"}], trace_ref="t", ts=1.0)
        return sb

    ml = MainLoop(skills_dir=tmp_path / "skills")
    r = ml.drive("把 README 翻译成英文并写回文件", slow_brain=_slow("done"))

    sats = ml.satisfaction.samples(r.sig)
    assert len(sats) == 1                              # C2:生产路径真的记了(没被静默吞)
    # C1:**首个**被核验的跑就是 achievement 1.0,不是滞后的 0.5
    assert sats[0].achievement == 1.0
    assert sats[0].overall == pytest.approx(1.0)        # 首跑无基线→效率 1.0→overall 1.0


# ---- 零回归:observe 还原纯净(不再记满意度,行为与从前一字不差)----

def test_observe_is_pure_again():
    usage = InMemoryUsageStore()
    run = _run("plain", success=True, n_tools=1)
    counts = observe([run], usage)
    sig = compute_signature(run)
    assert counts.get(sig) == 1
    assert usage.get(sig).usage_count == 1
    assert usage.get(sig).success_count == 1
