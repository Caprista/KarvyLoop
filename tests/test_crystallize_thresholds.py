"""test_crystallize_thresholds — 结晶旋钮可配置(9.4-门2,用户选 A:调旋钮一版)。

门1 真机发现:结晶门槛(min_usage_count=5)+ 去抖(60s)原本是**硬编码常量**,
config.yaml 里的 crystallize.min_usage_count/min_success_rate 是**装饰键(从没读)**。
本拍:改成真 config-driven,真实用户测试期能不改代码调灵敏度。

AC:
- AC1: CrystallizeThresholds 默认 = 原硬编码值(5/0.8/60/3.0/2),向后兼容
- AC2: _read_thresholds_from_config 读 config.yaml(缺字段用默认,非法值退默认不崩)
- AC3: observe 的 debounce_sec 生效(0 → 快速重复也计数;大窗口 → 去抖抑制)
- AC4: maybe_promote 的 min_usage_count 生效(调低 → 更早够"高频价值")
- AC5: build_main_loop 把 config 旋钮接进 MainLoop.thresholds
"""
from __future__ import annotations

from pathlib import Path

from karvyloop.crystallize.crystallize import (
    DEFAULT_THRESHOLDS,
    CrystallizeThresholds,
    maybe_promote,
)
from karvyloop.crystallize.observe import observe
from karvyloop.crystallize.store import InMemoryUsageStore
from karvyloop.crystallize.verify import VerifyStore
from karvyloop.schemas import UsageStats
from karvyloop.schemas.atom import AtomRun


# ---- AC1 ----
def test_defaults_match_legacy():
    d = DEFAULT_THRESHOLDS
    assert d.min_usage_count == 5
    assert d.min_success_rate == 0.8
    assert d.usage_debounce_sec == 60.0
    assert d.promote_score == 3.0
    assert d.generalized_distinct == 2


# ---- AC2 ----
def test_read_thresholds_from_config(tmp_path: Path):
    from karvyloop.cli.run_loop import _read_thresholds_from_config
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "crystallize:\n"
        "  min_usage_count: 3\n"
        "  usage_debounce_sec: 0\n"
        "  min_success_rate: 0.5\n",
        encoding="utf-8",
    )
    t = _read_thresholds_from_config(cfg)
    assert t.min_usage_count == 3
    assert t.usage_debounce_sec == 0.0
    assert t.min_success_rate == 0.5
    # 没填的用默认
    assert t.promote_score == DEFAULT_THRESHOLDS.promote_score


def test_read_thresholds_missing_or_bad(tmp_path: Path):
    from karvyloop.cli.run_loop import _read_thresholds_from_config
    assert _read_thresholds_from_config(None) is DEFAULT_THRESHOLDS
    bad = tmp_path / "bad.yaml"
    bad.write_text("crystallize:\n  min_usage_count: not-a-number\n", encoding="utf-8")
    assert _read_thresholds_from_config(bad) is DEFAULT_THRESHOLDS  # 非法 → 退默认


# ---- AC3: observe debounce ----
def _run(sig_intent: str, ts: float, ok=True) -> AtomRun:
    return AtomRun(atom_id="a", input={"intent": sig_intent}, output={"text": "x"},
                   success=ok, tool_calls=[], trace_ref="t", ts=ts)


def test_observe_debounce_param():
    store = InMemoryUsageStore()
    intent = "打包项目"
    # 两次相隔 5s
    observe([_run(intent, 1000.0)], store, debounce_sec=0)
    observe([_run(intent, 1005.0)], store, debounce_sec=0)
    from karvyloop.crystallize.signature import compute_signature
    sig = compute_signature(_run(intent, 1000.0))
    assert store.get(sig).usage_count == 2  # debounce=0 → 都计数

    store2 = InMemoryUsageStore()
    observe([_run(intent, 1000.0)], store2, debounce_sec=60)
    observe([_run(intent, 1005.0)], store2, debounce_sec=60)
    assert store2.get(sig).usage_count == 1  # 60s 窗口内 → 去抖抑制


# ---- AC4: maybe_promote honors min_usage_count ----
def test_maybe_promote_honors_min_usage_count():
    store = InMemoryUsageStore()
    verify = VerifyStore()
    sig = "sig-hf"
    store.put(sig, UsageStats(usage_count=3, success_count=3, last_used_at=1000.0))
    verify.mark_verified(sig, "t", note="x", clock=lambda: 1000.0)
    # 隔离 high_freq 旋钮:把 score/success_rate 门槛放开
    low = CrystallizeThresholds(min_usage_count=3, promote_score=0.0, min_success_rate=0.0)
    high = CrystallizeThresholds(min_usage_count=5, promote_score=0.0, min_success_rate=0.0)
    d_low = maybe_promote(sig, store, verify, now=1000.0, thresholds=low)
    d_high = maybe_promote(sig, store, verify, now=1000.0, thresholds=high)
    assert d_low.kind.value == "ready"      # usage 3 >= 3 → 高频够 → 结晶
    assert d_high.kind.value == "not_yet"   # usage 3 < 5 → 还不够


