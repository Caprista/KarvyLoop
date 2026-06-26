"""Auditor —— 8 项灵魂层合规检查。

**核心不变量**(doc §4):
- E2 不抛(severity 走 finding)
- E3 7 文件不齐时 ≥1 error
- E6 检查对象 = 注入的 agent_dir
- E8 不直接 import 任何 LLM 库

设计:docs/15 §3.2 + §3.3。
"""
from __future__ import annotations

import logging
import pathlib
import re
from typing import Callable, Optional

from .conformance import AuditorFinding, AuditorReport
from .severity import ERROR, INFO, WARNING

logger = logging.getLogger(__name__)


# 7 文件槽位(同 Adapter 拍 4)
SOUL_SLOTS: tuple[str, ...] = (
    "IDENTITY", "SOUL", "USER", "MEMORY", "COMMITMENT", "VERIFY", "COMPOSITION",
)

# 冲突关键词库(IDENTITY vs SOUL 不一致)—— 5 组内置
CONFLICT_PAIRS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("激进/保守", ("激进", "aggressive", "冒险", "bold"), ("保守", "保守", "cautious", "稳", "conservative")),
    ("数据/直觉", ("数据驱动", "data-driven", "metrics"), ("直觉", "直觉", "intuition", "gut")),
    ("速度/质量", ("快", "fast", "速度", "ship"), ("完美", "perfect", "严谨", "rigor", "polish")),
    ("个人/团队", ("独立", "solo", "own"), ("团队", "team", "协作", "collaborate")),
    ("创新/稳定", ("创新", "innovate", "novel"), ("稳定", "stable", "proven", "reliable")),
)


