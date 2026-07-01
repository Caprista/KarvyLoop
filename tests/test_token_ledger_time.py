"""test_token_ledger_time — Token 时段统计(Hardy 2026-06-27:token 没按时段统计,
不知道损耗什么时候发生的)。验 buckets(任意粒度时间序列)+ recent(时间线)+ API 暴露。"""
from __future__ import annotations

import types

from karvyloop.llm.token_ledger import TokenLedger


def _ledger_with_clock():
    """可控时钟的内存账本:在 3 个不同时段各记几条。"""
    box = {"t": 1_000_000.0}
    led = TokenLedger(path=None, clock=lambda: box["t"])
    # 时段 1(t=1_000_000,小时 A):2 次
    led.record(source="import", model="m", input=100, output=10)
    led.record(source="import", model="m", input=200, output=20)
    # 时段 2(+2 小时,小时 C):1 次
    box["t"] = 1_000_000.0 + 7200
    led.record(source="orchestrate", model="m", input=50, output=5)
    # 时段 3(再 +1 分钟,同小时 C 但不同分钟桶):1 次
    box["t"] = 1_000_000.0 + 7200 + 60
    led.record(source="pressure", model="m", input=1000, output=100)
    return led


def test_buckets_hourly_separates_time_periods():
    led = _ledger_with_clock()
    hourly = led.buckets(interval_sec=3600)
    # 小时 A(2 次,input 300)和小时 C(2 次,input 1050)应是两个不同桶
    assert len(hourly) == 2, f"两个时段没分开: {hourly}"
    by_total = {b["calls"]: b for b in hourly}
    assert set(by_total) == {2, 2} or len(hourly) == 2
    totals = sorted(b["total"] for b in hourly)
    assert totals == [330, 1155], f"分桶汇总错: {totals}"  # A:300+30, C:1050+105
    # newest-first
    assert hourly[0]["bucket_start"] > hourly[1]["bucket_start"]


def test_buckets_minute_granularity_finds_spike():
    led = _ledger_with_clock()
    minute = led.buckets(interval_sec=60)
    # 分钟级:时段 1(同分钟 2 条)、时段 2、时段 3 各一桶 = 3 桶
    assert len(minute) == 3, f"分钟级没拆出 3 桶: {minute}"
    spike = max(minute, key=lambda b: b["total"])
    assert spike["total"] == 1100 and spike["calls"] == 1, "尖峰桶(pressure 1000/100)没定位到"


def test_recent_timeline_newest_first_with_source():
    led = _ledger_with_clock()
    rec = led.recent(limit=10)
    assert len(rec) == 4 and rec[0]["source"] == "pressure", "时间线不是最新在前"
    assert rec[0]["total"] == 1100 and "label" in rec[0] and rec[0]["ts"] > rec[-1]["ts"]


def test_since_window_filters_buckets():
    led = _ledger_with_clock()
    # 只要时段 2/3 之后(since = +7000s)
    recent_only = led.buckets(interval_sec=3600, since=1_000_000.0 + 7000)
    assert len(recent_only) == 1 and recent_only[0]["total"] == 1155


# ---- API 暴露 ----
def _client_with_ledger():
    from fastapi.testclient import TestClient

    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.token_ledger = _ledger_with_clock()
    return TestClient(app)


def test_api_tokens_includes_by_hour_and_recent():
    c = _client_with_ledger()
    j = c.get("/api/tokens").json()
    assert "by_hour" in j and "recent" in j, "时段/时间线没暴露到 /api/tokens"
    assert len(j["by_hour"]) == 2 and len(j["recent"]) == 4
    assert j["totals"]["total"] == 1485


def test_api_token_buckets_custom_interval():
    c = _client_with_ledger()
    j = c.get("/api/tokens/buckets", params={"interval": 60}).json()
    assert j["interval"] == 60 and len(j["buckets"]) == 3
    # 荒谬 interval 被夹
    assert c.get("/api/tokens/buckets", params={"interval": 999999}).json()["interval"] == 86400


def test_api_tokens_empty_without_ledger():
    from fastapi.testclient import TestClient

    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    c = TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None))
    j = c.get("/api/tokens").json()
    assert j["by_hour"] == [] and j["recent"] == []  # 无账本优雅空
