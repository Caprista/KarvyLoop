"""L2 角色（#0 §2.4 / #1 §3.3 / #7 §1）。

**【2026-06-16 修正】** 角色 = **配方 + 灵魂**。**不是**"原子集合+编排+BDI"。

- 原子是 role 的**不可再分构建块**（化学意义的"原子"）。
- 原子是**公共能力池**——**不**属于任何 role,role **使用**原子,**不**"拥有"原子。
- role 的**灵魂**层有 7 文件:IDENTITY/SOUL/USER/COMMITMENT/VERIFY/MEMORY/COMPOSITION.yaml。
  详见 #0 §2.4 关键边界 + 4 上下文加载规则。
- role 层 BDI 载体 = SOUL/USER/MEMORY(记忆) + COMMITMENT(愿望) + COMPOSITION(意图配方)。
  原子的 BDI 是"工程 harness";role 的 BDI 是"对话 + 上下文 + 灵魂"。**负载不同** (#0 §4.4)。
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from ._base import Schema


class RoleSpec(Schema):
    """抽象角色定义（.md 文件 + COMPOSITION.yaml）——**可**以**复**制**。

    `model`：角色级模型引用；None → 层叠到域/全局 default（#1 §3.1）。
    `composition_ref`：指向 COMPOSITION.yaml（配方；怎么用原子）。
    `soul_refs`：灵魂层 7 文件的引用映射（**值**通常是相对于业务域根的路径串）。

    灵魂层 7 文件加载规则（#0 §2.4 4 上下文加载规则）:
      - 全场景加载:IDENTITY / SOUL / USER / MEMORY / COMPOSITION
      - 条件加载:COMMITMENT (pursuit 命中时) / VERIFY (进入判定步骤时)
    """

    id: str
    composition_ref: str = Field(min_length=1)  # → COMPOSITION.yaml;非空
    soul_refs: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "灵魂层 7 文件的路径映射。keys 是逻辑名(如 'IDENTITY'/'SOUL'/'USER'/"
            "'COMMITMENT'/'VERIFY'/'MEMORY'),values 是相对于业务域根的路径串。"
        ),
    )
    model: Optional[str] = None
