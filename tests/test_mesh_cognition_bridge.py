"""test_mesh_cognition_bridge — 真认知(Belief)接 MeshLog:outbox 发事件 + 幂等回放 + 端到端。

锁的不变量(影响评估三大冲突的解):
- store 保主真相,事件经现有写咽喉回放(冲突1);
- 同 content 幂等跳过、**绝不复活本地已失效的**(冲突2 + 防复活);
- 回声抑制:远端回放不再发事件(无限循环是同步系统的经典坟场)。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.mesh.cognition_bridge import (  # noqa: E402
    K_BELIEF, apply_belief_events, attach_memory_emitter, belief_from_payload,
    replay_log_into_memory, sync_id_for,
)
from karvyloop.mesh.synclog import HLC, MeshEvent, MeshLog  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402


def _belief(content, **kw):
    return Belief(content=content, provenance=kw.pop("provenance", {"source": "test"}),
                  freshness_ts=kw.pop("freshness_ts", 1.0), scope=kw.pop("scope", "personal"), **kw)


# ---- outbox:本地写 → 发事件(+回声抑制)----

def test_local_write_emits_event_with_origin_and_sync_id():
    mem, log = MemoryManager(), MeshLog("dev-a")
    attach_memory_emitter(mem, log)
    mem.write(_belief("咖啡要少糖"))
    evs = [e for e in log.entries() if e.kind == K_BELIEF]
    assert len(evs) == 1
    p = evs[0].payload
    assert p["content"] == "咖啡要少糖"
    assert p["provenance"]["origin_device"] == "dev-a"          # 出生地戳
    assert p["provenance"]["sync_id"] == sync_id_for("咖啡要少糖")  # 内容寻址 id(去重键对齐)


def test_remote_origin_write_does_not_reemit():
    """回声抑制:带别人 origin_device 的写(=远端回放)绝不再发事件(防无限循环)。"""
    mem, log = MemoryManager(), MeshLog("dev-b")
    attach_memory_emitter(mem, log)
    mem.write(_belief("A 学的", provenance={"source": "test", "origin_device": "dev-a"}))
    assert len([e for e in log.entries() if e.kind == K_BELIEF]) == 0


def test_emit_failure_never_breaks_write():
    """钩子炸了写入照常成功(同步是增益,写入是地基)。"""
    mem = MemoryManager()
    def _boom(_b):
        raise RuntimeError("mesh down")
    mem.on_write = _boom
    assert mem.write(_belief("写入不受累")) is True
    assert mem._index.get("写入不受累") is not None


# ---- 回放:远端事件 → 本地库(幂等 + 防复活 + 宁空勿毒)----

def _remote_ev(content, device="dev-a", wall=1000, payload_extra=None):
    p = {"content": content, "provenance": {"source": "t", "origin_device": device},
         "freshness_ts": 1.0, "scope": "personal", "invalid_at": None, "invalid_reason": ""}
    p.update(payload_extra or {})
    return MeshEvent(device_id=device, hlc=HLC(wall, 0), kind=K_BELIEF, payload=p)


def test_apply_lands_and_is_idempotent():
    mem = MemoryManager()
    ev = _remote_ev("A 设备学到的通用知识")
    assert apply_belief_events(mem, [ev]) == 1
    assert mem._index.get("A 设备学到的通用知识") is not None
    assert apply_belief_events(mem, [ev]) == 0                 # 再放一遍 → 幂等跳过


def test_apply_never_resurrects_local_invalidated():
    """本地已失效(archive/invalidate)的认知,远端旧事件**绝不复活**(同 content 在库=跳过)。"""
    mem = MemoryManager()
    mem.write(_belief("过时的做法"))
    mem.invalidate(mem._index.get("过时的做法"), reason="已过时")   # 失效不删,index 还在
    before = mem._index.get("过时的做法")
    assert before is not None and before.invalid_at is not None
    assert apply_belief_events(mem, [_remote_ev("过时的做法")]) == 0
    after = mem._index.get("过时的做法")
    assert after.invalid_at is not None, "远端旧事件把本地失效态复活了(防复活破了)"


def test_apply_skips_garbage_payload():
    mem = MemoryManager()
    bad = MeshEvent(device_id="x", hlc=HLC(1, 0), kind=K_BELIEF, payload={"content": None})
    assert belief_from_payload(bad.payload) is None            # 宁空勿毒
    assert apply_belief_events(mem, [bad]) == 0


def test_apply_ignores_non_belief_kinds():
    mem = MemoryManager()
    ev = MeshEvent(device_id="x", hlc=HLC(1, 0), kind="task-offer", payload={"task_id": "t"})
    assert apply_belief_events(mem, [ev]) == 0


# ---- 端到端:A 学的 B 拿到(写→事件→日志交换→回放→可召回)----

def test_two_device_cognition_sync_end_to_end():
    mem_a, log_a = MemoryManager(), MeshLog("dev-a")
    mem_b, log_b = MemoryManager(), MeshLog("dev-b")
    attach_memory_emitter(mem_a, log_a)
    attach_memory_emitter(mem_b, log_b)

    mem_a.write(_belief("Hardy 喜欢先对齐目标再讨论"))          # A 学到
    mem_b.write(_belief("周报每周五下午写"))                    # B 学到

    # 日志交换(delta gossip,同 sync 客户端机制)
    a2b, b2a = log_a.delta(log_b.frontier()), log_b.delta(log_a.frontier())
    fresh_b = [e for e in a2b if not log_b.contains(e.event_id)]
    fresh_a = [e for e in b2a if not log_a.contains(e.event_id)]
    log_b.merge(a2b, wall=2000)
    log_a.merge(b2a, wall=2000)
    apply_belief_events(mem_b, fresh_b)
    apply_belief_events(mem_a, fresh_a)

    # 双向:A 学的 B 可召回,B 学的 A 可召回
    assert mem_b._index.get("Hardy 喜欢先对齐目标再讨论") is not None
    assert mem_a._index.get("周报每周五下午写") is not None
    assert "先对齐目标" in mem_b.recall_block("怎么开讨论 对齐 目标")
    # 回声抑制:B 回放 A 的认知不产生新事件(两边日志条数一致=收敛稳定)
    assert len(log_a) == len(log_b) == 2


def test_replay_log_into_memory_reconciles_offline_merge():
    """CLI 离线 mesh-sync 合并了日志 → console 启动对账把还不在库的补回放(幂等)。"""
    mem, log = MemoryManager(), MeshLog("dev-b")
    log.merge([_remote_ev("离线同步来的认知")], wall=3000)     # 日志有、库还没有
    assert replay_log_into_memory(mem, log) == 1
    assert mem._index.get("离线同步来的认知") is not None
    assert replay_log_into_memory(mem, log) == 0               # 再对账 → 幂等
