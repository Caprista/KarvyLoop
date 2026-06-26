"""Envelope ↔ JSON 编码(transport/envelope_codec.py)。

Q2 兑现式:字段顺序稳定(sort_keys)+ 签名 hex 保留 = 跨进程 byte-stable。
参考 docs/22 §3.4。
"""
from __future__ import annotations

import dataclasses
import json
from typing import Any

from karvyloop.domain import Address

from ..envelope import Envelope, EnvelopeType
from ..envelope import (
    QA,                              # 兼容旧引用
    BroadcastPayload,
    RejectPayload,
    ProposePayload,
    TaskPayload,
)


def _payload_to_dict(payload: Any) -> dict:
    """Envelope payload → dict(支持 dataclass + dict 两种)。"""
    if dataclasses.is_dataclass(payload) and not isinstance(payload, type):
        return dataclasses.asdict(payload)
    if isinstance(payload, dict):
        return dict(payload)
    # 兜底:不强类型 fallback
    return {"_raw": str(payload)}


def _payload_from_dict(d: dict, env_type: str) -> Any:
    """dict → payload dataclass(按 env_type 分发,11 个 EnvelopeType 各归各位)。

    5 问硬规则 T8:必须保留签名 + payload 类型 round-trip。
    """
    if env_type == EnvelopeType.BROADCAST.value:
        return BroadcastPayload(**d)
    if env_type == EnvelopeType.REJECT.value:
        return RejectPayload(**d)
    if env_type == EnvelopeType.PROPOSE.value:
        return ProposePayload(**d)
    if env_type in (EnvelopeType.TASK_ASSIGN.value, EnvelopeType.TASK_PROGRESS.value, EnvelopeType.TASK_DONE.value):
        return TaskPayload(**d) if d else d
    if env_type in (EnvelopeType.ASK.value, EnvelopeType.ANSWER.value):
        return QA(**d) if d else d
    # audit_request / audit_response 暂用 dict(本拍未涉及,留拍 4)
    return d


def _addr_to_dict(addr: Address) -> dict:
    return {"domain_id": addr.domain_id, "role": addr.role, "agent_id": addr.agent_id}


def _addr_from_dict(d: dict) -> Address:
    return Address(domain_id=d["domain_id"], role=d["role"], agent_id=d["agent_id"])


def encode(env: Envelope) -> bytes:
    """Envelope → JSON bytes(签名字段保留为 hex)。

    8 不变量 T8 强制:signature 字段**不**丢。
    """
    d = {
        "type": env.type,
        "from": _addr_to_dict(env.from_),
        "by": [_addr_to_dict(b) for b in env.by],
        "to": _addr_to_dict(env.to),
        "payload": _payload_to_dict(env.payload),
        "ts": env.ts,
        "signature": env.signature.hex() if env.signature else "",
    }
    return json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")


def decode(raw: bytes) -> Envelope:
    """JSON bytes → Envelope(签名 hex → bytes)。

    Raises:
        json.JSONDecodeError: raw 不是合法 JSON。
        KeyError: 必**须**字段缺失。
        ValueError: payload 反序列化失败。
    """
    d = json.loads(raw.decode("utf-8"))
    return Envelope(
        type=d["type"],
        from_=_addr_from_dict(d["from"]),
        by=tuple(_addr_from_dict(b) for b in d["by"]),
        to=_addr_from_dict(d["to"]),
        payload=_payload_from_dict(d["payload"], d["type"]),
        ts=d["ts"],
        signature=bytes.fromhex(d["signature"]) if d.get("signature") else b"",
    )
