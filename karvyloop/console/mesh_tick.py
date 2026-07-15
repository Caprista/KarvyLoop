"""console/mesh_tick.py — 设备 mesh 持续同步 + 探活 + 任务板对账(docs/74 §5/§6.2/§6.3)。

命名循 knowledge_tick/promotion_tick 先例。每 MESH_TICK_S 一轮:

① **任务板·发布侧对账**(mesh_task_board.publish_local_tasks,幂等):本地 running 任务
  上板(offer+自认领)/ 心跳续租(lease/3 节奏)/ 本地终态补 done。先上账再拨号 →
  本轮新事件顺着同一轮同步出门。单机无对端也照写本地日志(成本≈0,后来加入的设备补历史)。
② 读花名册,对**非本机、room+relay_url 齐**的每台对端做一次 mesh_sync_with_peer
  (E2E 经 relay,拨它的 mesh 房):
  - 成功 → mark_seen(last_seen 新鲜 → `online()` 真);
  - 失败(room_busy / console_offline / 超时 / 网断)→ **debug 级吞掉** —— 对端连不上
    = 它下线,last_seen 自然变陈,**这就是探活**(SWIM suspect 心智:不在场=渐陈,
    绝不 error 刷屏;单台失败不阻其它台)。
③ **任务板·接活侧**(同步后的新鲜日志):别机任务 lease 过期(claimer 掉线没续租)且本机
  可行 → 弹 H2A 接活卡(每 task_id 只弹一次,seen 落盘防重弹)。**绝不 auto-execute**。

单机用户(花名册无可拨对端)→ ①后直接返回,零流量。挂载在 console/app.py lifespan
(relay_url 挂了才起;mesh 是增益不是地基)。任务板故障绝不挡同步/探活(下轮补账)。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from karvyloop.console.mesh_task_board import publish_local_tasks, scan_takeover_proposals
from karvyloop.mesh.fingerprint import device_fingerprint
from karvyloop.mesh.registry import DeviceRegistry
from karvyloop.mesh.sync_client import mesh_sync_with_peer

logger = logging.getLogger(__name__)

# 同步/探活间隔〔常数待 Trace 记分布后标定;取 60 < ONLINE_WINDOW_S=90 ≈ 1.5 tick,
# 一次瞬断(单 tick 失败)不足以把在线对端判离线 —— SWIM suspect 心智〕。
MESH_TICK_S = 60.0


async def mesh_tick(app: Any) -> dict:
    """一轮 mesh 同步/探活/任务板对账。返回 {peers, synced, failed, tasks}(+可选 reason)。"""
    sd = getattr(app.state, "mesh_state_dir", None)     # None = 默认 ~/.karvyloop(与路由一致)
    # ① 任务板·发布侧对账(先上账再拨号:本轮新事件顺同一轮同步出门)。
    #    板故障绝不挡同步/探活(幂等对账,下轮补账)。
    tasks_sum: dict = {}
    try:
        tasks_sum = publish_local_tasks(app)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[mesh_tick] 任务板发布对账失败(下轮补账): {e}")
    reg = DeviceRegistry(sd)
    peers = [d for d in reg.list_all()
             if not d.is_self and d.device_id and d.room and d.relay_url]
    if not peers:                                       # 单机用户:零流量(本地账已记)
        return {"peers": 0, "synced": 0, "failed": 0, "tasks": tasks_sum}
    my_id = str(device_fingerprint(sd).get("device_id") or "")
    if not my_id:                                       # 本机还没 relay 身份 → 拨不了,诚实返回
        return {"peers": len(peers), "synced": 0, "failed": 0, "tasks": tasks_sum,
                "reason": "no_identity"}
    my_relay = getattr(app.state, "relay_url", "") or ""
    synced = failed = 0
    for d in peers:
        try:
            # mesh_sync_with_peer 是真 async(websockets)→ 直接 await,不 to_thread。
            # fingerprint=d.device_id:花名册的 device_id 就是对端 relay 身份指纹(防中间人验它)。
            await mesh_sync_with_peer(d.relay_url, d.room, fingerprint=d.device_id,
                                      my_device_id=my_id, state_dir=sd,
                                      my_relay_url=my_relay or None)
            reg.mark_seen(d.device_id)
            synced += 1
        except asyncio.CancelledError:
            raise                                       # 关停路径:不吞
        except Exception as e:  # noqa: BLE001 — 连不上=它下线,last_seen 自然变陈,这就是探活
            failed += 1
            logger.debug(f"[mesh_tick] peer {d.device_id[:16]} unreachable "
                         f"({type(e).__name__}) — last_seen ages out naturally")
    # ③ 任务板·接活侧(同步后的新鲜日志):可接的别机中断任务 → H2A 卡(绝不 auto-execute;
    #    REJECT/无人拍 = 什么都不做,任务留 reclaimable 给别的设备)。
    try:
        cards = scan_takeover_proposals(app)
        if cards:
            from karvyloop.console.proposals import broadcast_proposal
            for c in cards:
                await broadcast_proposal(app, c)
            tasks_sum = dict(tasks_sum)
            tasks_sum["takeover_cards"] = len(cards)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[mesh_tick] 接活扫描失败(下轮再来): {e}")
    return {"peers": len(peers), "synced": synced, "failed": failed, "tasks": tasks_sum}


__all__ = ["mesh_tick", "MESH_TICK_S"]
