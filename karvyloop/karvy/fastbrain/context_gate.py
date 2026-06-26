"""context_gate — 上下文依赖门(M3+ 拍 9.1b,公共快脑工具)。

设计:docs/26 §B(CV-9)+ docs/25 §6.5(FB-9)。

**职责**:判断"一句话能否脱离当前对话上下文独立理解"。
- 强依赖(指代/省略/"刚才那个"/极短应答)→ **不准走快脑**(只有慢脑能消解指代)
- 也用于 CV-11:上下文依赖的 intent **不结晶**(临时映射不进永久库)

**与 FB-3 QA 门控同源**:qa.can_crystallize 挡"时变 + 上下文强依赖"问题不沉淀;
本门挡"上下文依赖"句不走快脑 / 不结晶。同一个原则,作用在快慢脑协作路径上。

**关键语义**(docs/26 §1.1 缺口①):
- "删掉它" —— "它"在对话 A 指文件、对话 B 指记忆;快脑只看单句会快速给错答案
- → 这种句**必须**进慢脑(读对话上下文消解"它")

**0.1.0 MVP**:纯规则(指代词 + 省略/承接词 + 极短应答)。0.2.0 升级小模型。

**纪律**:公共机制(任何 agent/role 可调)/ 不参与 A2A / 不依赖小卡私有(FB-5)。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

__all__ = ["is_context_dependent", "DEPENDENT_MARKERS"]


# 中文指代 / 省略 / 承接 标记词(子串匹配;命中 + 有上下文 = 强依赖)
# 注:故意**不**含 "他/他们"(撞"其他")、裸 "再"(撞"再次/再说")—— 误判会烦用户,
# 0.1.0 取高精度子集(漏判可补规则)。
_CN_MARKERS = (
    # 指代
    "它", "它们", "她", "她们",
    "这个", "那个", "这些", "那些", "这件", "那件", "这条", "那条", "这样", "那样",
    "上面", "前面", "刚才", "刚刚", "之前那", "上一个", "上次那",
    # 承接 / 省略(顺着上文说)
    "继续", "接着", "然后呢", "还有呢", "再来", "换一个", "下一个",
)

# 英文标记(**词边界**匹配 —— 防 "it" 命中 "git"、"them" 命中 "theme")
_EN_MARKERS = (
    "it", "them", "that one", "this one", "the same", "continue", "next one",
)

# 对外暴露(测试/诊断用)
DEPENDENT_MARKERS = _CN_MARKERS + _EN_MARKERS

_CN_RE = re.compile("|".join(re.escape(w) for w in _CN_MARKERS))
_EN_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _EN_MARKERS) + r")\b",
    re.IGNORECASE,
)

# 极短应答(有上下文时 = 顺着上文,如"好"/"对"/"是的"/"删")
_SHORT_REPLY_RE = re.compile(
    r"^\s*(好|好的|对|对的|是|是的|不|不对|不是|嗯|行|可以|删|改|加|要|不要|继续|算了|换)\s*[。.!！~]*\s*$"
)


def is_context_dependent(intent: str, *, has_context: bool) -> bool:
    """这句 intent 是否强依赖当前对话上下文?

    Args:
        intent: 用户这一句。
        has_context: 当前对话是否已有前文(无前文 → 无可依赖 → 永远 False)。

    Returns:
        True = 强依赖(指代/省略/极短应答)→ 不走快脑 + 不结晶(CV-9/CV-11)
        False = 可独立理解 → 正常走快脑判断

    设计:无上下文时一律 False(第一句没有"它"可指)。
    """
    if not has_context:
        return False
    if not intent or not intent.strip():
        return False
    if _SHORT_REPLY_RE.match(intent):
        logger.debug(f"[context_gate] dependent(极短应答): {intent!r}")
        return True
    if _CN_RE.search(intent) or _EN_RE.search(intent):
        logger.debug(f"[context_gate] dependent(指代/承接): {intent!r}")
        return True
    return False
