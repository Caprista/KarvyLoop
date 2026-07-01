"""onboarding — M2.0 拍 3 常驻引导。

设计:docs/13-resident-onboarding.md。

**5 段流水线**:Observe → Suggest → Suppress → Inject → Persist
**任意 endpoint 抽象**:EndpointRegistry (借业界 PlatformRegistry 思想)
**5 类 first-touch hint**:no_role_yet / first_skill_use / first_pursuit /
                          first_atom_compose / first_long_tool

**核心不变量**(doc §4):
- I1 每条 hint 只显示一次 / per-install
- I2 hint 投递不打扰当前 turn
- I3 endpoint 不可用时静默跳过
- I4 本拍不调 LLM(关键词启发式 + 字符串常量)
- I5 强制 deontic.guardrails (沿用 Paradigm Loader L0)
- I6 接受/拒绝/忽略写进 MEMORY
- I7 全部依赖注入,无全局实例
"""
from __future__ import annotations

from .hints import HINTS, OnboardingFlag
from .registry import EndpointEntry, EndpointRegistry, endpoint_registry
from .policy import OnboardingPolicy, PolicyDecision, default_policy
from .observe import classify_intent, observe_message
from .rag import DocHit, doc_rag_search

__all__ = [
    "HINTS",
    "OnboardingFlag",
    "EndpointEntry",
    "EndpointRegistry",
    "endpoint_registry",
    "OnboardingPolicy",
    "PolicyDecision",
    "default_policy",
    "classify_intent",
    "observe_message",
    "DocHit",
    "doc_rag_search",
]
