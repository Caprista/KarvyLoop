"""Trace 三层漏斗 — 原文 + 摘要双层 ring buffer(M3+ 拍 9.0a)。

设计:docs/25-fastbrain-architecture.md §5 + 用户原话 2026-06-17。

**职责**(本拍 9.0a):
- 原文层:10MB 容量 ring buffer(高写入,丢)
- 摘要层:50MB 容量 ring buffer(事件驱动,可独立存在)
- 习惯层:**9.0b 拍,本拍不实做**

**灵魂铁律**(FB-4 / FB-5 / FB-7):
- FB-4:三层各自独立 — 不依赖(本拍只 2 层)
- FB-4:原文可丢,摘要可独立存在
- FB-4:字节计数(**不**是行数),容量满了就覆最旧
- FB-5:本模块**不**依赖小卡私有组件(不 import `karvy.atoms`)
- FB-7:本模块**不**写"意图分析"功能 — 那是 IntentAnalyst 职责

**借(Q5)**:
- sqlite3 stdlib + WAL(同 cognition/sqlite_trace.py 模式)
- dataclass 风格(同 trace.TrackEntry)

**自造**:
- 双层独立 ring buffer(原文 + 摘要各一表)
- 字节计数 + 按容量覆最旧(全文 drop,不用 partial trim)
"""
from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# 默认容量 — 原文 10MB / 摘要 50MB(用户 2026-06-17 原话 "10MB 起步")
DEFAULT_RAW_CAPACITY_BYTES = 10 * 1024 * 1024
DEFAULT_SUMMARY_CAPACITY_BYTES = 50 * 1024 * 1024


# 满判定阈值:实际字节超过 90% 容量即认为"快满"(防抖动)
FULL_RATIO = 0.9


