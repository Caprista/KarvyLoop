"""楔子：一次性工具 / 使用统计 / 结晶技能（#2 / #7 §1）。

软件是消耗品（EphemeralTool）；用得足够多 + 过验证门 → 结晶成持久技能（Skill）；
冷落的自动消亡。这是唯一做深的护城河。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ._base import Schema
from .capability import Capability


class EphemeralTool(Schema):
    """一次性工具：由 Forge 生成并执行；默认消耗品（#2 §2.1）。

    `trace_refs`：指回 Trace 的来源条目（provenance，#4 §3.1）——observe() 据此派生候选。
    """

    id: str
    from_intent: str  # 触发它的那句话
    code: str  # Forge 生成的脚本/函数体
    input_schema: dict
    output_schema: dict
    required_capabilities: list[Capability] = Field(default_factory=list)
    trace_refs: list[str] = Field(default_factory=list)
    created_at: float
    ttl: float  # 默认短，到期 GC（除非进入候选）


class UsageStats(Schema):
    """使用统计——"用进废退"的数据基础（#2 §2.2）。"""

    usage_count: int = 0
    last_used_at: float = 0
    success_count: int = 0  # 通过验证门的次数
    failure_count: int = 0
    recall_count: int = 0  # 快脑召回命中次数(拍 9:强信号 —— 比 usage_count 更准的"用进"指标)
    param_variants: list[dict] = Field(default_factory=list)  # 参数模式（判泛化性，#2 §4.3）
    steered_by_user: list[str] = Field(default_factory=list)  # 用户中途纠正（喂 improve，#2 §6）
    intent_repr: str = ""  # 9.4:该 cluster 的代表意图(token-overlap 累积聚类用;同任务不同说法归并)


class Skill(Schema):
    """结晶产物（SKILL.md 的内存态，#2 §3）。进 L0 技能库。

    `scope`：personal（私人，跟 Agent 走）/ domain（域技能）——#0 私人 vs 域边界。
    `verify_proof`：结晶时必须有一次通过验证门的证明（没门不结晶，#2 §4 关1）。
    """

    name: str
    manifest: dict  # SKILL.md frontmatter
    body: str  # SKILL.md 正文
    from_candidate: str
    usage: UsageStats = Field(default_factory=UsageStats)
    verify_proof: dict
    scope: Literal["personal", "domain"] = "personal"
    created_at: float
    evolved_at: float  # 上次被 improve 更新
