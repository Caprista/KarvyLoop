"""Paradigm Loader 的协议层:7 layer 顺序 + 4 上下文加载规则 + 7 文件清单。

**本模块是 #0 §2.4 4 规则 + 7 文件清单的机器可读版**——文档是给人看的,本文件是给
Loader/Wizard/Ethos 看的"唯一真理"。

**不**动 .md 文件内容(那是 Wizard 拍 1);**不**改 schema 字段(那是 M0)。
**只**表达"什么 context 触发什么 layer/文件被加载"。

设计:docs/10-paradigm-loader.md §3.3 4 上下文加载规则。
"""

from __future__ import annotations

import dataclasses
from typing import Callable, Optional


# ---- 7 个灵魂文件(#0 §2.4 7 文件清单的灵魂层 6 个 + 配方 1 个)-----------

# 灵魂层 6 个文件(SOUL / IDENTITY / USER / COMMITMENT / VERIFY / MEMORY)
# 第 7 个是 COMPOSITION.yaml,走 composition_ref 单独处理
SOUL_FILES: tuple[str, ...] = (
    "IDENTITY",
    "SOUL",
    "USER",
    "COMMITMENT",
    "VERIFY",
    "MEMORY",
)

# 标准 7 文件清单(灵魂层 6 + 配方 1)的全部 keys
ROLE_INSTANCE_KEYS: tuple[str, ...] = (
    "IDENTITY",
    "SOUL",
    "USER",
    "COMMITMENT",
    "VERIFY",
    "MEMORY",
    "COMPOSITION",
)


# ---- 7 layer 顺序(#0 §2.4.1 + docs/10 §3.2)-----------

# 顺序敏感:Layer 0 在最前(MUST + 稳),Layer 6 在最尾(动态)。
# 中间层可条件加载。详见 docs/10 §3.2 顺序理由(LLM 注意力锚定效应)。
LAYER_ORDER: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)

# 每个 layer 的语义
LAYER_MEANING: dict[int, str] = {
    0: "domain.deontic.guardrails(硬护栏)",
    1: "role.identity + role.soul(我是谁 + 灵魂)",
    2: "role.composition(配方:用哪些原子)",
    3: "role.commitment(当前 pursuit 承诺)",
    4: "role.verify_gate(验证门)",
    5: "role.user_md + role.memory_md(动态记忆)",
    6: "tools(按角色授权过滤) + environment",
}

# MUST 加载的 layer(任何 context 下都加载;降级时**永不**砍)
MUST_LAYERS: frozenset[int] = frozenset({0, 1, 2})

# 条件加载的 layer
CONDITIONAL_LAYERS: frozenset[int] = frozenset({3, 4, 5})

# 最不稳定的 layer(降级时优先砍)
TAIL_LAYERS: frozenset[int] = frozenset({5, 6})


# ---- 4 上下文加载规则(#0 §2.4 4 上下文加载规则)-----------

@dataclasses.dataclass(frozen=True)
class LoadRule:
    """一条加载规则——"什么 context 触发什么 layer 被加载"。

    字段:
      id:        规则 ID(R1/R2/R3/R4)
      predicate: 接受 ParadigmContext,返回 bool
      layers:    命中时**必**加载的 layer
      description: 人类可读描述(给日志/Wizard 提示用)
    """
    id: str
    predicate: Callable[["ParadigmContext"], bool]
    layers: tuple[int, ...]
    description: str


def _has_pursuit(ctx: "ParadigmContext") -> bool:
    return ctx.current_pursuit is not None


def _entering_verification(ctx: "ParadigmContext") -> bool:
    """进入'判定'步骤:当前 pursuit 标记 entering_verification 或 user message 含触发词。"""
    p = ctx.current_pursuit
    if p is not None and getattr(p, "entering_verification", False):
        return True
    msg = (ctx.user_message or "").lower()
    return "?verify?" in msg or "判定" in msg or "verify" in msg.lower()


def _in_business_domain(ctx: "ParadigmContext") -> bool:
    """在业务域(而非私聊/Share/兼职)——VALUE.md 走域层(细则见 §2.5 M2+ 落地)。

    简化版判定:environment.channel ∈ {"share", "private_chat", "side_job"} → False
    """
    env = ctx.environment or {}
    ch = env.get("channel", "default")
    return ch not in {"share", "private_chat", "side_job"}


# R1: 全场景 —— 任何对话都加载 Layer 0/1/2/5(节流)
R1_FULL_SCENE = LoadRule(
    id="R1",
    predicate=lambda ctx: True,  # 永远 True
    layers=(0, 1, 2, 5),
    description="全场景加载:任何对话都进上下文",
)

# R2: pursuit 命中 —— 加载 Layer 3(COMMITMENT)
R2_PURSUIT_HIT = LoadRule(
    id="R2",
    predicate=_has_pursuit,
    layers=(3,),
    description="pursuit 命中时加载 COMMITMENT(不是任何对话都要带 OKR)",
)

# R3: 判定步骤 —— 加载 Layer 4(VERIFY)
R3_VERIFY_STEP = LoadRule(
    id="R3",
    predicate=_entering_verification,
    layers=(4,),
    description='进入"判定/评估"步骤时加载验证门',
)

# R4: 域层 —— VALUE.md 走域层,私聊/Share/兼职不加载
R4_DOMAIN_LAYER = LoadRule(
    id="R4",
    predicate=_in_business_domain,
    layers=(),  # 不直接加载 layer,只影响 VALUE.md 是否进域上下文
    description="VALUE.md 跟着业务域走,私聊/Share/兼职不加载",
)

ALL_RULES: tuple[LoadRule, ...] = (R1_FULL_SCENE, R2_PURSUIT_HIT, R3_VERIFY_STEP, R4_DOMAIN_LAYER)
