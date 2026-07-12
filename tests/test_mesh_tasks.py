"""test_mesh_tasks — 共享日志任务板:并发认领裁决 + lease 过期重排(散的承重梁)+ 双设备收敛。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.mesh.synclog import MeshLog  # noqa: E402
from karvyloop.mesh.tasks import (  # noqa: E402
    ST_CLAIMED, ST_DONE, ST_OFFERED, ST_RECLAIMABLE,
    claim_task, claimable_for, complete_task, heartbeat_task,
    materialize_tasks, offer_task,
)


def _exchange(a: MeshLog, b: MeshLog, wall: int) -> None:
    a2b, b2a = a.delta(b.frontier()), b.delta(a.frontier())
    b.merge(a2b, wall=wall)
    a.merge(b2a, wall=wall)


# ---- 基本态 ----

def test_offer_then_claim():
    log = MeshLog("a")
    offer_task(log, "t1", ["coding"], {"intent": "跑测试"}, wall=1000, lease_s=60)
    t = materialize_tasks(log.entries(), now=1001)["t1"]
    assert t.status == ST_OFFERED and t.needs == ["coding"]
    claim_task(log, "t1", wall=1002)
    t = materialize_tasks(log.entries(), now=1003)["t1"]
    assert t.status == ST_CLAIMED and t.claimer == "a" and t.lease_until == 1002 + 60


def test_done_terminal_ignores_later_claim():
    log = MeshLog("a")
    offer_task(log, "t1", [], {}, wall=1000, lease_s=60)
    claim_task(log, "t1", wall=1001)
    complete_task(log, "t1", {"ok": True}, wall=1002)
    claim_task(log, "t1", wall=1003)                    # 完成后再认领 → 无效
    t = materialize_tasks(log.entries(), now=1004)["t1"]
    assert t.status == ST_DONE and t.result == {"ok": True}


# ---- 并发认领裁决(HLC 最早赢)----

def test_concurrent_claim_earliest_wins_and_converges():
    a, b = MeshLog("dev-a"), MeshLog("dev-b")
    offer_task(a, "t1", ["coding"], {}, wall=1000, lease_s=60)
    _exchange(a, b, wall=1000)                          # B 看到报价
    claim_task(a, "t1", wall=1001)                      # A 先认领(墙钟更早 → HLC 更早)
    claim_task(b, "t1", wall=1002)                      # B 后认领
    _exchange(a, b, wall=1003)                          # 两边都拿到两条 claim

    ta = materialize_tasks(a.entries(), now=1003)["t1"]
    tb = materialize_tasks(b.entries(), now=1003)["t1"]
    assert ta.claimer == tb.claimer == "dev-a", "并发认领没裁成 HLC 最早的赢/没收敛"
    assert ta.status == tb.status == ST_CLAIMED


# ---- lease 过期 → 可再认领(散的承重梁)----

def test_lease_expiry_makes_reclaimable():
    log = MeshLog("a")
    offer_task(log, "t1", [], {}, wall=1000, lease_s=60)
    claim_task(log, "t1", wall=1001)                    # lease_until = 1061
    assert materialize_tasks(log.entries(), now=1050)["t1"].status == ST_CLAIMED   # 未过期
    assert materialize_tasks(log.entries(), now=1100)["t1"].status == ST_RECLAIMABLE  # 过期→可再认领


def test_heartbeat_renews_lease():
    log = MeshLog("a")
    offer_task(log, "t1", [], {}, wall=1000, lease_s=60)
    claim_task(log, "t1", wall=1001)                    # lease_until=1061
    heartbeat_task(log, "t1", wall=1055)                # 续到 1115
    assert materialize_tasks(log.entries(), now=1100)["t1"].status == ST_CLAIMED   # 心跳续上,没过期


def test_reclaim_after_claimer_dies():
    """散的核心:claimer 掉线没续租,lease 过期后别的设备重认领 → 活不丢,换机器跑。"""
    a, b = MeshLog("dev-a"), MeshLog("dev-b")
    offer_task(a, "t1", ["coding"], {}, wall=1000, lease_s=60)
    claim_task(a, "t1", wall=1001)                      # A 认领,lease_until=1061
    _exchange(a, b, wall=1001)
    # A 掉线(不再心跳);B 在 lease 过期后(1100 > 1061)重认领
    claim_task(b, "t1", wall=1100)
    _exchange(a, b, wall=1100)
    t = materialize_tasks(b.entries(), now=1101)["t1"]
    assert t.claimer == "dev-b" and t.status == ST_CLAIMED, "lease 过期后没被重认领(散没兜住)"


# ---- feasibility 过滤本设备能认领的 ----

def test_claimable_for_filters_by_capability():
    log = MeshLog("a")
    offer_task(log, "code-job", ["coding"], {}, wall=1000, lease_s=60)
    offer_task(log, "photo-job", ["camera"], {}, wall=1000, lease_s=60)
    ev = log.entries()
    pc_can = {t.task_id for t in claimable_for(ev, now=1001, my_caps=["coding", "shell"])}
    phone_can = {t.task_id for t in claimable_for(ev, now=1001, my_caps=["camera", "voice"])}
    assert pc_can == {"code-job"} and phone_can == {"photo-job"}   # 各接各能干的,不硬派
