"""test_tokens_query_api — 分时段 token 查询 API(Hardy 碎碎念⑥:
"token 用量统计需要分时段统计,可以分时段查询,笼统的查询等于没有查询")。

验 `GET /api/tokens/query`:窗口过滤(start_ts/end_ts)、粒度(hour|day)时间序列、
by_source 排行、缺省窗口(最近 7 天)、无账本优雅空。全部**只读** —— 记账逻辑一字不动。
"""
from __future__ import annotations

import time

from karvyloop.llm.token_ledger import TokenLedger

# 固定基准:取"今天本地零点 + 12h"当 now,让跨天/跨小时写入落在确定的日历日上。
# **封顶到墙钟 now-5s**:缺省查询窗口截止于 time.time(),正午之前跑时"今天正午"那条会落在
# 未来被窗口排除 → 3 个 default-window 测试无端拉红(时间依赖 flake)。min 保证"今天"锚点永不
# 超过 now,过去时段(1/2 天前)记录仍稳落在各自日历日/小时桶(after-noon 退回原 noon 行为)。
_NOW = time.time()
_TODAY_NOON = min(
    time.mktime(time.strptime(time.strftime("%Y-%m-%d", time.localtime(_NOW)), "%Y-%m-%d")) + 12 * 3600,
    _NOW - 5.0,
)
_DAY = 86400.0


def _ledger_across_periods() -> TokenLedger:
    """可控时钟内存账本:3 个日历日 × 多小时,共 5 条(跨时段数据,真 record 写入)。"""
    box = {"t": _TODAY_NOON}
    led = TokenLedger(path=None, clock=lambda: box["t"])
    # 10 天前(应被缺省 7 天窗口排除)
    box["t"] = _TODAY_NOON - 10 * _DAY
    led.record(source="ancient", model="m", input=9999, output=999)
    # 2 天前 10:00 与 11:00(同日两小时桶)
    box["t"] = _TODAY_NOON - 2 * _DAY - 2 * 3600   # 10:00
    led.record(source="forge", model="m", input=100, output=10)
    box["t"] = _TODAY_NOON - 2 * _DAY - 1 * 3600   # 11:00
    led.record(source="forge", model="m", input=200, output=20)
    # 昨天 12:00
    box["t"] = _TODAY_NOON - 1 * _DAY
    led.record(source="agent_import", model="m", input=1000, output=100)
    # 今天 12:00
    box["t"] = _TODAY_NOON
    led.record(source="drive", model="m", input=50, output=5)
    return led


def _client(led=None):
    from fastapi.testclient import TestClient

    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    if led is not None:
        app.state.token_ledger = led
    return TestClient(app)


def test_default_window_last_7_days_day_granularity():
    """缺省 = 最近 7 天 / day:10 天前的记录被排除;3 个日历日 3 桶,oldest-first。"""
    c = _client(_ledger_across_periods())
    j = c.get("/api/tokens/query").json()
    assert j["granularity"] == "day"
    assert j["end_ts"] - j["start_ts"] == 7 * 86400.0
    # 窗口总量:不含 ancient(9999/999)
    assert j["totals"]["input"] == 1350 and j["totals"]["output"] == 135
    assert j["totals"]["total"] == 1485 and j["totals"]["calls"] == 4
    # 3 个日历日 → 3 桶,升序(画柱状)
    days = [b["label"] for b in j["series"]]
    assert len(days) == 3 and days == sorted(days), f"series 不是升序日桶: {days}"
    assert [b["calls"] for b in j["series"]] == [2, 1, 1]
    assert j["series"][0]["total"] == 330  # 2 天前:100+10+200+20


def test_hour_granularity_splits_same_day():
    """granularity=hour:同一天的 10:00 / 11:00 两条分进两个小时桶。"""
    c = _client(_ledger_across_periods())
    j = c.get("/api/tokens/query", params={"granularity": "hour"}).json()
    assert j["granularity"] == "hour"
    assert len(j["series"]) == 4, f"4 个小时时段没分开: {j['series']}"
    starts = [b["bucket_start"] for b in j["series"]]
    assert starts == sorted(starts) and all(s % 3600 == 0 for s in starts)
    assert [b["total"] for b in j["series"]] == [110, 220, 1100, 55]


def test_explicit_window_filters_records():
    """显式 start_ts/end_ts:只取"昨天"一天 → 只剩 agent_import 那条。"""
    c = _client(_ledger_across_periods())
    j = c.get("/api/tokens/query", params={
        "start_ts": _TODAY_NOON - 1 * _DAY - 3600,
        "end_ts": _TODAY_NOON - 1 * _DAY + 3600,
    }).json()
    assert j["totals"] == {"input": 1000, "output": 100, "cache_read": 0,
                           "cache_write": 0, "total": 1100, "calls": 1}
    assert len(j["series"]) == 1 and j["series"][0]["calls"] == 1
    assert [s["source"] for s in j["by_source"]] == ["agent_import"]


def test_by_source_ranked_by_burn():
    """by_source 排行:窗口内烧得多在前(agent_import 1100 > forge 330 > drive 55)。"""
    c = _client(_ledger_across_periods())
    j = c.get("/api/tokens/query").json()
    assert [s["source"] for s in j["by_source"]] == ["agent_import", "forge", "drive"]
    assert j["by_source"][0]["total"] == 1100 and j["by_source"][1]["calls"] == 2


def test_invalid_granularity_falls_back_to_day_and_swapped_window_reconciled():
    """荒谬粒度 → day 兜底(响应回显实际用的);start/end 传反 → 调和不 4xx。"""
    c = _client(_ledger_across_periods())
    j = c.get("/api/tokens/query", params={"granularity": "weird"}).json()
    assert j["granularity"] == "day"
    j2 = c.get("/api/tokens/query", params={
        "start_ts": _TODAY_NOON + 100, "end_ts": _TODAY_NOON - 100}).json()
    assert j2["start_ts"] < j2["end_ts"] and j2["totals"]["calls"] == 1  # 只套住今天那条


def test_no_ledger_graceful_empty():
    """无账本 → 优雅空结构(不 500),窗口/粒度照回显。"""
    c = _client(led=None)
    j = c.get("/api/tokens/query").json()
    assert j["totals"]["calls"] == 0 and j["by_source"] == [] and j["series"] == []
    assert j["granularity"] == "day" and j["start_ts"] < j["end_ts"]


def test_window_series_day_uses_local_calendar_day():
    """ledger 层:day 序列必须按**本地日历日**(day 列),不是 UTC 的 ts//86400 桶。
    构造两条同一本地日、但跨 UTC 日界(本地早 4 点/晚 20 点,UTC+8 下 UTC 日不同)的记录。"""
    day0 = time.mktime(time.strptime("2026-06-15", "%Y-%m-%d"))  # 本地零点
    box = {"t": day0 + 4 * 3600}
    led = TokenLedger(path=None, clock=lambda: box["t"])
    led.record(source="a", model="m", input=10, output=1)
    box["t"] = day0 + 20 * 3600
    led.record(source="a", model="m", input=20, output=2)
    series = led.window_series(start_ts=day0, end_ts=day0 + 86400, granularity="day")
    assert len(series) == 1 and series[0]["label"] == "2026-06-15", (
        f"同一本地日被切开(UTC 日界 bug): {series}")
    assert series[0]["total"] == 33 and series[0]["calls"] == 2
    assert series[0]["bucket_start"] == int(day0)
