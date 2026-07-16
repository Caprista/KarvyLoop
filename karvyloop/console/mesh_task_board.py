"""console/mesh_task_board — mesh 任务板实驱:发布对账 + 接活 H2A(docs/74 §6.2/§6.3 第二环·切片B)。

把已建好的任务板原语(mesh/tasks:offer/claim/heartbeat/done + materialize HLC 裁决)接进生产:
**全部走 mesh_tick 的幂等对账模式**——不 hook TaskRegistry 热路径(少脚手架;崩了下轮补账)。

- **发布侧**(每 tick):本地 running 任务上板(offer+自认领)、按 lease/3 心跳续租、
  本地终态补 done。幂等靠 materialize 后的状态判断该不该写事件,绝不重复 offer/complete。
- **接活侧**(每 tick 同步后):日志上 lease 过期(claimer 掉线没续租)且本机可行的别机任务
  → 弹 **H2A 接活卡**(每 task_id 只弹一次,seen 落盘跨重启防重弹)。**绝不 auto-execute**
  (Hardy 拍过:H2A 确认才动);REJECT/无人拍 → 什么都不做,任务留 reclaimable 给别的设备。
- **ACCEPT** = claim 上账 → 本地从头重跑(骑 run_task 的 ACCEPT handler,Ring-1 同语义,
  不搞 checkpoint)→ 跑完 complete 上账(失败也如实记终态:人已看到结果,别让别台继续弹卡)。

单位契约:mesh 日志的 wall/at 一律**毫秒**(与 cognition/skill bridge 同调,HLC 排序才同刻度);
mesh/tasks 的 lease_s 与 at 同单位 → 这里传毫秒(TASK_LEASE_S * 1000)。

task_id 直接用本地 TaskRecord id(uuid4 hex[:12]):同主人设备间 48bit 随机撞车概率可忽略,
且发布方板上 id ↔ mesh id 恒等,免一层映射;来源可溯靠 payload.source_device(不靠 id 前缀)。

诚实边界:日志 append-only 累积(事件条均 ~200B,心跳按 lease/3 不按 tick,量小);单机无对端
也照写本地日志——成本≈0,设备后来加入时能补上历史。心跳的记账债由 mesh/compact 天级清
(只删已 done 任务的陈年心跳,gossip-安全论证在那边);seen 台账由 prune_seen_done 同频清 done。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from karvyloop.mesh.fingerprint import device_fingerprint
from karvyloop.mesh.registry import DeviceRegistry
from karvyloop.mesh.schedule import feasible
from karvyloop.mesh.store import MeshLogStore
from karvyloop.mesh.tasks import (
    ST_CLAIMED, ST_DONE, ST_OFFERED, ST_RECLAIMABLE, TaskState,
    claim_task, complete_task, heartbeat_task, materialize_tasks, offer_task,
)

logger = logging.getLogger(__name__)

# lease 时长〔待 Trace 标定 —— 首版拍的;硬约束:> 2×MESH_TICK_S(120s)+ 同步传播延迟
# (对端要等它自己的下轮 tick 才拉到心跳,分钟级)。取 15min:一次瞬断/一轮同步失手
# 远不足以把活着的设备判死,宁可晚接不误抢(误抢 = 双跑)。〕
TASK_LEASE_S = 15 * 60.0
# 心跳间隔 = lease/3(标准租约心智:一个 lease 窗内 ≥2 次续租机会,单次 tick 失手不掉租)。
# 绝不 60s 每 tick 刷一条 —— 日志 append-only,心跳频率就是膨胀率。〔随 TASK_LEASE_S 一起标定〕
TASK_HEARTBEAT_EVERY_S = TASK_LEASE_S / 3

# 接活卡 seen 台账(每 task_id 只弹一次;落盘 = 跨重启也不重弹)。
SEEN_FILE = "mesh_task_seen.json"
_SEEN_CAP = 2000   # 只留最近 N 个 id(防无限长;老任务早终态,重弹判定用不上)


def _wall_ms(now_ms: Optional[int]) -> int:
    return int(time.time() * 1000) if now_ms is None else int(now_ms)


def _seen_path(sd) -> Path:
    return (Path(sd) if sd else (Path.home() / ".karvyloop")) / SEEN_FILE


def _load_seen(app: Any, sd) -> list:
    """seen 台账:进程内挂 app.state(list 保序,配合 cap 淘汰最老);首次从盘读。"""
    seen = getattr(app.state, "_mesh_takeover_seen", None)
    if seen is not None:
        return seen
    out: list = []
    try:
        data = json.loads(_seen_path(sd).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("seen"), list):
            out = [str(x) for x in data["seen"]][-_SEEN_CAP:]
    except Exception:   # noqa: BLE001 — 坏文件当空账(最坏重弹一次卡,不崩)
        out = []
    app.state._mesh_takeover_seen = out
    return out


def _persist_seen(app: Any, sd, seen: list) -> None:
    seen[:] = seen[-_SEEN_CAP:]
    try:
        p = _seen_path(sd)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"seen": seen}, ensure_ascii=False), encoding="utf-8")
    except Exception:   # noqa: BLE001 — 落盘失败不阻塞(最坏重启后重弹一次,人再拍一下)
        logger.debug("[mesh_task_board] seen 台账落盘失败(重启后可能重弹一次)")


# ---- ① 发布侧对账(mesh_tick 每轮;幂等:看 materialize 后的状态决定写不写)----

def publish_local_tasks(app: Any, *, now_ms: Optional[int] = None) -> dict:
    """把本地任务看板对账到 mesh 日志。返回 {offered, heartbeats, completed}(+可选 reason)。

    - running 且板上无此 id → offer(needs=[] 第一刀)+ 自认领;
    - running 且本机持租 → 到心跳点(lease/3)才续租(单调,不每 tick 刷);
    - 本地终态(done/error)且 mesh 未 done 且**本机是 claimer** → complete(带终态)。
      别台已接管(claimer≠我)→ 不动它的账;从没上过板的已终态任务 → 不补(死账不上板)。
    """
    zero = {"offered": 0, "heartbeats": 0, "completed": 0}
    reg = getattr(app.state, "task_registry", None)
    if reg is None:
        return {**zero, "reason": "no_registry"}
    sd = getattr(app.state, "mesh_state_dir", None)
    my_id = str(device_fingerprint(sd).get("device_id") or "")
    if not my_id:
        return {**zero, "reason": "no_identity"}
    try:
        board = reg.list()
    except Exception:   # noqa: BLE001
        return {**zero, "reason": "board_unreadable"}
    if not board:
        return zero

    now = _wall_ms(now_ms)
    store = MeshLogStore(sd)
    log = store.open_log(my_id)
    mesh = materialize_tasks(log.entries(), now=now)
    new_events = []
    offered = heartbeats = completed = 0
    for t in board:
        tid = str(t.get("id") or "")
        if not tid:
            continue
        status = str(t.get("status") or "")
        st = mesh.get(tid)
        if status == "running":
            if st is None:
                payload = {"intent": str(t.get("intent") or ""), "who": str(t.get("who") or ""),
                           "domain_id": str(t.get("domain_id") or "l0"),
                           "role": str(t.get("role") or ""), "source_device": my_id}
                new_events.append(offer_task(log, tid, [], payload, wall=now,
                                             lease_s=TASK_LEASE_S * 1000.0))
                new_events.append(claim_task(log, tid, wall=now))   # 自认领:本机正在跑它
                offered += 1
            elif st.status == ST_DONE or st.claimer != my_id:
                continue   # mesh 已终态 / 别台持租(接管中)→ 不动别人的账
            elif (st.lease_until - now) <= (TASK_LEASE_S - TASK_HEARTBEAT_EVERY_S) * 1000.0:
                # 到心跳点(距上次续租 ≥ lease/3;lease 已过但没人抢 → 心跳照样续回,自愈)
                new_events.append(heartbeat_task(log, tid, wall=now))
                heartbeats += 1
        elif status in ("done", "error"):
            # 只有**本机持租**的才由本机记终态(别台接管中轮不到我;从没上板的死账不补)
            if st is not None and st.status != ST_DONE and st.claimer == my_id:
                new_events.append(complete_task(
                    log, tid, {"status": status, "result": str(t.get("result") or "")[:280],
                               "device": my_id}, wall=now))
                completed += 1
    if new_events:
        store.append(new_events)   # 只追加本轮新产事件(不全量 diff,append-only 高效)
    return {"offered": offered, "heartbeats": heartbeats, "completed": completed}


# ---- ② 接活侧(mesh_tick 每轮同步后;H2A 卡,绝不 auto-execute)----

def takeover_proposal_for(t: TaskState, *, device_label: str, now: Optional[float] = None):
    """给一条 lease 过期的别机任务造一张 H2A 接活卡。intent 空 → None(宁空勿弹)。"""
    from karvyloop import i18n
    from karvyloop.karvy.atoms import Proposal
    from karvyloop.karvy.proposal_registry import KIND_MESH_TAKEOVER
    p = t.payload or {}
    intent = str(p.get("intent") or "").strip()
    if not intent:
        return None
    short = intent if len(intent) <= 40 else intent[:40] + "…"
    source = str(p.get("source_device") or t.claimer or "")
    return Proposal(
        summary=i18n.t("mesh.takeover.summary", device=device_label, intent=short),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.8,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=(time.time() if now is None else now),
        kind=KIND_MESH_TAKEOVER,
        payload={
            "intent": intent,                                   # run_task handler 骑行兼容
            "domain_id": str(p.get("domain_id") or "l0"),
            "role": str(p.get("role") or ""),
            "source": "mesh_task_board.takeover",
            "mesh_task_id": t.task_id,                          # ACCEPT 上账(claim/done)用
            "source_device": source,
        },
        # 稳定 id 绑 task_id(不走 summary 派生:两条同 intent 的中断任务必须是两张卡)
        proposal_id=f"{KIND_MESH_TAKEOVER}-0-{t.task_id}",
        # 决策依据(ch4):谁家设备、凭什么判它中断(lease 过期没心跳)、ACCEPT 意味着什么
        basis=i18n.t("mesh.takeover.basis", task_id=t.task_id, device=device_label, source=source[:8] or "?"),
        context_ref={},   # 本机板上还没有这条任务,没有可跳的本地目标(诚实留空)
    )


def scan_takeover_proposals(app: Any, *, now_ms: Optional[int] = None) -> List:
    """扫 mesh 日志找"可接的别机中断任务" → 造 H2A 卡列表(每 task_id 只弹一次,seen 落盘)。

    判据:状态 ST_RECLAIMABLE(认领过但 lease 过期 = claimer 掉线没续租)且 claimer≠本机
    且 feasible(needs, 本机 caps)。本机自己的过租任务不弹(发布侧心跳会自愈续回)。
    """
    sd = getattr(app.state, "mesh_state_dir", None)
    fp = device_fingerprint(sd)
    my_id = str(fp.get("device_id") or "")
    if not my_id:
        return []
    now = _wall_ms(now_ms)
    log = MeshLogStore(sd).open_log(my_id)
    my_caps = list(fp.get("capabilities") or [])
    seen = _load_seen(app, sd)
    reg = DeviceRegistry(sd)
    out: List = []
    for t in materialize_tasks(log.entries(), now=now).values():
        if t.status != ST_RECLAIMABLE:
            continue
        if not t.claimer or t.claimer == my_id:
            continue
        if not feasible(t.needs, my_caps):
            continue
        if t.task_id in seen:
            continue
        dev = reg.get(t.claimer)
        label = ((dev.label or dev.os) if dev else "") or t.claimer[:8]
        prop = takeover_proposal_for(t, device_label=label, now=now / 1000.0)
        if prop is None:
            continue
        out.append(prop)
        seen.append(t.task_id)
    if out:
        _persist_seen(app, sd, seen)
    return out


def prune_seen_done(app: Any, *, now_ms: Optional[int] = None) -> int:
    """seen 台账语义清理(mesh_tick 低频维护 pass 挂进来):清掉「已 ST_DONE」的 task_id。

    seen 的唯一职责是防重弹;done 是终态(materialize 里 K_DONE 无条件赢,永不再回
    reclaimable)→ 这些 id 永远不会再进弹卡判据,记着纯占位、还挤 _SEEN_CAP 的位子。
    **只清 done**:reclaimable/claimed/offered 的保留(它们还会再满足弹卡判据,清了=重弹);
    日志里查无此 id 的也保留(宁保守勿重弹:可能是坏行被防御跳过的任务)。返回清掉条数。
    """
    sd = getattr(app.state, "mesh_state_dir", None)
    seen = _load_seen(app, sd)
    if not seen:
        return 0
    tasks = materialize_tasks(MeshLogStore(sd).load_events(), now=_wall_ms(now_ms))
    keep = [tid for tid in seen
            if not (tid in tasks and tasks[tid].status == ST_DONE)]
    pruned = len(seen) - len(keep)
    if pruned:
        seen[:] = keep                        # 就地改:进程内台账(app.state 同一 list)一起收缩
        _persist_seen(app, sd, seen)
        logger.info(f"[mesh_task_board] seen 台账清 {pruned} 条已终态任务(剩 {len(keep)})")
    return pruned


# ---- ③ 板面只读快照(routes_mesh /api/mesh/board 的读半身;纯读,零事件零副作用)----

BOARD_INTENT_MAX = 80   # 板面 intent 摘要长度(设备卡一行放得下;全文在发布方本地板上,不复制上墙)


def board_snapshot(sd, *, now_ms: Optional[int] = None) -> dict:
    """把盘上 mesh 日志折叠成「我的设备各自在跑什么」的只读快照(docs/74 §6.5 可见面)。

    与发布/接活同一套读逻辑(MeshLogStore.load_events + materialize_tasks)→ 板面所见 =
    tick 所裁,不另算一套。**纯读**:不写事件、不心跳、不 claim(看板不该改板,K4)。

    分组:非终态任务按 **claimer**(没人认领 → source_device)归到设备名下;done 不上板
    (板 = 在跑/排队/中断待接,不是历史账——历史在 Trace)。row 只带展示所需字段;
    `status` 是机器态(offered/claimed/reclaimable),人话标签在前端 i18n(后端不产 UI 文案)。
    `lease_remaining_s` 给"在跑(还剩X)"用(claimed 恒 >0:过期即被 materialize 判成
    reclaimable);排序 reclaimable 最前(要人管的顶上),同态按 task_id 稳定。
    """
    now = _wall_ms(now_ms)
    by_dev: dict = {}
    total = 0
    for t in materialize_tasks(MeshLogStore(sd).load_events(), now=now).values():
        if t.status == ST_DONE:
            continue
        p = t.payload or {}
        intent = str(p.get("intent") or "").strip()
        row = {
            "task_id": t.task_id,
            "intent": intent if len(intent) <= BOARD_INTENT_MAX else intent[:BOARD_INTENT_MAX] + "…",
            "status": t.status,
            "claimer": t.claimer,
            "source_device": str(p.get("source_device") or ""),
            "lease_until": t.lease_until,
            "lease_remaining_s": int((t.lease_until - now) / 1000),
        }
        by_dev.setdefault(t.claimer or row["source_device"], []).append(row)
        total += 1
    order = {ST_RECLAIMABLE: 0, ST_CLAIMED: 1, ST_OFFERED: 2}
    for rows in by_dev.values():
        rows.sort(key=lambda r: (order.get(r["status"], 3), r["task_id"]))
    return {"tasks_by_device": by_dev, "total": total}


# ---- ④ ACCEPT 兑现 handler(claim 上账 → 骑 run_task 从头重跑 → complete 上账)----

def make_mesh_takeover_handler(app: Any) -> Callable[[object], Tuple[bool, str]]:
    """mesh_takeover ACCEPT 兑现:只在用户拍了 ACCEPT 后被调(K5)。

    重跑本体**骑 run_task 的 ACCEPT handler**(payload 兼容:intent/domain_id/role)——
    同一条 Ring-1 语义路径(登记新本地任务 + 治理注入 + 独立验收),零复制。本 handler 只加
    mesh 记账:跑前 claim(别台看到有人接了;真赢没赢由 HLC 裁,H2A 人拍板本就是 at-least-once
    的上层保证),跑完 complete(终态如实带上,**失败也记**——人已看到结果,别让别台继续弹卡)。
    记账失败不挡重跑(重跑是给人的价值;账缺 claim → lease 窗后别台至多多弹一次卡,人再拍)。
    """
    def _account(mesh_tid: str, my_id: str, sd, make_event) -> None:
        try:
            store = MeshLogStore(sd)
            log = store.open_log(my_id)
            store.append([make_event(log)])
        except Exception:   # noqa: BLE001
            logger.debug("[mesh_takeover] mesh 记账失败(不挡重跑;下轮对账/lease 窗兜底)")

    def handler(proposal) -> Tuple[bool, str]:
        from karvyloop.console.proposal_handlers import _run_task_handler
        payload = getattr(proposal, "payload", None) or {}
        mesh_tid = str(payload.get("mesh_task_id") or "")
        sd = getattr(app.state, "mesh_state_dir", None)
        my_id = str(device_fingerprint(sd).get("device_id") or "")
        can_account = bool(mesh_tid and my_id)
        if can_account:   # ① claim 上账(先认领再跑)
            _account(mesh_tid, my_id, sd,
                     lambda log: claim_task(log, mesh_tid, wall=_wall_ms(None)))
        # ② 本地从头重跑(Ring-1 同语义,不搞 checkpoint)
        ok, detail = _run_task_handler(app)(proposal)
        if can_account:   # ③ complete 上账(终态如实)
            _account(mesh_tid, my_id, sd,
                     lambda log: complete_task(
                         log, mesh_tid,
                         {"ok": bool(ok), "detail": str(detail or "")[:280], "device": my_id},
                         wall=_wall_ms(None)))
        if ok and can_account:
            from karvyloop import i18n
            return ok, i18n.t("mesh.takeover.receipt", detail=detail)
        return ok, detail

    return handler


__all__ = ["TASK_LEASE_S", "TASK_HEARTBEAT_EVERY_S", "SEEN_FILE", "BOARD_INTENT_MAX",
           "publish_local_tasks", "scan_takeover_proposals", "prune_seen_done",
           "board_snapshot", "takeover_proposal_for", "make_mesh_takeover_handler"]
