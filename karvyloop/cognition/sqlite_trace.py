"""Sqlite-backed TraceStore(M3+ 批 6)。

设计:plans/snoopy-singing-sunbeam.md §批 6。

借(Q5):
- 接口同 `cognition/trace.py` TraceStore(append + query + query_atom_runs + all_tasks)
- sqlite3 stdlib + WAL 防 Windows 锁文件

自造:
- 表 schema(trace_entries,复合主键 task_id+seq)
- 从 row → TraceEntry 重建(payload 反序列化 JSON)
- task_id 内部分配:append 时 entry.task_id 必须有值(TraceStore 是 append-only
  底座,caller 决定 task_id)

边界:
- M1 v1 简化:不接 jsonl 双写(plan §Q2 提到 JSONL 但 sqlite 已覆盖持久化 + 可重放);
  `cmd_replay <task_id>` 直接读 sqlite 印 NDJSON 到 stdout。
- 不支持按 kind + agent + source 联合查询(M1 v1:仅 task_id,可选 kind filter)。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from karvyloop.schemas import AtomRun

from .trace import TraceEntry, TraceStore


_TRACE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trace_entries (
  task_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  ts REAL NOT NULL,
  agent TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  trace_ref TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (task_id, seq)
);
CREATE INDEX IF NOT EXISTS trace_entries_kind ON trace_entries(kind);
"""


def _open_sqlite(path: Optional[Path]) -> sqlite3.Connection:
    conn = sqlite3.connect(
        ":memory:" if path is None else str(path),
        check_same_thread=False,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


class SqliteTraceStore(TraceStore):
    """sqlite 后端 TraceStore。M3+ 批 6 新增。

    seq 自增策略:同 task_id 内自增,跨 task_id 独立计数(与 InMemoryTraceStore 同款)。
    """

    def __init__(self, path: Optional[Path] = None, *, clock=time.time) -> None:
        self._path = Path(path) if path else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = _open_sqlite(self._path)
        self._conn.executescript(_TRACE_SCHEMA_SQL)
        self._conn.commit()
        self._lock = threading.Lock()
        self._clock = clock
        self._closed = False

    def prune_raw(self, max_raw: int) -> int:
        """docs/27 原文层容量环(sqlite 版):只丢大块原文(DROPPABLE_KINDS)超额最旧;
        eval_fact(可能未评)+ 提炼物一律保留。留最新:ORDER BY ts DESC, **rowid DESC**(ts 打平时
        按插入序留最新,修对抗验收 C-2:原 DESC 在打平时反而留了最旧)。返回丢弃条数。"""
        drop = "('atom_run','user_turn','assistant_turn','tool_call')"
        with self._lock:
            if self._closed:
                return 0
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM trace_entries WHERE kind IN {drop}").fetchone()[0]
            if total <= max_raw:
                return 0
            self._conn.execute(
                f"DELETE FROM trace_entries WHERE kind IN {drop} AND rowid NOT IN "
                f"(SELECT rowid FROM trace_entries WHERE kind IN {drop} "
                f"ORDER BY ts DESC, rowid DESC LIMIT ?)", (max_raw,))
            self._conn.commit()
            return total - max_raw

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

    def append(self, entry: TraceEntry) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 FROM trace_entries WHERE task_id = ?",
                (entry.task_id,),
            ).fetchone()
            seq = int(row[0])
            entry.seq = seq
            if not entry.ts:
                entry.ts = self._clock()
            self._conn.execute(
                "INSERT INTO trace_entries (task_id, seq, kind, payload_json, ts, agent, source, trace_ref) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.task_id, seq, entry.kind,
                    json.dumps(entry.payload, ensure_ascii=False),
                    entry.ts, entry.agent, entry.source,
                    (entry.payload or {}).get("trace_ref", "") if isinstance(entry.payload, dict) else "",
                ),
            )
            self._conn.commit()
            return f"{entry.task_id}:{seq}"

    def query(self, task_id: str, *, kind: Optional[str] = None,
              start_ts: Optional[float] = None,
              end_ts: Optional[float] = None) -> list[TraceEntry]:
        """同 InMemory 版语义:可选 kind + 可选时间窗(闭区间 `start_ts <= ts <= end_ts`;
        None = 不限,向后兼容 —— 旧调用 `query(tid, kind=...)` 行为一字不变)。"""
        sql = ("SELECT task_id, seq, kind, payload_json, ts, agent, source "
               "FROM trace_entries WHERE task_id = ?")
        params: list = [task_id]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY seq ASC"
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_atom_runs(self, task_id: str) -> list[AtomRun]:
        """继承 InMemory 同款语义:把所有 atom_run 事件反序列化为 AtomRun。"""
        out: list[AtomRun] = []
        for e in self.query(task_id, kind="atom_run"):
            p = e.payload if isinstance(e.payload, dict) else {}
            try:
                out.append(AtomRun(
                    atom_id=p.get("atom_id", ""),
                    input=p.get("input", {}),
                    output=p.get("output"),
                    success=p.get("success", False),
                    tool_calls=p.get("tool_calls", []),
                    trace_ref=p.get("trace_ref", ""),
                    ts=p.get("ts", e.ts),
                    terminal=p.get("terminal") or None,  # §15:终止语义随重建保留(否则结晶侧读回是 None)
                ))
            except Exception:
                # 防御:坏数据不阻塞(append-only 不该被坏 payload 阻塞)
                continue
        return out

    def all_tasks(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT task_id FROM trace_entries ORDER BY task_id",
            ).fetchall()
        return [r[0] for r in rows]

    @staticmethod
    def _row_to_entry(row: tuple) -> TraceEntry:
        task_id, seq, kind, payload_json, ts, agent, source = row
        return TraceEntry(
            task_id=task_id,
            seq=int(seq),
            kind=kind,
            payload=json.loads(payload_json) if payload_json else {},
            ts=float(ts),
            agent=agent or "",
            source=source or "",
        )


__all__ = ["SqliteTraceStore"]