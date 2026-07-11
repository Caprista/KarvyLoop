"""collab/gate — 防平行独白两件套(docs/73 §4):可见性 gate 白名单 + per-(room,member) 限流。

@ 提及派发已有(routes/workflow_engine 把 mention 解析成成员);本模块加两道确定性闸,
把"派给谁"从"匹配到就派"收紧成"在册且没被限流才派":

1. **VisibilityGate**:每 Room 的成员白名单 —— **只有 Room 在册成员才能被派发**。
   防平行独白的第一道:不在这个场里的参与者,@ 到也不派(deny-by-default)。

2. **MemberRateLimiter**:per-(room, member) 限流 —— 同一个成员在一个 Room 里 min_interval
   窗口内只派一次。防止一句话把同一成员刷屏 / 多轮把外部执行体打爆 / 平行独白。
   纯内存(限流是"别烦人"的礼貌,不是账;重启清零可接受,同 AmbientCooldown 先例)。

**为什么不直接复用 AmbientCooldown**:AmbientCooldown 键是 intent 指纹(同信号冷却),
本处键是 (room, member) 身份对(同成员冷却)——语义不同。但**冷却实现同构**(dict + ttl +
有界清理),这里写一个最小的身份键版本,不硬套 intent 指纹那套。
"""
from __future__ import annotations

import time
from typing import Iterable, Optional

# per-(room,member) 默认最小派发间隔(秒):同一成员在同一 Room 内的连续派发下限。
# 默认 0 = 不限流(零回归:没显式设就是老行为);>0 才生效。
DEFAULT_MIN_INTERVAL_S = 0.0


class VisibilityGate:
    """每 Room 成员白名单闸:只有在册成员能被派发(防平行独白第一道)。

    纯逻辑,无状态 —— 白名单来自 Room.member_ids();派发前用 `allow(room, pid)` 过一遍。
    """

    @staticmethod
    def allowed_ids(room) -> set[str]:
        """Room 的可派发白名单(在册成员 participant_id 集合)。room=None → 空(全拒)。"""
        if room is None:
            return set()
        try:
            return room.member_ids()
        except Exception:  # noqa: BLE001 — 取不到白名单当空(deny-by-default)
            return set()

    @classmethod
    def allow(cls, room, participant_id: str) -> bool:
        """这个 participant 能不能在这个 Room 被派发?只有在册成员能(deny-by-default)。"""
        return bool(participant_id) and participant_id in cls.allowed_ids(room)

    @classmethod
    def filter_targets(cls, room, participant_ids: Iterable[str]) -> list[str]:
        """把一批候选目标过滤成"在册可派"的子集(保序去重)。不在册的静默丢(记在调用侧)。"""
        wl = cls.allowed_ids(room)
        out: list[str] = []
        seen: set[str] = set()
        for pid in (participant_ids or ()):
            if pid and pid in wl and pid not in seen:
                seen.add(pid)
                out.append(pid)
        return out


class MemberRateLimiter:
    """per-(room, member) 限流:同成员在同 Room 的 min_interval 窗口内只派一次。

    纯内存(重启清零,可接受)。键 = (room_id, participant_id)。`allow` 只查不改;`mark` 记时戳。
    典型用法:派发前 `if limiter.allow(room_id, pid): dispatch(); limiter.mark(room_id, pid)`。
    """

    def __init__(self, min_interval_s: float = DEFAULT_MIN_INTERVAL_S) -> None:
        self._min = max(0.0, float(min_interval_s))
        self._last: dict[tuple[str, str], float] = {}

    def allow(self, room_id: str, participant_id: str, *, now: Optional[float] = None) -> bool:
        """这个 (room, member) 现在能不能被派发?间隔 0 → 恒 True(零回归)。"""
        if self._min <= 0.0:
            return True
        key = (room_id or "", participant_id or "")
        ts = self._last.get(key)
        if ts is None:
            return True
        _now = time.time() if now is None else now
        return (_now - ts) >= self._min

    def mark(self, room_id: str, participant_id: str, *, now: Optional[float] = None) -> None:
        """记一次派发时戳(有界清理,防表膨胀)。"""
        if self._min <= 0.0:
            return
        _now = time.time() if now is None else now
        if len(self._last) > 512:   # 有界:顺手清过期(同 AmbientCooldown 先例)
            self._last = {k: v for k, v in self._last.items() if (_now - v) < self._min}
        self._last[(room_id or "", participant_id or "")] = _now

    def gate_and_mark(self, room_id: str, participant_id: str,
                      *, now: Optional[float] = None) -> bool:
        """一步:能派就 mark 并返 True;被限流返 False。方便调用点单行使用。"""
        if self.allow(room_id, participant_id, now=now):
            self.mark(room_id, participant_id, now=now)
            return True
        return False


__all__ = ["VisibilityGate", "MemberRateLimiter", "DEFAULT_MIN_INTERVAL_S"]
