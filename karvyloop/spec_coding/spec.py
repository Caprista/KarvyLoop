"""Stage 2: Spec Composer —— intent → spec.md 文本。

**AC2 不变量**:spec.md 含**至少**4 段(目标/输入/输出/verify)。

设计:docs/12 §3.1 Stage 2。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Optional

from .intent import Intent


# spec.md 必含的 4 段(AC2 锁)
SPEC_REQUIRED_SECTIONS: tuple[str, ...] = (
    "## 目标",
    "## 输入",
    "## 输出",
    "## verify",
)


@dataclasses.dataclass(frozen=True)
class Spec:
    """spec.md 的内容(纯文本 + 元数据)。"""
    md_text: str
    goal: str
    sections: tuple[str, ...]   # 实际含的 section 标题

    def has_required_sections(self) -> bool:
        """AC2 校验:是否含 SPEC_REQUIRED_SECTIONS 的所有 4 段。"""
        return all(s in self.md_text for s in SPEC_REQUIRED_SECTIONS)


def compose_spec(intent: Intent) -> Spec:
    """把 Intent 整理成 spec.md 文本。

    简化版(M2+ 升级 LLM):
      - 4 段固定(目标/输入/输出/verify)
      - 内容从 intent.goal 衍生
    """
    goal = intent.goal or "(未指定)"
    ts = datetime.now(timezone.utc).isoformat()
    md = (
        f"<!-- karvyloop.spec_coding generated -->\n"
        f"<!-- ts: {ts} -->\n"
        f"<!-- intent_confidence: {intent.confidence} -->\n"
        f"\n"
        f"# Spec: {goal}\n"
        f"\n"
        f"## 目标\n\n{goal}\n\n"
        f"## 输入\n\n(由调用方传入,M2+ 升级时由 LLM 推断)\n\n"
        f"## 输出\n\n(由调用方返回, M2+ 升级时由 LLM 推断)\n\n"
        f"## verify\n\n"
        f"- [ ] 端到端可执行(由 Stage 4 实现后,Stage 5 验证)\n"
        f"- [ ] 满足 #2 §4 verify gate(score≥3 ∧ sr≥0.8)\n"
    )
    return Spec(md_text=md, goal=goal, sections=SPEC_REQUIRED_SECTIONS)
