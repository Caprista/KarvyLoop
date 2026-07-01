"""Spec-coding 开关 + thresholds。

**AC6 不变量**:`enabled=False` → pipeline 返 None(完全**不**触发)。
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class SpecCodingConfig:
    """spec_coding 模块的配置。"""
    enabled: bool = True
    # Stage 3 选 atom 时,只从已注册列表里挑(防编)
    strict_atom_registry: bool = True
    # Stage 4 实现写到 tmp / sandbox(不**写**到主目录)
    write_to_sandbox_only: bool = True
    # Stage 5 结晶走 M1.5 "归档可逆"
    reversible_archive: bool = True
    # 日志前缀
    log_prefix: str = "[SpecCoding]"


def default_config() -> SpecCodingConfig:
    """默认配置。"""
    return SpecCodingConfig()
