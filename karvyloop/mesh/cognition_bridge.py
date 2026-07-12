"""mesh/cognition_bridge — 真认知(记忆 Belief)↔ MeshLog 的桥(outbox 式,docs/74 §5.2)。

**架构裁决(2026-07-12 影响评估 + 世界雷达 + 第三镜)**:store 仍是主真相,MeshLog 只当同步管道
——写咽喉后发一条"完整条目"事件(Event-Carried State Transfer,MeshLog 本身就是 outbox),
远端事件经**现有写咽喉** `mem.write()` 幂等回放。不重建存储成视图(第三镜:那是前大模型/大集群
的过度工程;我们同主人+追加为主,冲突面几乎为零)。

**Hardy 拍的产品语义(2026-07-12)**:
- 域私有认知**也同步**(我的所有设备=一个我;域隔离是召回时按 applies.domain 逻辑过滤,每台设备
  同样生效,不靠"存在哪台机器")。
- A 设备确认过的直接在 B 生效(**确认跟人走,不跟设备走**;未确认的照旧走它自己的确认门)。

**回声抑制(无状态)**:本地新写的 belief 无 `origin_device` → 钩子盖戳 + 发事件;远端回放来的
带别人的 `origin_device` → 钩子跳过(不再发)。gossip 转发由 MeshLog 层的 delta 交换承担
(远端事件 merge 进我的日志,第三台设备从我这拿),桥**只为本地新认知**造事件。

**幂等/防复活**:apply 前查 index 按 content——**已存在(含已 archive 的)一律跳过**,
绝不用旧事件覆盖/复活本地态(保守 first-write-wins;同 content 语义上就是同一条认知)。
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Iterable, List, Optional

from karvyloop.mesh.synclog import MeshEvent, MeshLog

logger = logging.getLogger(__name__)

K_BELIEF = "belief-created"     # 与 docs/74 事件词根一致


def sync_id_for(content: str) -> str:
    """Belief 的稳定同步 id = 内容寻址(sha1 前 16 hex)。

    去重键对齐(影响评估冲突2):store 按 content 收敛 ⇔ 同 content 必同 id ⇔ 幂等 apply 天然。
    """
    return hashlib.sha1((content or "").encode("utf-8")).hexdigest()[:16]


def belief_to_payload(belief) -> dict:
    """Belief → 事件 payload(带全状态;字段形状同 BeliefStore.save_all)。"""
    return {
        "content": belief.content,
        "provenance": dict(belief.provenance or {}),
        "freshness_ts": belief.freshness_ts,
        "scope": belief.scope,
        "invalid_at": belief.invalid_at,
        "invalid_reason": belief.invalid_reason,
    }


def belief_from_payload(d: dict):
    """事件 payload → Belief;坏数据返 None(宁空勿毒:坏事件绝不进认知库)。"""
    from karvyloop.schemas.cognition import Belief
    try:
        d = d or {}
        inv = d.get("invalid_at", None)
        return Belief(
            content=str(d["content"]),
            provenance=dict(d["provenance"]),
            freshness_ts=float(d["freshness_ts"]),
            scope=str(d["scope"]),
            invalid_at=(float(inv) if inv is not None else None),
            invalid_reason=str(d.get("invalid_reason", "") or ""),
        )
    except Exception:  # noqa: BLE001
        return None


def attach_memory_emitter(mem, log: MeshLog, store=None) -> None:
    """把"写咽喉→发事件"的 outbox 钩子挂上 MemoryManager(console 入口接线时调一次)。

    本地新写(无 origin_device)→ 盖 origin_device + sync_id 戳 → 发 K_BELIEF 事件 → 持久化日志。
    远端回放来的(有 origin_device)→ 跳过(回声抑制)。钩子内任何异常由 write() 吞(写入是地基)。
    """
    def _emit(belief) -> None:
        prov = belief.provenance if isinstance(belief.provenance, dict) else {}
        if prov.get("origin_device"):
            return                                   # 远端回放 → 不再发(回声抑制)
        prov["origin_device"] = log.device_id        # 盖戳:这条认知诞生在本设备(审计,不降信任)
        prov.setdefault("sync_id", sync_id_for(belief.content))
        log.append(K_BELIEF, belief_to_payload(belief), wall=int(time.time() * 1000))
        if store is not None:
            store.persist_new(log)                   # 日志落盘;失败由钩子外层吞,写入不受累

    mem.on_write = _emit


def apply_belief_events(mem, events: Iterable[MeshEvent]) -> int:
    """把远端同步来的 belief 事件**幂等回放**进本地认知库(经现有写咽喉)。返回真落库条数。

    - 只认 K_BELIEF;坏 payload 跳过(宁空勿毒)。
    - **同 content 已在库(含已 archive)→ 跳过**:不重复、不复活、不覆盖本地态。
    - 经 mem.write() 回放 → 走全部现有校验/持久化;事件带 origin_device → 钩子不回声。
    """
    if mem is None:
        return 0
    applied = 0
    for ev in (events or []):
        if ev.kind != K_BELIEF:
            continue
        belief = belief_from_payload(ev.payload)
        if belief is None:
            continue
        try:
            if mem._index.get(belief.content) is not None:   # noqa: SLF001 — 同 content 在库:跳过
                continue
            if not isinstance(belief.provenance, dict) or not belief.provenance.get("origin_device"):
                # 防御:远端事件必须带出生地戳(没有就补成事件的 device_id),保回声抑制成立
                belief.provenance = dict(belief.provenance or {})
                belief.provenance["origin_device"] = ev.device_id or "peer"
            mem.write(belief)
            applied += 1
        except Exception:  # noqa: BLE001 — 单条坏事件不拖垮整轮回放
            logger.warning("[mesh] 回放一条远端认知失败,已跳过(不拖垮其余)")
            continue
    return applied


def replay_log_into_memory(mem, log: MeshLog) -> int:
    """启动/接线时的对账:把日志里**还不在库**的 belief 事件补回放(幂等)。

    覆盖"CLI 离线 mesh-sync 合并了日志、console 下次启动补上"的缝(影响评估接线项④)。
    """
    return apply_belief_events(mem, [e for e in log.entries() if e.kind == K_BELIEF])


__all__ = ["K_BELIEF", "sync_id_for", "belief_to_payload", "belief_from_payload",
           "attach_memory_emitter", "apply_belief_events", "replay_log_into_memory"]
