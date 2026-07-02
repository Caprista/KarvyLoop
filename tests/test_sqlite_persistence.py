"""sqlite 持久化测试(M3+ 批 6)。

设计:plans/snoopy-singing-sunbeam.md §批 6。

AC 列表:
  AC1: SqliteUsageStore put/get 往返 + 重启后数据在(进程级隔离用 tmp_path)
  AC2: SqliteVerifyStore mark_verified/has_gate/latest_proof 持久
  AC3: SqliteTraceStore append + query_atom_runs(task_id) 能取回 AtomRun
  AC4: MainLoop.drive 走完 → trace.store 有 ≥1 条记录(慢脑路径)
  AC5: 跑过一次后"重启"(新 MainLoop 实例 + SqliteUsageStore 同样 path)→ 结晶仍在 +
       UsageStore usage_count ≥ 1(真重启等价物:关闭旧连接,新建连接读)
  AC6: cmd_replay <task_id> 读 trace.sqlite → stdout NDJSON(任务 ID 存在/不存在)
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition import SqliteTraceStore, TraceEntry  # noqa: E402
from karvyloop.crystallize import (  # noqa: E402
    InMemoryUsageStore,
    SkillIndex,
    SqliteUsageStore,
    SqliteVerifyStore,
    VerifyStore,
)
from karvyloop.schemas import UsageStats  # noqa: E402
from karvyloop.schemas.atom import AtomRun  # noqa: E402
from karvyloop.runtime.main_loop import MainLoop  # noqa: E402
from karvyloop.cli.replay import cmd_replay  # noqa: E402
from karvyloop.cli.run_loop import build_main_loop, run_intent_via_loop  # noqa: E402


# ---------- helpers ----------

def _stub_slow_brain_factory():
    """同拍 4/5 stub:同 input → 同 sig → high_freq 路径。"""
    call_count = {"n": 0}

    def factory(**kwargs):
        def slow_brain(intent: str) -> tuple[str, AtomRun]:
            n = call_count["n"]
            call_count["n"] += 1
            ts = 1000.0 + n * 200.0
            run = AtomRun(
                atom_id=f"atom-{n}",
                input={"intent": intent},
                output={"text": f"ok-{intent}-{n}"},
                success=True,
                tool_calls=[{"name": "run_command"}],  # brick3:代表真干活→可结晶
                trace_ref=f"trace://atom/{n}",
                ts=ts,
            )
            return (f"ok-{intent}-{n}", run)

        return slow_brain

    return factory


def _build_loop_with_persistent_stores(tmp_path, *, clock_offset=0.0):
    """build_main_loop 同款 wiring 但走 tmp_path(单测不能写真盘 ~/.karvyloop/)。"""
    base_ts = 1000.0 + clock_offset
    state = {"now": base_ts}

    def clock() -> float:
        return state["now"]

    ml = MainLoop(
        skills_dir=tmp_path / "skills",
        store=SqliteUsageStore(tmp_path / "usage.sqlite"),
        verify=SqliteVerifyStore(tmp_path / "verify.sqlite"),
        trace=SqliteTraceStore(tmp_path / "trace.sqlite"),
        clock=clock,
        result_classifier=lambda *_a: "stable",  # §13:确定性桩→stable,测回放/持久化
    )
    ml.bootstrap()
    ml._test_advance = lambda secs: state.__setitem__("now", state["now"] + secs)  # type: ignore[attr-defined]
    return ml


# ---------- AC1: SqliteUsageStore put/get 往返 + 重启后数据在 ----------

class TestAC1SqliteUsageStorePersistence:
    """AC1: SqliteUsageStore.put → get 往返 + 重启(关 → 开同 path)后数据仍在。"""

    def test_put_get_roundtrip(self, tmp_path):
        store = SqliteUsageStore(tmp_path / "usage.sqlite")
        stats = UsageStats(
            usage_count=3, last_used_at=1234.5,
            success_count=3, failure_count=0,
            param_variants=[{"intent": "a"}, {"intent": "b"}],
            steered_by_user=["fix typo"],
        )
        store.put("sig-1", stats)
        got = store.get("sig-1")
        assert got is not None
        assert got.usage_count == 3
        assert got.success_count == 3
        assert got.param_variants == [{"intent": "a"}, {"intent": "b"}]
        assert got.steered_by_user == ["fix typo"]
        store.close()

    def test_persistence_across_reopen(self, tmp_path):
        """关连接 → 同 path 重开 → 数据仍在(等价进程重启)。"""
        path = tmp_path / "usage.sqlite"
        s1 = SqliteUsageStore(path)
        s1.put("sig-x", UsageStats(usage_count=7, success_count=5, failure_count=2))
        s1.close()
        # 重启:同 path 重新打开
        s2 = SqliteUsageStore(path)
        got = s2.get("sig-x")
        assert got is not None
        assert got.usage_count == 7
        assert got.success_count == 5
        s2.close()

    def test_archive_persists(self, tmp_path):
        """archive 标记落盘(供 recall auto-restore 用)。"""
        path = tmp_path / "usage.sqlite"
        s1 = SqliteUsageStore(path)
        s1.put("sig-y", UsageStats(usage_count=2))
        s1.archive("sig-y")
        s1.close()
        s2 = SqliteUsageStore(path)
        assert s2.is_archived("sig-y") is True
        s2.restore("sig-y")
        assert s2.is_archived("sig-y") is False
        s2.close()

    def test_get_or_create_creates_new(self, tmp_path):
        """get_or_create 没记录时新建 + 返回空 UsageStats。"""
        store = SqliteUsageStore(tmp_path / "usage.sqlite")
        stats = store.get_or_create("sig-new")
        assert stats.usage_count == 0
        assert stats.success_count == 0
        store.close()


# ---------- AC2: SqliteVerifyStore mark_verified/has_gate/latest_proof 持久 ----------

class TestAC2SqliteVerifyStorePersistence:
    """AC2: VerifyStore.mark_verified → has_gate + latest_proof + 持久。"""

    def test_mark_verified_then_has_gate(self, tmp_path):
        store = SqliteVerifyStore(tmp_path / "verify.sqlite")
        store.mark_verified("sig-1", "trace://atom/1", note="success")
        assert store.has_gate("sig-1") is True
        assert store.has_gate("sig-missing") is False
        store.close()

    def test_latest_proof_returns_newest(self, tmp_path):
        store = SqliteVerifyStore(tmp_path / "verify.sqlite")
        store.mark_verified("sig-1", "trace://atom/1", clock=lambda: 1000.0)
        store.mark_verified("sig-1", "trace://atom/2", clock=lambda: 2000.0)
        proof = store.latest_proof("sig-1")
        assert proof is not None
        assert proof.trace_ref == "trace://atom/2"
        assert proof.at == 2000.0
        store.close()

    def test_persistence_across_reopen(self, tmp_path):
        path = tmp_path / "verify.sqlite"
        s1 = SqliteVerifyStore(path)
        s1.mark_verified("sig-z", "trace://atom/9", note="persistent")
        s1.close()
        s2 = SqliteVerifyStore(path)
        assert s2.has_gate("sig-z") is True
        proof = s2.latest_proof("sig-z")
        assert proof is not None
        assert proof.trace_ref == "trace://atom/9"
        s2.close()


# ---------- AC3: SqliteTraceStore append + query_atom_runs 往返 ----------

class TestAC3SqliteTraceStoreAtomRunRoundtrip:
    """AC3: append(TraceEntry atom_run) → query_atom_runs(task_id) 返 AtomRun 列表。"""

    def test_append_then_query_atom_runs(self, tmp_path):
        trace = SqliteTraceStore(tmp_path / "trace.sqlite")
        run = AtomRun(
            atom_id="a1", input={"intent": "summarize"},
            output={"text": "ok"}, success=True,
            tool_calls=[], trace_ref="trace://atom/a1", ts=1000.0,
        )
        ref = trace.append(TraceEntry(
            task_id="task-001", kind="atom_run",
            payload={
                "atom_id": run.atom_id, "input": dict(run.input),
                "output": dict(run.output) if isinstance(run.output, dict) else run.output,
                "success": run.success, "tool_calls": list(run.tool_calls),
                "trace_ref": run.trace_ref, "ts": run.ts,
            },
            ts=run.ts, source="test", agent="",
        ))
        assert ref == "task-001:0"
        runs = trace.query_atom_runs("task-001")
        assert len(runs) == 1
        assert runs[0].atom_id == "a1"
        assert runs[0].input == {"intent": "summarize"}
        assert runs[0].success is True
        assert runs[0].trace_ref == "trace://atom/a1"
        trace.close()

    def test_all_tasks_lists_unique_task_ids(self, tmp_path):
        trace = SqliteTraceStore(tmp_path / "trace.sqlite")
        for tid in ["a", "a", "b"]:
            trace.append(TraceEntry(
                task_id=tid, kind="atom_run", payload={"trace_ref": "t"},
                ts=1000.0, source="test", agent="",
            ))
        assert trace.all_tasks() == ["a", "b"]
        trace.close()

    def test_persistence_across_reopen(self, tmp_path):
        path = tmp_path / "trace.sqlite"
        s1 = SqliteTraceStore(path)
        s1.append(TraceEntry(
            task_id="task-persist", kind="atom_run",
            payload={"atom_id": "a1", "input": {"x": 1}, "output": {"y": 2},
                     "success": True, "tool_calls": [], "trace_ref": "t1", "ts": 1000.0},
            ts=1000.0, source="test", agent="",
        ))
        s1.close()
        s2 = SqliteTraceStore(path)
        runs = s2.query_atom_runs("task-persist")
        assert len(runs) == 1
        assert runs[0].atom_id == "a1"
        s2.close()


# ---------- AC4: MainLoop.drive 走完 → trace ≥1 条 ----------

class TestAC4MainLoopAppendsToTrace:
    """AC4: MainLoop.drive 跑一次慢脑 → self.trace 至少有 1 条 atom_run 记录。"""

    def test_drive_writes_atom_run_to_trace(self, tmp_path):
        ml = _build_loop_with_persistent_stores(tmp_path)
        with patch("karvyloop.cli.run_loop.forge_slow_brain_factory", _stub_slow_brain_factory()):
            r = run_intent_via_loop(
                "summarize", ml,
                token=MagicMock(), sandbox=MagicMock(), gateway=MagicMock(),
                workspace_root=str(tmp_path),
            )
        assert r == 0
        assert ml.stats.slow_brain_runs == 1
        # trace 持久在 SqliteTraceStore;重开连接读
        trace_path = tmp_path / "trace.sqlite"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        # 关旧 + 开新(等价进程重启读)
        ml.trace.close()
        new_trace = SqliteTraceStore(trace_path)
        tasks = new_trace.all_tasks()
        assert len(tasks) >= 1
        # 取第一个 task 的所有 atom_run
        all_runs = []
        for t in tasks:
            all_runs.extend(new_trace.query_atom_runs(t))
        assert len(all_runs) >= 1
        assert all_runs[0].atom_id.startswith("atom-")
        new_trace.close()

    def test_drive_writes_crystallize_event(self, tmp_path):
        """5 次同 input 后,第 5 次触发 crystallized → trace 里有 'crystallize' kind。"""
        ml = _build_loop_with_persistent_stores(tmp_path)
        with patch("karvyloop.cli.run_loop.forge_slow_brain_factory", _stub_slow_brain_factory()):
            for _ in range(5):
                ml._test_advance(200.0)  # type: ignore[attr-defined]
                run_intent_via_loop(
                    "summarize", ml,
                    token=MagicMock(), sandbox=MagicMock(), gateway=MagicMock(),
                    workspace_root=str(tmp_path),
                )
        assert ml.stats.crystallizations == 1
        # 找含 crystallize 的 task
        ml.trace.close()
        new_trace = SqliteTraceStore(tmp_path / "trace.sqlite")
        kinds_per_task = {t: {e.kind for e in new_trace.query(t)} for t in new_trace.all_tasks()}
        assert any("crystallize" in ks for ks in kinds_per_task.values()), kinds_per_task
        new_trace.close()


# ---------- AC5: "重启" 后 UsageStats + SkillIndex 都还在 ----------

class TestAC5RestartPersistsCrystallization:
    """AC5: 跑过一次结晶 → "重启"(新 MainLoop + 同 sqlite path)→ UsageStats 有数据 + SkillIndex 仍 recall 命中。"""

    def test_restart_preserves_crystallization_and_usage(self, tmp_path):
        # 第一次:跑 5 次触发结晶
        ml1 = _build_loop_with_persistent_stores(tmp_path)
        with patch("karvyloop.cli.run_loop.forge_slow_brain_factory", _stub_slow_brain_factory()):
            for _ in range(5):
                ml1._test_advance(200.0)  # type: ignore[attr-defined]
                run_intent_via_loop(
                    "send email", ml1,
                    token=MagicMock(), sandbox=MagicMock(), gateway=MagicMock(),
                    workspace_root=str(tmp_path),
                )
        assert ml1.stats.crystallizations == 1
        ml1.store.close()
        ml1.verify.close()
        ml1.trace.close()

        # 第二次:新 MainLoop 实例,同 sqlite path(SqliteUsageStore/SqliteTraceStore/SqliteVerifyStore)
        ml2 = MainLoop(
            skills_dir=tmp_path / "skills",
            store=SqliteUsageStore(tmp_path / "usage.sqlite"),
            verify=SqliteVerifyStore(tmp_path / "verify.sqlite"),
            trace=SqliteTraceStore(tmp_path / "trace.sqlite"),
            result_classifier=lambda *_a: "stable",  # §13:重启后仍走回放路径(测持久化)
        )
        ml2.bootstrap()  # 从 SKILL.md 重建 SkillIndex
        # 找一个 sig 的 UsageStats(usage_count 应 >= 1)
        sigs = [s for s, _ in ml2.store.all()]
        assert len(sigs) >= 1
        any_used = any(st.usage_count >= 1 for _, st in ml2.store.all())
        assert any_used
        # 第 6 次同 intent 走快脑(同拍 4/5 既有行为)
        with patch("karvyloop.cli.run_loop.forge_slow_brain_factory",
                   lambda *a, **k: (_ for _ in ()).throw(AssertionError("6th 应走快脑"))):
            ml2._clock = lambda: 5000.0  # 任意
            ml2.stats.drive_calls += 0  # no-op
            r = ml2.drive(
                "send email",
                slow_brain=lambda intent: (_ for _ in ()).throw(
                    AssertionError("6th 应走快脑 — 不应调 slow_brain")
                ),
            )
        assert r.brain.value == "fast"
        ml2.store.close()
        ml2.verify.close()
        ml2.trace.close()


# ---------- AC6: cmd_replay <task_id> 读 trace.sqlite NDJSON ----------

class TestAC6CmdReplayNDJSON:
    """AC6: cmd_replay 读 trace.sqlite 印 NDJSON 到 stdout。"""

    def test_replay_existing_task_id_prints_ndjson(self, tmp_path, capsys):
        # 准备:append 2 条到 trace
        trace_path = tmp_path / "trace.sqlite"
        trace = SqliteTraceStore(trace_path)
        trace.append(TraceEntry(
            task_id="task-test-001", kind="atom_run",
            payload={"atom_id": "a1", "input": {"intent": "x"},
                     "output": {"text": "ok"}, "success": True,
                     "tool_calls": [], "trace_ref": "t1", "ts": 1000.0},
            ts=1000.0, source="test", agent="",
        ))
        trace.append(TraceEntry(
            task_id="task-test-001", kind="crystallize",
            payload={"sig": "sig-x", "name": "skill_xx", "trace_ref": "t1"},
            ts=1100.0, source="main_loop", agent="",
        ))
        trace.close()

        rc = cmd_replay("task-test-001", trace_path=trace_path)
        assert rc == 0
        out = capsys.readouterr().out
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) == 2
        e0 = json.loads(lines[0])
        assert e0["task_id"] == "task-test-001"
        assert e0["kind"] == "atom_run"
        assert e0["payload"]["atom_id"] == "a1"
        e1 = json.loads(lines[1])
        assert e1["kind"] == "crystallize"
        assert e1["payload"]["sig"] == "sig-x"

    def test_replay_missing_task_id_returns_nonzero_and_lists(self, tmp_path, capsys):
        trace_path = tmp_path / "trace.sqlite"
        trace = SqliteTraceStore(trace_path)
        trace.append(TraceEntry(
            task_id="existing-task", kind="atom_run",
            payload={"trace_ref": "t"},
            ts=1000.0, source="test", agent="",
        ))
        trace.close()

        rc = cmd_replay("nonexistent-task", trace_path=trace_path)
        assert rc == 1
        err = capsys.readouterr().err
        assert "nonexistent-task" in err
        assert "existing-task" in err  # 列出已有 task_id

    def test_replay_missing_trace_file_returns_2(self, tmp_path, capsys):
        rc = cmd_replay("any-task", trace_path=tmp_path / "no-such.sqlite")
        assert rc == 2
        err = capsys.readouterr().err
        assert "not found" in err or "不存在" in err

# ---- 门1 真机抓到:旧库迁移(缺 recall_count 列)----

def test_legacy_db_without_recall_count_migrates(tmp_path):
    """旧版 usage.sqlite(拍 9 前,无 recall_count 列)打开应自动 ALTER 补列,不崩。

    回归:门1 用真 key 跑 karvyloop run 时,升级用户的旧库在 recall_count_inc 处
    `OperationalError: no such column: recall_count` 硬崩。CREATE TABLE IF NOT EXISTS
    不给旧表补列 → 需幂等 ALTER 迁移。
    """
    import sqlite3
    from karvyloop.crystallize import SqliteUsageStore
    from karvyloop.schemas import UsageStats

    db = tmp_path / "legacy_usage.sqlite"
    # 造一个"旧 schema"表:故意不含 recall_count 列
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE usage_stats ("
        "sig TEXT PRIMARY KEY, usage_count INTEGER NOT NULL DEFAULT 0, "
        "last_used_at REAL NOT NULL DEFAULT 0, success_count INTEGER NOT NULL DEFAULT 0, "
        "failure_count INTEGER NOT NULL DEFAULT 0, param_variants_json TEXT NOT NULL DEFAULT '[]', "
        "steered_by_user_json TEXT NOT NULL DEFAULT '[]', archived INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute("INSERT INTO usage_stats (sig, usage_count) VALUES ('sig-old', 3)")
    conn.commit()
    conn.close()

    # 打开 = 触发迁移;不应抛
    store = SqliteUsageStore(db)
    cols = [r[1] for r in store._conn.execute("PRAGMA table_info(usage_stats)").fetchall()]
    assert "recall_count" in cols
    # recall_count_inc 不再崩(原 bug 点)
    store.recall_count_inc("sig-old")
    got = store.get("sig-old")
    assert got is not None and got.recall_count == 1
    # 旧数据保留
    assert got.usage_count == 3
    store.close()


def test_migration_idempotent_on_new_db(tmp_path):
    """新库(已含 recall_count)再开一次,幂等 ALTER 吞 OperationalError,不崩。"""
    from karvyloop.crystallize import SqliteUsageStore
    db = tmp_path / "fresh.sqlite"
    SqliteUsageStore(db).close()
    SqliteUsageStore(db).close()  # 第二次打开:列已存在,不应抛
