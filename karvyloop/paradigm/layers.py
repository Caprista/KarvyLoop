"""Paradigm Loader 的 7 个 layer 纯函数加载器。

每个函数:`(ctx) -> LayerContent`。
**纯函数**——无 IO(本拍不做 .md 文件 IO,AC6 验证"无 .md 也能跑");
**输入 ctx 就够**,不依赖其他模块状态。

设计:docs/10-paradigm-loader.md §3.2 7 layer 协议。
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .loader import ParadigmContext


@dataclasses.dataclass
class LayerContent:
    """一个 layer 的内容 + 加载来源。"""
    layer: int
    text: str
    source: str  # "domain.guardrails" / "role.soul_refs[IDENTITY]" / "default" 等


# ---- 一些默认文本(AC6:完全无 .md 也能跑)---------

_DEFAULT_IDENTITY = (
    "[default identity] This role has no IDENTITY.md provided. "
    "Treat the role_id as a placeholder identity."
)

_DEFAULT_SOUL = (
    "[default soul] This role has no SOUL.md provided. "
    "Operate on general helpfulness without assuming any specific values."
)

_DEFAULT_COMPOSITION = (
    "[default composition] This role has no COMPOSITION.yaml provided. "
    "Operate as a general-purpose role with no specialized atom set."
)


def _join(*parts: str) -> str:
    """用空行 join 多个非空段。"""
    return "\n\n".join(p for p in parts if p)


# ---- 7 个 layer 函数 ----

def load_L0_guardrails(ctx: "ParadigmContext") -> LayerContent:
    """Layer 0: 域 deontic guardrails(义务/禁止)。

    永不省略(硬护栏)。如果域没给 guardrails,给个"无强制护栏"占位。
    """
    rules = ctx.domain.guardrails or []
    if not rules:
        text = "[no guardrails] No deontic guardrails declared for this domain."
        src = "default"
    else:
        text = "[hard guardrails — MUST follow]\n" + "\n".join(f"- {r}" for r in rules)
        src = "domain.guardrails"
    # 9.5 loop-step1:R4 落地 —— 业务域的 value.md(价值观)也是 per-role 治理的一部分,
    # 编译进 L0(同一个 value,配上不同角色的 L1/L2 → 不同执行规范)。
    value_md = getattr(ctx.domain, "value_md", None)
    if value_md and value_md.strip():
        text = text + "\n\n[domain values — embody these (value.md)]\n" + value_md.strip()
        src = "domain.value_md" if src == "default" else "domain.guardrails+value_md"
    return LayerContent(layer=0, text=text, source=src)


def load_L1_identity_soul(ctx: "ParadigmContext") -> LayerContent:
    """Layer 1: role.identity + role.soul。

    两个都必加载(全场景 R1)。Identity 缺失走 default,soul 缺失也走 default。
    """
    ri = ctx.role_instance
    text = _join(ri.identity_text or _DEFAULT_IDENTITY, ri.soul_text or _DEFAULT_SOUL)
    # source 标记两个部分分别来自哪
    src_parts = []
    if ri.identity_text:
        src_parts.append("role.identity")
    else:
        src_parts.append("default")
    if ri.soul_text:
        src_parts.append("role.soul")
    else:
        src_parts.append("default")
    return LayerContent(layer=1, text=text, source="+".join(src_parts))


def load_L2_composition(ctx: "ParadigmContext") -> LayerContent:
    """Layer 2: role.composition(配方)。"""
    ri = ctx.role_instance
    text = ri.composition_text or _DEFAULT_COMPOSITION
    src = "role.composition" if ri.composition_text else "default"
    return LayerContent(layer=2, text=text, source=src)


def load_L3_commitment(ctx: "ParadigmContext") -> LayerContent:
    """Layer 3: role.commitment(仅当 pursuit 命中时调用——policy 已过滤)。"""
    p = ctx.current_pursuit
    # policy 已保证 current_pursuit is not None
    assert p is not None
    text = p.commitment_text or f"[commitment] Pursuit '{p.id}': {p.statement}"
    return LayerContent(layer=3, text=text, source="role.commitment")


def load_L4_verify_gate(ctx: "ParadigmContext") -> LayerContent:
    """Layer 4: role.verify_gate(仅当进入判定步骤时调用)。"""
    p = ctx.current_pursuit
    assert p is not None
    text = f"[verify gate for pursuit '{p.id}']\n{p.statement}\nverify_gate={p.verify_gate}"
    return LayerContent(layer=4, text=text, source="role.verify_gate")


def load_L5_user_memory(ctx: "ParadigmContext") -> LayerContent:
    """Layer 5: role.user_md + role.memory_md(动态;R1 全场景加载,容量紧张时优先砍)。"""
    ri = ctx.role_instance
    refs = ri.soul_refs or {}
    user_text = refs.get("USER") or ""
    memory_text = refs.get("MEMORY") or ""
    # 拼;空 = 不出现
    parts = []
    if user_text:
        parts.append(f"[user]\n{user_text}")
    if memory_text:
        parts.append(f"[memory]\n{memory_text}")
    text = _join(*parts) or "[no user/memory content]"
    src = "role.soul_refs[USER+MEMORY]" if (user_text or memory_text) else "default"
    return LayerContent(layer=5, text=text, source=src)


def load_L6_tools_env(ctx: "ParadigmContext") -> LayerContent:
    """Layer 6: tools(按角色授权过滤) + environment(最不稳,优先砍)。"""
    env = ctx.environment or {}
    # 简化:env 列表
    text_parts = []
    if env.get("tools"):
        text_parts.append(f"[tools available] {','.join(env['tools'])}")
    if env.get("channel"):
        text_parts.append(f"[channel] {env['channel']}")
    if env.get("sub_domain"):
        text_parts.append(f"[sub_domain] {env['sub_domain']}")
    text = _join(*text_parts) or "[no tools/env]"
    return LayerContent(layer=6, text=text, source="environment")
