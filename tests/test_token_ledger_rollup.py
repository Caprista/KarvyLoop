"""test_token_ledger_rollup — 滚动汇总(唯一无界持久台账收口)。

修的是 docs/87 §五「token_ledger sqlite 无界增长」:每次 LLM 调用记一行,常驻长跑单调涨。
修法=**保留期 + 滚动汇总**:早于保留期的**完整日**逐调用明细滚成按天×source×model 汇总行,
明细删、汇总留。

AC:
- 聚合守恒(核心不变量):滚动前后 totals / by_day / by_source / by_model **逐字段相等**(含 calls)。
- 幂等:滚两次,第二次 deleted=0,聚合仍相等。
- 近期明细保留:< 保留期的 task_total/run_totals 精确;被滚老 task/run 返回 0 但水位线可判"已归档"。
- 原子性:中途崩 → 整体 rollback,一条明细都不丢。
- 只滚完整日:恰好落在保留窗起始日的明细不滚。
- 跨重启持久:滚动结果 + 水位线落盘,重开仍在,汇总行不被再滚。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.llm.token_ledger import ROLLUP_RETAIN_DAYS, TokenLedger  # noqa: E402

_DAY = 86400.0
_NOW = 1_700_000_000.0  # 固定"现在"(2023-11),避免依赖真实时钟


def _ledger(clock_holder, path=None):
    return TokenLedger(path, clock=lambda: clock_holder[0])


def _rec(led, clock_holder, ts, **kw):
    """在指定墙钟 ts 记一行(led.record 用 self._clock() 取 ts + 派生 day)。"""
    clock_holder[0] = ts
    led.record(**kw)


def _snapshot(led):
    """聚合快照(与顺序无关的 map),用于滚动前后逐字段比对。"""
    return {
        "totals": led.totals(),
        "by_day": {r["day"]: r for r in led.by_day()},
        "by_source": {r["source"]: r for r in led.by_source()},
        "by_model": {r["model"]: r for r in led.by_model()},
    }


def _seed_old_and_recent(led, clk):
    """记若干老(>保留期)+ 近期明细,覆盖多 (day, source, model) 组合。"""
    old_a = _NOW - 200 * _DAY   # 200 天前
    old_b = _NOW - 150 * _DAY   # 150 天前
    recent_c = _NOW - 1 * _DAY  # 昨天
    recent_d = _NOW             # 今天
    # 老日 A:s1/m1 两笔(含 cache)、s2/m2 一笔
    _rec(led, clk, old_a, source="s1", model="m1", input=100, output=50, cache_read=10, cache_write=5)
    _rec(led, clk, old_a, source="s1", model="m1", input=100, output=50)
    _rec(led, clk, old_a, source="s2", model="m2", input=200, output=80, cache_read=20)
    # 老日 B:s1/m1 一笔、s1/m2 一笔
    _rec(led, clk, old_b, source="s1", model="m1", input=30, output=10)
    _rec(led, clk, old_b, source="s1", model="m2", input=40, output=20)
    # 近期:s1/m1 昨天、s2/m2 今天
    _rec(led, clk, recent_c, source="s1", model="m1", input=7, output=3)
    _rec(led, clk, recent_d, source="s2", model="m2", input=9, output=1)


def test_rollup_preserves_aggregates():
    """核心不变量:滚动前后 totals/by_day/by_source/by_model 逐字段相等(含 calls)。"""
    clk = [_NOW]
    led = _ledger(clk)
    _seed_old_and_recent(led, clk)
    clk[0] = _NOW
    before = _snapshot(led)

    res = led.rollup()
    assert res["deleted"] == 5, "老日 A(3)+B(2)=5 条明细应被删"
    assert res["summarized"] == 4, "老日按 (day,source,model) 折叠成 4 条汇总行"

    after = _snapshot(led)
    assert after == before, "滚动后聚合(含每天/每 source/每 model 的 calls)必须逐字段守恒"
    # 具体锁一条:老 source s1 的 calls 折叠后仍是原始调用数(不是汇总行数)
    assert after["by_source"]["s1"]["calls"] == before["by_source"]["s1"]["calls"]


def test_rollup_idempotent():
    """幂等:再滚一次 deleted=0(汇总行 rolled=1 不会被再折叠),聚合仍相等。"""
    clk = [_NOW]
    led = _ledger(clk)
    _seed_old_and_recent(led, clk)
    clk[0] = _NOW
    led.rollup()
    snap1 = _snapshot(led)

    res2 = led.rollup()
    assert res2["deleted"] == 0 and res2["summarized"] == 0
    assert _snapshot(led) == snap1


def test_rollup_keeps_recent_detail_and_archives_old():
    """近期 task_total/run_totals 精确保留;被滚老 task/run 返回 0,但水位线可判"已归档"。"""
    clk = [_NOW]
    led = _ledger(clk)
    _rec(led, clk, _NOW - 200 * _DAY, source="s", model="m",
         input=500, output=100, task_id="t_old", run_id="r_old")
    _rec(led, clk, _NOW, source="s", model="m",
         input=7, output=3, task_id="t_recent", run_id="r_recent")
    clk[0] = _NOW

    assert led.task_total("t_old") == 600
    assert led.run_totals("r_old")["calls"] == 1

    led.rollup()

    # 近期明细原样(task/run 精细查询对近期有意义)
    assert led.task_total("t_recent") == 10
    assert led.run_totals("r_recent") == {"input": 7, "output": 3, "total": 10, "calls": 1}
    # 老的明细已滚走 → 0,但不是"消耗为 0":水位线在,老 ts < 水位线 = 已归档
    assert led.task_total("t_old") == 0
    assert led.run_totals("r_old")["calls"] == 0
    wm = led.rollup_watermark()
    assert wm > 0
    assert (_NOW - 200 * _DAY) < wm, "老 task 的 ts 落在水位线以下 → 调用方可判'已归档'非'0 消耗'"
    assert _NOW >= wm, "近期 task 的 ts 在水位线之上 → 明细仍在,0 才是真 0"


def test_rollup_atomic_rollback_on_failure():
    """原子性:滚动写水位线那步崩 → 整体 rollback,明细/聚合一条不丢。"""
    clk = [_NOW]
    led = _ledger(clk)
    _seed_old_and_recent(led, clk)
    clk[0] = _NOW
    before = _snapshot(led)

    real = led._conn

    class _ConnProxy:
        """代理连接:滚动写水位线那步抛错模拟半路崩,其余(含 rollback)透传真连接。"""
        def execute(self, sql, *a, **k):
            if str(sql).strip().startswith("INSERT OR REPLACE INTO token_ledger_meta"):
                raise RuntimeError("simulated crash mid-rollup")
            return real.execute(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(real, name)

    led._conn = _ConnProxy()
    try:
        with pytest.raises(RuntimeError):
            led.rollup()
    finally:
        led._conn = real

    assert _snapshot(led) == before, "崩溃回滚后聚合必须与滚动前完全一致(删除被撤销)"
    assert led.rollup_watermark() == 0.0, "水位线未提交 → 仍是初始 0"
    # 回滚后仍可正常滚(状态干净)
    assert led.rollup()["deleted"] == 5


def test_rollup_only_complete_days():
    """只滚完整日:恰好落在保留窗起始日(day == cutoff_day)的明细不滚。"""
    clk = [_NOW]
    led = _ledger(clk)
    # 恰好 retain 天前(边界日,day == cutoff_day)→ 严格 < 不成立 → 保留
    _rec(led, clk, _NOW - ROLLUP_RETAIN_DAYS * _DAY, source="edge", model="m",
         input=11, output=1, task_id="t_edge")
    # 明显更老 → 滚
    _rec(led, clk, _NOW - (ROLLUP_RETAIN_DAYS + 30) * _DAY, source="old", model="m",
         input=22, output=2, task_id="t_old")
    clk[0] = _NOW

    res = led.rollup()
    assert res["deleted"] == 1, "只有更老那条被滚,边界日那条保留"
    assert led.task_total("t_edge") == 12, "边界日明细未滚 → 仍可 task 级精细查询"
    assert led.task_total("t_old") == 0


def test_rollup_persists_across_reopen(tmp_path):
    """跨重启:滚动结果 + 水位线落盘,重开仍在,汇总行不被再滚。"""
    p = tmp_path / "tokens.db"
    clk = [_NOW]
    led = _ledger(clk, p)
    _seed_old_and_recent(led, clk)
    clk[0] = _NOW
    led.rollup()
    before = _snapshot(led)
    wm = led.rollup_watermark()
    led.close()

    clk2 = [_NOW]
    led2 = _ledger(clk2, p)
    assert _snapshot(led2) == before, "重开后聚合守恒"
    assert led2.rollup_watermark() == wm, "水位线跨进程持久"
    # 重开再滚:老明细已是汇总行(rolled=1)→ 不再折叠
    assert led2.rollup()["deleted"] == 0


def test_rollup_empty_noop():
    """全是近期数据 → 没得滚:deleted=0,不碰盘,聚合不变,水位线仍 0。"""
    clk = [_NOW]
    led = _ledger(clk)
    _rec(led, clk, _NOW, source="s", model="m", input=5, output=5)
    _rec(led, clk, _NOW - 1 * _DAY, source="s", model="m", input=5, output=5)
    clk[0] = _NOW
    before = _snapshot(led)

    res = led.rollup()
    assert res == {"deleted": 0, "summarized": 0, "cutoff_day": res["cutoff_day"]}
    assert _snapshot(led) == before
    assert led.rollup_watermark() == 0.0


def test_rollup_migrates_legacy_db(tmp_path):
    """老库(无 calls/rolled 列)重开自动迁移:老行 calls=1、rolled=0,滚动照常守恒。"""
    import sqlite3
    p = tmp_path / "legacy.db"
    # 手造一个"迁移前"结构的库(有 task_id/run_id,无 calls/rolled)
    conn = sqlite3.connect(str(p))
    conn.executescript(
        "CREATE TABLE token_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
        "day TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'unknown', model TEXT NOT NULL DEFAULT '', "
        "input INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0, "
        "cache_read INTEGER NOT NULL DEFAULT 0, cache_write INTEGER NOT NULL DEFAULT 0, "
        "task_id TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '')")
    old_day = __import__("time").strftime("%Y-%m-%d",
                                          __import__("time").localtime(_NOW - 200 * _DAY))
    conn.execute("INSERT INTO token_usage (ts, day, source, model, input, output) VALUES (?,?,?,?,?,?)",
                 (_NOW - 200 * _DAY, old_day, "s", "m", 100, 20))
    conn.commit()
    conn.close()

    clk = [_NOW]
    led = _ledger(clk, p)  # __init__ 迁移补 calls/rolled + meta 表
    assert led.totals()["calls"] == 1, "老行迁移后 calls=1(SUM(calls)=COUNT)"
    before = _snapshot(led)
    res = led.rollup()
    assert res["deleted"] == 1
    assert _snapshot(led) == before, "迁移库滚动后聚合仍守恒"
