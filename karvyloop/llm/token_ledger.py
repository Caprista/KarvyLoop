"""token_ledger — Token 账本(测量层,M3+ 拍 9.3a / docs/28 TK-1)。

**为什么**:token 是生命线。测了才能优化。每次 LLM 调用记一条(按 source/model/类型/时间),
喂 token 看板,看清"钱花在哪个功能上"。

**设计**(docs/28 §3.1):
- `TokenLedger`:sqlite 落盘(`~/.karvyloop/tokens.db`),record + 按 source/model/day 聚合查询。
- `token_source(name)`:contextvar — 调用方标"这次 LLM 是谁烧的"(drive/forge/凝习惯/意图/…)。
  source 维度是关键:provider 只知 model,不知功能;靠 contextvar 从顶层传下来。
- `register_ledger` + 模块级 `record(...)`:provider 出 ChatResponse 后调一次 record,
  从 contextvar 取 source 写账本。未注册 ledger → no-op(测试/无账本场景不崩)。

**cache 列**:cache_read/write 已由 gateway adapter surface —— anthropic 走
cache_read_input_tokens/cache_creation_input_tokens,openai 系走 usage.prompt_tokens_details
.cached_tokens(DeepSeek 走 prompt_cache_hit_tokens)。gateway.complete 咽喉据 Usage 记进这两列,
prompt cache 命中省下的钱在看板/周报按模型 cost 表逐列(input/output/cache_*)可见。
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 滚动保留期(天):早于「今天 − N 天」的**完整日**逐调用明细滚成按天×source×model 汇总行,
# 明细删、汇总留。〔常数待 Trace 记分布后标定 —— 首版 90 天:by_day/by_source 的季度级趋势
# 保得住,只丢逐调用明细(task_total/run_totals/分钟级 buckets 这类精细查询只对近期有意义)。〕
ROLLUP_RETAIN_DAYS = 90


# ---- source contextvar ----

_SOURCE: contextvars.ContextVar[str] = contextvars.ContextVar("token_source", default="unknown")


@contextlib.contextmanager
def token_source(name: str):
    """标记接下来的 LLM 调用归属哪个功能(drive/forge/凝习惯/意图/governance/冲突…)。"""
    tok = _SOURCE.set(name or "unknown")
    try:
        yield
    finally:
        _SOURCE.reset(tok)


def current_source() -> str:
    return _SOURCE.get()


# ---- task contextvar(per-task 归因;#42 成本预估的地基)----

_TASK: contextvars.ContextVar[str] = contextvars.ContextVar("token_task", default="")


@contextlib.contextmanager
def token_task(task_id: str):
    """标记接下来的 LLM 调用归属哪个任务(任务看板 registry id)。

    与 token_source 正交:source 答"哪个功能烧的",task 答"哪次任务烧的"。
    contextvars 跨 await/to_thread 传播(asyncio 复制上下文),drive 顶层裹一次即可。"""
    tok = _TASK.set(task_id or "")
    try:
        yield
    finally:
        _TASK.reset(tok)


def current_task() -> str:
    return _TASK.get()


# ---- 全局 ledger 注册 + record 入口 ----

_LEDGER: Optional["TokenLedger"] = None


def register_ledger(ledger: Optional["TokenLedger"]) -> None:
    """注册全局账本(entry 接线时调;None = 关账本)。"""
    global _LEDGER
    _LEDGER = ledger


def get_ledger() -> Optional["TokenLedger"]:
    return _LEDGER


def _current_run_id() -> str:
    """可观测性①:run_id 串联 —— contextvar 在 Trace 模块(run_scope 是 Trace 的语义,
    账本只打标)。惰性 import + 兜底:任何异常 = ""(缺省,零行为变化)。"""
    try:
        from karvyloop.cognition.trace import current_run_id
        return current_run_id()
    except Exception:
        return ""


def record(
    *,
    model: str,
    input: int,
    output: int,
    cache_read: int = 0,
    cache_write: int = 0,
) -> None:
    """记一次 LLM 用量(source 从 contextvar 取)。未注册 ledger → no-op。"""
    led = _LEDGER
    if led is None:
        return
    try:
        led.record(
            source=current_source(), model=model or "",
            input=int(input or 0), output=int(output or 0),
            cache_read=int(cache_read or 0), cache_write=int(cache_write or 0),
            task_id=current_task(),
            run_id=_current_run_id(),
        )
    except Exception:
        # 账本失败绝不打断主流程(测量是增益,不是阻塞)
        pass


# ---- 账本(sqlite)----

_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  day TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'unknown',
  model TEXT NOT NULL DEFAULT '',
  input INTEGER NOT NULL DEFAULT 0,
  output INTEGER NOT NULL DEFAULT 0,
  cache_read INTEGER NOT NULL DEFAULT 0,
  cache_write INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS token_usage_day ON token_usage(day);
CREATE INDEX IF NOT EXISTS token_usage_source ON token_usage(source);
"""


