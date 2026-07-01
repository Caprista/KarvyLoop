"""Wizard 预演 —— 写完 7 文件后调 Paradigm Loader 看拼装结果。

设计:docs/11-wizard.md §3 Stage 2(预演)。
回环验证 = 写完立刻看 Loader 怎么用,AC4 锁住"7 layer 全有,不走 default"。
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from ..paradigm import load_paradigm, LoadedParadigm, ParadigmContext
from ..paradigm.loader import DomainView, RoleInstance
from .compositor import Compositor
from .bootstrapper import WIZARD_STEPS, WizardStep

logger = logging.getLogger(__name__)


def files_to_role_instance(
    *,
    role_id: str,
    files: dict[str, str],  # step_id → .md 文本
) -> RoleInstance:
    """7 个 .md 文本 → Paradigm Loader 的 RoleInstance 视图。

    files 的 key 期望是 WIZARD_STEPS 里的 step_id(IDENTITY/SOUL/.../COMPOSITION)。
    缺失或"暂不填"占位 → 走 Loader 的 default。
    """
    identity = files.get("IDENTITY", "")
    soul = files.get("SOUL", "")
    composition = files.get("COMPOSITION", "")
    soul_refs: dict[str, Optional[str]] = {
        "IDENTITY": files.get("IDENTITY"),
        "SOUL": files.get("SOUL"),
        "USER": files.get("USER"),
        "COMMITMENT": files.get("COMMITMENT"),
        "VERIFY": files.get("VERIFY"),
        "MEMORY": files.get("MEMORY"),
    }
    return RoleInstance(
        role_id=role_id,
        identity_text=identity or "",
        soul_text=soul or "",
        composition_text=composition or "",
        soul_refs=soul_refs,
    )


def preview_paradigm(
    *,
    role_id: str,
    domain_id: str,
    files: dict[str, str],
    guardrails: Optional[list[str]] = None,
) -> LoadedParadigm:
    """写完 7 文件后,调 Paradigm Loader 预演。

    返回 LoadedParadigm(供 Wizard UI 打印;或 AC4 测试断言)。

    AC4 不变量:7 layer **全**有(不**走** default)。
    """
    ri = files_to_role_instance(role_id=role_id, files=files)
    dv = DomainView(domain_id=domain_id, guardrails=guardrails or [])
    ctx = ParadigmContext(
        role_instance=ri,
        domain=dv,
        user_message="[wizard preview]",
        current_pursuit=None,  # preview 阶段无 pursuit
        environment={},        # preview 阶段无 environment → L6 不加载
    )
    return load_paradigm(ctx)
