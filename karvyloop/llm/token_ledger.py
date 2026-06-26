"""token_ledger — Token 账本(测量层,M3+ 拍 9.3a / docs/28 TK-1)。

**为什么**:token 是生命线。测了才能优化。每次 LLM 调用记一条(按 source/model/类型/时间),
喂 token 看板,看清"钱花在哪个功能上"。

**设计**(docs/28 §3.1):
- `TokenLedger`:sqlite 落盘(`~/.karvyloop/tokens.db`),record + 按 source/model/day 聚合查询。
- `token_source(name)`:contextvar — 调用方标"这次 LLM 是谁烧的"(drive/forge/凝习惯/意图/…)。
  source 维度是关键:provider 只知 model,不知功能;靠 contextvar 从顶层传下来。
- `register_ledger` + 模块级 `record(...)`:provider 出 ChatResponse 后调一次 record,
  从 contextvar 取 source 写账本。未注册 ledger → no-op(测试/无账本场景不崩)。

**诚实标记**:cache_read/write 列已留,但 transport 暂未 surface anthropic 的
cache_creation/cache_read_input_tokens → 当前 0(P1:transport 补 cache 字段)。
"""
from __future__ import annotations

import contextlib
import contextvars
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional


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


# ---- 全局 ledger 注册 + record 入口 ----

_LEDGER: Optional["TokenLedger"] = None


def register_ledger(ledger: Optional["TokenLedger"]) -> None:
    """注册全局账本(entry 接线时调;None = 关账本)。"""
    global _LEDGER
    _LEDGER = ledger


def get_ledger() -> Optional["TokenLedger"]:
    return _LEDGER


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
        self._conn.commit()
        self._lock = threading.Lock()
        self._clock = clock

    def record(
        self, *, source: str, model: str,
        input: int, output: int, cache_read: int = 0, cache_write: int = 0,
    ) -> None:
        ts = self._clock()
        with self._lock:
            self._conn.execute(
                "INSERT INTO token_usage (ts, day, source, model, input, output, cache_read, cache_write) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, _day_of(ts), source, model, input, output, cache_read, cache_write),
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

    def _where(self, since: Optional[float]) -> tuple[str, tuple]:
        if since is None:
            return "", ()
        return "WHERE ts >= ?", (since,)

    def _agg(self, _col: str, _params: tuple, since: Optional[float]) -> dict:
        w, p = self._where(since)
        with self._lock:
            row = self._conn.execute(
                f"SELECT COALESCE(SUM(input),0), COALESCE(SUM(output),0), "
                f"COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0), COUNT(*) "
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
                f"COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0), COUNT(*) "
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

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "TokenLedger",
    "token_source",
    "current_source",
    "register_ledger",
    "get_ledger",
    "record",
]
