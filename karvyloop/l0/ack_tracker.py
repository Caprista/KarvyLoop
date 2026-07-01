"""AckTracker — L0 ack 跟踪(l0/ack_tracker.py)。

F4-F5 不变量:ack_required=True 才进 pending() + 同一 agent 重复 ack 幂等。

设计:docs/24 §3.2。
"""
from __future__ import annotations

import dataclasses

from karvyloop.a2a import Envelope


@dataclasses.dataclass(frozen=True)
class AckState:
    """一条 L0 广播的 ack 状态。"""
    envelope_id: str            # 短 ID(signature 前 8 字节 hex)
    channel: str                # payload.tag
    message: str
    sent_at: str
    required: bool              # 是否要求 ack
    acknowledged_by: tuple[str, ...]  # 已 ack 的 agent_id 列表

    @property
    def pending_count(self) -> int:
        """未完成的 ack 数(简**化**:1 个主收件人)。"""
        return max(0, 1 - len(self.acknowledged_by))

    @property
    def is_acknowledged(self) -> bool:
        """是否**全**部**完**成** ack。"""
        return self.pending_count == 0


class AckTracker:
    """ack 跟踪器(注入式,无全局单例)。"""

    def __init__(self) -> None:
        self._states: dict[str, AckState] = {}

    def track(self, env: Envelope, required: bool) -> AckState:
        """新**广**播**到**来**时**开**始**跟**踪**。**返**回**新**建**的** AckState。"""
        env_id = self._envelope_id(env)
        # 已**经**跟**踪**过**了**(**同**一** envelope **的** signature **一**致**)?
        if env_id in self._states:
            return self._states[env_id]
        state = AckState(
            envelope_id=env_id,
            channel=env.payload.tag if hasattr(env.payload, "tag") else "unknown",
            message=env.payload.message if hasattr(env.payload, "message") else "",
            sent_at=env.ts,
            required=required,
            acknowledged_by=(),
        )
        self._states[env_id] = state
        return state

    def acknowledge(self, env_id: str, by_agent: str) -> AckState:
        """记**录** ack(**同**一** agent **重**复** ack **不**抛**错**,F5 **强**制**)**。

        Raises:
            KeyError: env_id 未跟踪。
        """
        if env_id not in self._states:
            raise KeyError(f"envelope_id '{env_id}' 未跟踪")
        state = self._states[env_id]
        # F5: 幂等 — 同一 agent 不重复加
        if by_agent in state.acknowledged_by:
            return state
        new_state = AckState(
            envelope_id=state.envelope_id,
            channel=state.channel,
            message=state.message,
            sent_at=state.sent_at,
            required=state.required,
            acknowledged_by=state.acknowledged_by + (by_agent,),
        )
        self._states[env_id] = new_state
        return new_state

    def pending(self) -> tuple[AckState, ...]:
        """未**完**成** ack **的**广**播**列**表**(F4 强**制**:**只**返** required=True **且**未**完**成**)**)"""
        return tuple(
            s for s in self._states.values()
            if s.required and not s.is_acknowledged
        )

    def all_states(self) -> tuple[AckState, ...]:
        """所有**跟**踪**的** AckState(**测**试**用**)**。"""
        return tuple(self._states.values())

    def count(self) -> int:
        return len(self._states)

    @staticmethod
    def _envelope_id(env: Envelope) -> str:
        """短** ID(signature **前** 8 **字**节** hex,**唯**一**性**靠** signature**)。"""
        if not env.signature:
            return f"empty-{env.ts}"
        return env.signature[:8].hex()
