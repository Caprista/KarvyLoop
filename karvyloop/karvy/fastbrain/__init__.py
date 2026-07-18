"""fastbrain — 公共快脑工具包(M3+ 拍 9.0 落地)。

设计:docs/25-fastbrain-architecture.md。

**核心定位**:
- **公共机制** — 任何 agent / role / 用户工作流**都可以**用
- **不**是 OS 主体(那是 karvy.core / atoms / courier / observer)
- **不**是小卡私有(那是 karvyloop.karvy.atoms + 未来 IntentAnalyst)

**子模块**(每个都对应 9.0 描述的"快脑 5 层"之一或多个):
- `skills` — 技能库(已结晶技能的可检索接口;在 #2 crystallize 之上)
- `qa` — QA 库(静态 / 规则类问答沉淀,"答过 N 次同样问题"复用)
- `trace_index` — Trace 三层漏斗基础设施(原文 + 摘要双层 ring buffer,9.0a)
- `trace_poll` — Trace 三层漏斗触发器(boot + daily,9.0a 骨架)
- `trace_habit` — Trace 三层漏斗习惯层(HabitStore + ModelRef 铺路 + BehaviorPatternAnalyzer 骨架,9.0b)

**纪律**:
- 本包**不**依赖小卡私有组件(atoms / 未来 IntentAnalyst)
- 本包**不**参与 A2A(K7 边界由调用方保证)
- 本包**不**写"意图分析"功能 — 那是小卡私有的 IntentAnalyst 职责
"""
from __future__ import annotations

# 0.1.0 骨架 — 实际逻辑在后续拍按 docs/25 落地
# 显式 __all__ 锁:不暴露"实现细节"模块路径
__all__ = [
    "skills",
    "qa",
    "trace_index",
    "trace_poll",
    "trace_habit",
    "context_gate",
]