def _read_file(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("read %s failed: %s", path, e)
        return ""


def _has_section(text: str) -> bool:
    """文件至少含 1 个 `## xxx` 段。"""
    return bool(re.search(r"^##\s+\S+", text, re.MULTILINE))


# ---- 8 项 check ID(协议不变量 AC8)----

CHECK_IDS: tuple[str, ...] = (
    "soul.slot_present",          # 7 文件齐
    "soul.file_not_empty",        # 文件非空
    "soul.has_section",           # 至少 1 个 ## 段
    "soul.identity_soul_consistent",  # 冲突检测
    "soul.commitment_specific",   # COMMITMENT 强度
    "soul.verify_has_gate",       # VERIFY 验证门
    "soul.user_has_subject",      # USER 范围
    "soul.composition_atoms_valid",    # COMPOSITION.yaml 引用合法
)


def all_check_ids() -> tuple[str, ...]:
    return CHECK_IDS


# ---- 单个 check 函数(可单独测试)----

def check_slot_present(agent_dir: pathlib.Path) -> AuditorFinding | None:
    """7 文件齐 — 缺哪个返 error。"""
    for slot in SOUL_SLOTS:
        fname = f"{slot}.md" if slot != "COMPOSITION" else "COMPOSITION.yaml"
        if not (agent_dir / fname).exists():
            return AuditorFinding(
                check_id="soul.slot_present",
                severity=ERROR,
                slot=slot,
                message=f"灵魂层缺文件: {fname}",
            )
    return None


def check_file_not_empty(agent_dir: pathlib.Path) -> list[AuditorFinding]:
    findings = []
    for slot in SOUL_SLOTS:
        fname = f"{slot}.md" if slot != "COMPOSITION" else "COMPOSITION.yaml"
        path = agent_dir / fname
        if not path.exists():
            continue
        text = _read_file(path)
        if not text.strip():
            findings.append(AuditorFinding(
                check_id="soul.file_not_empty",
                severity=WARNING,
                slot=slot,
                message=f"{fname} 内容为空",
            ))
    return findings


def check_has_section(agent_dir: pathlib.Path) -> list[AuditorFinding]:
    findings = []
    for slot in SOUL_SLOTS:
        fname = f"{slot}.md" if slot != "COMPOSITION" else "COMPOSITION.yaml"
        path = agent_dir / fname
        if not path.exists():
            continue
        text = _read_file(path)
        if text.strip() and not _has_section(text):
            findings.append(AuditorFinding(
                check_id="soul.has_section",
                severity=INFO,
                slot=slot,
                message=f"{fname} 缺 '## xxx' 段(建议加结构化段头)",
            ))
    return findings


def check_identity_soul_consistent(agent_dir: pathlib.Path) -> list[AuditorFinding]:
    findings = []
    identity = _read_file(agent_dir / "IDENTITY.md")
    soul = _read_file(agent_dir / "SOUL.md")
    if not identity or not soul:
        return findings
    identity_low = identity.lower()
    soul_low = soul.lower()
    for pair_name, a_keywords, b_keywords in CONFLICT_PAIRS:
        a_in = any(kw.lower() in identity_low for kw in a_keywords)
        b_in = any(kw.lower() in soul_low for kw in b_keywords)
        if a_in and b_in:
            findings.append(AuditorFinding(
                check_id="soul.identity_soul_consistent",
                severity=WARNING,
                message=f"IDENTITY vs SOUL 冲突: {pair_name} 两端都出现",
                detail=f"IDENTITY 含 {a_keywords}, SOUL 含 {b_keywords}",
            ))
    return findings


def check_commitment_specific(agent_dir: pathlib.Path) -> AuditorFinding | None:
    """COMMITMENT 强度 —— 至少有具体项目/时间相关词。"""
    text = _read_file(agent_dir / "COMMITMENT.md")
    if not text:
        return None
    # 具体性指标:数字 / Q1-Q4 / 月份 / OKR / 季度
    specific_markers = re.findall(r"(Q[1-4]|20\d{2}|OKR|季度|月份|\d+%|\d+ 个)", text, re.IGNORECASE)
    if len(specific_markers) < 1:
        return AuditorFinding(
            check_id="soul.commitment_specific",
            severity=WARNING,
            slot="COMMITMENT",
            message="COMMITMENT.md 缺具体承诺指标(建议加 Q1-Q4 / 数字 / 季度 OKR)",
        )
    return None


def check_verify_has_gate(agent_dir: pathlib.Path) -> AuditorFinding | None:
    """VERIFY 验证门 —— 至少有 1 个具体验证动作。"""
    text = _read_file(agent_dir / "VERIFY.md")
    if not text:
        return None
    gate_markers = re.findall(r"(跑|执行|测试|verify|test|跑通|通过|应|应得|must|should)", text, re.IGNORECASE)
    if len(gate_markers) < 1:
        return AuditorFinding(
            check_id="soul.verify_has_gate",
            severity=WARNING,
            slot="VERIFY",
            message="VERIFY.md 缺验证动作(建议加 '跑 X 测试' / '通过 Y 验证门')",
        )
    return None


def check_user_has_subject(agent_dir: pathlib.Path) -> AuditorFinding | None:
    """USER 范围 —— 至少有 1 个明确对象描述。"""
    text = _read_file(agent_dir / "USER.md")
    if not text:
        return None
    # 范围指标:角色 / 客户 / 团队 / 用户 / 行业
    subject_markers = re.findall(r"(客户|用户|团队|行业|客户类型|角色|目标人群)", text)
    if len(subject_markers) < 1:
        return AuditorFinding(
            check_id="soul.user_has_subject",
            severity=INFO,
            slot="USER",
            message="USER.md 缺明确对象描述(建议加 '客户类型 / 行业 / 目标人群')",
        )
    return None


def check_composition_atoms_valid(
    agent_dir: pathlib.Path,
    registered_atoms: Optional[tuple[str, ...]] = None,
) -> AuditorFinding | None:
    """COMPOSITION.yaml 引用合法 —— atom 在 registered_atoms 里。"""
    text = _read_file(agent_dir / "COMPOSITION.yaml")
    if not text or registered_atoms is None:
        return None
    # 抓 atom: <name> 形式
    refs = re.findall(r"atom:\s*([A-Za-z0-9_]+)", text)
    if not refs:
        return None
    bad = [r for r in refs if r not in registered_atoms]
    if bad:
        return AuditorFinding(
            check_id="soul.composition_atoms_valid",
            severity=ERROR,
            slot="COMPOSITION",
            message=f"COMPOSITION.yaml 引用未注册 atom: {bad}",
        )
    return None


# ---- Auditor 主类 ----

class Auditor:
    """灵魂层健康检查 —— 8 项 check 串联。"""

    def __init__(
        self,
        registered_atoms: Optional[tuple[str, ...]] = None,
        timestamp_fn: Optional[Callable[[], str]] = None,
    ) -> None:
        self._registered_atoms = registered_atoms
        self._timestamp_fn = timestamp_fn or (lambda: "1970-01-01T00:00:00Z")

    def audit(self, agent_dir: str, agent_id: str = "<unknown>") -> AuditorReport:
        """主入口:返 AuditorReport(**不**抛,E2)。"""
        path = pathlib.Path(agent_dir)
        findings: list[AuditorFinding] = []

        if not path.exists() or not path.is_dir():
            # dir 不存在 → 7 文件**全**缺,**只**报 1 个 error
            findings.append(AuditorFinding(
                check_id="soul.slot_present",
                severity=ERROR,
                message=f"agent_dir 不存在: {agent_dir}",
            ))
            return AuditorReport(
                agent_id=agent_id,
                findings=tuple(findings),
                checks_run=len(CHECK_IDS),
                checks_passed=0,
            )

        # 1) slot_present
        sp = check_slot_present(path)
        if sp is not None:
            findings.append(sp)
            # 缺文件 → 其他 6 项 check **不**跑(E3 仍**有** ≥1 error)
            return AuditorReport(
                agent_id=agent_id,
                findings=tuple(findings),
                checks_run=1,
                checks_passed=0,
            )

        # 2-7) 其余 7 项(若文件齐)
        findings.extend(check_file_not_empty(path))
        findings.extend(check_has_section(path))
        findings.extend(check_identity_soul_consistent(path))
        c = check_commitment_specific(path)
        if c:
            findings.append(c)
        v = check_verify_has_gate(path)
        if v:
            findings.append(v)
        u = check_user_has_subject(path)
        if u:
            findings.append(u)
        comp = check_composition_atoms_valid(path, self._registered_atoms)
        if comp:
            findings.append(comp)

        return AuditorReport(
            agent_id=agent_id,
            findings=tuple(findings),
            checks_run=len(CHECK_IDS),
            checks_passed=len(CHECK_IDS) - len(findings),
        )

    def attest(self, agent_dir: str, agent_id: str = "<unknown>") -> "AuditorAttestation":  # type: ignore[name-defined]
        """audit + 出 Attestation。"""
        from .conformance import AuditorAttestation
        report = self.audit(agent_dir, agent_id=agent_id)
        return AuditorAttestation.from_report(report, self._timestamp_fn())


def default_auditor(registered_atoms: Optional[tuple[str, ...]] = None) -> Auditor:
    return Auditor(registered_atoms=registered_atoms)
