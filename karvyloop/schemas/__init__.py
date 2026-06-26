"""KarvyLoop 数据契约（唯一权威定义；对应 #7 §1）。

各运行时模块的契约都从这里 import，不在别处另立定义。组织方式镜像本体论分层：
  能力(L0) → 模型注册表 → 原子(L1) → 角色(L2) → 域(L3/L4) → 认知 → 楔子 → 交互
"""

from __future__ import annotations

from ._base import Schema
from .atom import AtomRun, AtomSpec
from .capability import Capability, CapabilityToken
from .cognition import Belief, Pursuit
from .domain import DomainManifest, Norm
from .envelope import Envelope
from .model import (
    InputModality,
    ModelApi,
    ModelDefinition,
    ModelRole,
    ProviderAuthMode,
    ProviderConfig,
)
from .role import RoleSpec
from .skill import EphemeralTool, Skill, UsageStats

__all__ = [
    "Schema",
    # L0 能力
    "Capability",
    "CapabilityToken",
    # 模型注册表
    "ModelDefinition",
    "ProviderConfig",
    "ModelApi",
    "ModelRole",
    "InputModality",
    "ProviderAuthMode",
    # L1 原子
    "AtomSpec",
    "AtomRun",
    # L2 角色
    "RoleSpec",
    # L3/L4 域
    "Norm",
    "DomainManifest",
    # 认知
    "Pursuit",
    "Belief",
    # 楔子
    "EphemeralTool",
    "UsageStats",
    "Skill",
    # 交互
    "Envelope",
]
