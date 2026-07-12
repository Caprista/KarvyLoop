"""test_mesh_synclog — 共享日志排序+合并引擎(HLC + MeshLog)的正确性,核心是**收敛性**。

同主人 mesh 认知同步的内核:多设备各持一份日志,交换 delta 后必须收敛到**完全一致的顺序**
(否则各设备物化视图不一致 = 认知不共享)。纯逻辑,确定性测(wall 显式注入)。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.mesh.synclog import (  # noqa: E402
    HLC, MeshEvent, MeshLog, hlc_recv, hlc_tick,
)


# ---- HLC ----

def test_hlc_tick_monotonic():
    assert hlc_tick(HLC(1000, 0), 1001) == HLC(1001, 0)   # 墙钟前进 → counter 归零
    assert hlc_tick(HLC(1000, 3), 1000) == HLC(1000, 4)   # 同墙钟 → counter++
    assert hlc_tick(HLC(1000, 0), 999) == HLC(1000, 1)    # 墙钟倒退 → 仍单调(counter++)


def test_hlc_recv_advances_past_both():
    out = hlc_recv(HLC(1000, 5), HLC(1000, 9), 1000)      # 同墙钟 → max counter +1
    assert out == HLC(1000, 10)
    assert hlc_recv(HLC(1000, 0), HLC(2000, 3), 1500) == HLC(2000, 4)   # remote 领先
    assert hlc_recv(HLC(3000, 2), HLC(1000, 9), 1500) == HLC(3000, 3)   # local 领先


def test_hlc_lexicographic_order():
    assert HLC(1000, 0) < HLC(1000, 1) < HLC(1001, 0)


# ---- MeshLog 基本 ----

def test_append_entries_ordered_and_unique_ids():
    log = MeshLog("dev-a")
    e1 = log.append("trace", {"x": 1}, wall=1000)
    e2 = log.append("belief-created", {"m": "hi"}, wall=1000)
    assert e1.hlc < e2.hlc                                # 同墙钟内单调
    assert e1.event_id != e2.event_id and "dev-a@" in e1.event_id
    assert [e.event_id for e in log.entries()] == [e1.event_id, e2.event_id]


def test_frontier_per_device():
    log = MeshLog("a")
    log.append("trace", {}, wall=1000)
    last = log.append("trace", {}, wall=1005)
    log.merge([MeshEvent("b", HLC(1003, 0), "trace", {})], wall=1006)
    fr = log.frontier()
    assert fr["a"] == last.hlc and fr["b"] == HLC(1003, 0)


def test_delta_only_events_beyond_their_frontier():
    log = MeshLog("a")
    e1 = log.append("trace", {}, wall=1000)
    e2 = log.append("trace", {}, wall=1001)
    # 对方已有到 e1 → delta 只给 e2
    d = log.delta({"a": e1.hlc})
    assert [e.event_id for e in d] == [e2.event_id]
    # 对方完全没有 a → 全给
    assert len(log.delta({})) == 2


def test_merge_dedup_idempotent():
    log = MeshLog("a")
    ev = MeshEvent("b", HLC(1000, 0), "trace", {"y": 2})
    assert log.merge([ev], wall=2000) == 1
    assert log.merge([ev], wall=2001) == 0               # 再收同一条 → 幂等,不重加
    assert len(log) == 1


def test_event_serialization_roundtrip():
    ev = MeshEvent("dev-x", HLC(1234, 5), "skill-crystallized", {"s": "做表"})
    back = MeshEvent.from_dict(ev.to_dict())
    assert back == ev and back.event_id == "dev-x@1234.5"


# ---- 收敛性(核心)----

def _exchange(a: MeshLog, b: MeshLog, wall: int) -> None:
    """一轮双向同步:各自按对方 frontier 取 delta(合并前快照)→ 互相 merge。"""
    a_to_b = a.delta(b.frontier())
    b_to_a = b.delta(a.frontier())
    b.merge(a_to_b, wall=wall)
    a.merge(b_to_a, wall=wall)


def test_two_device_convergence():
    """两设备各自 append + 一轮交换 → 日志**完全一致且顺序一致**(认知共享的地基)。"""
    a, b = MeshLog("dev-a"), MeshLog("dev-b")
    a.append("trace", {"x": 1}, wall=1000)
    a.append("belief-created", {"m": "learned on A"}, wall=1001)
    b.append("trace", {"y": 2}, wall=1000)
    b.append("skill-crystallized", {"s": "made on B"}, wall=1002)

    _exchange(a, b, wall=2000)

    ea = [e.event_id for e in a.entries()]
    eb = [e.event_id for e in b.entries()]
    assert ea == eb, f"两设备未收敛到一致顺序: {ea} vs {eb}"
    assert len(a) == len(b) == 4
    # 再交换一轮 → 无新增(稳定)
    before = len(a)
    _exchange(a, b, wall=3000)
    assert len(a) == len(b) == before


def test_three_device_gossip_convergence():
    """三设备两两 gossip → 全部收敛一致(顺序无关:无论谁先跟谁同步,终态相同)。"""
    a, b, c = MeshLog("a"), MeshLog("b"), MeshLog("c")
    a.append("trace", {"who": "a"}, wall=1000)
    b.append("belief-created", {"who": "b"}, wall=1000)
    c.append("skill-crystallized", {"who": "c"}, wall=1000)
    c.append("pref-updated", {"who": "c2"}, wall=1004)

    # 任意顺序的两两同步,多跑几轮直到稳定
    for _ in range(3):
        _exchange(a, b, wall=5000)
        _exchange(b, c, wall=5001)
        _exchange(a, c, wall=5002)

    ea = [e.event_id for e in a.entries()]
    eb = [e.event_id for e in b.entries()]
    ec = [e.event_id for e in c.entries()]
    assert ea == eb == ec, "三设备未收敛"
    assert len(a) == 4
