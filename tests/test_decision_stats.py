"""test_decision_stats — 决策结晶复利信号(docs/02 §11 MVP 验证)。

记 H2A 决策结果 → 算"提案接受率"+ 近期 vs 早前趋势;样本不足如实不报(不杜撰)。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console.decision_stats import DecisionStats  # noqa: E402


def test_record_only_valid_decisions():
    s = DecisionStats()
    for d in ["ACCEPT", "REJECT", "DEFER", "garbage", ""]:
        s.record(d)
    assert s.summary()["decisions_total"] == 3   # garbage/"" 被忽略


def test_empty_summary_no_rate():
    s = DecisionStats().summary()
    assert s["decisions_total"] == 0
    assert s["accept_rate"] is None
    assert s["enough_for_trend"] is False
    assert s["trend"] is None


def test_accept_rate():
    s = DecisionStats()
    for _ in range(3):
        s.record("ACCEPT")
    s.record("REJECT")
    assert s.summary()["accept_rate"] == 0.75   # 3/4


def test_trend_none_when_sample_small():
    s = DecisionStats()
    for _ in range(5):   # < MIN_FOR_TREND(10)
        s.record("ACCEPT")
    summ = s.summary()
    assert summ["enough_for_trend"] is False
    assert summ["trend"] is None


def test_trend_up_when_recent_better():
    s = DecisionStats()
    # 早前 10 条多拒(接受率低),近 20 条全收 → 趋势向上
    for _ in range(10):
        s.record("REJECT")
    for _ in range(20):
        s.record("ACCEPT")
    summ = s.summary()
    assert summ["enough_for_trend"] is True
    assert summ["trend"] is not None and summ["trend"] > 0      # recent > older
    assert summ["recent_accept_rate"] == 1.0


def test_persistence_roundtrip(tmp_path):
    p = tmp_path / "decision_stats.json"
    s = DecisionStats(path=p)
    s.record("ACCEPT")
    s.record("REJECT")
    assert p.exists()
    s2 = DecisionStats(path=p)   # 重载
    assert s2.summary()["decisions_total"] == 2


def test_corrupt_file_starts_empty(tmp_path):
    p = tmp_path / "decision_stats.json"
    p.write_text("{ not valid json", encoding="utf-8")
    s = DecisionStats(path=p)   # 坏文件不致命
    assert s.summary()["decisions_total"] == 0


def test_cap_keeps_recent(tmp_path):
    s = DecisionStats()
    for _ in range(250):   # > _CAP(200)
        s.record("ACCEPT")
    assert s.summary()["decisions_total"] == 200
