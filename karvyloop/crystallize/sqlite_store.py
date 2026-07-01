"""Sqlite-backed UsageStore + VerifyStore(M3+ 批 6)。

设计:plans/snoopy-singing-sunbeam.md §批 6。

借(Q5):
- 接口同 UsageStore / VerifyStore(`store.py` + `verify.py` 的 Protocol/基类)
- sqlite3 stdlib(无第三方依赖)
- WAL 模式防 Windows 锁文件(coding/session.py:68-75 atomic_append 同款精神)
- 线程安全:connection 单例 + 锁(M1 v1 simplify;真并发场景 P1)

自造:
- 表 schema(usage_stats / verify_gate)
- 从 row → UsageStats Pydantic 重建(param_variants / steered_by_user JSON 列)
- 从 row → VerifyResult dataclass 重建

路径默认:`~/.karvyloop/usage.sqlite` + `verify.sqlite`(同根目录);测试用 `:memory:`
或 `tmp_path/usage.sqlite`。

边界:
- **M1 v1 简化**:不支持 schema 迁移;若列缺 → 启动时建表(若表存在但 schema 不匹配
  → fail-loud,用户重建)。
- 拍 9:recall_count_inc 真做(基类 + sqlite 同步)+ 加 `recall_count INTEGER DEFAULT 0` 列。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterator, Optional

from karvyloop.schemas import UsageStats

from .store import UsageStore
from .verify import VerifyResult, VerifyStore


# ---- Schema ----

_USAGE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS usage_stats (
  sig TEXT PRIMARY KEY,
  usage_count INTEGER NOT NULL DEFAULT 0,
  last_used_at REAL NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0,
  failure_count INTEGER NOT NULL DEFAULT 0,
  recall_count INTEGER NOT NULL DEFAULT 0,
  param_variants_json TEXT NOT NULL DEFAULT '[]',
  steered_by_user_json TEXT NOT NULL DEFAULT '[]',
  intent_repr TEXT NOT NULL DEFAULT '',
  archived INTEGER NOT NULL DEFAULT 0
);
"""

