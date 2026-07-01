"""paradigm_prompt — value.md → per-role 执行规范 的编译器接缝(9.5 loop-step1)。

病根(审计):`paradigm_loader`(7 层编译器)写得很美,但**只在 Wizard 预演里被调**;
真执行路径(console drive → forge)用的是"governance 字符串拼前缀",不是编译。
这违背铁律——"理念不能转化为执行方法就是垃圾"。

本模块把缝接上:给一个**角色库里的角色**(materialized agent 目录,7 灵魂文件)+ 它所在**业务域**
(value.md + deontic),用 `load_paradigm` 编译成 per-role 的 system prompt(同一个 value、不同角色不同行为)。
缺角色目录 / 编译为空 → 返 None,调用方回退到轻量 persona(0 回归)。

设计:docs/00 §0.6 harness 层 + docs/10 paradigm-loader。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .prompt import CodingPrompt

logger = logging.getLogger(__name__)

_SOUL_SLOTS = ("IDENTITY", "SOUL", "USER", "MEMORY", "COMMITMENT", "VERIFY")


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def build_role_paradigm_prompt(
    role_view,
    domain=None,
    *,
    intent: str = "",
    cwd: str = "/",
) -> Optional[CodingPrompt]:
    """把角色灵魂(7 文件)+ 域 value.md/deontic 编译成 per-role system prompt。

    role_view: 角色库 RoleView(需有 `.path` 指向 agent 目录、`.id`)。
    domain: BusinessDomain(有 `.deontic` / `.value_md` / `.id`)或 None(无域 = 个人场)。
    返回 CodingPrompt(可直接喂 forge 的 system_prompt);无法编译 → None(调用方回退 persona)。
    """
    try:
        from karvyloop.paradigm.loader import (
            DomainView, ParadigmContext, RoleInstance, load_paradigm,
        )
        d = Path(getattr(role_view, "path", "") or "")
        if not str(d) or not (d / "COMPOSITION.yaml").exists():
            return None  # 不是一个 materialized 角色目录 → 回退

        role_instance = RoleInstance(
            role_id=getattr(role_view, "id", "role"),
            identity_text=_read(d / "IDENTITY.md"),
            soul_text=_read(d / "SOUL.md"),
            composition_text=_read(d / "COMPOSITION.yaml"),
            soul_refs={
                slot: (str(d / f"{slot}.md") if (d / f"{slot}.md").exists() else None)
                for slot in _SOUL_SLOTS
            },
        )

        guardrails: list[str] = []
        value_md: Optional[str] = None
        domain_id = "l0"
        if domain is not None:
            deo = getattr(domain, "deontic", None)
            if deo is not None:
                guardrails = (
                    list(getattr(deo, "forbid", ()) or ())
                    + list(getattr(deo, "oblige", ()) or ())
                )
            vm = getattr(domain, "value_md", None)
            value_md = (getattr(vm, "text", None) or None)
            domain_id = getattr(domain, "id", "l0") or "l0"

        ctx = ParadigmContext(
            role_instance=role_instance,
            domain=DomainView(domain_id=domain_id, guardrails=guardrails, value_md=value_md),
            user_message=intent,
            environment={"cwd": cwd} if cwd else {},
        )
        text = load_paradigm(ctx).to_system_prompt()
        if not text.strip():
            return None
        # 工作区块照旧(9.5 P1):告诉它写哪
        ws = f"你的工作区:{cwd}(要写文件/跑代码就在这,有读写权限,别往 /tmp 写)"
        return CodingPrompt(static=[text], dynamic_blocks=[ws])
    except Exception:
        # 真异常(非"无目录"那种 explicit return None)→ 记一笔,别静默吞掉
        logger.warning("build_role_paradigm_prompt 编译失败,回退 persona", exc_info=True)
        return None


__all__ = ["build_role_paradigm_prompt"]
