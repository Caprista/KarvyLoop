"""serializers — dataclass → JSON-friendly dict(M3+ 批 8.5-C)。

设计:plans/snoopy-singing-sunbeam.md §批 8.5-C。

K 边界:K4 — 只做 JSON 转换,**不**构造 Envelope / **不**调 apply_*。
借:Q5 — 借 stdlib `dataclasses.asdict`;**自造**极薄一层处理 envelope payload。
"""
from __future__ import annotations

import dataclasses
from typing import Any

from karvyloop.a2a import Envelope
from karvyloop.cli.main_loop import DriveResult
from karvyloop.workbench.main_loop_bridge import DriveOutcome
from karvyloop.workbench.snapshot import WidgetSnapshot


def widget_snapshot(snap: WidgetSnapshot) -> dict[str, Any]:
    """WidgetSnapshot → dict(JSON-friendly)。"""
    return {
        "domains": list(snap.domains),
        "current_domain": snap.current_domain,
        "broadcasts": [envelope_to_dict(e) for e in snap.broadcasts],
        "task_count": snap.task_count,
        "pursuit_count": snap.pursuit_count,
        "unhealthy": snap.unhealthy,
        "crystallized_skills": list(snap.crystallized_skills),
        "last_fast_brain_skill": snap.last_fast_brain_skill,
        "last_drive_text": snap.last_drive_text,
        "last_error": snap.last_error,
        "last_intent": snap.last_intent,
    }


def drive_result_to_dict(result: DriveResult) -> dict[str, Any]:
    """DriveResult → dict(JSON-friendly)。"""
    return {
        "brain": str(result.brain),
        "intent": result.intent,
        "text": result.text,
        "sig": result.sig,
        "skill_name": result.skill_name,
        "restored": result.restored,
        "crystallized": result.crystallized,
        "fast_brain_hit": result.fast_brain_hit,
        "task_id": result.task_id,
    }


def drive_outcome_to_dict(outcome: DriveOutcome) -> dict[str, Any]:
    """DriveOutcome(TUI 桥)→ dict(JSON-friendly)。"""
    return {
        "intent": outcome.intent,
        "brain": str(outcome.brain),
        "text": outcome.text,
        "skill_name": outcome.skill_name,
        "fast_brain_hit": outcome.fast_brain_hit,
        "crystallized": outcome.crystallized,
        "error": outcome.error,
        "events": list(getattr(outcome, "events", []) or []),  # 9.4:结构化渲染事件
    }


def envelope_to_dict(env: Envelope) -> dict[str, Any]:
    """Envelope → dict(JSON-friendly)。

    payload 可能是 dataclass 或裸 dict;dataclass 用 asdict,dict 保留。
    """
    payload = env.payload
    if dataclasses.is_dataclass(payload) and not isinstance(payload, type):
        payload_dict = dataclasses.asdict(payload)
    elif isinstance(payload, dict):
        payload_dict = dict(payload)
    else:
        payload_dict = {"_repr": str(payload)}
    return {
        "type": str(env.type),
        "from": str(env.from_),
        "by": [str(b) for b in env.by],
        "to": str(env.to),
        "payload": payload_dict,
        "ts": env.ts,
    }


__all__ = [
    "widget_snapshot",
    "drive_result_to_dict",
    "drive_outcome_to_dict",
    "envelope_to_dict",
]