_VERIFY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS verify_gate (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sig TEXT NOT NULL,
  trace_ref TEXT NOT NULL,
  passed INTEGER NOT NULL,
  at REAL NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS verify_gate_sig_at ON verify_gate(sig, at);
"""


def _open_sqlite(path: Optional[Path]) -> sqlite3.Connection:
    """打开 sqlite 连接 + WAL 模式。

    path=None → :memory:;path=`/some/dir/store.sqlite` → 物理文件(parent 自动 mkdir)。
    """
    conn = sqlite3.connect(
        ":memory:" if path is None else str(path),
        check_same_thread=False,
    )
    conn.execute("PRAGMA journal_mode=WAL")  # 防 Windows 锁文件诡异
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


class SqliteUsageStore(UsageStore):
    """sqlite 后端 UsageStore。M3+ 批 6 新增。"""

    def __init__(self, path: Optional[Path] = None, *, clock=time.time) -> None:
        self._path = Path(path) if path else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = _open_sqlite(self._path)
        self._conn.executescript(_USAGE_SCHEMA_SQL)
        # 迁移旧库:拍 9 加的 recall_count 列,CREATE TABLE IF NOT EXISTS 不会给**已存在**的
        # 旧表补列 → 升级用户的旧 usage.sqlite 缺该列,recall_count_inc 会 OperationalError 崩。
        # 幂等 ALTER(列已存在 → OperationalError,吞掉)。门1 真机抓到。
        try:
            self._conn.execute(
                "ALTER TABLE usage_stats ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass  # 列已存在(新库 / 已迁移)
        # 9.4:intent_repr 列(token-overlap 累积聚类的代表意图);同样幂等 ALTER 迁移旧库
        try:
            self._conn.execute(
                "ALTER TABLE usage_stats ADD COLUMN intent_repr TEXT NOT NULL DEFAULT ''"
            )
        except sqlite3.OperationalError:
            pass
        self._conn.commit()
        self._lock = threading.Lock()
        self._clock = clock
        self._closed = False

    def close(self) -> None:
        """显式关闭(M3+ 批 6:cmd_run 退出前调)。"""
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

    # ---- CRUD ----

    def get_or_create(self, sig: str) -> UsageStats:
        with self._lock:
            row = self._conn.execute(
                "SELECT sig, usage_count, last_used_at, success_count, failure_count, recall_count, "
                "param_variants_json, steered_by_user_json, intent_repr "
                "FROM usage_stats WHERE sig = ?",
                (sig,),
            ).fetchone()
            if row is None:
                stats = UsageStats()
                self._conn.execute(
                    "INSERT INTO usage_stats (sig, usage_count, last_used_at, "
                    "success_count, failure_count, recall_count, "
                    "param_variants_json, steered_by_user_json) "
                    "VALUES (?, 0, 0, 0, 0, 0, '[]', '[]')",
                    (sig,),
                )
                self._conn.commit()
                return stats
            return self._row_to_stats(row)

    def get(self, sig: str) -> Optional[UsageStats]:
        with self._lock:
            row = self._conn.execute(
                "SELECT sig, usage_count, last_used_at, success_count, failure_count, recall_count, "
                "param_variants_json, steered_by_user_json, intent_repr "
                "FROM usage_stats WHERE sig = ?",
                (sig,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_stats(row)

    def put(self, sig: str, stats: UsageStats) -> None:
        with self._lock:
            # 不调 self.is_archived(死锁 — threading.Lock 非可重入);inline 查
            row = self._conn.execute(
                "SELECT archived FROM usage_stats WHERE sig = ?",
                (sig,),
            ).fetchone()
            archived = 1 if (row and row[0]) else 0
            self._conn.execute(
                "INSERT OR REPLACE INTO usage_stats "
                "(sig, usage_count, last_used_at, success_count, failure_count, recall_count, "
                "param_variants_json, steered_by_user_json, intent_repr, archived) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sig,
                    stats.usage_count,
                    stats.last_used_at,
                    stats.success_count,
                    stats.failure_count,
                    stats.recall_count,
                    json.dumps(list(stats.param_variants), ensure_ascii=False),
                    json.dumps(list(stats.steered_by_user), ensure_ascii=False),
                    stats.intent_repr,
                    archived,
                ),
            )
            self._conn.commit()

    def archive(self, sig: str) -> None:
        with self._lock:
            # 若行不存在也照样落 archived 标记(供 recall restore 用)
            self._conn.execute(
                "INSERT INTO usage_stats (sig, usage_count, last_used_at, "
                "success_count, failure_count, recall_count, "
                "param_variants_json, steered_by_user_json, archived) "
                "VALUES (?, 0, 0, 0, 0, 0, '[]', '[]', 1) "
                "ON CONFLICT(sig) DO UPDATE SET archived = 1",
                (sig,),
            )
            self._conn.commit()

    def restore(self, sig: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE usage_stats SET archived = 0 WHERE sig = ?",
                (sig,),
            )
            self._conn.commit()

    def is_archived(self, sig: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT archived FROM usage_stats WHERE sig = ?",
                (sig,),
            ).fetchone()
        return bool(row and row[0])

    def all(self) -> Iterator[tuple[str, UsageStats]]:
        # Snapshot 整张表(避免持锁返回 iterator 时锁释放)
        with self._lock:
            rows = self._conn.execute(
                "SELECT sig, usage_count, last_used_at, success_count, failure_count, recall_count, "
                "param_variants_json, steered_by_user_json, intent_repr "
                "FROM usage_stats",
            ).fetchall()
        for row in rows:
            yield (row[0], self._row_to_stats(row))

    def recall_count_inc(self, sig: str) -> None:
        # 拍 9:真做 +1;记入 recall_count 列(快脑召回命中 = 技能真有用)。
        with self._lock:
            self._conn.execute(
                "UPDATE usage_stats SET recall_count = recall_count + 1 WHERE sig = ?",
                (sig,),
            )
            self._conn.commit()

    # ---- helper ----

    @staticmethod
    def _row_to_stats(row: tuple) -> UsageStats:
        sig, uc, lua, sc, fc, rc, pv_json, su_json, intent_repr = row
        return UsageStats(
            usage_count=int(uc),
            last_used_at=float(lua),
            success_count=int(sc),
            failure_count=int(fc),
            recall_count=int(rc),
            param_variants=list(json.loads(pv_json or "[]")),
            steered_by_user=list(json.loads(su_json or "[]")),
            intent_repr=intent_repr or "",
        )


class SqliteVerifyStore(VerifyStore):
    """sqlite 后端 VerifyStore。M3+ 批 6 新增。"""

    def __init__(self, path: Optional[Path] = None, *, clock=time.time) -> None:
        self._path = Path(path) if path else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = _open_sqlite(self._path)
        self._conn.executescript(_VERIFY_SCHEMA_SQL)
        self._conn.commit()
        self._lock = threading.Lock()
        self._clock = clock
        self._closed = False

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

    def mark_verified(self, sig: str, trace_ref: str, *, note: str = "",
                      clock=None) -> VerifyResult:
        clk = clock if clock is not None else (self._clock if self._clock else time.time)
        at = clk()
        with self._lock:
            self._conn.execute(
                "INSERT INTO verify_gate (sig, trace_ref, passed, at, note) "
                "VALUES (?, ?, 1, ?, ?)",
                (sig, trace_ref, at, note or ""),
            )
            self._conn.commit()
        return VerifyResult(sig=sig, trace_ref=trace_ref, passed=True, at=at, note=note or "")

    def has_gate(self, sig: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM verify_gate WHERE sig = ? AND passed = 1 LIMIT 1",
                (sig,),
            ).fetchone()
        return row is not None

    def latest_proof(self, sig: str) -> Optional[VerifyResult]:
        with self._lock:
            row = self._conn.execute(
                "SELECT sig, trace_ref, passed, at, note FROM verify_gate "
                "WHERE sig = ? AND passed = 1 ORDER BY at DESC LIMIT 1",
                (sig,),
            ).fetchone()
        if row is None:
            return None
        return VerifyResult(
            sig=row[0], trace_ref=row[1], passed=bool(row[2]), at=float(row[3]), note=row[4],
        )

    def proofs(self, sig: str) -> list[VerifyResult]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT sig, trace_ref, passed, at, note FROM verify_gate "
                "WHERE sig = ? ORDER BY at ASC",
                (sig,),
            ).fetchall()
        return [
            VerifyResult(sig=r[0], trace_ref=r[1], passed=bool(r[2]), at=float(r[3]), note=r[4])
            for r in rows
        ]


__all__ = ["SqliteUsageStore", "SqliteVerifyStore"]