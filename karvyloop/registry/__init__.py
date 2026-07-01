"""registry — 工具/技能注册表：buildTool fail-closed + 配置期收窄工具面

规格（函数级实现架构 + 签名级接口 + 验收标准）：docs/modules/registry.md
里程碑：M0。状态：实现 + 通过 self-acceptance。
"""

from __future__ import annotations

from .registry import TOOL_SEARCH_THRESHOLD_TOKENS, ToolRegistry, _estimate_tokens
from .skills import (
    SkillFrontmatter,
    load_skill,
    load_skills_dir,
    parse_frontmatter,
)
from .tool import TOOL_DEFAULTS, Tool, build_tool, is_factory_built

__all__ = [
    # tool.py
    "TOOL_DEFAULTS", "Tool", "build_tool", "is_factory_built",
    # registry.py
    "ToolRegistry", "TOOL_SEARCH_THRESHOLD_TOKENS", "_estimate_tokens",
    # skills.py
    "SkillFrontmatter", "parse_frontmatter",
    "load_skill", "load_skills_dir",
]
