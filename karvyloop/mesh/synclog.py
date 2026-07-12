"""mesh/synclog — 同主人设备间"共享日志"的排序+合并引擎(docs/74 §5.2 认知同步内核)。

这是 mesh 认知共享/共同沉淀的**正确性内核**:纯逻辑、transport 无关、可确定性测。
认知(Trace 事件 / 记忆 Belief / 技能结晶 / 偏好更新)都当**日志事件**,各设备各持一份日志,
靠交换"你没有的事件(delta)"收敛。上层物化视图(记忆库/技能库)从合并后的日志建。

**排序 = HLC(混合逻辑时钟)**,不是向量时钟(radar B:我们追加为主、极少并发改同一条,
用不上向量的冲突检测肌肉;HLC 贴墙钟+保因果序,更轻)。合并 = 按 HLC 全序去重。
**追加型事件天然无冲突并集合并**;同一条并发**改**的冲突消解在上层(保留双版本+升 H2A),不在此。

**第三镜**:事件 payload 语义自描述(Belief 是自然语言+结构元数据),新版设备读旧版事件靠
"模型读得懂",本层不做 schema 版本管理/upcaster —— 它只管**排序与合并**,不解释 payload 语义。
"""
from __future__ import annotations

import dataclasses
from typing import Dict, Iterable, List


@dataclasses.dataclass(frozen=True, order=True)
class HLC:
    """混合逻辑时钟:(wall 毫秒, counter)。字典序比较即因果序;counter 拆同一毫秒内的多事件。"""
    wall: int = 0
    counter: int = 0

    def __str__(self) -> str:
        return f"{self.wall}.{self.counter}"

    @staticmethod
    def parse(s: str) -> "HLC":
        w, _, c = (s or "0.0").partition(".")
        return HLC(int(w or 0), int(c or 0))


def hlc_tick(prev: HLC, wall_now: int) -> HLC:
    """本地产一个新事件的 HLC(单调):墙钟前进则重置 counter,否则同墙钟内 counter+1。"""
    if wall_now > prev.wall:
        return HLC(wall_now, 0)
    return HLC(prev.wall, prev.counter + 1)


def hlc_recv(local: HLC, remote: HLC, wall_now: int) -> HLC:
    """收到远端事件后推进本地时钟(HLC 标准算法),保证本地后续事件因果晚于已收的。"""
    w = max(wall_now, local.wall, remote.wall)
    if w == local.wall and w == remote.wall:
        c = max(local.counter, remote.counter) + 1
    elif w == local.wall:
        c = local.counter + 1
    elif w == remote.wall:
        c = remote.counter + 1
    else:
        c = 0
    return HLC(w, c)


@dataclasses.dataclass(frozen=True)
class MeshEvent:
    """共享日志的一条事件。event_id = device_id@hlc 全局唯一(去重键)。"""
    device_id: str
    hlc: HLC
    kind: str            # "trace" / "belief-created" / "skill-crystallized" / "pref-updated" ...
    payload: dict

    @property
    def event_id(self) -> str:
        return f"{self.device_id}@{self.hlc}"

    def to_dict(self) -> dict:
        return {"device_id": self.device_id, "hlc": str(self.hlc),
                "kind": self.kind, "payload": self.payload}

    @staticmethod
    def from_dict(d: dict) -> "MeshEvent":
        d = d or {}
        return MeshEvent(device_id=str(d.get("device_id") or ""),
                         hlc=HLC.parse(str(d.get("hlc") or "0.0")),
                         kind=str(d.get("kind") or ""), payload=dict(d.get("payload") or {}))


def _order_key(e: MeshEvent):
    """全序 = (HLC, device_id):HLC 相同(不同设备同一逻辑时刻)时按 device_id 稳定定序,保证
    所有设备合并后**顺序完全一致**(收敛的关键)。"""
    return (e.hlc, e.device_id)


class MeshLog:
    """一台设备持有的共享日志(append-only + 可合并)。收敛协议:交换 frontier → 传 delta → merge。

    - append(kind, payload, wall) → 本设备产一条新事件(HLC 单调)。
    - frontier() → {device_id: max HLC}(我有到哪了)。
    - delta(their_frontier) → 对方 frontier 之后我有的事件(要发给对方)。
    - merge(events, wall) → 收对方的 delta,去重 + 推进本地时钟。
    - entries() → 全序快照(所有设备合并后顺序一致)。
    """

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self._events: Dict[str, MeshEvent] = {}     # event_id -> event
        self._clock = HLC()

    def append(self, kind: str, payload: dict, *, wall: int) -> MeshEvent:
        self._clock = hlc_tick(self._clock, wall)
        ev = MeshEvent(self.device_id, self._clock, kind, dict(payload or {}))
        self._events[ev.event_id] = ev
        return ev

    def frontier(self) -> Dict[str, HLC]:
        out: Dict[str, HLC] = {}
        for ev in self._events.values():
            cur = out.get(ev.device_id)
            if cur is None or ev.hlc > cur:
                out[ev.device_id] = ev.hlc
        return out

    def delta(self, their_frontier: Dict[str, HLC]) -> List[MeshEvent]:
        """对方没有的事件(该设备的 hlc 超过对方 frontier;对方没这个设备则全给)。"""
        out: List[MeshEvent] = []
        for ev in self._events.values():
            tf = (their_frontier or {}).get(ev.device_id)
            if tf is None or ev.hlc > tf:
                out.append(ev)
        return sorted(out, key=_order_key)

    def merge(self, events: Iterable[MeshEvent], *, wall: int) -> int:
        """收对方 delta:加入我没有的(按 event_id 去重),每收一条推进本地时钟。返回新增条数。"""
        added = 0
        for ev in sorted(events, key=_order_key):
            if ev.event_id in self._events:
                continue                              # 已有 → 幂等,不二次加
            self._events[ev.event_id] = ev
            self._clock = hlc_recv(self._clock, ev.hlc, wall)
            added += 1
        return added

    def contains(self, event_id: str) -> bool:
        """这条事件在不在本日志(消费侧判"哪些是新来的"用,别摸私有 _events)。"""
        return (event_id or "") in self._events

    def entries(self) -> List[MeshEvent]:
        return sorted(self._events.values(), key=_order_key)

    def __len__(self) -> int:
        return len(self._events)


__all__ = ["HLC", "hlc_tick", "hlc_recv", "MeshEvent", "MeshLog"]
