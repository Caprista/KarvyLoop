"""qa — QA 库(静态/规则类问答沉淀,公共快脑工具)。

设计:docs/25-fastbrain-architecture.md §4 QA 快脑。

**职责**:
- 沉淀"大脑答过 N 次的同类问题"成 FAQ
- 命中 → 直接复用答案(不烧 token,不出大脑)
- **关键门控**:必须是"静态 / 规则类"问题才能沉淀(用户原话:今天天气不沉淀)

**门控算法**(0.1.0 MVP 用规则,见 docs/25 §4.1):
- 黑名单词:"今天/昨天/明天/现在/当前/最近"等时变信号
- 模式识别:含时间词 / 上下文依赖词 → 拒沉淀
- 静态问题(规则 / 定义 / 流程):白名单可凝
- 0.2.0 升级小模型意图分类兜底

**纪律**:
- 公共机制 — 任何 agent / role 可调
- 不参与 A2A
- 0.1.0 骨架 — 门控规则 + 沉淀/查询接口
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ["QAEntry", "QALibrary", "can_crystallize", "lookup_qa"]


# 0.1.0 MVP 门控规则 — 黑名单词(命中 → 拒沉淀)
_TIME_VARIANT_WORDS = (
    "今天", "昨天", "明天", "前天", "后天",
    "现在", "当前", "此刻", "最近", "最新",
    "今天", "today", "yesterday", "tomorrow",
    "now", "current", "currently", "latest", "recent",
)

_CONTEXT_DEPENDENT_WORDS = (
    "我的", "我那个", "我刚才", "我之前", "我上次的",
    "那件", "那次", "那个", "上次",
    "my", "mine", "that one", "the one i",
)

# 黑名单 regex(粗筛,任何时变 / 上下文强依赖命中即拒)
_BLOCK_RE = re.compile(
    "|".join(re.escape(w) for w in (_TIME_VARIANT_WORDS + _CONTEXT_DEPENDENT_WORDS)),
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QAEntry:
    """QA 库条目。"""
    question_signature: str  # 归一化后的问题签名(0.1.0 stub:用原问题)
    answer: str
    times_answered: int = 1


def can_crystallize(question: str) -> bool:
    """判断问题能否凝进 QA 库(0.1.0 MVP:纯规则)。

    Args:
        question: 用户原始问题。

    Returns:
        True = 可凝(静态 / 规则类);False = 禁凝(时变 / 上下文强依赖)。
    """
    if not question or not question.strip():
        return False
    if _BLOCK_RE.search(question):
        logger.debug(f"[fastbrain.qa] reject(时变/上下文): {question!r}")
        return False
    return True


class QALibrary:
    """QA 库(0.1.0 骨架:内存 dict,不持久化)。"""

    def __init__(self) -> None:
        self._store: dict[str, QAEntry] = {}

    def put(self, question: str, answer: str) -> Optional[QAEntry]:
        """沉淀一条 QA。门控 fail 返 None,否则返存入的 entry。"""
        if not can_crystallize(question):
            return None
        sig = question.strip().lower()  # 0.1.0 stub:归一化 = strip + lower
        existing = self._store.get(sig)
        if existing is not None:
            self._store[sig] = QAEntry(
                question_signature=sig,
                answer=existing.answer,  # 答案不变,只增计数
                times_answered=existing.times_answered + 1,
            )
        else:
            self._store[sig] = QAEntry(question_signature=sig, answer=answer)
        return self._store[sig]

    def lookup(self, question: str) -> Optional[QAEntry]:
        """查 QA 库。命中返 entry,不命中返 None。"""
        sig = question.strip().lower()
        return self._store.get(sig)


def lookup_qa(question: str) -> Optional[QAEntry]:
    """便捷函数(0.1.0 骨架:返 None)。"""
    return QALibrary().lookup(question)
