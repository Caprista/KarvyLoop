"""test_crystallized_ts_memento — P1.5 灵魂后端口④:结晶时间戳 + 周五纪念物。

契约(形状冻结):
- /api/skills 每项加 `crystallized_ts`(nullable):crystallize 落 SKILL.md 时在
  frontmatter 记 `crystallized_ts:`;**加性**,老技能无标 → null(不伪造出生记录)。
- GET /api/desk/memento → {"week_label","tasks_done","skills_new","decisions","tokens_total"}
  (复用 weekly_digest 的确定性汇总;有 digest 水位读现成,不重算重的)。

AC:
- AC1 crystallize 真路径落盘 → frontmatter 带 crystallized_ts,可读回
- AC2 加性:build_skill_md 不传 created_ts → 无此行;老技能 API 返 null
- AC3 不破坏 511c6f6 verified 标逻辑:带 crystallized_ts 行时 mark_skill_verified 照常翻标
- AC4 /api/skills 暴露 crystallized_ts(新技能 float / 老技能 None)
- AC5 /api/desk/memento:水位+卡在 → 读现成 digest;否则确定性重建;形状冻结
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from karvyloop.console import build_console_app
from karvyloop.crystallize import (
    InMemoryUsageStore,
    VerifyStore,
    build_skill_md,
    crystallize,
    mark_skill_verified,
    observe,
    read_crystallized_ts,
    write_skill_md,
)
from karvyloop.karvy.observer import WorkbenchObserver
from karvyloop.schemas import AtomRun


def _run(intent: str, month: str, ts: float) -> AtomRun:
    return AtomRun(atom_id="a1", input={"intent": intent, "month": month},
                   output={"ok": True}, success=True, tool_calls=[],
                   trace_ref=f"t-{month}", ts=ts)


def _crystallize_one(tmp_path: Path, name: str = "monthly-report"):
    """走真结晶路径(两关都过)落一个 SKILL.md。"""
    from karvyloop.crystallize import compute_signature
    store, verify = InMemoryUsageStore(), VerifyStore()
    t0 = 1_700_000_000.0
    runs = [_run("monthly report", f"2026-{m:02d}", t0 + m * 100) for m in range(1, 5)]
    observe(runs, store, clock=lambda: t0 + 500)
    sig = compute_signature(runs[0])
    verify.mark_verified(sig, "t-2026-01", note="trace-based", clock=lambda: t0 + 500)
    skill = crystallize(
        sig, name=name, description="monthly report", body="# Steps\n1. do it",
        when_to_use="monthly report", arguments=[{"name": "month", "type": "string"}],
        store=store, verify=verify, skills_dir=tmp_path / "skills",
        scope="user", now=t0 + 600,
    )
    return skill, tmp_path / "skills" / name / "SKILL.md"


# ---- AC1/AC2: 落盘点写戳(加性) ----

def test_crystallize_writes_crystallized_ts(tmp_path):
    before = time.time()
    skill, path = _crystallize_one(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "crystallized_ts:" in text.split("\n---")[0]   # 在 frontmatter 里
    cts = read_crystallized_ts(text)
    assert cts is not None and before - 1 <= cts <= time.time() + 1
    # frontmatter 戳与内存态 created_at 同一个(不各 time.time() 各的)
    assert abs(cts - skill.created_at) < 0.01


def test_build_skill_md_without_created_ts_is_unchanged():
    md = build_skill_md("old-skill", "desc", "body", signature="sig-1",
                        verify_proof={"passed_at": 1, "verifier": "manual"},
                        trace_refs=[])
    assert "crystallized_ts" not in md          # 加性:不传不写
    assert read_crystallized_ts(md) is None     # 老技能 → null
    # 正文里恰好有同形行也不误读(只看 frontmatter 块)
    md2 = build_skill_md("x", "d", "crystallized_ts: 999", signature="s",
                         verify_proof={"passed_at": 1, "verifier": "manual"},
                         trace_refs=[])
    assert read_crystallized_ts(md2) is None


# ---- AC3: 不破坏 verified 标逻辑(511c6f6) ----

def test_mark_skill_verified_keeps_crystallized_ts(tmp_path):
    md = build_skill_md("s", "d", "body", signature="sig",
                        verify_proof={"passed_at": 1, "verifier": "auto"},
                        trace_refs=["t1"], verified=False, created_ts=123.5)
    p = write_skill_md(tmp_path / "s", md)
    assert mark_skill_verified(p) is True       # false → true 照常翻
    text = p.read_text(encoding="utf-8")
    assert "verified: true" in text
    assert read_crystallized_ts(text) == 123.5  # 戳一个字没动
    assert mark_skill_verified(p) is True       # 幂等照旧


# ---- AC4: /api/skills 暴露(新 float / 老 null) ----

def test_api_skills_exposes_crystallized_ts(tmp_path):
    from karvyloop.runtime.main_loop import MainLoop
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    ml = MainLoop(skills_dir=tmp_path / "skills")
    # 新技能:真结晶路径(带戳)
    _skill, _path = _crystallize_one(tmp_path, name="fresh-skill")
    # 老技能:无 crystallized_ts 行
    old_md = build_skill_md("legacy-skill", "old", "body", signature="sig-old",
                            verify_proof={"passed_at": 1, "verifier": "manual"},
                            trace_refs=[], when_to_use="legacy")
    write_skill_md(tmp_path / "skills" / "legacy-skill", old_md)
    ml.skill_index.rebuild_from_disk(ml.skills_dir)
    app.state.main_loop = ml
    skills = {s["name"]: s for s in TestClient(app).get("/api/skills").json()["skills"]}
    assert isinstance(skills["fresh-skill"]["crystallized_ts"], float)
    assert skills["legacy-skill"]["crystallized_ts"] is None   # 老技能无标 → null,不编


# ---- AC5: /api/desk/memento ----

MEMENTO_KEYS = {"week_label", "tasks_done", "skills_new", "decisions", "tokens_total"}


def test_memento_from_digest_projection():
    from karvyloop.cognition.weekly_digest import memento_from_digest
    m = memento_from_digest({
        "window": {"start_label": "2026-06-26", "end_label": "2026-07-03"},
        "tasks": {"succeeded": 12}, "skills": {"crystallized_count": 3},
        "decisions": {"total": 4}, "tokens": {"total": 56789},
    })
    assert m == {"week_label": "2026-06-26 → 2026-07-03", "tasks_done": 12,
                 "skills_new": 3, "decisions": 4, "tokens_total": 56789}


def test_api_desk_memento_computed_fallback():
    """无水位/无卡 → 确定性重建(零 LLM),形状冻结、不崩。"""
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    body = TestClient(app).get("/api/desk/memento").json()
    assert MEMENTO_KEYS <= set(body)
    assert body["source"] == "computed"
    assert body["tasks_done"] == 0 and body["skills_new"] == 0
    assert "→" in body["week_label"]


def test_api_desk_memento_reads_existing_digest(tmp_path, monkeypatch):
    """有 digest 水位且周报卡还挂着 → 直接读现成结构化 digest(不重算)。"""
    import asyncio
    from karvyloop.cognition import weekly_digest as wd
    from karvyloop.cognition.trace import TraceEntry, TraceStore
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry

    tick_path = tmp_path / "weekly_digest_tick.json"
    monkeypatch.setattr(wd, "_default_state_path", lambda: tick_path)

    now = time.time()
    trace = TraceStore()
    trace.append(TraceEntry(task_id="tk1", kind="atom_run",
                            payload={"success": True, "atom_id": "a1"}, ts=now - 100))
    trace.append(TraceEntry(task_id="tk1", kind="crystallize",
                            payload={"name": "s1", "sig": "sig1"}, ts=now - 50))
    reg = PendingProposalRegistry()
    res = asyncio.run(wd.weekly_digest_tick(trace=trace, registry=reg,
                                            state_path=tick_path, now=now))
    assert res["ran"] is True

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.proposal_registry = reg
    body = TestClient(app).get("/api/desk/memento").json()
    assert MEMENTO_KEYS <= set(body)
    assert body["source"] == "digest"          # 读的是现成 digest,没重算
    assert body["tasks_done"] == 1 and body["skills_new"] == 1
    assert body["decisions"] == 0 and body["tokens_total"] == 0
