"""routes_mesh — 设备 mesh 同步端点(/api/mesh/*):设备间交换 MeshLog delta(docs/74)。

两设备真同步的 **console 一半**:对端(经 relay 的 remote 客户端)拉本机 frontier + 推它的 delta,
本机合并 + 持久化 + 回本机的 delta。一个来回 = 双向 gossip 收敛(和 synclog 收敛测同机制)。
客户端一半(`mesh sync <peer>` 经 relay 调这两个端点)是要两机验的那步。

**同主人**:mesh 日志是"我的认知/任务"在我设备间流动,E2E 经 relay(信使不拆信),不出我边界。
自带 APIRouter,由 app.py include_router。
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from karvyloop.mesh.synclog import HLC, MeshEvent

router = APIRouter(prefix="/api")


def _mesh_state_dir(app):
    return getattr(app.state, "mesh_state_dir", None)   # None = 默认 ~/.karvyloop;测试注入 tmp


def _mesh_log(app):
    """取/建本机 MeshLog(从盘加载,device_id=relay 身份指纹;无身份退回 'local')。"""
    log = getattr(app.state, "mesh_log", None)
    if log is not None:
        return log
    from karvyloop.mesh.fingerprint import device_fingerprint
    from karvyloop.mesh.store import MeshLogStore
    sd = _mesh_state_dir(app)
    did = device_fingerprint(sd).get("device_id") or "local"
    store = MeshLogStore(sd)
    log = store.open_log(did)
    app.state.mesh_log = log
    app.state.mesh_log_store = store
    return log


def _frontier_dict(log) -> dict:
    return {d: str(h) for d, h in log.frontier().items()}


@router.get("/mesh/frontier")
def api_mesh_frontier(request: Request) -> dict[str, Any]:
    """本机 MeshLog 前沿(对端据此算它该给我什么、我该给它什么)。"""
    log = _mesh_log(request.app)
    return {"device_id": log.device_id, "frontier": _frontier_dict(log)}


class MeshSyncRequest(BaseModel):
    frontier: dict = Field(default_factory=dict, description="对端已有到哪 device_id -> 'wall.counter'")
    events: list = Field(default_factory=list, description="对端给我的 delta(事件 dict 列表)")


@router.post("/mesh/sync")
def api_mesh_sync(req: MeshSyncRequest, request: Request) -> dict[str, Any]:
    """收对端 delta → 合并 + 持久化 → 回我按对端 frontier 算出的 delta(一来回双向同步)。"""
    log = _mesh_log(request.app)
    incoming = []
    for e in (req.events or [])[:20000]:            # 封顶防滥用
        try:
            incoming.append(MeshEvent.from_dict(e))
        except Exception:                            # noqa: BLE001 — 坏事件跳过,不阻塞
            continue
    fresh = [e for e in incoming if not log.contains(e.event_id)]   # merge 前定格"哪些是新来的"
    added = log.merge(incoming, wall=int(time.time() * 1000))
    store = getattr(request.app.state, "mesh_log_store", None)
    if store is not None and added:
        try:
            store.persist_new(log)
        except Exception:                            # noqa: BLE001 — 持久化失败不阻断同步
            pass
    # 真认知落地(docs/74 slice2):新来的 belief 事件幂等回放进本地认知库(store 保主真相,
    # 经现有写咽喉 mem.write;同 content 已在库跳过,绝不复活/覆盖本地态)。未接 memory → 跳过。
    mem = getattr(request.app.state, "memory", None)
    if mem is not None and fresh:
        try:
            from karvyloop.mesh.cognition_bridge import apply_belief_events
            apply_belief_events(mem, fresh)
        except Exception:                            # noqa: BLE001 — 回放失败不阻断同步本身
            pass
    # 技能事件(slice3a):远端结晶的技能落进本地技能树(幂等,同 name 已在跳过)。
    skills_dir = getattr(request.app.state, "mesh_skills_dir", None)
    if skills_dir is not None and fresh:
        try:
            from karvyloop.mesh.skill_bridge import apply_skill_events
            apply_skill_events(fresh, skills_dir)
        except Exception:                            # noqa: BLE001
            pass
    their_fr = {}
    for d, v in (req.frontier or {}).items():
        try:
            their_fr[str(d)] = HLC.parse(str(v))
        except Exception:                            # noqa: BLE001
            continue
    out = [e.to_dict() for e in log.delta(their_fr)]
    return {"merged": added, "events": out, "frontier": _frontier_dict(log)}


# ---------------------------------------------------------------------------
# 设备花名册(用户可见面 —— cli.cmd_devices 的 console 半身,同语义)
# ---------------------------------------------------------------------------

@router.get("/mesh/devices")
def api_mesh_devices(request: Request) -> dict[str, Any]:
    """我的设备 mesh:本机自注册(刷新 last_seen)+ 列花名册(能力指纹/在线态/本机标记)。"""
    from karvyloop.mesh.fingerprint import device_fingerprint
    from karvyloop.mesh.registry import DeviceRegistry
    sd = _mesh_state_dir(request.app)
    reg = DeviceRegistry(sd)
    fp = device_fingerprint(sd)
    reg.register_self(fp)                            # 无 relay 身份 → None,不入册(可诚实提示)
    devs = sorted(reg.list_all(), key=lambda d: (not d.is_self, d.label, d.device_id))
    out = []
    for d in devs:
        rec = d.to_dict()
        rec["online"] = d.online()
        out.append(rec)
    return {"devices": out, "self_id": str(fp.get("device_id") or ""),
            "has_identity": bool(fp.get("device_id"))}


class MeshDeviceRemoveRequest(BaseModel):
    device_id: str = ""
    confirm: bool = Field(default=False, description="收窄/删本机时须显式二次确认")


@router.post("/mesh/devices/remove")
def api_mesh_device_remove(req: MeshDeviceRemoveRequest, request: Request) -> dict[str, Any]:
    """知情删除(docs/74 §6.2,与 cli.cmd_devices_remove 同语义):删前算**能力增量**——
    该设备独占的能力(其它设备都没有)非空 = 能力边界收窄,或删的是本机 → 必须 confirm=true
    再动手;否则先回 requires_confirm + 会永久失去什么,让人知情后拍板(H2A)。"""
    from karvyloop.mesh.registry import DeviceRegistry
    from karvyloop.mesh.schedule import capability_delta_on_remove
    reg = DeviceRegistry(_mesh_state_dir(request.app))
    devs = reg.list_all()
    dev = next((d for d in devs if d.device_id == (req.device_id or "")), None)
    if dev is None:
        return {"ok": False, "reason": "not_found"}
    lost = sorted(capability_delta_on_remove(dev, devs))
    if (lost or dev.is_self) and not req.confirm:
        return {"ok": False, "requires_confirm": True, "narrowed": lost,
                "is_self": dev.is_self, "label": dev.label or dev.device_id}
    reg.remove(dev.device_id)
    return {"ok": True, "removed": True, "narrowed": lost, "is_self": dev.is_self}


__all__ = ["router"]
