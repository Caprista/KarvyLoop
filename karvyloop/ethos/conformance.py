"""Auditor 数据模型 + Attestation 哈希。

借业界 policy-conformance finding 思想
+ policy attestation 思想
(clean-room 借思想不抄代码)。

设计:docs/15 §3.4。
"""
from __future__ import annotations

import dataclasses
import hashlib
from typing import Any, Iterable, Optional

from .severity import ERROR, INFO, WARNING


@dataclasses.dataclass(frozen=True)
class AuditorFinding:
    """一条灵魂层 check 的 finding(对应 HealthFinding)。"""
    check_id: str            # "soul.slot_present" / "identity.not_empty" / ...
    severity: str            # Severity 之一
    message: str
    slot: Optional[str] = None        # 关联的 7 文件槽位(IDENTITY/SOUL/...)
    detail: str = ""                  # 详情(冲突词、缺内容等)


@dataclasses.dataclass(frozen=True)
class AuditorReport:
    """一次 audit 的总报告。"""
    agent_id: str
    findings: tuple[AuditorFinding, ...]
    checks_run: int
    checks_passed: int

    @property
    def ok(self) -> bool:
        """无 error finding = ok。"""
        return not any(f.severity == ERROR for f in self.findings)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == INFO)

    def findings_sorted(self) -> tuple[AuditorFinding, ...]:
        """E5:error 排前 warning 排前 info。"""
        from .severity import severity_rank
        return tuple(sorted(self.findings, key=lambda f: severity_rank(f.severity)))


def _finding_payload(f: AuditorFinding) -> str:
    """稳定序列化 finding 用于哈希。"""
    return f"{f.check_id}|{f.severity}|{f.slot or ''}|{f.message}|{f.detail}"


def compute_attestation_hash(findings: Iterable[AuditorFinding]) -> str:
    """E4:对**所**有 finding 计**算**稳定哈希(任一**变** → 哈希**变**)。

    拍 5 v0:sha256 → 8 hex(M3+ 升级 Merkle tree)。
    """
    h = hashlib.sha256()
    # 顺序:按 severity 排序后取,保证**同**样 finding 集**同**样哈希
    from .severity import severity_rank
    sorted_f = sorted(findings, key=lambda f: (severity_rank(f.severity), f.check_id, f.slot or ""))
    for f in sorted_f:
        h.update(_finding_payload(f).encode("utf-8"))
    return h.hexdigest()[:8]


@dataclasses.dataclass(frozen=True)
class AuditorAttestation:
    """灵魂层合规证明 —— 给 Syntonos (拍 6) / 外部审计用。"""
    agent_id: str
    attested_at: str                  # ISO timestamp
    checks_run: int
    checks_passed: int
    findings_count: int
    attestation_hash: str             # 8 hex(拍 5 v0 简化)
    findings: tuple[AuditorFinding, ...]
    ok: bool

    @classmethod
    def from_report(cls, report: AuditorReport, attested_at: str) -> "AuditorAttestation":
        return cls(
            agent_id=report.agent_id,
            attested_at=attested_at,
            checks_run=report.checks_run,
            checks_passed=report.checks_passed,
            findings_count=len(report.findings),
            attestation_hash=compute_attestation_hash(report.findings),
            findings=report.findings,
            ok=report.ok,
        )
