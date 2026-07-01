"""karvyloop.spec_coding —— 静默从对话生成 skill 的 5 段流水线(M2.0 拍 2)。

**不**是"AI 自动写 Soul"(那是 Wizard 拍 1)——是"AI 静默从对话提取用户想做啥,然后自己 spec → 实现 → 结晶"。

设计:docs/12-spec-driven-coding.md。决策:CONTEXT/01-decision-log §十七。
"""

from .config import SpecCodingConfig, default_config
from .pipeline import (
    PipelineContext,
    SkillCandidate,
    SpecCodingError,
    spec_coding_pipeline,
)

__all__ = [
    # 公开 API
    "spec_coding_pipeline",
    "PipelineContext",
    "SkillCandidate",
    "SpecCodingError",
    "SpecCodingConfig",
    "default_config",
]
