"""Paradigm Loader 主编排。

`load_paradigm(ctx)`:输入 ParadigmContext,返回 LoadedParadigm(7 layer 顺序 + budget + 日志)。

设计:docs/10-paradigm-loader.md §3。
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Optional

from .budget import Budget, TokenCounter
from .layers import (
    LayerContent,
    load_L0_guardrails,
    load_L1_identity_soul,
    load_L2_composition,
    load_L3_commitment,
    load_L4_verify_gate,
    load_L5_user_memory,
    load_L6_tools_env,
)
from .policy import (
    ALL_RULES,
    CONDITIONAL_LAYERS,
    LAYER_MEANING,
    LAYER_ORDER,
    MUST_LAYERS,
    TAIL_LAYERS,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class RoleInstance:
    """role 实例的轻量视图(loader 不依赖完整 RoleSpec;Wizard 拍 1 会做完整装配)。

    soul_refs keys 期望是 SOUL_FILES + COMPOSITION。值是路径串(可空 → 走 default)。
    """
    role_id: str
    identity_text: str
    soul_text: str
    composition_text: str
    soul_refs: dict[str, Optional[str]]  # 7 文件路径引用;None/空 = 该文件未提供


@dataclasses.dataclass
class DomainView:
    """DomainManifest 的轻量视图(loader 不依赖完整 schema)。"""
    domain_id: str
    guardrails: list[str]  # 字符串化的 deontic guardrails(义务/禁止)
    value_md: Optional[str] = None  # 业务域的 VALUE.md 内容(R4 决定是否加载)


@dataclasses.dataclass
class PursuitView:
    """Pursuit 的轻量视图(loader 只用 id + statement + verify_gate + entering_verification)。"""
    id: str
    statement: str
    verify_gate: dict
    commitment_text: Optional[str] = None
    entering_verification: bool = False


@dataclasses.dataclass
class ParadigmContext:
    """Loader 的输入。"""
    role_instance: RoleInstance
    domain: DomainView
    user_message: str
    current_pursuit: Optional[PursuitView] = None
    environment: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class LoadedParadigm:
    """Loader 的输出。"""
    layers: dict[int, LayerContent]  # key = layer(0..6)
    loaded_layers: list[int]         # 实际加载的 layer 列表(顺序)
    dropped_layers: list[int]        # 因 budget 砍掉的 layer
    budget: tuple[int, int]          # (used, cap)
    log_line: str                    # 可观测性日志

    def to_system_prompt(self) -> str:
        """按 layer 顺序拼成最终 system prompt(M2+ executor 调这个)。"""
        parts: list[str] = []
        for n in LAYER_ORDER:
            if n in self.layers:
                lc = self.layers[n]
                parts.append(f"<!-- layer {n}: {LAYER_MEANING[n]} -->\n{lc.text}")
        return "\n\n".join(parts)


def load_paradigm(
    ctx: ParadigmContext,
    *,
    budget: Optional[Budget] = None,
    token_counter: Optional[TokenCounter] = None,
) -> LoadedParadigm:
    """主编排函数:policy 决定哪些 layer 加载 + budget 决定是否降级 + layers 实际加载。

    **保证**:
      1. MUST_LAYERS(0,1,2)任何 context 下**必**加载
      2. 降级时按 TAIL_LAYERS(5,6)先砍,**不**砍 MUST
      3. 日志格式稳定(AC5)
      4. 空 .md 容错(AC6)—— Layer 0/1 走 default
    """
    counter = token_counter or TokenCounter()
    bg = budget or Budget(cap=200_000 * 7 // 10, counter=counter)  # 默认 200k ctx × 0.7

    # ---- Phase 1:policy 决定加载哪些 layer ----
    # 先 MUST 全打捞
    candidate: set[int] = set(MUST_LAYERS)
    # 再按规则条件加载
    for rule in ALL_RULES:
        if rule.predicate(ctx):
            candidate.update(rule.layers)
    # L6 (tools + env) 仅当 environment 有内容时加载
    # (R1/R2/R3/R4 都不直接管 L6 — 它是"执行上下文"层,非灵魂/规范/承诺)
    if ctx.environment:
        candidate.add(6)

    # ---- Phase 2:按 layer 顺序加载(可能生成多个 layer)----
    layers: dict[int, LayerContent] = {}
    for n in LAYER_ORDER:
        if n not in candidate:
            continue
        if n == 0:
            layers[n] = load_L0_guardrails(ctx)
        elif n == 1:
            layers[n] = load_L1_identity_soul(ctx)
        elif n == 2:
            layers[n] = load_L2_composition(ctx)
        elif n == 3:
            layers[n] = load_L3_commitment(ctx)
        elif n == 4:
            layers[n] = load_L4_verify_gate(ctx)
        elif n == 5:
            layers[n] = load_L5_user_memory(ctx)
        elif n == 6:
            layers[n] = load_L6_tools_env(ctx)

    # ---- Phase 3:budget 校验 + 降级(只砍 TAIL_LAYERS)----
    loaded = list(layers.keys())
    dropped: list[int] = []
    # 计算初始 token
    bg.reset()
    for n in loaded:
        bg.add(layers[n].text)

    while bg.over_budget() and layers:
        # 找最大的 TAIL_LAYER 砍(优先砍 6 再 5)
        for victim in (6, 5):
            if victim in layers and victim in TAIL_LAYERS:
                del layers[victim]
                dropped.append(victim)
                bg.reset()
                for n in LAYER_ORDER:
                    if n in layers:
                        bg.add(layers[n].text)
                break
        else:
            # 没有任何 TAIL_LAYER 可砍(都已砍光);再砍就是砍 MUST,**不**做
            break

    loaded_final = [n for n in LAYER_ORDER if n in layers]

    # ---- Phase 4:日志(AC5 锁格式)----
    log_line = (
        f"[ParadigmLoader] role={ctx.role_instance.role_id} "
        f"domain={ctx.domain.domain_id} "
        f"loaded={loaded_final} dropped={dropped} "
        f"budget={bg.used()}/{bg.cap}"
    )
    logger.info(log_line)

    return LoadedParadigm(
        layers=layers,
        loaded_layers=loaded_final,
        dropped_layers=dropped,
        budget=(bg.used(), bg.cap),
        log_line=log_line,
    )
