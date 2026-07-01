"""L3/L4 业务域 + deontic 规范（#0 §2.5/§3 / #4 §4.1 / #7 §1）。

业务域 = 公司，子域 = 部门（继承父域文化 + 叠加自己的）。
企业文化/世界观的可执行形态 = deontic 规范（义务/许可/禁止）。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from ._base import Schema


class Norm(Schema):
    """一条 deontic 规范。

    `scope`：
      - guardrail 硬护栏：自顶向下强制，子域不可覆盖（合规/安全红线）
      - default   软默认：最具体者胜（任务 > 部门 > 公司）
    两类层叠方向相反，必须分开解析（#0 §3.4 / #5 §3）。
    """

    kind: Literal["obligation", "permission", "prohibition"]
    rule: str  # 可被策略引擎判定的规则
    scope: Literal["guardrail", "default"]


class DomainManifest(Schema):
    """业务域清单（#4 §4.1）——"谁在域内"的唯一定义 + 共享范围。

    `members`：[{"addr": "RootID.AgentID", "access": "read|write|admin"}]
    `deontic`：{"guardrails": [Norm], "defaults": [Norm]}
    `visibility`：{"secret": [...], "shared": [...]}（域机密 vs 域内共享）
    `model`：域级模型引用；None → 层叠到全局 default。
    """

    domain_id: str
    parent_domain: Optional[str] = None
    members: list[dict] = Field(default_factory=list)
    structural: dict = Field(default_factory=dict)
    functional_sop: dict = Field(default_factory=dict)
    deontic: dict = Field(default_factory=dict)
    kb_ref: str
    visibility: dict = Field(default_factory=dict)
    model: Optional[str] = None
