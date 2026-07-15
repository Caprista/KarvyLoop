"""mesh — 同主人多设备协同(docs/74)。

**这是"我的多台设备=一个 karvyloop 的资源池"那条线,不是"分享给别人"(docs/73)。**
同主人 = 天然全信任域(relay `SCOPE_FULL="你自己的设备"`已编码此语义),认知可双向同步。

第一刀(本模块当前范围):**设备能力指纹 + 设备花名册**(radar 定的最省起点)——
"我有哪些设备、各自什么能力、上次见到是什么时候"。复用 relay 全栈:每台设备 = 自己的
relay 房间(`console --relay`),互访 = `remote` 进对方房间(slice 3a/3b 已建),点对点已通。

**明确待后续(设计较重,需先定 transport)**:
- 活 presence(实时"谁在线")——靠 relay 长连生死当心跳,需 relay 报设备在线态。
- 认知同步——op-log + HLC 建在 Trace 上(第三镜重判:事件语义自描述,不写永久 upcaster;
  见 docs/74 §2 + [[world-radar-third-lens-pre-llm-assumption]])。
- 调度协同——feasibility→ranking,croniter 当入口。
"""
from karvyloop.mesh.compact import compact_mesh_log
from karvyloop.mesh.fingerprint import device_fingerprint
from karvyloop.mesh.registry import DeviceRecord, DeviceRegistry
from karvyloop.mesh.schedule import (
    capability_delta_on_remove, feasible, feasible_devices, scheduled_task_at_risk,
)
from karvyloop.mesh.skill_bridge import apply_skill_events, attach_skill_emitter, notify_crystallized
from karvyloop.mesh.store import MeshLogStore
from karvyloop.mesh.synclog import HLC, MeshEvent, MeshLog
from karvyloop.mesh.tasks import (
    TaskState, claim_task, claimable_for, complete_task, heartbeat_task,
    materialize_tasks, offer_task,
)

__all__ = ["device_fingerprint", "DeviceRecord", "DeviceRegistry",
           "HLC", "MeshEvent", "MeshLog",
           "feasible", "feasible_devices", "capability_delta_on_remove", "scheduled_task_at_risk",
           "TaskState", "offer_task", "claim_task", "heartbeat_task", "complete_task",
           "materialize_tasks", "claimable_for", "MeshLogStore", "compact_mesh_log",
           "attach_skill_emitter", "notify_crystallized", "apply_skill_events"]
