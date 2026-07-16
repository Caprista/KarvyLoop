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
    source_id: str               # "claude" / "codex" / "agent-bundle" / "generic-json"
    source_path: str             # 源路径(读 .md 用)
    system_prompt: str           # 必填:外部 agent 的人设/指令
    tools: tuple[dict, ...]      # 可选:很多 agency-agent 是纯人设无显式 tools(由 LLM 从人设提炼能力)
    skills: tuple[dict, ...] = ()
    memory_files: tuple[str, ...] = ()
    soul_files: tuple[str, ...] = ()
    user_files: tuple[str, ...] = ()
    agent_name: str = ""
    raw_metadata: dict = dataclasses.field(default_factory=dict)

    def is_minimal(self) -> bool:
        """可导入的最低要求 = **有人设(system_prompt)**。tools 不再强制 ——
        真实 agency 里大量 agent 是纯人设/技能式定义、无显式 tools(实测 Hardy 的 245 个皆如此);
        要求 tools 会把它们全拒之门外。tools 缺失时由 LLM 拆解从人设**提炼能力为原子**
        (没可执行原子 → 按 advisory_persona 顾问角色导入,见 routes.api_agent_import)。"""
        return bool(self.system_prompt)


# ---- 4 内置 parser ----

def parse_claude_manifest(payload: dict, source_path: str = "<dict>") -> ExternalManifest:
    """~/.claude/CLAUDE.md + settings.json 的解析器。"""
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


def parse_agent_bundle_manifest(payload: dict, source_path: str = "<dict>") -> ExternalManifest:
    """通用分层 agent bundle 清单的解析器(互操作导入格式)。

    预期字段:system_prompt / tools / soul_files / memory_files / user_files
    """
    system_prompt = payload.get("system_prompt", "")
    tools_raw = payload.get("tools", [])
    if not isinstance(tools_raw, list):
        tools_raw = []
    return ExternalManifest(
        source_id="agent-bundle",
        source_path=source_path,
        system_prompt=system_prompt,
        tools=tuple(tools_raw),
        skills=tuple(payload.get("skills", [])),
        memory_files=tuple(payload.get("memory_files", [])),
        soul_files=tuple(payload.get("soul_files", [])),
        user_files=tuple(payload.get("user_files", [])),
        agent_name=payload.get("agent_name", "agent-bundle-import"),
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


# ---- 多 agent 系统 bundle(docs/84 #3:agents[] + topology 原样透传)----

# 系统 bundle 解析层封顶(与 system_import 的 IR 封顶同一口径;解析层先截,LLM 层再守一遍)
MAX_BUNDLE_AGENTS = 24


@dataclasses.dataclass(frozen=True)
class SystemBundle:
    """多 agent 系统 bundle:agents[](每项 = ExternalManifest)+ topology(源格式原样透传)。

    topology **刻意不定 schema**(docs/84 #3):LangGraph/CrewAI/AutoGen/… 各家形状不一,
    读懂它是 SYSTEM_TRIAGE(一次 LLM)的活;这里只透传,绝不猜结构。
    IR/plan 均不持久化 —— bundle 本身也只活在一次 plan/apply 请求里。
    """
    source_id: str
    source_path: str
    name: str                              # bundle 顶层名(可空;域名候选)
    agents: tuple[ExternalManifest, ...]
    topology: Any                          # 原样透传(dict/list/str 都可能)
    agents_total: int                      # 截断前的原始 agent 数(诚实报封顶)
    agents_dropped: tuple[str, ...]        # 连一句人设都拼不出的项(如实报,不静默丢)


def is_system_bundle(payload: Any) -> bool:
    """检测多 agent 系统 bundle:带非空 `agents` 列表字段即是(单 agent 清单没有它)。"""
    return isinstance(payload, dict) and isinstance(payload.get("agents"), list) and bool(payload["agents"])


def _agent_item_to_manifest(item: dict, idx: int, source_path: str) -> Optional[ExternalManifest]:
    """把 agents[] 的一项拼成 ExternalManifest;连一句人设都拼不出 → None(调用方记 dropped)。"""
    if not isinstance(item, dict):
        return None
    # 标准字段(system_prompt/instructions)整段即人设;否则按字段名标注拼接
    # (role/goal/backstory/description),原文不改写、不编内容,读懂交给 TRIAGE。
    prompt = ""
    for f in ("system_prompt", "instructions"):
        v = item.get(f)
        if isinstance(v, str) and v.strip():
            prompt = v.strip()
            break
    if not prompt:
        parts = [f"{f}: {item[f].strip()}" for f in ("role", "goal", "backstory", "description")
                 if isinstance(item.get(f), str) and item[f].strip()]
        prompt = "\n".join(parts)
    if not prompt:
        return None
    tools_raw = item.get("tools", [])
    if not isinstance(tools_raw, list):
        tools_raw = []
    name = str(item.get("name") or item.get("agent_name") or item.get("id") or f"agent-{idx + 1}").strip()
    return ExternalManifest(
        source_id="agent-bundle",
        source_path=source_path,
        system_prompt=prompt,
        tools=tuple(t for t in tools_raw if isinstance(t, (dict, str))),
        skills=tuple(item.get("skills", []) if isinstance(item.get("skills"), list) else ()),
        agent_name=name or f"agent-{idx + 1}",
        raw_metadata=dict(item),
    )


def parse_system_bundle(payload: dict, source_path: str = "<dict>") -> SystemBundle:
    """解析多 agent 系统 bundle → SystemBundle(agents 逐项成 ExternalManifest,topology 原样)。

    - 一个可用 agent 都没有 → ManifestError(J1 口径:拒收,不是静默空)。
    - agents 超 MAX_BUNDLE_AGENTS → 截断(agents_total 保留原始数,如实报封顶)。
    - 拼不出人设的项 → 丢进 agents_dropped(如实报,不静默)。
    """
    if not is_system_bundle(payload):
        raise ManifestError("not a system bundle: missing non-empty `agents` list")
    raw_agents = payload["agents"]
    total = len(raw_agents)
    manifests: list[ExternalManifest] = []
    dropped: list[str] = []
    seen_names: set[str] = set()
    for i, item in enumerate(raw_agents[:MAX_BUNDLE_AGENTS]):
        m = _agent_item_to_manifest(item, i, source_path)
        if m is None:
            label = ""
            if isinstance(item, dict):
                label = str(item.get("name") or item.get("agent_name") or item.get("id") or "").strip()
            dropped.append(label or f"agent-{i + 1}")
            continue
        # 同名去重(后到者加序号;名字是 IR 引用键,必须唯一)
        base = m.agent_name
        n = 2
        while m.agent_name in seen_names:
            m = dataclasses.replace(m, agent_name=f"{base}-{n}")
            n += 1
        seen_names.add(m.agent_name)
        manifests.append(m)
    if not manifests:
        raise ManifestError(
            f"system bundle has {total} agents but none usable (no persona text in any item)")
    return SystemBundle(
        source_id="agent-bundle",
        source_path=source_path,
        name=str(payload.get("name") or payload.get("system_name") or "").strip(),
        agents=tuple(manifests),
        topology=payload.get("topology"),
        agents_total=total,
        agents_dropped=tuple(dropped),
    )


# ---- 4 内置 source adapter 注册 ----

SourceAdapter = Callable[[dict, str], ExternalManifest]

EXTERNAL_SOURCES: dict[str, SourceAdapter] = {
    "claude": parse_claude_manifest,
    "codex": parse_codex_manifest,
    "agent-bundle": parse_agent_bundle_manifest,
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
