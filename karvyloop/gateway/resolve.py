"""模型解析：软默认层叠（gateway/resolve.py）。

复用 #0 §3.4 的"软默认、最具体者胜"：原子 > 角色 > 域 > 全局 default。
不写第二套配置系统。规格：docs/modules/gateway.md §3。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .registry import ModelRegistry


@dataclass
class ResolveScope:
    """模型解析作用域。由 executor(atom) / roles(role) / domains(domain) 逐层填（#9 §5 G3）。"""
    atom_model: Optional[str] = None
    role_model: Optional[str] = None
    domain_model: Optional[str] = None


def resolve_model(scope: ResolveScope, reg: ModelRegistry) -> str:
    """最具体者胜：任一层填了就用它（并校验存在）；全空落全局 default。"""
    for ref in (scope.atom_model, scope.role_model, scope.domain_model):
        if ref:
            reg.get(ref)        # 填了但查不到 → UnknownModelError（fail-closed）
            return ref
    return reg.default_chat       # 全空 → 全局 default
