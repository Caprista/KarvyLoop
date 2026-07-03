"""认知内核：Pursuit 对象 + Belief 记忆（#0 §4 / #4 / #7 §1）。

BDI 三层：Belief(记忆) / Desire(目标) / Intention(承诺计划)。
Pursuit = 跨层一等目标对象（Desire→Intention），带承诺/修订/验证门闭环。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from ._base import Schema


class Pursuit(Schema):
    """跨层目标对象（#0 §4.2，原 goal）。可挂在原子/角色/域三层。"""

    id: str
    level: Literal["atom", "role", "domain"]
    statement: str
    commitment_condition: str  # 什么成立就坚持（提升为 Intention）
    revision_triggers: list[str] = Field(default_factory=list)  # 什么变化就重规划/挂起/放弃
    verify_gate: dict  # 确定性的"算完成了吗"判定
    status: Literal["active", "committed", "revised", "done", "dropped"] = "active"


class Belief(Schema):
    """一条记忆（BDI 的 Belief）。带 provenance/freshness，是 Belief 不是真理（#4 §7）。

    时效与使用信号（记忆冲突消解接线）：
    - `invalid_at`：**失效不删**——被更新/矛盾的旧知识打失效标记（时间戳），仍留库可审计、
      可翻案；召回默认过滤（"用户吃素"半年后变"吃肉"，旧条不再被召回但历史在）。
    - `invalid_reason`：为什么失效（supersede 判定 / 人工归档），审计可读。
    - `last_recalled_ts` / `recall_count`：使用信号（哪条被召回过、多久没用），召回时轻量刷、
      不进热路径算分；daily 慢侧据此出"疑似过时，归档？"建议。
    """

    content: str
    provenance: dict  # {"source","agent","ts","trace_ref"}
    freshness_ts: float
    scope: Literal["personal", "domain"]
    invalid_at: Optional[float] = None
    invalid_reason: str = ""
    last_recalled_ts: float = 0.0
    recall_count: int = 0
