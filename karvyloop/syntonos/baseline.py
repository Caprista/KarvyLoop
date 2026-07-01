"""Stage 1 Baseline + 行为事件模型。

**核心不变量**:
- S1 baseline 来自拍 5 Attestation
- S6 baseline + events = 注入
- S8 不调 LLM

设计:docs/16 §3.2 + §3.3。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Mapping, Optional

from karvyloop.ethos import AuditorAttestation

logger = logging.getLogger(__name__)


# 4 类内**置** event_type(拍 6 v0 占位,真**实**插**装**留 M3+)
EVENT_TYPES: tuple[str, ...] = (
    "tool_call",
    "soul_loaded",
    "keyword_spoken",
    "pursuit_created",
)


@dataclasses.dataclass(frozen=True)
class BehaviorEvent:
    """一条行为事件。"""
    timestamp: str                 # ISO
    agent_id: str
    event_type: str                # EVENT_TYPES 之一
    payload: dict                  # event-type-specific

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            logger.warning(
                "BehaviorEvent.event_type=%r not in EVENT_TYPES=%r",
                self.event_type, EVENT_TYPES,
            )

    def extract_keywords(self) -> tuple[str, ...]:
        """S8:从 payload 提取关键词(对账用,**不**调 LLM)。

        keyword_spoken → payload['keyword']
        tool_call     → payload.get('tool', '')
        soul_loaded   → payload.get('slot', '')
        pursuit_created → payload.get('pursuit_id', '')
        """
        if self.event_type == "keyword_spoken":
            kw = self.payload.get("keyword", "")
            return (kw,) if kw else ()
        if self.event_type == "tool_call":
            tool = self.payload.get("tool", "")
            return (tool,) if tool else ()
        if self.event_type == "soul_loaded":
            slot = self.payload.get("slot", "")
            return (slot,) if slot else ()
        return ()


# 拍 6 v0 内**置** 5 组灵**魂**层关**键**词**(从**灵**魂**层**抽**取**的**方**法**留**拍 5 升**级**)
DEFAULT_SOUL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "IDENTITY": ("产品经理", "工程师", "设计师", "战略", "运营", "PM", "engineer"),
    "SOUL": ("数据驱动", "激进", "保守", "温和", "完美", "速度", "质量", "data-driven"),
    "USER": ("客户", "用户", "团队", "行业", "目标人群", "内部", "外部"),
    "COMMITMENT": ("Q1", "Q2", "Q3", "Q4", "季度", "OKR", "上线", "ship"),
    "VERIFY": ("跑通", "测试", "verify", "通过", "端到端", "E2E"),
}


@dataclasses.dataclass(frozen=True)
class SyntonosBaseline:
    """基**准**线 = 拍 5 Attestation + 灵**魂**层关**键**词** + 禁**忌**词**(S1)。"""
    attestation: AuditorAttestation
    soul_keywords: dict[str, tuple[str, ...]]
    forbidden_keywords: tuple[str, ...] = ()
    captured_at: str = "1970-01-01T00:00:00Z"

    @property
    def baseline_hash(self) -> str:
        """S7:身**份**对账**用 attestation_hash。"""
        return self.attestation.attestation_hash

    def all_expected_keywords(self) -> tuple[str, ...]:
        """所有预期关**键**词**(展**平**)。"""
        out: list[str] = []
        for slot, kws in self.soul_keywords.items():
            out.extend(kws)
        return tuple(out)


def build_baseline_from_attestation(
    attestation: AuditorAttestation,
    soul_keywords: Optional[Mapping[str, tuple[str, ...]]] = None,
    forbidden_keywords: Optional[tuple[str, ...]] = None,
    captured_at: str = "1970-01-01T00:00:00Z",
) -> SyntonosBaseline:
    """S1 + AC1 入口:从 Attestation 构**造** baseline。"""
    return SyntonosBaseline(
        attestation=attestation,
        soul_keywords=dict(soul_keywords) if soul_keywords else dict(DEFAULT_SOUL_KEYWORDS),
        forbidden_keywords=forbidden_keywords or (),
        captured_at=captured_at,
    )
