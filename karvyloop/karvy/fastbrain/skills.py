"""skills — 技能库(已结晶技能的可检索接口,公共快脑工具)。

设计:docs/25-fastbrain-architecture.md §3 技能快脑。

**职责**:
- 提供"按 intent / 签名 / 关键词 检索已结晶技能"的公共接口
- 命中 → 直接调用技能(不烧 token,不出大脑)
- 不命中 → 调用方决定:转大脑 / 转其他快脑

**纪律**:
- 公共机制 — 任何 agent / role 可调
- 不依赖小卡私有组件(不 import karvy.atoms 中任何具体类)
- 不参与 A2A(技能执行走业务域或 MainLoop,不在本模块)
- 0.1.0 骨架 — 实际检索/匹配逻辑在后续拍补

**借 Q5**:#2 crystallize 已落地的 `crystallize.store` / `crystallize.recall` 是
底层数据源;本模块**只**包一层"按 fastbrain 调用约定"的接口,不重写检索算法。
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ["SkillLookup", "lookup_skill"]


class SkillLookup:
    """技能库查询器(0.1.0 骨架)。

    0.1.0 留接口;实际查询走 crystallize.recall()。
    """

    def __init__(self, crystallize_store: Optional[object] = None) -> None:
        self._store = crystallize_store  # 0.1.0 留空,后续注入

    def lookup(self, intent: str) -> Optional[object]:
        """按 intent 查技能。命中返 skill,不命中返 None。

        0.1.0 骨架:返 None(总是"不命中",等后续拍接 crystallize.recall)。
        """
        logger.debug(f"[fastbrain.skills] lookup intent={intent!r} (0.1.0 stub)")
        return None


def lookup_skill(intent: str) -> Optional[object]:
    """便捷函数(0.1.0 骨架:返 None)。"""
    return SkillLookup().lookup(intent)
