"""dev-report #7 — 质量评自适应节奏:固定 24h 太慢,活跃用户差技能污染召回排序最多 24h。

`_review_decision` 纯函数定每个 tick 的动作:到点跑整套 daily / 没到点但积压够了提前补质量评 / 否则空转。
backlog 提前评**不重置** daily 时钟(整套维护仍按天走)。
"""
from __future__ import annotations

from karvyloop.console.app import _review_decision


def test_daily_due_runs_full_bundle():
    assert _review_decision(now=100.0, next_daily=100.0, backlog=0, trigger=20) == "daily"
    assert _review_decision(now=101.0, next_daily=100.0, backlog=999, trigger=20) == "daily"  # daily 优先


def test_backlog_triggers_early_quality_before_daily():
    assert _review_decision(now=50.0, next_daily=100.0, backlog=20, trigger=20) == "backlog"  # 攒够 → 提前
    assert _review_decision(now=50.0, next_daily=100.0, backlog=21, trigger=20) == "backlog"


def test_idle_when_neither():
    assert _review_decision(now=50.0, next_daily=100.0, backlog=19, trigger=20) == "idle"   # 没到点、积压不够
    assert _review_decision(now=50.0, next_daily=100.0, backlog=0, trigger=20) == "idle"


def test_trigger_zero_disables_backlog_path():
    # trigger=0 → 关掉积压提前评,纯按 daily(留给"我不要这个加速"的退路)
    assert _review_decision(now=50.0, next_daily=100.0, backlog=999, trigger=0) == "idle"
    assert _review_decision(now=100.0, next_daily=100.0, backlog=999, trigger=0) == "daily"