# 表 schema(原文/摘要 各一表,结构相同;seq 自增 + payload_json)
GLOBAL_SCOPE = "global"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trace_raw (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  payload_json TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  scope TEXT NOT NULL DEFAULT 'global',
  prev_hash TEXT NOT NULL DEFAULT '',
  hash TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS trace_raw_seq ON trace_raw(seq);
CREATE TABLE IF NOT EXISTS trace_summary (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  payload_json TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  scope TEXT NOT NULL DEFAULT 'global',
  prev_hash TEXT NOT NULL DEFAULT '',
  hash TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS trace_summary_seq ON trace_summary(seq);
"""
# scope 索引在 ALTER 迁移后建(旧 db 此时才有 scope 列)
_SCOPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS trace_raw_scope ON trace_raw(scope);
CREATE INDEX IF NOT EXISTS trace_summary_scope ON trace_summary(scope);
"""


def _chain_hash(prev_hash: str, payload_json: str, scope: str, ts: float) -> str:
    """hash-chain(TR-5 篡改可检测):h = sha256(prev + payload + scope + ts)。"""
    h = hashlib.sha256()
    h.update((prev_hash + "\x00" + payload_json + "\x00" + scope + "\x00" + repr(ts)).encode("utf-8"))
    return h.hexdigest()


def _open_sqlite(path: Path) -> sqlite3.Connection:
    """开 sqlite 连接(WAL,跨进程安全)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@dataclass(frozen=True)
class TraceRecord:
    """一条 trace 记录(原文/摘要共用格式)。

    fields:
        seq: 自增主键(层内独立计数)
        ts: 时间戳(秒,time.time 同款)
        payload: 业务负载(任意 JSON-serializable)
        size_bytes: payload 序列化后的字节数(用于容量计算)
    """
    seq: int
    ts: float
    payload: dict
    size_bytes: int = 0
    scope: str = "global"
    hash: str = ""


class TraceIndex:
    """双层 ring buffer:原文 + 摘要。

    行为契约:
        - append_raw / append_summary 立刻写 sqlite,满了覆最旧
        - list_raw / list_summary 返新→旧
        - raw_bytes / summary_bytes 返当前层总字节
        - is_raw_full / is_summary_full 返是否超 90% 容量
        - 跨进程安全(WAL);线程安全(internal lock)
    """

    def __init__(
        self,
        path: Path,
        *,
        raw_capacity: int = DEFAULT_RAW_CAPACITY_BYTES,
        summary_capacity: int = DEFAULT_SUMMARY_CAPACITY_BYTES,
        clock=time.time,
    ) -> None:
        if raw_capacity <= 0:
            raise ValueError(f"raw_capacity must > 0, got {raw_capacity}")
        if summary_capacity <= 0:
            raise ValueError(f"summary_capacity must > 0, got {summary_capacity}")
        self._path = Path(path)
        self._raw_capacity = raw_capacity
        self._summary_capacity = summary_capacity
        self._clock = clock
        self._conn = _open_sqlite(self._path)
        self._conn.executescript(_SCHEMA_SQL)
        # 9.3c-2 迁移:旧 db 补 scope/prev_hash/hash 列(已存在则忽略)
        for tbl in ("trace_raw", "trace_summary"):
            for col, decl in (("scope", "TEXT NOT NULL DEFAULT 'global'"),
                              ("prev_hash", "TEXT NOT NULL DEFAULT ''"),
                              ("hash", "TEXT NOT NULL DEFAULT ''")):
                try:
                    self._conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")
                except sqlite3.OperationalError:
                    pass  # 列已存在
        self._conn.executescript(_SCOPE_INDEX_SQL)  # 列就绪后建 scope 索引
        self._conn.commit()
        self._lock = threading.Lock()
        self._closed = False

    # ---- 容量接口 ----

    @property
    def raw_capacity(self) -> int:
        return self._raw_capacity

    @property
    def summary_capacity(self) -> int:
        return self._summary_capacity

    def raw_bytes(self) -> int:
        """原文层当前总字节。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM trace_raw"
            ).fetchone()
        return int(row[0])

    def summary_bytes(self) -> int:
        """摘要层当前总字节。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM trace_summary"
            ).fetchone()
        return int(row[0])

    def is_raw_full(self) -> bool:
        return self.raw_bytes() >= int(self._raw_capacity * FULL_RATIO)

    def is_summary_full(self) -> bool:
        return self.summary_bytes() >= int(self._summary_capacity * FULL_RATIO)

    # ---- append 接口 ----

    def append_raw(self, payload: dict, *, scope: str = GLOBAL_SCOPE) -> TraceRecord:
        """追加原文(TR-3 带 scope)。满则按 seq ASC 覆最旧直到非满。"""
        return self._append("trace_raw", self._raw_capacity, payload, scope)

    def append_summary(self, payload: dict, *, scope: str = GLOBAL_SCOPE) -> TraceRecord:
        """追加摘要(TR-3 带 scope)。满则按 seq ASC 覆最旧直到非满。"""
        return self._append("trace_summary", self._summary_capacity, payload, scope)

    def _append(self, table: str, capacity: int, payload: dict, scope: str) -> TraceRecord:
        if self._closed:
            raise RuntimeError("TraceIndex 已 close,不可再 append")
        ts = self._clock()
        payload_json = json.dumps(payload, ensure_ascii=False)
        size_bytes = len(payload_json.encode("utf-8"))
        scope = scope or GLOBAL_SCOPE
        with self._lock:
            # hash-chain(TR-5)**按 scope 串**:prev = 同 scope 上一条的 hash(空则 "")。
            # 配合 per-scope 淘汰:删的是该 scope 链的前缀,不会在链中间留洞误报篡改(对抗自查)。
            row = self._conn.execute(
                f"SELECT hash FROM {table} WHERE scope = ? ORDER BY seq DESC LIMIT 1", (scope,)
            ).fetchone()
            prev_hash = (row[0] if row else "") or ""
            cur_hash = _chain_hash(prev_hash, payload_json, scope, ts)
            cur = self._conn.execute(
                f"INSERT INTO {table} (ts, payload_json, size_bytes, scope, prev_hash, hash) "
                f"VALUES (?, ?, ?, ?, ?, ?)",
                (ts, payload_json, size_bytes, scope, prev_hash, cur_hash),
            )
            seq = int(cur.lastrowid)
            self._evict_until(table, capacity, scope)
            self._conn.commit()
        return TraceRecord(seq=seq, ts=ts, payload=payload, size_bytes=size_bytes,
                           scope=scope, hash=cur_hash)

    def _evict_until(self, table: str, capacity: int, scope: str) -> None:
        """覆最旧直到**该 scope** 的总量 < 90% 容量(防抖)。

        **每 scope 一份 `capacity` 预算**(Hardy 2026-06-28):固定全局 10MB 不随 role 数涨 →
        忙 role/scope 会覆掉安静 role 还没被消费的上下文。改成按 scope 各自淘汰 → 总量随 scope 数
        正相关增长(1 scope 用 10MB、N scope 用 10MB×N),且忙 scope **覆不到**安静 scope。
        单 scope(常见 global)时行为与原全局环一致。
        """
        threshold = int(capacity * FULL_RATIO)
        while True:
            row = self._conn.execute(
                f"SELECT COALESCE(SUM(size_bytes), 0) FROM {table} WHERE scope = ?", (scope,)
            ).fetchone()
            if int(row[0]) < threshold:
                return
            # 删**该 scope** 最旧一条(单条删,防一次删多 = 单事务开销)
            oldest = self._conn.execute(
                f"SELECT seq FROM {table} WHERE scope = ? ORDER BY seq ASC LIMIT 1", (scope,)
            ).fetchone()
            if oldest is None:
                return
            self._conn.execute(
                f"DELETE FROM {table} WHERE seq = ?", (int(oldest[0]),)
            )

    # ---- list 接口 ----

    def list_raw(self, limit: int = 100, *, scope: Optional[str] = None) -> list[TraceRecord]:
        """列最近 N 条原文(新→旧);scope 给定则只列该 scope(TR-3)。"""
        return self._list("trace_raw", limit, scope)

    def list_summary(self, limit: int = 100, *, scope: Optional[str] = None) -> list[TraceRecord]:
        """列最近 N 条摘要(新→旧);scope 给定则只列该 scope(TR-3)。"""
        return self._list("trace_summary", limit, scope)

    def _list(self, table: str, limit: int, scope: Optional[str] = None) -> list[TraceRecord]:
        with self._lock:
            if scope is None:
                rows = self._conn.execute(
                    f"SELECT seq, ts, payload_json, size_bytes, scope, hash "
                    f"FROM {table} ORDER BY seq DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"SELECT seq, ts, payload_json, size_bytes, scope, hash "
                    f"FROM {table} WHERE scope = ? ORDER BY seq DESC LIMIT ?",
                    (scope, limit),
                ).fetchall()
        return [
            TraceRecord(
                seq=int(r[0]), ts=float(r[1]),
                payload=json.loads(r[2]) if r[2] else {},
                size_bytes=int(r[3]), scope=r[4] or GLOBAL_SCOPE, hash=r[5] or "",
            )
            for r in rows
        ]

    def verify_chain(self, layer: str = "raw") -> tuple[bool, int]:
        """校验 hash-chain(TR-5 篡改可检测)。

        Args:
            layer: "raw" / "summary"

        Returns:
            (ok, first_broken_seq):ok=True 链完整;否则 first_broken_seq = 第一处断链 seq。
            注:ring buffer 覆最旧后,只校验**剩余前缀**(最旧剩余的 prev 指向已驱逐项,不校该链)。
        """
        table = "trace_raw" if layer == "raw" else "trace_summary"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT seq, ts, payload_json, scope, prev_hash, hash "
                f"FROM {table} ORDER BY seq ASC"
            ).fetchall()
        # 链**按 scope** 校验(per-scope 淘汰下,每条链各自连续;跨 scope 不相干)。
        prev_by_scope: dict = {}             # scope → 上一条有 hash 记录的 hash
        for r in rows:
            seq, ts, pj, scope, prev_hash, stored_hash = r
            scope = scope or GLOBAL_SCOPE
            if not stored_hash:
                # 旧 db 迁移前写的行无 hash → 不可校验(非篡改),跳过 + 重置该 scope 链
                prev_by_scope[scope] = None
                continue
            # 重算 hash(用本行存的 prev_hash)
            expect = _chain_hash(prev_hash or "", pj, scope, float(ts))
            if expect != stored_hash:
                return (False, int(seq))  # 本行被篡改(payload/scope/ts 改了)
            # 链接连续性:本行 prev_hash 应等于**同 scope** 上一有 hash 行的 hash(首/驱逐/legacy 后不校)
            pic = prev_by_scope.get(scope)
            if pic is not None and (prev_hash or "") != pic:
                return (False, int(seq))  # 中间被删/插
            prev_by_scope[scope] = stored_hash
        return (True, -1)

    # ---- 关闭 ----

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # ---- context manager ----

    def __enter__(self) -> "TraceIndex":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


__all__ = [
    "DEFAULT_RAW_CAPACITY_BYTES",
    "DEFAULT_SUMMARY_CAPACITY_BYTES",
    "FULL_RATIO",
    "GLOBAL_SCOPE",
    "TraceIndex",
    "TraceRecord",
]
