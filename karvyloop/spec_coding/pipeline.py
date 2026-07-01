"""Spec-coding 5 段流水线 主编排。

设计:docs/12-spec-driven-coding.md §3.1。
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Optional, Sequence

from .config import SpecCodingConfig, default_config
from .crystallize import SkillCandidate, crystallize
from .implement import Implementation, implement
from .intent import Intent, extract_intent
from .spec import Spec, compose_spec
from .tech_select import TechStack, select_tech

logger = logging.getLogger(__name__)


class SpecCodingError(Exception):
    """spec_coding pipeline 任何阶段失败都包装成它。"""


@dataclasses.dataclass
class PipelineContext:
    """5 段流水线的输入。"""
    messages: Sequence[str]                  # 对话消息序列
    registered_atoms: Sequence[str]          # 已注册 atom id 列表(AC3 防编)
    registered_mcp_servers: Optional[Sequence[str]] = None
    base_dir: Optional[str] = None           # 测试/沙箱注入


@dataclasses.dataclass
class PipelineResult:
    """5 段流水线成功跑通的产物。"""
    intent: Intent
    spec: Spec
    tech: TechStack
    implementation: Implementation
    skill: SkillCandidate


def spec_coding_pipeline(
    ctx: PipelineContext,
    *,
    config: Optional[SpecCodingConfig] = None,
) -> Optional[PipelineResult]:
    """5 段流水线主编排。

    AC6 不变量:config.enabled=False → 返 None(完全**不**触发)
    AC7 不变量:任一段失败 → 已生成文件**不**回滚(归档可逆,本拍不实际生成归档)
    """
    cfg = config or default_config()
    log_p = cfg.log_prefix

    # AC6:开关
    if not cfg.enabled:
        logger.info("%s enabled=False → 跳过", log_p)
        return None

    # Stage 1: Intent
    intent = extract_intent(ctx.messages)
    if intent is None:
        logger.info("%s stage=1 intent 未提取到 → 跳过整条 pipeline", log_p)
        return None
    logger.info("%s stage=1 intent.goal=%r conf=%.2f", log_p, intent.goal, intent.confidence)

    # Stage 2: Spec
    try:
        spec = compose_spec(intent)
    except Exception as e:
        raise SpecCodingError(f"Stage 2 compose_spec failed: {e}") from e
    if not spec.has_required_sections():
        raise SpecCodingError(
            f"Stage 2 spec 缺必含 section: required={('## 目标','## 输入','## 输出','## verify')}"
        )
    logger.info("%s stage=2 spec.md sections=%d", log_p, len(spec.sections))

    # Stage 3: Tech
    try:
        tech = select_tech(
            spec.md_text,
            ctx.registered_atoms,
            ctx.registered_mcp_servers,
        )
    except Exception as e:
        raise SpecCodingError(f"Stage 3 select_tech failed: {e}") from e
    if not tech.is_valid(ctx.registered_atoms):
        # AC7:失败不回滚(本拍没真生成文件)
        raise SpecCodingError(
            f"Stage 3 tech 选型不通过:atoms={tech.atoms} not all in registered={list(ctx.registered_atoms)}"
        )
    logger.info("%s stage=3 tech.atoms=%s", log_p, tech.atoms)

    # Stage 4: Implement (写到 base_dir;AC4 不写到主目录)
    import pathlib
    base = pathlib.Path(ctx.base_dir) if ctx.base_dir else None
    try:
        impl = implement(spec, tech, base_dir=base)
    except Exception as e:
        raise SpecCodingError(f"Stage 4 implement failed: {e}") from e
    logger.info("%s stage=4 impl.artifact=%s", log_p, impl.artifact_path)

    # Stage 5: Crystallize (写到 base_dir/skills/ 子目录;AC8 产出 SKILL.md)
    try:
        skill = crystallize(impl, spec.goal, base_dir=base)
    except Exception as e:
        raise SpecCodingError(f"Stage 5 crystallize failed: {e}") from e
    if not skill.has_agentskills_io_frontmatter():
        raise SpecCodingError(
            "Stage 5 skill 缺 agentskills.io frontmatter(name + description)"
        )
    logger.info("%s stage=5 skill.name=%s path=%s", log_p, skill.name, skill.skill_path)

    return PipelineResult(
        intent=intent,
        spec=spec,
        tech=tech,
        implementation=impl,
        skill=skill,
    )
