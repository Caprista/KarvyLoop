"""test_pursuit_first_cut — 外环 Pursuit(招牌"闭环完整性")第一刀(docs/88 §9)。

覆盖:
- PursuitStore 落盘 / 读回 / 坏文件不崩 / active 过滤。
- pursuit_tick 集成:committed → mock ml.drive 跑一次 → gate 从 fail→pass 跨 tick → **自动 done**;
  revision_trigger 命中 → 升 REVISE 卡且**不自动改方向**;infeasible → 升现有不可行卡挂起。
- H2A 硬规则:KIND_PURSUIT_REVISE/COMMIT ∈ HIGH_RISK_KINDS 且**绝不被静音兑现**。
- verify_gate 求值**零 LLM**(mock gateway 断言零调用)。
- pursuit 派生 task 进 TaskRegistry 且按 pursuit_id 可过滤。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.pursuit import PursuitManager  # noqa: E402
from karvyloop.cognition.pursuit_store import (  # noqa: E402
    PursuitRecord, PursuitStore, new_pursuit_id,
)
from karvyloop.console.pursuit_tick import assemble_context, pursuit_tick  # noqa: E402
from karvyloop.console.proposal_handlers import build_proposal_handlers  # noqa: E402
from karvyloop.console.tasks import TaskRegistry  # noqa: E402
from karvyloop.karvy.proposal_registry import (  # noqa: E402
    KIND_INFEASIBLE_REPORT, KIND_PURSUIT_COMMIT, KIND_PURSUIT_REVISE,
    PendingProposalRegistry, proposal_for_pursuit_commit, proposal_for_pursuit_revise,
)
from karvyloop.karvy.silence import HIGH_RISK_KINDS, SilenceGrantStore, try_silence  # noqa: E402
from karvyloop.schemas import Pursuit  # noqa: E402


# ---------------------------------------------------------------- helpers
def _pursuit(gate: dict, *, statement="重构直到测试全绿", triggers=None, level="atom") -> Pursuit:
    return Pursuit(id=new_pursuit_id(level), level=level, statement=statement,
                   commitment_condition="", revision_triggers=list(triggers or []),
                   verify_gate=gate, status="active")


def _fake_app(tmp_path, *, main_loop=None):
    """最小 fake app.state:pursuit_store/manager + task_registry + registry + handlers。"""
    store = PursuitStore(tmp_path / "pursuits.json")
    mgr = PursuitManager(memory=None)
    reg = PendingProposalRegistry()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        pursuit_store=store, pursuit_manager=mgr,
        task_registry=TaskRegistry(),
        proposal_registry=reg,
        main_loop=main_loop,
        memory=None, trace=None,
        runtime_kwargs={"token": None, "sandbox": None, "gateway": None,
                        "workspace_root": str(tmp_path)},
        taste_predictions=None, decision_log=None,
        ws_clients=set(),
        pursuit_advance_interval_s=0.0,
    ))
    app.state.proposal_handlers = build_proposal_handlers(app)
    return app


def _commit(app, rec):
    """走承诺 handler 真路径把 Pursuit 升 committed(第一刀:人 ACCEPT=committed)。"""
    app.state.pursuit_store.put(rec)
    h = app.state.proposal_handlers[KIND_PURSUIT_COMMIT]
    card = proposal_for_pursuit_commit(pursuit_id=rec.id, statement=rec.pursuit.statement,
                                       gate_desc="x", ts=1.0)
    ok, _ = h(card)
    assert ok
    return app.state.pursuit_store.get(rec.id)


# ---------------------------------------------------------------- PursuitStore
def test_store_roundtrip(tmp_path):
    store = PursuitStore(tmp_path / "p.json")
    rec = PursuitRecord(_pursuit({"type": "file_exists", "path": "/x"}),
                        title="T", owner="karvy", domain_id="l0")
    rec.progress_note = "跑了一拍"
    rec.note_task("task123")
    store.put(rec)
    # 新 store 从同路径读回
    store2 = PursuitStore(tmp_path / "p.json")
    got = store2.get(rec.id)
    assert got is not None
    assert got.pursuit.statement == "重构直到测试全绿"
    assert got.pursuit.verify_gate == {"type": "file_exists", "path": "/x"}
    assert got.progress_note == "跑了一拍"
    assert "task123" in got.last_task_ids
    assert got.owner == "karvy"


def test_store_bad_file_does_not_crash(tmp_path):
    p = tmp_path / "p.json"
    p.write_text("{not valid json at all", encoding="utf-8")
    store = PursuitStore(p)   # 不抛
    assert store.all() == []
    assert store.active_count() == 0


def test_store_active_excludes_terminal(tmp_path):
    store = PursuitStore(tmp_path / "p.json")
    a = PursuitRecord(_pursuit({"type": "file_exists", "path": "/a"}))
    b = PursuitRecord(_pursuit({"type": "file_exists", "path": "/b"}))
    b.pursuit = b.pursuit.model_copy(update={"status": "done"})
    c = PursuitRecord(_pursuit({"type": "file_exists", "path": "/c"}))
    c.pursuit = c.pursuit.model_copy(update={"status": "dropped"})
    for r in (a, b, c):
        store.put(r)
    ids = {r.id for r in store.active()}
    assert a.id in ids and b.id not in ids and c.id not in ids
    assert store.active_count() == 1


# ---------------------------------------------------------------- 招牌:zero-LLM gate
def test_verify_gate_is_zero_llm(tmp_path):
    """verify_gate 求值绝不触发 LLM —— 塞一个"一被调用就炸"的 gateway,is_done 照样跑出结果。"""
    calls = {"n": 0}

    class _PoisonGateway:
        def __getattr__(self, name):
            def _boom(*a, **k):
                calls["n"] += 1
                raise AssertionError("verify_gate 触发了 LLM 调用(招牌硬保证被破坏)")
            return _boom

    mgr = PursuitManager(memory=None)
    target = tmp_path / "hit.txt"
    p = _pursuit({"type": "file_exists", "path": str(target)})
    # gateway 在 context 里也不会被碰(is_done 只跑确定性 gate);先 fail 后 pass。
    ctx = {"gateway": _PoisonGateway()}
    assert mgr.is_done(p, ctx) is False
    target.write_text("done", encoding="utf-8")
    assert mgr.is_done(p, ctx) is True
    # test_pass gate 也零 LLM(跑子进程,不问模型)——结果真假无关紧要,只验 gateway 从未被碰。
    p2 = _pursuit({"type": "test_pass", "cmd": "python -c pass"})
    mgr.is_done(p2, ctx)
    assert calls["n"] == 0   # gateway 从未被碰(招牌硬保证:verify_gate 求值零 LLM)


# ---------------------------------------------------------------- H2A 硬规则
def test_pursuit_kinds_in_high_risk():
    assert "pursuit_commit" in HIGH_RISK_KINDS
    assert "pursuit_revise" in HIGH_RISK_KINDS


def test_pursuit_revise_never_silenced(tmp_path):
    """KIND_PURSUIT_REVISE 绝不被"挣来的静音"自动兑现 —— 即便伪造一个已授权桶也拦住。"""
    # ① grant 硬地板:高危 kind 授不出权
    gstore = SilenceGrantStore(tmp_path / "grants.json")
    assert gstore.grant("pursuit_revise") is None
    assert gstore.grant("pursuit_commit") is None
    # ② 伪造一个授权 grant 硬塞进桶,try_silence 仍返 False(HIGH_RISK 短路先于查授权)
    from karvyloop.karvy.silence import bucket_key
    import time as _t
    b = bucket_key("pursuit_revise", "")
    gstore._grants[b] = {"kind": "pursuit_revise", "domain": "",
                         "granted_at": _t.time(), "expires_at": _t.time() + 99999,
                         "n": 99, "hits": 99, "revoked_at": None, "revoke_reason": ""}
    app = _fake_app(tmp_path)
    app.state.silence_grants = gstore
    card = proposal_for_pursuit_revise(pursuit_id="atom:x", statement="s",
                                       revision_reason="r", ts=1.0)
    assert try_silence(app, card) is False


# ---------------------------------------------------------------- tick: 自动 done
def test_tick_committed_gate_fail_then_pass_auto_done(tmp_path, monkeypatch):
    """committed → gate fail → drive 造出 gate 目标文件 → **当拍再验一次** → 自动 done(无人干预)。"""
    target = tmp_path / "green.txt"

    class _Result:
        terminal = "completed"
        error = ""
        text = "已重构"
        sig = ""
        task_id = "drive-trace-77"   # 真伤2:回填这个到派生 task 的 trace_id

    class _ML:
        trace = None
        def __init__(self):
            self.calls = 0
        def drive(self, intent, slow_brain=None):
            self.calls += 1
            target.write_text("ok", encoding="utf-8")   # 这一拍把 gate 从 fail 翻 pass
            return _Result()

    ml = _ML()
    import karvyloop.runtime.main_loop as ml_mod
    monkeypatch.setattr(ml_mod, "forge_slow_brain_factory", lambda **kw: (lambda *a, **k: ("", None)))
    app = _fake_app(tmp_path, main_loop=ml)
    rec = PursuitRecord(_pursuit({"type": "file_exists", "path": str(target)}),
                        owner="karvy", domain_id="l0")
    rec = _commit(app, rec)
    assert rec.pursuit.status == "committed"

    # 一拍搞定:gate fail → drive 造文件 → 当拍再验 → 自动 done(same-tick completion)
    c1 = asyncio.run(pursuit_tick(app))
    assert c1["advanced"] == 1 and c1["done"] == 1
    assert ml.calls == 1
    r1 = app.state.pursuit_store.get(rec.id)
    assert r1.pursuit.status == "done"
    assert r1.advances == 1
    assert r1.id not in {r.id for r in app.state.pursuit_store.active()}   # 归档退出活跃集
    # 派生 task 进 TaskRegistry 且带 pursuit_id;真伤2:advance task 的 trace_id 已回填
    tasks = [t for t in app.state.task_registry.list() if t.get("pursuit_id") == rec.id]
    assert len(tasks) >= 2   # 推进 task + 完成回执 task
    advance_tasks = [t for t in tasks if t.get("trace_id") == "drive-trace-77"]
    assert len(advance_tasks) == 1, "派生推进 task 的 trace_id 未回填(真伤2)"
    # 完成回执:任务看板多一条 done 记录(复用任务账)
    done_receipts = [t for t in app.state.task_registry.list()
                     if t.get("pursuit_id") == rec.id and t.get("status") == "done"]
    assert done_receipts

    # 已 done → 再 tick 不动
    c2 = asyncio.run(pursuit_tick(app))
    assert c2 == {"checked": 0, "done": 0, "revised": 0, "advanced": 0, "infeasible": 0}


# ---------------------------------------------------------------- tick: 修订不自动改方向
def test_tick_revision_trigger_raises_card_no_auto_change(tmp_path, monkeypatch):
    monkeypatch.setattr("karvyloop.runtime.main_loop.forge_slow_brain_factory",
                        lambda **kw: (lambda *a, **k: ("", None)))
    app = _fake_app(tmp_path, main_loop=types.SimpleNamespace(trace=None))
    p = _pursuit({"type": "file_exists", "path": str(tmp_path / "never")},
                 statement="盯竞品", triggers=["budget_exhausted == true"])
    rec = PursuitRecord(p, owner="karvy", domain_id="l0")
    rec = _commit(app, rec)
    # 模拟上次推进耗尽预算(确定性探针)→ 触发修订
    rec.last_infeasible = True
    app.state.pursuit_store.put(rec)
    before = rec.pursuit.statement

    c = asyncio.run(pursuit_tick(app))
    assert c["revised"] == 1 and c["advanced"] == 0
    r = app.state.pursuit_store.get(rec.id)
    assert r.pursuit.status == "revised"
    assert r.pursuit.statement == before   # **不自动改方向**
    # 升了一张 REVISE 卡(挂待决表)
    cards = [pr for pr in app.state.proposal_registry.pending()
             if getattr(pr, "kind", "") == KIND_PURSUIT_REVISE]
    assert len(cards) == 1
    assert r.id in (cards[0].payload or {}).get("pursuit_id", "")


# ---------------------------------------------------------------- tick: infeasible → 现有不可行卡
def test_tick_infeasible_raises_existing_report_card(tmp_path, monkeypatch):
    class _Result:
        terminal = "max_turns"   # 没跑完 → replan;耗尽预算 → infeasible
        error = ""
        text = ""
        sig = ""
        task_id = ""

    class _ML:
        trace = None
        def drive(self, intent, slow_brain=None):
            return _Result()

    monkeypatch.setattr("karvyloop.runtime.main_loop.forge_slow_brain_factory",
                        lambda **kw: (lambda *a, **k: ("", None)))
    app = _fake_app(tmp_path, main_loop=_ML())
    rec = PursuitRecord(_pursuit({"type": "file_exists", "path": str(tmp_path / "never")}),
                        owner="karvy", domain_id="l0")
    rec = _commit(app, rec)

    c = asyncio.run(pursuit_tick(app))
    assert c["infeasible"] == 1
    r = app.state.pursuit_store.get(rec.id)
    assert r.suspended is True
    cards = [pr for pr in app.state.proposal_registry.pending()
             if getattr(pr, "kind", "") == KIND_INFEASIBLE_REPORT]
    assert len(cards) == 1
    # 挂起后不再自动推进(仍确定性验完成,但不 pursue)
    c2 = asyncio.run(pursuit_tick(app))
    assert c2["advanced"] == 0


# ---------------------------------------------------------------- context 白名单
def test_assemble_context_whitelist(tmp_path):
    app = _fake_app(tmp_path)
    rec = PursuitRecord(_pursuit({"type": "file_exists", "path": "/x"}))
    rec.last_terminal = "completed"
    rec.last_verdict_passed = True
    rec.last_infeasible = False
    rec.consecutive_failures = 2
    ctx = assemble_context(app, rec)
    assert set(ctx.keys()) == {"terminal", "verdict_passed", "done",
                               "budget_exhausted", "days_running", "infra_dead",
                               "consecutive_failures"}
    assert ctx["terminal"] == "completed"
    assert ctx["verdict_passed"] is True
    assert ctx["consecutive_failures"] == 2   # P2 残余小扩:连败计数进白名单,revision_triggers 可引


# ---------------------------------------------------------------- commit handler → committed
def test_commit_handler_sets_committed(tmp_path):
    app = _fake_app(tmp_path)
    rec = PursuitRecord(_pursuit({"type": "file_exists", "path": "/x"}))
    app.state.pursuit_store.put(rec)
    assert rec.pursuit.status == "active"
    h = app.state.proposal_handlers[KIND_PURSUIT_COMMIT]
    card = proposal_for_pursuit_commit(pursuit_id=rec.id, statement=rec.pursuit.statement,
                                       gate_desc="x", ts=1.0)
    ok, _ = h(card)
    assert ok
    assert app.state.pursuit_store.get(rec.id).pursuit.status == "committed"


def test_revise_handler_drops_pursuit(tmp_path):
    app = _fake_app(tmp_path)
    rec = PursuitRecord(_pursuit({"type": "file_exists", "path": "/x"}))
    rec.pursuit = rec.pursuit.model_copy(update={"status": "committed"})
    app.state.pursuit_store.put(rec)
    h = app.state.proposal_handlers[KIND_PURSUIT_REVISE]
    card = proposal_for_pursuit_revise(pursuit_id=rec.id, statement=rec.pursuit.statement,
                                       revision_reason="r", ts=1.0)
    ok, _ = h(card)
    assert ok
    assert app.state.pursuit_store.get(rec.id).pursuit.status == "dropped"


# ---------------------------------------------------------------- routes (HTTP)
def _console_client(tmp_path):
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.pursuit_store = PursuitStore(tmp_path / "pursuits.json")
    app.state.pursuit_manager = PursuitManager(memory=None)
    app.state.proposal_registry = PendingProposalRegistry()
    return app, TestClient(app)


def test_route_create_lists_and_details(tmp_path):
    app, client = _console_client(tmp_path)
    r = client.post("/api/pursuit", json={
        "statement": "重构直到 pytest tests/foo 全绿",
        "verify_gate": {"type": "test_pass", "cmd": "pytest -q tests/foo"},
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["pursuit_id"]
    assert data["commit_proposal_id"]   # 升了承诺卡
    # 承诺卡进了待决表(H2A:承诺是决策)
    assert any(getattr(p, "kind", "") == KIND_PURSUIT_COMMIT
               for p in app.state.proposal_registry.pending())
    # 列
    lst = client.get("/api/pursuits").json()
    assert lst["active_count"] == 1
    assert lst["pursuits"][0]["status"] == "active"
    # 详情
    det = client.get(f"/api/pursuit/{data['pursuit_id']}").json()
    assert det["ok"] is True
    assert det["pursuit"]["statement"].startswith("重构直到")
    assert det["pursuit"]["tasks"] == []   # 还没推进,无派生 task


def test_route_rejects_unsupported_gate(tmp_path):
    app, client = _console_client(tmp_path)
    r = client.post("/api/pursuit", json={
        "statement": "x", "verify_gate": {"type": "predicate", "expr": "a == b"}})
    assert r.status_code == 200
    assert r.json()["ok"] is False   # 第一刀只 test_pass / file_exists


# ---------------------------------------------------------------- 真伤1①:成本硬地板
def test_tick_max_advances_hard_floor(tmp_path, monkeypatch):
    """gate 永不过 + drive 老"成功" → 真推进达 PURSUIT_MAX_ADVANCES 上限 → 挂起 + 升 REVISE 卡
    (不靠用户 revision_trigger 的确定性兜底,防无限烧钱)。"""
    from karvyloop.console.pursuit_tick import PURSUIT_MAX_ADVANCES

    class _Result:
        terminal = "completed"; error = ""; text = "自述做完了"; sig = ""; task_id = ""

    class _ML:
        trace = None
        def __init__(self): self.calls = 0
        def drive(self, intent, slow_brain=None):
            self.calls += 1
            return _Result()   # 老"成功",但从不满足 gate

    monkeypatch.setattr("karvyloop.runtime.main_loop.forge_slow_brain_factory",
                        lambda **kw: (lambda *a, **k: ("", None)))
    app = _fake_app(tmp_path, main_loop=_ML())
    rec = PursuitRecord(_pursuit({"type": "file_exists", "path": str(tmp_path / "never")}),
                        owner="karvy", domain_id="l0")
    rec = _commit(app, rec)

    # 跑到上限前一拍:仍 committed,未挂起
    for _ in range(PURSUIT_MAX_ADVANCES - 1):
        asyncio.run(pursuit_tick(app))
    r = app.state.pursuit_store.get(rec.id)
    assert r.pursuit.status == "committed" and r.suspended is False
    assert r.advances == PURSUIT_MAX_ADVANCES - 1
    # 无 REVISE 卡(还没到上限)
    assert not [p for p in app.state.proposal_registry.pending()
                if getattr(p, "kind", "") == KIND_PURSUIT_REVISE]

    # 达上限那一拍 → 挂起 + 升 REVISE 卡
    c = asyncio.run(pursuit_tick(app))
    assert c["revised"] == 1
    r = app.state.pursuit_store.get(rec.id)
    assert r.advances == PURSUIT_MAX_ADVANCES
    assert r.suspended is True
    assert r.pursuit.status == "revised"
    cards = [p for p in app.state.proposal_registry.pending()
             if getattr(p, "kind", "") == KIND_PURSUIT_REVISE]
    assert len(cards) == 1
    assert str(PURSUIT_MAX_ADVANCES) in (cards[0].basis or "") or "推进" in (cards[0].basis or "")

    # 挂起后不再推进(drive 不再被调),但仍是终态外的挂起
    drives_at_cap = app.state.main_loop.calls
    asyncio.run(pursuit_tick(app))
    assert app.state.main_loop.calls == drives_at_cap   # 不再 pursue


# ---------------------------------------------------------------- 真伤1②:节流不被异常旁路
def test_tick_throttle_not_bypassed_on_exception(tmp_path, monkeypatch):
    """pursue 抛异常时:last_advance_ts 仍写入 → 6h 节流窗内不再重试(不是每 tick 一次)。
    且异常不计入 advances(免虚高)。"""
    class _BoomML:
        trace = None
        def __init__(self): self.calls = 0
        def drive(self, intent, slow_brain=None):
            self.calls += 1
            raise RuntimeError("慢脑中途炸")

    monkeypatch.setattr("karvyloop.runtime.main_loop.forge_slow_brain_factory",
                        lambda **kw: (lambda *a, **k: ("", None)))
    boom = _BoomML()
    app = _fake_app(tmp_path, main_loop=boom)
    app.state.pursuit_advance_interval_s = 6 * 3600   # 生产节流开
    rec = PursuitRecord(_pursuit({"type": "file_exists", "path": str(tmp_path / "never")}),
                        owner="karvy", domain_id="l0")
    rec = _commit(app, rec)

    t0 = time.time()
    for k in range(6):   # 1 小时 6 个 10min tick —— 6h 节流下只该真推进 1 次
        asyncio.run(pursuit_tick(app, now=t0 + k * 600))
    assert boom.calls == 1, f"节流被旁路:drive 被调 {boom.calls} 次"
    r = app.state.pursuit_store.get(rec.id)
    assert r.last_advance_ts >= t0            # 异常路径也写了节流戳
    assert r.advances == 0                    # 异常不计入 advances


# ---------------------------------------------------------------- P2 残余:连续失败硬地板
def test_tick_consecutive_failures_suspend_and_card(tmp_path, monkeypatch):
    """pursue 每拍都炸 → advances 永不 +1(旧硬地板永不触发)→ 连败计数在第 N 拍**精确**触发:
    挂起 + 升同款 REVISE 卡 + 之后 tick 不再 drive(关死"节流上限无限静默重试")。"""
    from karvyloop.console.pursuit_tick import PURSUIT_MAX_CONSECUTIVE_FAILURES

    class _BoomML:
        trace = None
        def __init__(self): self.calls = 0
        def drive(self, intent, slow_brain=None):
            self.calls += 1
            raise RuntimeError("每拍都炸")

    monkeypatch.setattr("karvyloop.runtime.main_loop.forge_slow_brain_factory",
                        lambda **kw: (lambda *a, **k: ("", None)))
    boom = _BoomML()
    app = _fake_app(tmp_path, main_loop=boom)   # interval=0 → 无节流,逐拍推进
    rec = PursuitRecord(_pursuit({"type": "file_exists", "path": str(tmp_path / "never")}),
                        owner="karvy", domain_id="l0")
    rec = _commit(app, rec)

    # 上限前一拍:仍 committed 未挂起、无卡;advances 恒 0(异常不计)= 旧硬地板确实盖不住
    for _ in range(PURSUIT_MAX_CONSECUTIVE_FAILURES - 1):
        asyncio.run(pursuit_tick(app))
    r = app.state.pursuit_store.get(rec.id)
    assert r.pursuit.status == "committed" and r.suspended is False
    assert r.consecutive_failures == PURSUIT_MAX_CONSECUTIVE_FAILURES - 1
    assert r.advances == 0
    assert not [p for p in app.state.proposal_registry.pending()
                if getattr(p, "kind", "") == KIND_PURSUIT_REVISE]

    # 第 N 拍 → 精确触发:挂起 + 升 REVISE 卡
    c = asyncio.run(pursuit_tick(app))
    assert c["revised"] == 1
    r = app.state.pursuit_store.get(rec.id)
    assert r.consecutive_failures == PURSUIT_MAX_CONSECUTIVE_FAILURES
    assert r.suspended is True and r.pursuit.status == "revised"
    cards = [p for p in app.state.proposal_registry.pending()
             if getattr(p, "kind", "") == KIND_PURSUIT_REVISE]
    assert len(cards) == 1
    assert r.id in (cards[0].payload or {}).get("pursuit_id", "")

    # 挂起后不再 drive(无限静默重试被关死)
    calls_at_cap = boom.calls
    asyncio.run(pursuit_tick(app))
    assert boom.calls == calls_at_cap


def test_tick_failure_count_resets_on_success(tmp_path, monkeypatch):
    """失败 3 拍后真推进成功 → 计数清零(地板只逮"连续"失败,不背旧账)。"""
    class _Result:
        terminal = "completed"; error = ""; text = "跑通了"; sig = ""; task_id = ""

    class _FlakyML:
        trace = None
        def __init__(self, boom_times): self.calls = 0; self.boom_times = boom_times
        def drive(self, intent, slow_brain=None):
            self.calls += 1
            if self.calls <= self.boom_times:
                raise RuntimeError("前几拍炸")
            return _Result()   # 之后成功(gate 仍不满足 → 不 done,留在 committed)

    monkeypatch.setattr("karvyloop.runtime.main_loop.forge_slow_brain_factory",
                        lambda **kw: (lambda *a, **k: ("", None)))
    app = _fake_app(tmp_path, main_loop=_FlakyML(3))
    rec = PursuitRecord(_pursuit({"type": "file_exists", "path": str(tmp_path / "never")}),
                        owner="karvy", domain_id="l0")
    rec = _commit(app, rec)

    for _ in range(3):
        asyncio.run(pursuit_tick(app))
    assert app.state.pursuit_store.get(rec.id).consecutive_failures == 3
    asyncio.run(pursuit_tick(app))   # 第 4 拍:真推进成功
    r = app.state.pursuit_store.get(rec.id)
    assert r.consecutive_failures == 0        # 清零
    assert r.advances == 1                    # 真推进才 +1
    assert r.pursuit.status == "committed" and r.suspended is False
    assert not [p for p in app.state.proposal_registry.pending()
                if getattr(p, "kind", "") == KIND_PURSUIT_REVISE]


def test_tick_infra_dead_not_counted_as_pursuit_failure(tmp_path, monkeypatch):
    """确定性 infra-dead(is_infra_dead terminal)不算 pursuit 的错:连败计数不 +1 也不清零。"""
    class _InfraResult:
        terminal = "infra_dead"; error = ""; text = ""; sig = ""; task_id = ""

    class _ScriptML:
        trace = None
        def __init__(self, script): self.script = list(script)
        def drive(self, intent, slow_brain=None):
            step = self.script.pop(0)
            if step == "boom":
                raise RuntimeError("炸")
            return _InfraResult()

    monkeypatch.setattr("karvyloop.runtime.main_loop.forge_slow_brain_factory",
                        lambda **kw: (lambda *a, **k: ("", None)))
    app = _fake_app(tmp_path, main_loop=_ScriptML(["boom", "boom", "infra"]))
    rec = PursuitRecord(_pursuit({"type": "file_exists", "path": str(tmp_path / "never")}),
                        owner="karvy", domain_id="l0")
    rec = _commit(app, rec)

    asyncio.run(pursuit_tick(app))
    asyncio.run(pursuit_tick(app))
    assert app.state.pursuit_store.get(rec.id).consecutive_failures == 2
    asyncio.run(pursuit_tick(app))   # infra-dead 拍
    r = app.state.pursuit_store.get(rec.id)
    assert r.consecutive_failures == 2   # 不 +1(不怪 pursuit)也不清零(没成功)
    assert r.suspended is False and r.pursuit.status == "committed"


def test_store_old_json_without_consecutive_failures_defaults_zero(tmp_path):
    """老 JSON(第一刀落的盘,无 consecutive_failures 键)读回不炸、默认 0;新值可落盘读回。"""
    import json as _json
    p = tmp_path / "p.json"
    store = PursuitStore(p)
    rec = PursuitRecord(_pursuit({"type": "file_exists", "path": "/x"}))
    store.put(rec)
    # 模拟老盘:把新键删掉
    data = _json.loads(p.read_text(encoding="utf-8"))
    for d in data:
        d.pop("consecutive_failures", None)
    p.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
    store2 = PursuitStore(p)
    got = store2.get(rec.id)
    assert got is not None
    assert got.consecutive_failures == 0
    # 新值 roundtrip
    got.consecutive_failures = 3
    store2.put(got)
    assert PursuitStore(p).get(rec.id).consecutive_failures == 3


# ---------------------------------------------------------------- 真伤3:平台感知 split
def test_split_test_pass_cmd_platform_aware():
    import os as _os
    from karvyloop.cognition.pursuit import split_test_pass_cmd
    # 引号参数:两平台都应剥壳
    assert split_test_pass_cmd('python -c "import sys"') == ["python", "-c", "import sys"]
    if _os.name == "nt":
        # 反斜杠路径:Windows 上必须保真(旧 POSIX shlex 会拆碎成 C:Usersx.py)
        assert split_test_pass_cmd(r"python C:\Users\ch\x.py") == ["python", r"C:\Users\ch\x.py"]


def test_route_rejects_unsplittable_test_pass_cmd(tmp_path):
    app, client = _console_client(tmp_path)
    # 未闭合引号 → 拆不出可执行命令 → 400 人话(别静默进库后每 tick FileNotFoundError 永红)
    r = client.post("/api/pursuit", json={
        "statement": "x", "verify_gate": {"type": "test_pass", "cmd": 'python "unclosed'}})
    assert r.status_code == 200
    assert r.json()["ok"] is False