def _day_of(ts: float) -> str:
    """ts → 'YYYY-MM-DD'(本地日,用于按天聚合)。"""
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class TokenLedger:
    """Token 用量账本(sqlite,跨进程)。"""

    def __init__(self, path: Optional[Path] = None, *, clock=time.time) -> None:
        self._path = Path(path) if path else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            ":memory:" if path is None else str(self._path), check_same_thread=False
        )
        self._conn.executescript(_SCHEMA)
        # 迁移(幂等):老库补 task_id 列(per-task 归因;老行 task_id='' 不参与任务级聚合,诚实)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(token_usage)").fetchall()}
        if "task_id" not in cols:
            self._conn.execute("ALTER TABLE token_usage ADD COLUMN task_id TEXT NOT NULL DEFAULT ''")
            self._conn.execute("CREATE INDEX IF NOT EXISTS token_usage_task ON token_usage(task_id)")
        # 迁移(幂等):老库补 run_id 列(可观测性①串联;老行 run_id='' 诚实缺省,不猜)
        if "run_id" not in cols:
            self._conn.execute("ALTER TABLE token_usage ADD COLUMN run_id TEXT NOT NULL DEFAULT ''")
            self._conn.execute("CREATE INDEX IF NOT EXISTS token_usage_run ON token_usage(run_id)")
        # 迁移(幂等):滚动汇总的两根柱子 —— calls(每明细行=1 次调用;汇总行=被折叠的原调用数,
        # 聚合查询一律 SUM(calls) 而非 COUNT(*),so 折叠后 by_day 的 calls 也守恒)+ rolled(0=逐调用
        # 明细,1=滚动汇总行;汇总行 task_id/run_id 空,不参与任务/run 级查询,raw timeline 也排除它)。
        if "calls" not in cols:
            self._conn.execute("ALTER TABLE token_usage ADD COLUMN calls INTEGER NOT NULL DEFAULT 1")
        if "rolled" not in cols:
            self._conn.execute("ALTER TABLE token_usage ADD COLUMN rolled INTEGER NOT NULL DEFAULT 0")
            self._conn.execute("CREATE INDEX IF NOT EXISTS token_usage_rolled ON token_usage(rolled)")
        # 滚动水位线台账(单行 kv):rollup_watermark = ts < 此值的明细已被滚成汇总(archived 边界)。
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS token_ledger_meta (key TEXT PRIMARY KEY, value REAL NOT NULL DEFAULT 0)")
        self._conn.commit()
        self._lock = threading.Lock()
        self._clock = clock

    def record(
        self, *, source: str, model: str,
        input: int, output: int, cache_read: int = 0, cache_write: int = 0,
        task_id: str = "",
        run_id: str = "",   # 可观测性①:本次调用属于哪次 run(缺省 ''=无 scope;记账逻辑本身不变)
    ) -> None:
        ts = self._clock()
        with self._lock:
            self._conn.execute(
                "INSERT INTO token_usage (ts, day, source, model, input, output, cache_read, cache_write, task_id, run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, _day_of(ts), source, model, input, output, cache_read, cache_write, task_id or "", run_id or ""),
            )
            self._conn.commit()

    # ---- 聚合查询(喂看板)----

    def totals(self, *, since: Optional[float] = None) -> dict:
        """总量(input/output/cache/total + 行数)。"""
        return self._agg("", (), since)

    def by_source(self, *, since: Optional[float] = None) -> list[dict]:
        return self._group("source", since)

    def by_model(self, *, since: Optional[float] = None) -> list[dict]:
        return self._group("model", since)

    def by_day(self, *, since: Optional[float] = None) -> list[dict]:
        return self._group("day", since)

    def buckets(self, *, interval_sec: int = 3600, since: Optional[float] = None,
                limit: int = 200) -> list[dict]:
        """按固定间隔分桶的时间序列 —— **回答"token 是什么时候烧的"**(docs/28:by_day 只到天,
        整场会话压成一桶,看不出时段;这里到任意粒度,默认按小时,压测可传 60 秒看分钟级)。

        每桶 floor 到 interval 边界,newest-first;label 用本地时。interval_sec 由调用方校验为正整数。
        """
        interval = max(1, int(interval_sec))
        w, p = self._where(since)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT CAST(ts / {interval} AS INTEGER) * {interval} AS bucket, "
                f"COALESCE(SUM(input),0), COALESCE(SUM(output),0), "
                f"COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0), COALESCE(SUM(calls),0) "
                f"FROM token_usage {w} GROUP BY bucket ORDER BY bucket DESC LIMIT ?",
                (*p, int(limit)),
            ).fetchall()
        out = []
        for r in rows:
            bstart = int(r[0])
            out.append({
                "bucket_start": bstart,
                "label": time.strftime("%Y-%m-%d %H:%M", time.localtime(bstart)),
                "input": int(r[1]), "output": int(r[2]),
                "cache_read": int(r[3]), "cache_write": int(r[4]),
                "total": int(r[1]) + int(r[2]), "calls": int(r[5]),
            })
        return out

    def run_totals(self, run_id: str) -> dict:
        """某次 run 烧了多少(可观测性③ replay --run 摘要)。**只读**,从现有记账行算,不新增记账。"""
        if not run_id:
            return {"input": 0, "output": 0, "total": 0, "calls": 0}
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(input),0), COALESCE(SUM(output),0), COALESCE(SUM(calls),0) "
                "FROM token_usage WHERE run_id=?", (run_id,)).fetchone()
        return {"input": int(row[0]), "output": int(row[1]),
                "total": int(row[0]) + int(row[1]), "calls": int(row[2])}

    def task_total(self, task_id: str) -> int:
        """某任务烧了多少(input+output)。"""
        if not task_id:
            return 0
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(input),0)+COALESCE(SUM(output),0) FROM token_usage WHERE task_id=?",
                (task_id,)).fetchone()
        return int(row[0] or 0)

    def estimate_task_cost(self, *, n: int = 10) -> dict:
        """"花钱之前告诉你"(#42 打计费黑箱):最近 n 个**有归因**任务的消耗分布。

        诚实边界:只统计 task_id 非空的行(归因接线之前的历史不猜);样本 <3 → n 照实返回、
        由调用方决定不显示。返回 {n, mean, min, max}(单位 token,input+output)。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_id, SUM(input)+SUM(output) AS total, MAX(ts) AS last "
                "FROM token_usage WHERE task_id != '' GROUP BY task_id "
                "ORDER BY last DESC LIMIT ?", (max(1, int(n)),)).fetchall()
        totals = [int(r[1] or 0) for r in rows]
        if not totals:
            return {"n": 0, "mean": 0, "min": 0, "max": 0}
        return {"n": len(totals), "mean": int(sum(totals) / len(totals)),
                "min": min(totals), "max": max(totals)}

    # ---- 只读窗口查询(周报卡等时段汇总;不动记账逻辑)----

    def window_totals(self, *, start_ts: Optional[float] = None,
                      end_ts: Optional[float] = None) -> dict:
        """时间窗内总量(闭区间 `start_ts <= ts <= end_ts`;None=不限)。**只读**。"""
        w, p = self._window_where(start_ts, end_ts)
        with self._lock:
            row = self._conn.execute(
                f"SELECT COALESCE(SUM(input),0), COALESCE(SUM(output),0), "
                f"COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0), COALESCE(SUM(calls),0) "
                f"FROM token_usage {w}", p,
            ).fetchone()
        inp, out, cr, cw, n = row
        return {
            "input": int(inp), "output": int(out),
            "cache_read": int(cr), "cache_write": int(cw),
            "total": int(inp) + int(out), "calls": int(n),
        }

    def window_by_model(self, *, start_ts: Optional[float] = None,
                        end_ts: Optional[float] = None) -> list[dict]:
        """时间窗内按 model 聚合(spend budget 按每模型价格算钱要 per-model 分解)。**只读**。

        含 cache_read/cache_write —— 花费换算按模型 cost 表逐列算(input/output/cache_*)。"""
        w, p = self._window_where(start_ts, end_ts)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT model, COALESCE(SUM(input),0), COALESCE(SUM(output),0), "
                f"COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0), COALESCE(SUM(calls),0) "
                f"FROM token_usage {w} GROUP BY model "
                f"ORDER BY SUM(input)+SUM(output) DESC", p,
            ).fetchall()
        return [
            {"model": r[0], "input": int(r[1]), "output": int(r[2]),
             "cache_read": int(r[3]), "cache_write": int(r[4]),
             "total": int(r[1]) + int(r[2]), "calls": int(r[5])}
            for r in rows
        ]

    def window_by_source(self, *, start_ts: Optional[float] = None,
                         end_ts: Optional[float] = None) -> list[dict]:
        """时间窗内按 source 聚合("这周谁烧的"),烧得多在前。**只读**。"""
        w, p = self._window_where(start_ts, end_ts)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT source, COALESCE(SUM(input),0), COALESCE(SUM(output),0), COALESCE(SUM(calls),0) "
                f"FROM token_usage {w} GROUP BY source "
                f"ORDER BY SUM(input)+SUM(output) DESC", p,
            ).fetchall()
        return [
            {"source": r[0], "input": int(r[1]), "output": int(r[2]),
             "total": int(r[1]) + int(r[2]), "calls": int(r[3])}
            for r in rows
        ]

    def window_series(self, *, start_ts: Optional[float] = None,
                      end_ts: Optional[float] = None,
                      granularity: str = "day", limit: int = 1000) -> list[dict]:
        """时间窗内按粒度聚合的时间序列(分时段查询/前端画柱状)。**只读**,oldest-first。

        granularity:
        - "day"  → 按 `day` 列(本地日历日)分组。**不能**用 `ts/86400` 整除桶 —— 那是 UTC 日界,
          在 UTC+8 会把"一天"切在早上 8 点(时区 bug);day 列写入时就是本地日,天然正确。
        - "hour" → 3600 秒桶(整小时对齐,整小时偏移时区都正确;与 buckets() 同口径)。
        其它值按 "day" 处理(调用方 route 已先夹断,这里兜底)。
        """
        w, p = self._window_where(start_ts, end_ts)
        lim = max(1, int(limit))
        if granularity == "hour":
            interval = 3600
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT CAST(ts / {interval} AS INTEGER) * {interval} AS bucket, "
                    f"COALESCE(SUM(input),0), COALESCE(SUM(output),0), "
                    f"COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0), COALESCE(SUM(calls),0) "
                    f"FROM token_usage {w} GROUP BY bucket ORDER BY bucket ASC LIMIT ?",
                    (*p, lim),
                ).fetchall()
            return [
                {"bucket_start": int(r[0]),
                 "label": time.strftime("%Y-%m-%d %H:00", time.localtime(int(r[0]))),
                 "input": int(r[1]), "output": int(r[2]),
                 "cache_read": int(r[3]), "cache_write": int(r[4]),
                 "total": int(r[1]) + int(r[2]), "calls": int(r[5])}
                for r in rows
            ]
        with self._lock:
            rows = self._conn.execute(
                f"SELECT day, MIN(ts), COALESCE(SUM(input),0), COALESCE(SUM(output),0), "
                f"COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0), COALESCE(SUM(calls),0) "
                f"FROM token_usage {w} GROUP BY day ORDER BY day ASC LIMIT ?",
                (*p, lim),
            ).fetchall()
        out = []
        for r in rows:
            try:  # bucket_start = 该本地日零点(前端画轴用);解析失败退首条 ts(不崩)
                bstart = int(time.mktime(time.strptime(str(r[0]), "%Y-%m-%d")))
            except (ValueError, OverflowError):
                bstart = int(r[1])
            out.append({
                "bucket_start": bstart, "label": str(r[0]),
                "input": int(r[2]), "output": int(r[3]),
                "cache_read": int(r[4]), "cache_write": int(r[5]),
                "total": int(r[2]) + int(r[3]), "calls": int(r[6]),
            })
        return out

    @staticmethod
    def _window_where(start_ts: Optional[float], end_ts: Optional[float]) -> tuple[str, tuple]:
        conds, params = [], []
        if start_ts is not None:
            conds.append("ts >= ?")
            params.append(start_ts)
        if end_ts is not None:
            conds.append("ts <= ?")
            params.append(end_ts)
        return (("WHERE " + " AND ".join(conds)) if conds else ""), tuple(params)

    def recent(self, *, limit: int = 50) -> list[dict]:
        """最近 N 条原始调用(时间线:何时、哪个 source/model、烧多少)—— 定位某次尖峰是谁。

        只取逐调用明细(rolled=0);滚动汇总行(rolled=1)不是"一次调用",不进原始时间线。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, source, model, input, output, cache_read, cache_write "
                "FROM token_usage WHERE rolled=0 ORDER BY ts DESC LIMIT ?", (int(limit),),
            ).fetchall()
        return [
            {"ts": float(r[0]),
             "label": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[0])),
             "source": r[1], "model": r[2], "input": int(r[3]), "output": int(r[4]),
             "cache_read": int(r[5]), "cache_write": int(r[6]), "total": int(r[3]) + int(r[4])}
            for r in rows
        ]

    def _where(self, since: Optional[float]) -> tuple[str, tuple]:
        if since is None:
            return "", ()
        return "WHERE ts >= ?", (since,)

    def _agg(self, _col: str, _params: tuple, since: Optional[float]) -> dict:
        w, p = self._where(since)
        with self._lock:
            row = self._conn.execute(
                f"SELECT COALESCE(SUM(input),0), COALESCE(SUM(output),0), "
                f"COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0), COALESCE(SUM(calls),0) "
                f"FROM token_usage {w}", p,
            ).fetchone()
        inp, out, cr, cw, n = row
        return {
            "input": int(inp), "output": int(out),
            "cache_read": int(cr), "cache_write": int(cw),
            "total": int(inp) + int(out), "calls": int(n),
        }

    def _group(self, col: str, since: Optional[float]) -> list[dict]:
        w, p = self._where(since)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {col}, COALESCE(SUM(input),0), COALESCE(SUM(output),0), "
                f"COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0), COALESCE(SUM(calls),0) "
                f"FROM token_usage {w} GROUP BY {col} ORDER BY SUM(input)+SUM(output) DESC", p,
            ).fetchall()
        return [
            {
                col: r[0], "input": int(r[1]), "output": int(r[2]),
                "cache_read": int(r[3]), "cache_write": int(r[4]),
                "total": int(r[1]) + int(r[2]), "calls": int(r[5]),
            }
            for r in rows
        ]

    # ---- 滚动汇总(唯一无界持久台账收口:老明细滚成按天汇总,明细删、聚合守恒)----

    def rollup(self, *, retain_days: int = ROLLUP_RETAIN_DAYS,
               now: Optional[float] = None) -> dict:
        """把早于保留期的**完整日**逐调用明细滚成按天×source×model 汇总行:明细删、汇总留。

        为什么这样滚(逻辑可证,不靠运气):
        - **聚合守恒**(核心不变量):汇总行的 input/output/cache_read/cache_write/calls 恰好是被删
          明细在该 (day, source, model) 下的和 → totals / by_day / by_source / by_model(全按
          `GROUP BY` 后 `SUM`,calls 也走 `SUM(calls)`)滚动前后**逐字段相等**。丢的只是逐调用
          明细(task_total/run_totals/分钟级 buckets 这类精细查询——它们只对近期有意义)。
        - **只滚完整日**:边界按 `day` 字符串(ISO 日,字典序=时序)比,`day < cutoff_day` 才滚,
          绝不切半天 → 每 (day,source,model) 至多一条汇总行,幂等(汇总行 rolled=1,下轮不再滚)。
        - **原子**:聚合(读进内存)→ 删明细 → 插汇总 → 抬水位线,全在一个事务里;任一步崩即
          `rollback`,一条明细都不丢(sqlite 事务天然)。
        - **归档诚实**:抬 rollup_watermark(=cutoff_day 零点 ts);ts < 水位线的老 task/run 明细
          已不在,task_total/run_totals 返回 0,但调用方拿 `rollup_watermark()` 一比即知"已归档"
          而非"消耗为 0"(不静默变 0)。

        返回 {deleted(删的明细行数), summarized(新增汇总行数), cutoff_day(保留窗起始日)}。
        """
        now = self._clock() if now is None else now
        cutoff_day = _day_of(now - max(0, int(retain_days)) * 86400)
        with self._lock:
            # ① 聚合待滚明细(完整日、非汇总行)。COUNT(*) 拿被折叠的明细行数(=删几条),
            #    SUM(calls) 拿被折叠的原调用数(汇总行的 calls,守恒 by_day 的 calls)。
            groups = self._conn.execute(
                "SELECT day, source, model, "
                "COALESCE(SUM(input),0), COALESCE(SUM(output),0), "
                "COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0), "
                "COALESCE(SUM(calls),0), COUNT(*) "
                "FROM token_usage WHERE day < ? AND rolled=0 "
                "GROUP BY day, source, model",
                (cutoff_day,),
            ).fetchall()
            if not groups:
                return {"deleted": 0, "summarized": 0, "cutoff_day": cutoff_day}
            deleted = sum(int(g[8]) for g in groups)
            try:
                # ② 删明细(完整日、非汇总)
                self._conn.execute(
                    "DELETE FROM token_usage WHERE day < ? AND rolled=0", (cutoff_day,))
                # ③ 每 (day,source,model) 插一条汇总行(ts=该本地日零点,day 列不变;
                #    task_id/run_id 空——归属被折叠掉;calls 累计原调用数;rolled=1)
                for day, source, model, inp, out, cr, cw, calls, _n in groups:
                    try:
                        bstart = float(time.mktime(time.strptime(str(day), "%Y-%m-%d")))
                    except (ValueError, OverflowError):
                        bstart = now
                    self._conn.execute(
                        "INSERT INTO token_usage "
                        "(ts, day, source, model, input, output, cache_read, cache_write, "
                        "task_id, run_id, calls, rolled) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, 1)",
                        (bstart, str(day), source, model, int(inp), int(out),
                         int(cr), int(cw), int(calls)),
                    )
                # ④ 抬水位线(cutoff_day 零点;取 max 单调不回退)
                try:
                    wm = float(time.mktime(time.strptime(cutoff_day, "%Y-%m-%d")))
                except (ValueError, OverflowError):
                    wm = now
                prev = self._conn.execute(
                    "SELECT value FROM token_ledger_meta WHERE key='rollup_watermark'").fetchone()
                new_wm = max(float(prev[0]) if prev else 0.0, wm)
                self._conn.execute(
                    "INSERT OR REPLACE INTO token_ledger_meta (key, value) "
                    "VALUES ('rollup_watermark', ?)", (new_wm,))
                self._conn.commit()
            except Exception:
                self._conn.rollback()   # 崩了整体回滚:明细/汇总/水位线全不落,一条不丢
                raise
        # 不静默截断纪律:滚了多少必须上 log(info 一行,天级频率不刷屏)
        logger.info(
            "[token_ledger] 滚动汇总:删 %d 条逐调用明细 → %d 条按天汇总行"
            "(保留最近 %d 天明细;< %s 的完整日已归档,聚合值不变)",
            deleted, len(groups), int(retain_days), cutoff_day)
        return {"deleted": deleted, "summarized": len(groups), "cutoff_day": cutoff_day}

    def rollup_watermark(self) -> float:
        """滚动水位线(float ts):ts < 此值的逐调用明细已被滚成汇总(archived 边界);0.0=从未滚动。

        task_total/run_totals 对 ts < 水位线的老 task/run 已无明细 → 返回 0;调用方拿它一比即可
        区分"已归档(明细滚走)"和"消耗为 0",不把归档误读成零消耗。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM token_ledger_meta WHERE key='rollup_watermark'").fetchone()
        return float(row[0]) if row else 0.0

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "TokenLedger",
    "ROLLUP_RETAIN_DAYS",
    "token_source",
    "token_task",
    "current_task",
    "current_source",
    "register_ledger",
    "get_ledger",
    "record",
]