# ---- AC7: 满意度旋钮(satisfaction_floor / satisfaction_min_samples)也走 config ----
# 内部审计(docs/68 半接线):CrystallizeThresholds 有这俩字段、maybe_promote 也消费,
# 但 _read_thresholds_from_config 原来不读 → 用户改 config.yaml 的满意度地板无效。
def test_read_satisfaction_knobs_from_config(tmp_path: Path):
    from karvyloop.cli.run_loop import _read_thresholds_from_config
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "crystallize:\n"
        "  satisfaction_floor: 0.9\n"
        "  satisfaction_min_samples: 2\n",
        encoding="utf-8",
    )
    t = _read_thresholds_from_config(cfg)
    assert t.satisfaction_floor == 0.9
    assert t.satisfaction_min_samples == 2
    # 没填的用默认
    assert t.min_usage_count == DEFAULT_THRESHOLDS.min_usage_count
    assert t.promote_score == DEFAULT_THRESHOLDS.promote_score


class _StubSatisfaction:
    """duck-typed SatisfactionStore 桩:固定近期均分 0.5(= 纯"未核验成功"历史)。"""

    def samples(self, sig):
        return [object(), object(), object()]   # 3 份样本,过 min_samples 门

    def mean_overall_recent(self, sig):
        return 0.5


def test_maybe_promote_honors_configured_satisfaction_floor(tmp_path: Path):
    """构造自定义配置 → 走 maybe_promote → 断言满意度地板真被尊重(半接线验收)。"""
    from karvyloop.cli.run_loop import _read_thresholds_from_config
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "crystallize:\n"
        "  min_usage_count: 3\n"
        "  promote_score: 0.0\n"
        "  min_success_rate: 0.0\n"
        "  satisfaction_floor: 0.9\n"
        "  satisfaction_min_samples: 2\n",
        encoding="utf-8",
    )
    t = _read_thresholds_from_config(cfg)

    store = InMemoryUsageStore()
    verify = VerifyStore()
    sig = "sig-sat"
    store.put(sig, UsageStats(usage_count=3, success_count=3, last_used_at=1000.0))
    verify.mark_verified(sig, "t", note="x", clock=lambda: 1000.0)
    sat = _StubSatisfaction()

    # 默认地板 0.45:0.5 不算差评 → 晋升(对照组,证明只有地板在变)
    loose = CrystallizeThresholds(min_usage_count=3, promote_score=0.0, min_success_rate=0.0)
    d_default = maybe_promote(sig, store, verify, now=1000.0, thresholds=loose,
                              satisfaction=sat)
    assert d_default.kind.value == "ready"

    # config 地板 0.9:0.5 < 0.9 → 被满意度关拦下(config 真生效)
    d_cfg = maybe_promote(sig, store, verify, now=1000.0, thresholds=t, satisfaction=sat)
    assert d_cfg.kind.value == "not_yet"
    assert "satisfaction" in d_cfg.reason


# ---- AC5: build_main_loop wiring ----
def test_build_main_loop_wires_thresholds(tmp_path: Path):
    from karvyloop.cli.run_loop import build_main_loop, close_main_loop_stores
    cfg = tmp_path / "config.yaml"
    cfg.write_text("crystallize:\n  min_usage_count: 2\n  usage_debounce_sec: 0\n", encoding="utf-8")
    ml = build_main_loop(config_path=cfg, skills_dir=tmp_path / "skills",
                         usage_store_path=tmp_path / "u.sqlite",
                         verify_store_path=tmp_path / "v.sqlite",
                         trace_store_path=tmp_path / "t.sqlite")
    try:
        assert ml.thresholds.min_usage_count == 2
        assert ml.thresholds.usage_debounce_sec == 0.0
    finally:
        close_main_loop_stores(ml)


# ---- AC6: crystallize() 用调用方传入的 thresholds(否则 drive 判 ready、本函数用默认重判 not-ready 抛) ----
def test_crystallize_honors_passed_thresholds(tmp_path: Path):
    from karvyloop.crystallize.crystallize import crystallize
    store = InMemoryUsageStore()
    verify = VerifyStore()
    sig = "sig-th"
    # usage=2 → 默认 promote_score=3.0 下 score 不够;低阈值下够
    store.put(sig, UsageStats(usage_count=2, success_count=2, last_used_at=1000.0))
    verify.mark_verified(sig, "t", note="x", clock=lambda: 1000.0)
    low = CrystallizeThresholds(min_usage_count=2, promote_score=1.0, min_success_rate=0.0)

    # 传低阈值 → 成功结晶(不抛、写出 skill)
    sk = crystallize(sig, name="skill_th", description="d", body="b", when_to_use="w",
                     arguments=None, store=store, verify=verify, skills_dir=tmp_path / "skills",
                     now=1000.0, thresholds=low)
    assert sk is not None and (tmp_path / "skills" / "skill_th" / "SKILL.md").exists()

    # 不传(默认 promote_score=3.0)→ 同一 sig 重判 not-ready → 抛(证明阈值真被用上)
    import pytest
    with pytest.raises(ValueError):
        crystallize(sig, name="skill_th2", description="d", body="b", when_to_use="w",
                    arguments=None, store=store, verify=verify, skills_dir=tmp_path / "skills2",
                    now=1000.0)
