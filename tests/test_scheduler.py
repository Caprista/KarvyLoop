"""test_scheduler — 定时任务核心:cron 校验 / next_run / due 触发 / 持久化 / CRUD。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.karvy.scheduler import SchedulerStore, next_run_after  # noqa: E402

# 2026-06-25 09:00:00 UTC 附近的时间戳基准(用固定 epoch 算,避开时区:croniter 默认按 naive/local)
import datetime as _dt
def _ts(y, mo, d, h, mi=0):
    return _dt.datetime(y, mo, d, h, mi).timestamp()


def test_invalid_cron_rejected():
    st = SchedulerStore()
    assert st.add("not a cron", "干活") is None
    assert next_run_after("bad", _ts(2026, 6, 25, 9)) is None


def test_next_run_daily_8am():
    base = _ts(2026, 6, 25, 9)            # 9 点(已过当天 8 点)
    nxt = next_run_after("0 8 * * *", base)
    assert nxt == _ts(2026, 6, 26, 8)     # 下一个 8 点是明天


def test_due_fires_in_window():
    st = SchedulerStore(clock=lambda: _ts(2026, 6, 25, 8, 1))  # 现在 8:01
    t = st.add("0 8 * * *", "每天 8 点汇总")
    # since=7:59 → 窗口跨过 8:00 触发点 → 到点
    due = st.due(since=_ts(2026, 6, 25, 7, 59))
    assert [x.id for x in due] == [t.id]
    # since=8:00(触发点之后)→ 不重复触发
    assert st.due(since=_ts(2026, 6, 25, 8, 0, )) == []


def test_disabled_not_due():
    st = SchedulerStore(clock=lambda: _ts(2026, 6, 25, 8, 1))
    t = st.add("0 8 * * *", "x")
    st.set_enabled(t.id, False)
    assert st.due(since=_ts(2026, 6, 25, 7, 59)) == []


def test_mark_run_prevents_retrigger_same_window():
    now = _ts(2026, 6, 25, 8, 1)
    st = SchedulerStore(clock=lambda: now)
    t = st.add("0 8 * * *", "x")
    st.mark_run(t.id, "ok", ts=now)     # 刚跑过
    # last_run=8:01 → 下一个触发是明天 8 点 → 这窗口不再 due
    assert st.due(since=_ts(2026, 6, 25, 7, 59)) == []


def test_persistence_round_trip(tmp_path):
    p = tmp_path / "schedules.json"
    st = SchedulerStore(p)
    t = st.add("0 9 * * 1", "每周一汇总", title="周报", target_domain="d1", target_role="产品经理")
    st2 = SchedulerStore(p)            # 重新加载
    got = st2.get(t.id)
    assert got and got.cron == "0 9 * * 1" and got.title == "周报"
    assert got.target_domain == "d1" and got.target_role == "产品经理"
    assert st2.remove(t.id) and SchedulerStore(p).all() == []
