"""test_mesh_compact — mesh 日志 compaction + seen 台账清理(记账债,保守第一刀)。

语义锁:
① 只删「task-heartbeat + 该任务已 task-done + 早于 N 天」;未 done 任务的心跳一条不动;
   N 天内的心跳不删;坏行原样保留(压缩不顺手清尸)。
② 硬护栏:每设备最高 HLC 事件永不删 —— frontier 不回退,对端不会把删掉的心跳重发回来,
   设备的"最新事件"也不会因恰好是心跳而从 mesh 蒸发。
③ 压缩前后 materialize 的 status/result 逐任务一致(done 终态无条件赢;DONE 任务的
   claimer/lease 是死字段,允许漂移且有测试钉住这个诚实边界);未 done 任务整态全等。
④ 压缩后 frontier 逐设备不变;delta 对落后对端仍收敛(视图级),全量对端不复活已删心跳。
⑤ seen 台账只清 ST_DONE 的 id;reclaimable 留着防重弹;查无此 id 的保守保留。
⑥ 原子性:replace 半途崩 = 老文件原样 + tmp 不残留。
⑦ mesh_tick 低频挂载:首轮跑(启动清积压)并带回 {scanned, dropped_heartbeats, seen_pruned};
   周期没到不跑;超行数阈值(字节代理)提早跑;维护失败绝不挡同步语义。
测试身份一律 FAKE。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import karvyloop.mesh.compact as compact  # noqa: E402
from karvyloop.mesh.compact import compact_mesh_log  # noqa: E402
from karvyloop.mesh.store import MeshLogStore  # noqa: E402
from karvyloop.mesh.synclog import MeshLog  # noqa: E402
from karvyloop.mesh.tasks import (  # noqa: E402
    K_DONE, K_HEARTBEAT, K_OFFER, ST_DONE, ST_RECLAIMABLE,
    claim_task, complete_task, heartbeat_task, materialize_tasks, offer_task,
)

A = "dev-A-FAKE"
B = "dev-B-FAKE"
DAY_MS = 24 * 60 * 60 * 1000
MIN5 = 5 * 60 * 1000
T0 = 1_700_000_000_000                                        # 起始墙钟(毫秒,mesh 单位契约)
NOW = T0 + compact.COMPACT_HEARTBEAT_MIN_AGE_DAYS * DAY_MS + DAY_MS   # 8 天后:T0 心跳全过年龄门
LEASE_MS = 15 * 60 * 1000.0


def _seed_done_with_heartbeats(tmp, *, hb=3, task_id="t1"):
    """A 设备:offer+claim → 心跳×hb(每 5min)→ done → 尾随一条 trace
    (让心跳不是 A 的最高 HLC 事件,可删性不被硬护栏遮住)。共 4+hb 条。"""
    log = MeshLog(A)
    offer_task(log, task_id, [], {"intent": "跑每日汇总", "source_device": A},
               wall=T0, lease_s=LEASE_MS)
    claim_task(log, task_id, wall=T0)
    for i in range(hb):
        heartbeat_task(log, task_id, wall=T0 + (i + 1) * MIN5)
    complete_task(log, task_id, {"status": "done", "result": "ok"}, wall=T0 + 12 * MIN5)
    log.append("trace", {"note": "之后还有别的事件"}, wall=T0 + 13 * MIN5)
    MeshLogStore(tmp).append(log.entries())
    return log


# ---- ① 删什么/不删什么 ----

def test_drops_old_heartbeats_of_done_task(tmp_path):
    _seed_done_with_heartbeats(tmp_path, hb=3)
    out = compact_mesh_log(tmp_path, now_ms=NOW)
    assert out == {"scanned": 7, "dropped_heartbeats": 3}
    evs = MeshLogStore(tmp_path).load_events()
    assert [e for e in evs if e.kind == K_HEARTBEAT] == []
    kinds = {e.kind for e in evs}
    assert K_OFFER in kinds and K_DONE in kinds and "trace" in kinds   # 只删心跳,其余全留


def test_undone_task_heartbeats_untouched(tmp_path):
    """没 done 的任务一条心跳都不动(哪怕陈年、哪怕不是 frontier)——宁少删勿破租约语义。"""
    log = MeshLog(A)
    offer_task(log, "t-open", [], {"intent": "还在跑"}, wall=T0, lease_s=LEASE_MS)
    claim_task(log, "t-open", wall=T0)
    for i in range(4):
        heartbeat_task(log, "t-open", wall=T0 + (i + 1) * MIN5)
    log.append("trace", {}, wall=T0 + 10 * MIN5)              # 心跳不是 frontier 也照样不删
    MeshLogStore(tmp_path).append(log.entries())
    before = [e.event_id for e in MeshLogStore(tmp_path).load_events()]
    out = compact_mesh_log(tmp_path, now_ms=NOW)
    assert out["dropped_heartbeats"] == 0
    assert [e.event_id for e in MeshLogStore(tmp_path).load_events()] == before


def test_recent_heartbeats_kept(tmp_path):
    """N 天内的心跳不删(等各对端都见过再删;年龄门同时保证被删心跳对"现在"的租约判定零影响)。"""
    log = MeshLog(A)
    offer_task(log, "t3", [], {"intent": "x"}, wall=NOW - DAY_MS - MIN5, lease_s=LEASE_MS)
    claim_task(log, "t3", wall=NOW - DAY_MS - MIN5)
    heartbeat_task(log, "t3", wall=NOW - DAY_MS)              # 1 天前(< 7 天年龄门)
    complete_task(log, "t3", {"status": "done"}, wall=NOW - DAY_MS + MIN5)
    log.append("trace", {}, wall=NOW - DAY_MS + 2 * MIN5)
    MeshLogStore(tmp_path).append(log.entries())
    out = compact_mesh_log(tmp_path, now_ms=NOW)
    assert out["dropped_heartbeats"] == 0
    assert any(e.kind == K_HEARTBEAT for e in MeshLogStore(tmp_path).load_events())


def test_corrupt_lines_preserved_verbatim(tmp_path):
    """坏行不是 compaction 的猎物:原样保留(防御式跳读是 load 的事,压缩不顺手清尸)。"""
    _seed_done_with_heartbeats(tmp_path)
    store = MeshLogStore(tmp_path)
    with store.path.open("a", encoding="utf-8") as f:
        f.write("{half-written garbage\n")
    out = compact_mesh_log(tmp_path, now_ms=NOW)
    assert out == {"scanned": 8, "dropped_heartbeats": 3}
    assert "{half-written garbage" in store.path.read_text(encoding="utf-8")


# ---- ② 硬护栏:frontier 事件永不删 ----

def test_device_frontier_event_never_dropped(tmp_path):
    """B 的最高 HLC 事件恰是"已 done 任务的陈年心跳"→ 硬护栏留下:frontier 不回退
    (对端不会当"你缺的"重发),且 B 若再不上线,它的最新事件仍可由别人替它传播。"""
    log_a = MeshLog(A)
    offer_task(log_a, "t2", [], {"intent": "x"}, wall=T0, lease_s=LEASE_MS)
    complete_task(log_a, "t2", {"status": "done"}, wall=T0 + 10 * MIN5)
    log_b = MeshLog(B)
    claim_task(log_b, "t2", wall=T0 + 1000)
    heartbeat_task(log_b, "t2", wall=T0 + MIN5)               # B 的最高 HLC 事件 = 陈年心跳
    store = MeshLogStore(tmp_path)
    store.append(log_a.entries())
    store.append(log_b.entries())
    out = compact_mesh_log(tmp_path, now_ms=NOW)
    assert out["dropped_heartbeats"] == 0                     # 唯一候选被硬护栏拦下
    evs = store.load_events()
    assert any(e.kind == K_HEARTBEAT and e.device_id == B for e in evs)


# ---- ③ 材料化不变 + ④ frontier/delta 收敛 ----

def test_materialize_status_and_result_unchanged(tmp_path):
    """压缩前后每个任务 status/result 逐一相同;未 done 任务整个 TaskState 全等。
    对抗构造:B 的竞争 claim 落在「A 心跳续出来的租」窗口里 —— 删心跳会翻 claim 裁决,
    但只漂移 DONE 任务的 claimer(死字段:发布对账/接活扫描对 DONE 从不读它)。"""
    log_a = MeshLog(A)
    offer_task(log_a, "t1", [], {"intent": "x"}, wall=T0, lease_s=LEASE_MS)
    claim_task(log_a, "t1", wall=T0)                          # 租到 T0+15min
    heartbeat_task(log_a, "t1", wall=T0 + 14 * 60 * 1000)     # 续到 T0+29min
    complete_task(log_a, "t1", {"status": "done", "result": "ok"}, wall=T0 + 60 * 60 * 1000)
    offer_task(log_a, "t-open", [], {"intent": "还在跑"}, wall=T0 + 1, lease_s=LEASE_MS)
    claim_task(log_a, "t-open", wall=T0 + 2)
    heartbeat_task(log_a, "t-open", wall=T0 + MIN5)           # 未 done 对照组(最后=A frontier)
    log_b = MeshLog(B)
    claim_task(log_b, "t1", wall=T0 + 20 * 60 * 1000)         # 有心跳→抢不到;删了心跳→抢到
    store = MeshLogStore(tmp_path)
    store.append(log_a.entries())
    store.append(log_b.entries())
    before = materialize_tasks(store.load_events(), now=NOW)
    out = compact_mesh_log(tmp_path, now_ms=NOW)
    assert out["dropped_heartbeats"] == 1                     # 只有 t1 那条(t-open 的不动)
    after = materialize_tasks(store.load_events(), now=NOW)
    assert after.keys() == before.keys()
    for tid in before:
        assert after[tid].status == before[tid].status, tid
        assert after[tid].result == before[tid].result, tid
    assert after["t-open"] == before["t-open"]                # 未 done:整态全等(一条没动)
    # 钉住诚实边界:DONE 任务的 claimer 允许因删心跳而漂移(死字段,无任何读者)
    assert before["t1"].status == ST_DONE
    assert before["t1"].claimer == A and after["t1"].claimer == B


def test_frontier_unchanged_after_compaction(tmp_path):
    _seed_done_with_heartbeats(tmp_path)
    store = MeshLogStore(tmp_path)
    before = store.open_log(A).frontier()
    out = compact_mesh_log(tmp_path, now_ms=NOW)
    assert out["dropped_heartbeats"] == 3
    assert store.open_log(A).frontier() == before             # per-device max HLC 一台不变


def test_delta_still_converges_for_lagging_peer(tmp_path):
    """④ 落后对端:压缩后 delta 已无陈年心跳,但 done 终态照常到达 → 视图级收敛;
    全量对端(压缩前副本)不会把删掉的心跳发回来(我 frontier 没回退)→ 不抖动不复活。"""
    orig = _seed_done_with_heartbeats(tmp_path)
    compact_mesh_log(tmp_path, now_ms=NOW)
    a = MeshLogStore(tmp_path).open_log(A)
    # 一无所知的落后对端,收 delta 后材料化终态一致
    lag = MeshLog(B)
    lag.merge(a.delta(lag.frontier()), wall=NOW)
    t = materialize_tasks(lag.entries(), now=NOW)["t1"]
    assert t.status == ST_DONE and t.result == {"status": "done", "result": "ok"}
    assert len(lag) == len(a)                                 # 我有的它全有(删过的我也没有)
    # 全量对端(压缩前完整副本):对我 frontier 求 delta = 空 → 已删心跳不复活
    full = MeshLog(B)
    full.merge(orig.entries(), wall=NOW)
    assert full.delta(a.frontier()) == []


# ---- ⑤ seen 台账只清 done ----

def test_seen_prune_only_done(tmp_path):
    """seen 只清已 ST_DONE 的 id;reclaimable 留着防重弹;查无此 id 的保守保留;幂等。"""
    import karvyloop.console.mesh_task_board as board
    log = MeshLog(B)
    offer_task(log, "t-done", [], {"intent": "x"}, wall=T0, lease_s=LEASE_MS)
    claim_task(log, "t-done", wall=T0)
    complete_task(log, "t-done", {"status": "done"}, wall=T0 + MIN5)
    offer_task(log, "t-open", [], {"intent": "y"}, wall=T0, lease_s=LEASE_MS)
    claim_task(log, "t-open", wall=T0)                        # 不 done → NOW 时租过期 = reclaimable
    MeshLogStore(tmp_path).append(log.entries())
    (tmp_path / board.SEEN_FILE).write_text(
        json.dumps({"seen": ["t-done", "t-open", "t-ghost"]}), encoding="utf-8")
    app = SimpleNamespace(state=SimpleNamespace(mesh_state_dir=tmp_path))
    assert board.prune_seen_done(app, now_ms=NOW) == 1
    assert app.state._mesh_takeover_seen == ["t-open", "t-ghost"]   # 进程内台账同步收缩
    data = json.loads((tmp_path / board.SEEN_FILE).read_text(encoding="utf-8"))
    assert data["seen"] == ["t-open", "t-ghost"]                    # 落盘一致
    assert board.prune_seen_done(app, now_ms=NOW) == 0              # 幂等
    st = materialize_tasks(MeshLogStore(tmp_path).load_events(), now=NOW)
    assert st["t-open"].status == ST_RECLAIMABLE              # 防重弹的账确实还需要


# ---- ⑥ 原子性 ----

def test_atomic_rewrite_failure_leaves_original_intact(tmp_path, monkeypatch):
    """replace 半途崩(Windows 句柄占用/断电)= 老文件原样、tmp 不残留 —— 宁不删勿半删。"""
    _seed_done_with_heartbeats(tmp_path)
    before = [e.event_id for e in MeshLogStore(tmp_path).load_events()]

    def _boom(src, dst):
        raise PermissionError("模拟 replace 失败")
    monkeypatch.setattr(compact.os, "replace", _boom)
    with pytest.raises(PermissionError):
        compact_mesh_log(tmp_path, now_ms=NOW)
    assert [e.event_id for e in MeshLogStore(tmp_path).load_events()] == before
    assert list(tmp_path.glob("*.tmp")) == []                 # 残留 tmp 已清


# ---- ⑦ mesh_tick 低频挂载 ----

def _tick_app(sd):
    return SimpleNamespace(state=SimpleNamespace(mesh_state_dir=sd, relay_url=""))


def _mute_publish(monkeypatch, mt):
    monkeypatch.setattr(mt, "publish_local_tasks",
                        lambda app: {"offered": 0, "heartbeats": 0, "completed": 0})


def test_mesh_tick_maintenance_first_tick_then_waits(tmp_path, monkeypatch):
    """首轮跑(启动清上个进程的积压)并带回三元组;第二轮周期没到 + 文件不超阈值 → 不跑。"""
    import karvyloop.console.mesh_tick as mt
    _mute_publish(monkeypatch, mt)
    app = _tick_app(tmp_path)
    out = asyncio.run(mt.mesh_tick(app))
    assert out["compact"] == {"scanned": 0, "dropped_heartbeats": 0, "seen_pruned": 0}
    out2 = asyncio.run(mt.mesh_tick(app))
    assert "compact" not in out2


def test_mesh_tick_maintenance_early_trigger_on_size(tmp_path, monkeypatch):
    """周期没到但 jsonl 超行数阈值(字节代理)→ 提早跑真删(T0 早于真墙钟 7 天开外)。"""
    import karvyloop.console.mesh_tick as mt
    _mute_publish(monkeypatch, mt)
    _seed_done_with_heartbeats(tmp_path)
    app = _tick_app(tmp_path)
    app.state._mesh_maintenance_tick = 7                      # 已过首轮,周期远没到
    monkeypatch.setattr(mt, "COMPACT_MAX_BYTES", 1)
    out = asyncio.run(mt.mesh_tick(app))
    assert out["compact"]["dropped_heartbeats"] == 3
    assert out["compact"]["scanned"] == 7


def test_mesh_tick_maintenance_failure_never_blocks(tmp_path, monkeypatch):
    """维护 pass 炸了 → tick 照常返回同步语义(原子重写保老账,下轮再来)。"""
    import karvyloop.console.mesh_tick as mt
    _mute_publish(monkeypatch, mt)

    def _boom(sd):
        raise RuntimeError("boom")
    monkeypatch.setattr(mt, "compact_mesh_log", _boom)
    out = asyncio.run(mt.mesh_tick(_tick_app(tmp_path)))
    assert "compact" not in out
    assert out["peers"] == 0 and out["synced"] == 0 and out["failed"] == 0
