"""test_task_monitor — 本机执行任务监控(docs/80 #4 第一环)。

锁:陈旧检测(running 且久无进展→揪出 / 有近事件不揪 / done·error 忽略)、last_progress_ts、
一次 tick 的效果(标中断+blocked 事件+升卡)、幂等(第二 tick 不重弹)。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import task_monitor as tm  # noqa: E402
from karvyloop.console.tasks import TaskRegistry  # noqa: E402


NOW = 1_000_000.0


def _mk(status, started, last_ev_ts=None):
    d = {"id": None, "status": status, "started": started, "intent": "整理周报",
         "who": "小卡", "domain_id": "l0", "role": ""}
    if last_ev_ts is not None:
        d["last_event"] = {"ts": last_ev_ts, "kind": "step", "text": "x"}
    return d


# ---- detect_stalled / last_progress_ts(纯函数)----

def test_last_progress_ts_takes_latest_event():
    t = _mk("running", NOW - 5000, last_ev_ts=NOW - 100)
    assert tm._last_progress_ts(t) == NOW - 100   # 最新事件比 started 新
    t2 = _mk("running", NOW - 50)                  # 无事件 → 退 started
    assert tm._last_progress_ts(t2) == NOW - 50


def test_detect_stalled_flags_only_old_running():
    tasks = [
        _mk("running", NOW - 5000, last_ev_ts=NOW - 4000),  # 久无进展 → 停滞
        _mk("running", NOW - 5000, last_ev_ts=NOW - 60),    # 刚有进展 → 不停滞
        _mk("done", NOW - 9999),                            # 已完成 → 忽略
        _mk("error", NOW - 9999),                           # 已失败 → 忽略
    ]
    stalled = tm.detect_stalled(tasks, now=NOW, threshold=600)
    assert len(stalled) == 1
    assert stalled[0]["last_event"]["ts"] == NOW - 4000


def test_detect_stalled_recent_running_not_flagged():
    tasks = [_mk("running", NOW - 100)]   # 刚起 100s < 阈值 → 不误杀活着的慢任务
    assert tm.detect_stalled(tasks, now=NOW, threshold=600) == []


# ---- run_task_monitor(真 registry + 假 app)----

class _App:
    class state:  # noqa: N801
        pass


def _app_with_task(tmp_path, *, age_s):
    """真 TaskRegistry,种一条 running 任务,把它的 start 事件 ts 拨老到 age_s 前。"""
    reg = TaskRegistry(cap=10)
    tid = reg.start(who="小卡", intent="整理周报")
    rec = reg._by_id[tid]
    old = tm.time.time() - age_s
    rec.started = old
    for ev in rec.events:
        ev["ts"] = old
    app = _App()
    app.state = _App.state()
    app.state.task_registry = reg
    app.state.ws_clients = set()          # broadcast_proposal 读它;空=不实发,零 client
    app.state.proposal_registry = None
    return app, reg, tid


def test_monitor_marks_stalled_and_raises_once(tmp_path, monkeypatch):
    app, reg, tid = _app_with_task(tmp_path, age_s=2 * 3600)   # 2 小时无进展

    sent = []

    async def _fake_broadcast(a, prop, **k):
        sent.append(prop)
        return 0
    monkeypatch.setattr("karvyloop.console.proposals.broadcast_proposal", _fake_broadcast)

    n = asyncio.run(tm.run_task_monitor(app, threshold=600))
    assert n == 1
    # 标中断
    assert reg._by_id[tid].status == "error"
    assert "中断" in reg._by_id[tid].result
    # blocked 事件(实时可见)在
    assert any(e.get("kind") == "blocked" for e in reg._by_id[tid].events)
    # 升了一张重试卡,来源标对
    assert len(sent) == 1
    assert sent[0].payload.get("source") == "task_monitor.stalled"
    assert sent[0].context_ref.get("id") == tid

    # 幂等:第二 tick 不重弹(该任务已 error + 已在 seen)
    n2 = asyncio.run(tm.run_task_monitor(app, threshold=600))
    assert n2 == 0 and len(sent) == 1


def test_monitor_ignores_fresh_running(tmp_path, monkeypatch):
    app, reg, tid = _app_with_task(tmp_path, age_s=10)   # 刚起 10s
    monkeypatch.setattr("karvyloop.console.proposals.broadcast_proposal",
                        lambda *a, **k: asyncio.sleep(0))
    n = asyncio.run(tm.run_task_monitor(app, threshold=600))
    assert n == 0
    assert reg._by_id[tid].status == "running"   # 活着的慢任务不误杀
