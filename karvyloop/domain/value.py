"""value — 业务域灵魂(value.md 解析)。

**核心不变量**(doc §4):
- D2(9.4d 修订)value.md **创建时可选**:空 = 域暂无价值观原则(以后可补),
  域的灵魂(deontic 强护栏)照常治理;**非空**则必须合规范(以 '# 价值观' 开头)。
  病根:用户明示"新建协作域可以没有价值观",旧 D2"非空灵魂级"被否。
- D7 全部依赖注入

设计:docs/18 §3.1。
"""
from __future__ import annotations

import dataclasses
import re
from typing import Optional


class ValueMdRequiredError(RuntimeError):
    """(9.4d 起不再由 parse 抛;保留供向后兼容 import / 未来策略校验。)"""


class ValueMdFormatError(RuntimeError):
    """value.md 非空但格式错(必须以 '# 价值观' 开头)。"""


@dataclasses.dataclass(frozen=True)
class ValueMd:
    """业务域灵魂(解析后的 value.md)。

    字段:
      text: 完整文本(包含 # 价值观 前缀);**空字符串 = 暂无价值观**(合法)
      principles: 提取出的核心原则(每行一条,#/- 开头);空 value → ()
    """
    text: str
    principles: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        """域暂无价值观原则(创建时未填,以后可补)。"""
        return not self.text.strip()

    @staticmethod
    def empty() -> "ValueMd":
        """无价值观的灵魂(创建时可选 → 默认空)。"""
        return ValueMd(text="", principles=())

    @staticmethod
    def parse(raw: Optional[str]) -> "ValueMd":
        """从原始 markdown 文本解析。

        D2(9.4d 修订):
          - 空 / None / 纯空白 → 空灵魂(合法,可后补)
          - 非空则必须以 '# 价值观' 开头(KarvyLoop 约定)

        提取原则:以 '#' 或 '-' 开头的非空行(去前缀)。
        """
        if raw is None:
            return ValueMd.empty()
        if not isinstance(raw, str):
            raise ValueMdFormatError(f"D2: value_md must be str, got {type(raw).__name__}")
        text = raw.strip()
        if not text:
            return ValueMd.empty()
        if not text.startswith("# 价值观"):
            raise ValueMdFormatError(
                "D2: non-empty value.md must start with '# 价值观' (KarvyLoop convention)"
            )

        # 提取原则行
        principles: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("# "):
                # 跳过标题行
                continue
            if stripped.startswith("#") or stripped.startswith("-"):
                # 去掉前缀
                principle = stripped.lstrip("#-").strip()
                if principle:
                    principles.append(principle)

        return ValueMd(text=text, principles=tuple(principles))
