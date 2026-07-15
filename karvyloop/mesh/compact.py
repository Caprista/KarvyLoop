"""mesh/compact — mesh 日志 compaction(保守第一刀:只删「已 done 任务的陈年心跳」)。

记账债:mesh 日志 append-only 只增不减,每个 running 任务按 lease/3(≈5min)累积一条
心跳事件(console/mesh_task_board),一条长跑任务一周就是 ~2000 行。本模块做
**gossip-安全的最小删除**,宁少删勿破同步。可删 = 同时满足三条:

  ① kind == task-heartbeat;
  ② 该 task 在本日志里已有 task-done(终态);
  ③ 心跳墙钟(payload.at)早于 now − COMPACT_HEARTBEAT_MIN_AGE_DAYS 天。

**硬护栏:绝不删任何设备的最高 HLC 事件**(frontier = per-device max HLC)。

为什么这样删 gossip-安全(逻辑可证,不靠运气):

- **材料化终态不变**:materialize_tasks 里 K_DONE 无条件置 status=ST_DONE + result;
  心跳只改 lease_until,而 lease_until 只在「claim 裁决」和「reclaimable 判定」被读——
  done 之后两处都不再看。且 ③ 的年龄门保证:被删心跳即便在场,它续的租(15min 量级)
  也早在 N 天前过期,对"现在"的 reclaimable 判定同样零影响。消费侧(发布对账 /
  接活扫描)对 DONE 任务从不读 claimer/lease。⇒ 删前删后每个任务的 status/result
  逐一相同。(诚实注:DONE 任务的 claimer/lease_until 这两个**死字段**可能因删心跳
  而不同——没有任何读者,接受。)
- **frontier 不回退**(硬护栏的作用):被删事件的 HLC 严格小于其设备的最高 HLC
  (最高那条被硬护栏留下),重载后 frontier 逐设备不变 ⇒ ① 我们对外报的 frontier
  不变,还没删的对端不会把这些心跳当"你缺的"再发回来(delta 只发 > frontier 的),
  不抖动不复活;② 每台设备的"最新事件"永远留存、永远可传播——不会因为它恰好是条
  心跳就从 mesh 里蒸发(设备可能再也不上线,它的 frontier 只有别人替它转述)。
- **落后对端仍收敛(视图级)**:对端没见过的心跳我们删了,它就永远收不到——「日志
  逐字节一致」这个性质对被删心跳**有意放弃**;但 offer/claim/done 一概不删,照常随
  delta 到达,材料化终态处处一致。各设备各自到期各自删,最终大家都不剩这些心跳。

原子性:tmp 全量写 + fsync + os.replace(POSIX/Windows 都原子)——重写要么整体生效
要么老文件原样(append-only 不破);坏 JSON 行**原样保留**(compaction 只删心跳,
不顺手清尸——那是 load_events 防御式跳过的事)。本函数纯同步无 await,挂在 mesh_tick
单协程里跑 = 对事件循环原子,不会与同 loop 的 append 交错。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from karvyloop.mesh.store import MeshLogStore
from karvyloop.mesh.synclog import HLC, MeshEvent
from karvyloop.mesh.tasks import K_DONE, K_HEARTBEAT

logger = logging.getLogger(__name__)

# 心跳最小年龄(天):早于 now − N 天的才删。〔常数待 Trace 标定 —— 首版拍 7 天:
# 盖住"设备一整周没开机"的离线窗;比它新的心跳留着陪跑,等各对端都见过再删。〕
COMPACT_HEARTBEAT_MIN_AGE_DAYS = 7
_DAY_MS = 24 * 60 * 60 * 1000

# 提早触发阈值(字节;行数的代理 —— 一事件一行 ~200B,2MB ≈ 1 万行)。〔待 Trace 标定〕
# 用字节不用行数:stat() 一次系统调用,每 tick 检查零读盘。
COMPACT_MAX_BYTES = 2 * 1024 * 1024


def _wall_ms(now_ms: Optional[int]) -> int:
    return int(time.time() * 1000) if now_ms is None else int(now_ms)


def _atomic_rewrite(store: MeshLogStore, lines: List[str]) -> None:
    """全量重写 JSONL:tmp 写全 + fsync + os.replace。崩在 replace 前 = 老文件原样;
    残留 tmp 在 finally 清掉(replace 成功后 tmp 已不在,unlink 是空操作)。"""
    tmp = store.path.with_name(store.path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt":
            try:
                os.chmod(tmp, 0o600)          # 与 store.append 同一权限纪律
            except Exception:  # noqa: BLE001
                pass
        os.replace(tmp, store.path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def compact_mesh_log(base_dir=None, *, now_ms: Optional[int] = None) -> dict:
    """一次 compaction pass。返回膨胀观测 {scanned, dropped_heartbeats}。

    只删「task-heartbeat 且该 task 已有 task-done 且心跳早于 N 天」;每设备最高 HLC
    事件绝不删(硬护栏,见模块 docstring);没得删 → 不碰盘(零重写)。
    """
    store = MeshLogStore(base_dir)
    try:
        text = store.path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — 无文件/读不了 = 没账可清,诚实零
        return {"scanned": 0, "dropped_heartbeats": 0}
    raw = [ln for ln in text.splitlines() if ln.strip()]
    # (原始行, 解析出的事件|None):坏行 None → 永远保留原样(不顺手清尸)
    entries: List[Tuple[str, Optional[MeshEvent]]] = []
    for ln in raw:
        try:
            entries.append((ln, MeshEvent.from_dict(json.loads(ln))))
        except Exception:  # noqa: BLE001
            entries.append((ln, None))
    scanned = len(entries)
    if not entries:
        return {"scanned": 0, "dropped_heartbeats": 0}

    now = _wall_ms(now_ms)
    cutoff = now - COMPACT_HEARTBEAT_MIN_AGE_DAYS * _DAY_MS
    parsed = [e for _, e in entries if e is not None]
    done_tasks = {str((e.payload or {}).get("task_id") or "")
                  for e in parsed if e.kind == K_DONE}
    done_tasks.discard("")
    # 硬护栏数据:per-device 最高 HLC(= 本日志对外报的 frontier)
    frontier: Dict[str, HLC] = {}
    for e in parsed:
        cur = frontier.get(e.device_id)
        if cur is None or e.hlc > cur:
            frontier[e.device_id] = e.hlc

    def _droppable(e: MeshEvent) -> bool:
        if e.kind != K_HEARTBEAT:
            return False
        if frontier.get(e.device_id) == e.hlc:
            return False                       # 硬护栏:设备最高 HLC 事件永不删
        tid = str((e.payload or {}).get("task_id") or "")
        if not tid or tid not in done_tasks:
            return False                       # 没 done 的任务,一条心跳都不动
        at = float((e.payload or {}).get("at") or e.hlc.wall)
        return at < cutoff                     # N 天内的新心跳留着

    kept = [ln for ln, e in entries if e is None or not _droppable(e)]
    dropped = scanned - len(kept)
    if dropped:
        _atomic_rewrite(store, kept)
        # 不静默截断纪律:删了多少必须上 log(info 一行,天级频率不刷屏)
        logger.info(f"[mesh_compact] 删 {dropped} 条已 done 任务的陈年心跳"
                    f"(≥{COMPACT_HEARTBEAT_MIN_AGE_DAYS} 天;扫描 {scanned},保留 {len(kept)})")
    return {"scanned": scanned, "dropped_heartbeats": dropped}


__all__ = ["compact_mesh_log", "COMPACT_HEARTBEAT_MIN_AGE_DAYS", "COMPACT_MAX_BYTES"]
