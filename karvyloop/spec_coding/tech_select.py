"""Stage 3: Tech Selector —— spec → tech 选型(LLM/atom/mcp)。

**AC3 不变量**:tech 选型**不**选不存在的 atom(从已注册列表校验)。

设计:docs/12 §3.1 Stage 3。
"""

from __future__ import annotations

import dataclasses
from typing import Optional, Sequence


@dataclasses.dataclass(frozen=True)
class TechStack:
    """Stage 3 选出的技术栈。

    字段:
      atoms:  调用的原子列表(必须**在**registered_atoms)
      mcp:    调用的 MCP server 列表(必须**在**registered_mcp_servers)
      model:  选用的 model 引用(留给 M2+ 升级)
    """
    atoms: tuple[str, ...]
    mcp: tuple[str, ...] = ()
    model: Optional[str] = None

    def is_valid(self, registered_atoms: Sequence[str]) -> bool:
        """AC3 校验:atoms 都在 registered_atoms 里。"""
        if not self.atoms:
            return False
        return all(a in registered_atoms for a in self.atoms)


def select_tech(
    spec_md: str,
    registered_atoms: Sequence[str],
    registered_mcp_servers: Optional[Sequence[str]] = None,
) -> TechStack:
    """从 spec.md 文本里启发式选 tech(M2+ 升级 LLM)。

    简化版(2 策略):
      1. 直接引用:抓 "atom: <name>" 形式
      2. 关键词推断:spec.md 含 "PPT"/"ppt" → 推断用 `write_ppt`(若 registered)

    关键不变量(AC3):返回的 TechStack.is_valid() 必须 True。
    """
    import re
    candidates: set[str] = set()
    # 1) 显式引用
    atom_pattern = re.compile(r"atom:\s*([A-Za-z0-9_]+)", re.IGNORECASE)
    candidates.update(atom_pattern.findall(spec_md))
    # 2) 关键词推断(中英文,简化版)
    keyword_map: dict[str, tuple[str, ...]] = {
        "ppt": ("write_ppt", "prd_review"),
        "PPT": ("write_ppt", "prd_review"),
        "大纲": ("write_ppt",),
        "幻灯": ("write_ppt",),
        "doc": ("read_doc",),
        "prd": ("prd_review",),
        "review": ("prd_review",),
    }
    for keyword, atoms in keyword_map.items():
        if keyword in spec_md:
            candidates.update(atoms)
    # 过滤:只保留在 registered_atoms 里的
    valid_atoms = tuple(a for a in candidates if a in registered_atoms)
    return TechStack(atoms=valid_atoms)
