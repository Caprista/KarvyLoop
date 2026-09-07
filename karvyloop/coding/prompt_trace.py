"""可回溯的 prompt 组成及基于快照的 merge 工具。"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PromptPart:
    """一个最终发送给模型的 prompt 片段及其真实来源。"""

    id: str
    text: str
    source: str = "system"  # soul | system | memory | context | user | runtime
    label: str = ""
    editable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "source": self.source,
                "label": self.label or self.id, "editable": self.editable}


@dataclass
class PromptTrace:
    """一次模型调用使用的 prompt 组成快照。"""

    parts: list[PromptPart] = field(default_factory=list)
    version: int = 1

    @property
    def digest(self) -> str:
        payload = "\n".join(f"{p.id}:{p.text}" for p in self.parts)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "digest": self.digest,
                "parts": [p.to_dict() for p in self.parts]}

    def merge(self, changes: dict[str, Any], *, base_digest: str = "") -> "PromptTrace":
        """像代码 merge 一样按 part id 合并，拒绝基线已变化的盲写。"""
        if base_digest and base_digest != self.digest:
            raise ValueError("prompt snapshot is stale")
        by_id = {p.id: p for p in self.parts}
        for part_id, raw in changes.items():
            if part_id not in by_id:
                raise KeyError(part_id)
            if not isinstance(raw, str):
                raise TypeError(part_id)
            old = by_id[part_id]
            if not old.editable:
                raise ValueError(f"prompt part is read-only: {part_id}")
            by_id[part_id] = PromptPart(old.id, raw, old.source, old.label, old.editable)
        return PromptTrace([by_id[p.id] for p in self.parts], self.version + 1)


def prompt_trace_from_blocks(static: list[str], dynamic: list[str], *,
                             static_source: str = "system",
                             dynamic_source: str = "runtime",
                             labels: list[str] | None = None) -> PromptTrace:
    parts: list[PromptPart] = []
    names = labels or []
    for index, text in enumerate(static):
        part_id = f"static-{index}"
        parts.append(PromptPart(part_id, text, static_source,
                                names[index] if index < len(names) else part_id))
    for index, text in enumerate(dynamic):
        parts.append(PromptPart(f"dynamic-{index}", text, dynamic_source,
                                f"dynamic-{index}"))
    return PromptTrace(parts)


def trace_from_dict(raw: dict[str, Any]) -> PromptTrace:
    parts = []
    for item in raw.get("parts", []):
        if isinstance(item, dict) and item.get("id"):
            parts.append(PromptPart(str(item["id"]), str(item.get("text", "")),
                                    str(item.get("source", "system")),
                                    str(item.get("label", "")), bool(item.get("editable", True))))
    return PromptTrace(parts, int(raw.get("version", 1)))


__all__ = ["PromptPart", "PromptTrace", "prompt_trace_from_blocks", "trace_from_dict"]