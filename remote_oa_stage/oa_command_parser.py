"""Deterministic parsing for the short OA commands used in DingTalk."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class OACommand:
    action: str
    indexes: tuple[int, ...] = ()
    remark: str = ""
    target: str = ""


_NUMS = re.compile(r"(?:\d+\s*(?:[-~至]\s*\d+)?)(?:\s*[,，、;；\s]+\s*\d+\s*(?:[-~至]\s*\d+)?)*")


def _indexes(raw: str) -> tuple[int, ...]:
    out: set[int] = set()
    for token in re.findall(r"\d+\s*(?:[-~至]\s*\d+)?", raw):
        bits = re.split(r"\s*[-~至]\s*", token)
        start = int(bits[0])
        end = int(bits[-1])
        if start <= 0 or end <= 0 or end - start > 100:
            continue
        out.update(range(min(start, end), max(start, end) + 1))
    return tuple(sorted(out))


def parse_oa_command(text: str) -> OACommand | None:
    """Parse a safe subset; return None for ambiguous natural language."""
    value = " ".join(str(text or "").strip().split())
    if value in {"?", "？", "继续", "下一批"}:
        return OACommand("continue")
    if value in {"刷新", "刷新列表", "重新查询待办"}:
        return OACommand("refresh")
    if value in {"重试", "再试一次"}:
        return OACommand("retry")

    action_match = re.search(r"(批准|同意|通过|拒绝|驳回|转发)", value)
    if not action_match:
        return None
    action_word = action_match.group(1)
    action = "approve" if action_word in {"批准", "同意", "通过"} else "reject" if action_word in {"拒绝", "驳回"} else "forward"
    before = value[:action_match.start()]
    after = value[action_match.end():].strip()
    number_text = before if re.search(r"\d", before) else after
    indexes = _indexes(number_text)
    if not indexes:
        return None

    remark = ""
    target = ""
    if action == "reject":
        match = re.search(r"(?:原因|理由)\s*[:：]?\s*(.+)$", value)
        if match:
            remark = match.group(1).strip()
    elif action == "forward":
        match = re.search(r"(?:给|至|到)\s*([^，,;；]+)", value)
        if match:
            target = match.group(1).strip()
    return OACommand(action, indexes, remark, target)


__all__ = ["OACommand", "parse_oa_command"]
