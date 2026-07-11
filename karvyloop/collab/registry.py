"""collab/registry — Room 一等对象 + RoomRegistry(持久化)。

Room = 协作场:id + 成员表(每成员 opacity 档)+ containment(每 channel 隔离 workspace_root
+ egress 作用域 + 访问 scope)。**不新增实体**——成员是 role/citizen 的引用,寻址复用复合键
`(域/room, participant_id)`(docs/00 §2.6)。

**containment 落地**:每个 Room 带自己的 RoomScope(workspace_root + egress_allowlist +
access_scope);编排器派活时从 Room 读 containment,交给 platform 沙箱强制(硬件不是可选)。

**M3 边界**:本对象只描述本地进程内的协作场;活托管 / 远程访问 / 回源校验 = docs/73 §6 M3。
`direction` 字段留位不驱动跨设备传输。
"""
from __future__ import annotations

import dataclasses
import time
from typing import Optional

from .room import (
    OPACITY_INTERNAL,
    PARTICIPANT_EXTERNAL,
    PARTICIPANT_ROLE,
    RoomMember,
    RoomScope,
)


@dataclasses.dataclass(frozen=True)
class Room:
    """一个一等协作场(承接自家 role + 外部 opaque 执行体)。

    - `room_id`:场的稳定 id(寻址用;域圆桌可用 `域::<domain_id>` 派生,l0 大群同理)。
    - `members`:成员表(RoomMember;每成员一档 opacity)。
    - `scope`:containment 三件(隔离 workspace_root + egress 作用域 + 访问 scope)。
    - `owner`:场主(默认 user;M3 托管访问会区分"我的 / 别人给我托管访问的")。
    - `origin_domain_id`:这个场投影自哪个域(域圆桌=该域;l0 大群=KARVY_WORLD_DOMAIN);
      **协作场是域的投影,不是替代**(本体论:Room 引用域,不吞并它)。
    """
    room_id: str
    members: tuple[RoomMember, ...] = ()
    scope: RoomScope = dataclasses.field(default_factory=RoomScope)
    owner: str = "user"
    origin_domain_id: str = ""
    title: str = ""
    created_at: float = 0.0

    def internal_members(self) -> list[RoomMember]:
        """自家 role 成员(产出进对话主线、占决策席)。"""
        return [m for m in self.members if m.kind == PARTICIPANT_ROLE and m.enters_mainline()]

    def external_members(self) -> list[RoomMember]:
        """外部 opaque 成员(供稿席,产出 untrusted 不进主线)。"""
        return [m for m in self.members if m.kind == PARTICIPANT_EXTERNAL]

    def member(self, participant_id: str, domain_id: str = "") -> Optional[RoomMember]:
        """按复合键 (域, participant_id) 取成员;域给空时退回按 id 任一匹配(私聊/无域)。"""
        pid = participant_id or ""
        did = domain_id or ""
        for m in self.members:
            if m.participant_id == pid and m.domain_id == did:
                return m
        if not did:
            for m in self.members:
                if m.participant_id == pid:
                    return m
        return None

    def member_ids(self) -> set[str]:
        """成员 participant_id 集合(可见性 gate 的白名单来源:只有在册成员能被派发)。"""
        return {m.participant_id for m in self.members if m.participant_id}

    def with_members(self, members) -> "Room":
        return dataclasses.replace(self, members=tuple(members))

    def to_dict(self) -> dict:
        return {
            "room_id": self.room_id,
            "members": [m.to_dict() for m in self.members],
            "scope": self.scope.to_dict(),
            "owner": self.owner,
            "origin_domain_id": self.origin_domain_id,
            "title": self.title,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "Room":
        d = d or {}
        return Room(
            room_id=str(d.get("room_id") or ""),
            members=tuple(RoomMember.from_dict(m) for m in (d.get("members") or [])),
            scope=RoomScope.from_dict(d.get("scope") or {}),
            owner=str(d.get("owner") or "user"),
            origin_domain_id=str(d.get("origin_domain_id") or ""),
            title=str(d.get("title") or ""),
            created_at=float(d.get("created_at") or 0.0),
        )


class RoomRegistry:
    """Room 注册表(持久化;用户数据默认存盘 —— [[user-data-persists-by-default]])。

    键 = room_id。持久化经注入的 `store`(load_all / save_all);无 store = 纯内存(测试)。
    加载失败不炸(空表起步,fail-loud 记 persist_error)。
    """

    def __init__(self, *, store=None) -> None:
        self._by_id: dict[str, Room] = {}
        self._store = store
        self.persist_error: str = ""
        if store is not None:
            try:
                for d in (store.load_all() or []):
                    r = Room.from_dict(d)
                    if r.room_id:
                        self._by_id[r.room_id] = r
            except Exception as e:  # noqa: BLE001 — 加载失败不炸,空表起步
                self.persist_error = f"load: {type(e).__name__}: {e}"

    def create(self, room_id: str, *, members=(), scope: Optional[RoomScope] = None,
               owner: str = "user", origin_domain_id: str = "", title: str = "",
               now: Optional[float] = None) -> tuple[Optional[Room], str]:
        """建一个 Room。room_id 已存在 → 拒(别覆盖在场协作;fail-loud)。返回 (room, error)。"""
        rid = (room_id or "").strip()
        if not rid:
            return None, "需要 room_id"
        if rid in self._by_id:
            return None, f"Room「{rid}」已存在"
        room = Room(
            room_id=rid, members=tuple(members),
            scope=scope if scope is not None else RoomScope(),
            owner=owner or "user", origin_domain_id=origin_domain_id or "",
            title=title or "", created_at=now if now is not None else time.time())
        self._by_id[rid] = room
        self._persist()
        return room, ""

    def get(self, room_id: str) -> Optional[Room]:
        return self._by_id.get(room_id or "")

    def add_member(self, room_id: str, member: RoomMember) -> tuple[Optional[Room], str]:
        """往 Room 加一个成员(复合键去重:同 (域, participant_id) 覆盖档,不重复上桌)。"""
        room = self._by_id.get(room_id or "")
        if room is None:
            return None, f"没有 Room「{room_id}」"
        key = member.composite_key()
        kept = [m for m in room.members if m.composite_key() != key]
        kept.append(member)
        room = room.with_members(kept)
        self._by_id[room_id] = room
        self._persist()
        return room, ""

    def remove_member(self, room_id: str, participant_id: str, domain_id: str = "") -> bool:
        """从 Room 撤一个成员(复合键)。返回是否有此成员。"""
        room = self._by_id.get(room_id or "")
        if room is None:
            return False
        key = (domain_id or "", participant_id or "")
        kept = tuple(m for m in room.members if m.composite_key() != key)
        if len(kept) == len(room.members):
            return False
        self._by_id[room_id] = room.with_members(kept)
        self._persist()
        return True

    def set_scope(self, room_id: str, scope: RoomScope) -> bool:
        """替换 Room 的 containment(workspace/egress/access_scope)。返回是否有此 Room。"""
        room = self._by_id.get(room_id or "")
        if room is None:
            return False
        self._by_id[room_id] = dataclasses.replace(room, scope=scope)
        self._persist()
        return True

    def remove(self, room_id: str) -> bool:
        if (room_id or "") not in self._by_id:
            return False
        self._by_id.pop(room_id, None)
        self._persist()
        return True

    def list_all(self) -> list[Room]:
        return list(self._by_id.values())

    def _persist(self) -> bool:
        if self._store is None:
            return True
        try:
            self._store.save_all([r.to_dict() for r in self._by_id.values()])
            self.persist_error = ""
            return True
        except Exception as e:  # noqa: BLE001
            self.persist_error = f"{type(e).__name__}: {e}"
            return False


__all__ = ["Room", "RoomRegistry"]
