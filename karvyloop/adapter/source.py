"""Stage 1 Source —— 外部 agent manifest 解析 + 4 内置 parser。

**核心不变量**(doc §4):
- J1 is_minimal 校验
- J7 全 Callable 注入

设计:docs/14 §3.3 + §3.7。
"""
from __future__ import annotations

import dataclasses
import json
import logging
import pathlib
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class ManifestError(ValueError):
    """外部 manifest 缺必填字段时抛。"""


@dataclasses.dataclass(frozen=True)
class ExternalManifest:
    """外部 agent 的 manifest (拍 4 v0 用 dict/JSON 即可)。"""
    source_id: str               # "claude" / "codex" / "openclaw-hermes" / "generic-json"
    source_path: str             # 源路径(读 .md 用)
    system_prompt: str           # 必填 (J1)
    tools: tuple[dict, ...]      # 必填 ≥1 (J1)
    skills: tuple[dict, ...] = ()
    memory_files: tuple[str, ...] = ()
    soul_files: tuple[str, ...] = ()
    user_files: tuple[str, ...] = ()
    agent_name: str = ""
    raw_metadata: dict = dataclasses.field(default_factory=dict)

    def is_minimal(self) -> bool:
        """AC1:必须含 system_prompt + tools(否则拒收)。"""
        return bool(self.system_prompt) and len(self.tools) >= 1


# ---- 4 内置 parser ----

def parse_claude_manifest(payload: dict, source_path: str = "<dict>") -> ExternalManifest:
    """CC ~/.claude/CLAUDE.md + settings.json 的解析器。"""
    system_prompt = payload.get("system_prompt") or payload.get("claude_md") or ""
    tools_raw = payload.get("tools", [])
    if not isinstance(tools_raw, list):
        tools_raw = []
    return ExternalManifest(
        source_id="claude",
        source_path=source_path,
        system_prompt=system_prompt,
        tools=tuple(tools_raw),
        skills=tuple(payload.get("skills", [])),
        memory_files=tuple(payload.get("memory_files", [])),
        user_files=tuple(payload.get("user_files", [])),
        agent_name=payload.get("agent_name", "claude-import"),
        raw_metadata=dict(payload),
    )


def parse_codex_manifest(payload: dict, source_path: str = "<dict>") -> ExternalManifest:
    """Codex ~/.codex/AGENTS.md + config.toml 的解析器。"""
    system_prompt = payload.get("system_prompt") or payload.get("agents_md") or ""
    tools_raw = payload.get("tools", payload.get("tools_list", []))
    if not isinstance(tools_raw, list):
        tools_raw = []
    return ExternalManifest(
        source_id="codex",
        source_path=source_path,
        system_prompt=system_prompt,
        tools=tuple(tools_raw),
        skills=tuple(payload.get("skills", [])),
        memory_files=tuple(payload.get("memory_files", [])),
        user_files=tuple(payload.get("user_files", [])),
        agent_name=payload.get("agent_name", "codex-import"),
        raw_metadata=dict(payload),
    )


def parse_openclaw_hermes_manifest(payload: dict, source_path: str = "<dict>") -> ExternalManifest:
    """openclaw migrate-hermes plan 产物的解析器。

    预期字段:system_prompt / tools / soul_files / memory_files / user_files
    """
    system_prompt = payload.get("system_prompt", "")
    tools_raw = payload.get("tools", [])
    if not isinstance(tools_raw, list):
        tools_raw = []
    return ExternalManifest(
        source_id="openclaw-hermes",
        source_path=source_path,
        system_prompt=system_prompt,
        tools=tuple(tools_raw),
        skills=tuple(payload.get("skills", [])),
        memory_files=tuple(payload.get("memory_files", [])),
        soul_files=tuple(payload.get("soul_files", [])),
        user_files=tuple(payload.get("user_files", [])),
        agent_name=payload.get("agent_name", "openclaw-hermes-import"),
        raw_metadata=dict(payload),
    )


def parse_generic_manifest(payload: dict, source_path: str = "<dict>") -> ExternalManifest:
    """通用 JSON 透传。"""
    system_prompt = payload.get("system_prompt", "")
    tools_raw = payload.get("tools", [])
    if not isinstance(tools_raw, list):
        tools_raw = []
    return ExternalManifest(
        source_id="generic-json",
        source_path=source_path,
        system_prompt=system_prompt,
        tools=tuple(tools_raw),
        skills=tuple(payload.get("skills", [])),
        memory_files=tuple(payload.get("memory_files", [])),
        user_files=tuple(payload.get("user_files", [])),
        agent_name=payload.get("agent_name", "generic-import"),
        raw_metadata=dict(payload),
    )


# ---- 4 内置 source adapter 注册 ----

SourceAdapter = Callable[[dict, str], ExternalManifest]

EXTERNAL_SOURCES: dict[str, SourceAdapter] = {
    "claude": parse_claude_manifest,
    "codex": parse_codex_manifest,
    "openclaw-hermes": parse_openclaw_hermes_manifest,
    "generic-json": parse_generic_manifest,
}


def discover_manifest(source_id: str, payload: dict, source_path: str = "<dict>") -> ExternalManifest:
    """AC1 + AC8 入口:从 source_id 选 parser,产 ExternalManifest。

    缺 system_prompt/tools → 抛 ManifestError(J1)。
    """
    parser = EXTERNAL_SOURCES.get(source_id)
    if parser is None:
        raise ManifestError(f"Unknown source_id: {source_id!r}; available={list(EXTERNAL_SOURCES)}")
    manifest = parser(payload, source_path)
    if not manifest.is_minimal():
        raise ManifestError(
            f"Manifest from {source_id!r} failed is_minimal: "
            f"system_prompt={bool(manifest.system_prompt)} tools={len(manifest.tools)}"
        )
    return manifest
