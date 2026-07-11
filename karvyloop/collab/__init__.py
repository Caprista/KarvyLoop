"""collab — agent 参与者的协作场:一等 Room/Channel 原语(docs/73 §4 基座)。

**为什么在 `collab/` 而不在 `channels/`**:`channels/`(email/webhook/inbox_pipe)是**人机 I/O
通道**——把外部世界的消息喂进小卡、把小卡的产出送出去。本模块是**agent 参与者的协作场**:
一群参与者(自家 role + 外部 opaque 执行体)围绕一个场协作。两者语义正交,别混淆命名。

**本体论对齐(docs/00 §2.6 / §2.6.5 · docs/73 §4)**——Room **不是新实体**:
- 成员 = 已有实体的引用:自家 role(L2)、外部 citizen(第四类 opaque 执行体);Room 不新造实体类。
- 寻址 = 复用现有复合键 `(域/room, participant_id)`——Room 落在与 domain 同一个地址空间,
  是"协作场"投影,不是替代 domain/role/citizen。域圆桌 / l0 大群都是它的特例。
- 编排 = **不重造圆桌**:圆桌引擎仍是编排器;Room 只提供**成员表 + 每成员 opacity 档 +
  每 channel containment(隔离 workspace_root + egress 作用域 + 访问 scope)**,供编排器读。

**containment 是硬件不是可选(docs/73 §0.5 四件)**:每个 Room 一个隔离 workspace_root +
自己的 egress allowlist(复用 #71 net_allowlist)+ scope 切片。对方在这个 Room 的 workspace 里
协作,够不到更广环境。这是访问的最小授权,由 platform 沙箱强制。

**M3 边界(本基座**不**含)**:活托管镜像 / 远程访问 / 回源在线校验(撤销)/ mesh 签名身份
= docs/73 §6 的 M3 phase。本基座只做**本地进程内的 Room 抽象 + 收敛现有圆桌客人席**,
不含跨设备通道。方向性字段(participant kind)已留位,但不实现活托管传输。
"""
from __future__ import annotations

from .gate import MemberRateLimiter, VisibilityGate
from .room import (
    OPACITY_INTERNAL,
    OPACITY_OPAQUE,
    OPACITY_OPAQUE_TEAM,
    PARTICIPANT_EXTERNAL,
    PARTICIPANT_ROLE,
    RoomMember,
    RoomScope,
)
from .registry import Room, RoomRegistry

__all__ = [
    "Room",
    "RoomRegistry",
    "RoomMember",
    "RoomScope",
    "VisibilityGate",
    "MemberRateLimiter",
    "OPACITY_INTERNAL",
    "OPACITY_OPAQUE",
    "OPACITY_OPAQUE_TEAM",
    "PARTICIPANT_ROLE",
    "PARTICIPANT_EXTERNAL",
]
