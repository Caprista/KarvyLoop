"""AdapterRegistry —— 4 类 source adapter 集中管理(注入友好)。"""
from __future__ import annotations

import dataclasses
import logging
from typing import Callable, Optional

from .source import EXTERNAL_SOURCES, ExternalManifest, SourceAdapter

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class AdapterEntry:
    """一个 source adapter 的元数据。"""
    source_id: str                # "claude" / "codex" / "agent-bundle" / "generic-json"
    label: str                    # "Claude Code" / "Codex" / ...
    parser: SourceAdapter         # (payload, source_path) -> ExternalManifest
    detect_fn: Callable[[str], bool] = lambda path: False   # 给定的路径是否属于此 source


class AdapterRegistry:
    """外部 source adapter 注册表(本拍 v0 = 4 内置,M3+ 加 LangChain/Autogen)。"""

    def __init__(self) -> None:
        self._entries: dict[str, AdapterEntry] = {}

    def register(self, entry: AdapterEntry) -> None:
        if entry.source_id in self._entries:
            logger.info("Adapter '%s' re-registered", entry.source_id)
        self._entries[entry.source_id] = entry

    def get(self, source_id: str) -> Optional[AdapterEntry]:
        return self._entries.get(source_id)

    def all_entries(self) -> list[AdapterEntry]:
        return list(self._entries.values())

    def is_registered(self, source_id: str) -> bool:
        return source_id in self._entries

    def auto_detect(self, path: str) -> Optional[str]:
        """根据路径猜 source_id(给 wizard / onboarding 用)。"""
        for sid, entry in self._entries.items():
            try:
                if entry.detect_fn(path):
                    return sid
            except Exception as e:
                logger.debug("auto_detect %s failed: %s", sid, e)
        return None


# 模块级单例(本拍自动注册 4 内置)
adapter_registry = AdapterRegistry()


# ---- 4 内置 ----

adapter_registry.register(AdapterEntry(
    source_id="claude",
    label="Claude Code",
    parser=EXTERNAL_SOURCES["claude"],
    detect_fn=lambda p: ".claude" in p or "CLAUDE.md" in p,
))

adapter_registry.register(AdapterEntry(
    source_id="codex",
    label="Codex",
    parser=EXTERNAL_SOURCES["codex"],
    detect_fn=lambda p: ".codex" in p or "AGENTS.md" in p,
))

adapter_registry.register(AdapterEntry(
    source_id="agent-bundle",
    label="Agent Bundle (JSON)",
    parser=EXTERNAL_SOURCES["agent-bundle"],
    detect_fn=lambda p: "agent-bundle" in p.lower() or "manifest" in p.lower(),
))

adapter_registry.register(AdapterEntry(
    source_id="generic-json",
    label="Generic JSON",
    parser=EXTERNAL_SOURCES["generic-json"],
    detect_fn=lambda p: p.endswith(".json"),
))
