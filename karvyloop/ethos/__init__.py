"""ethos — M2.0 拍 5 灵魂层治理双升级。

设计:docs/15-ethos-agent.md。

**Bootstrapper 升级**(接 Wizard 拍 1):
  3 职责:提问 / 答案整理 (拍 1 已做) / 答案解析 / 推荐灵魂层 3 问
**Auditor 升级**(接 Adapter 拍 4 + Validator 拍 0):
  8 项合规检查 + severity 3 档 + Attestation 合规证明

**核心不变量**(doc §4):
- E1 Bootstrapper 不强制注入 LLM (model_fn 为 None 走关键词 fallback)
- E2 Auditor 不抛(severity 走 finding)
- E3 7 文件不齐时必须有 ≥1 error finding
- E4 Attestation 哈希覆盖所有 finding
- E5 severity 排序稳定
- E6 Auditor 检查对象 = 注入的 agent_dir(不写 cwd)
- E7 Bootstrapper 解析 = InsightDict typed dataclass
- E8 强模型接口 = 注入 Callable
"""
from __future__ import annotations

from .severity import ERROR, INFO, WARNING, Severity, severity_rank
from .conformance import (
    AuditorAttestation,
    AuditorFinding,
    AuditorReport,
    compute_attestation_hash,
)
from .auditor import Auditor, all_check_ids, default_auditor
from .bootstrapper import (
    Bootstrapper,
    InsightDict,
    default_bootstrapper,
    interpret_answers,
    recommend_three_questions,
)

__all__ = [
    "Severity",
    "ERROR",
    "WARNING",
    "INFO",
    "severity_rank",
    "AuditorAttestation",
    "AuditorFinding",
    "AuditorReport",
    "compute_attestation_hash",
    "Auditor",
    "all_check_ids",
    "default_auditor",
    "Bootstrapper",
    "InsightDict",
    "default_bootstrapper",
    "interpret_answers",
    "recommend_three_questions",
]
