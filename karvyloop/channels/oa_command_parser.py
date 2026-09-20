"""Compatibility wrapper for the platform-neutral OA intent parser.

New code should import :func:`karvyloop.oa.intent.parse_oa_intent`; this module
remains temporarily so existing integrations do not break.
"""

from __future__ import annotations

from dataclasses import dataclass

from karvyloop.oa.intent import parse_oa_intent


@dataclass(frozen=True)
class OACommand:
    action: str
    indexes: tuple[int, ...] = ()
    remark: str = ""
    target: str = ""


def parse_oa_command(text: str) -> OACommand | None:
    intent = parse_oa_intent(text)
    if intent is None:
        return None
    return OACommand(intent.kind, intent.indexes, intent.remark, intent.target)


__all__ = ["OACommand", "parse_oa_command"]
