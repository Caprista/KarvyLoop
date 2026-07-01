"""System prompt 静/动态边界 + 缓存（gateway/system.py）。

HR-9：system prompt 拆静态段（角色/规范，全局可缓存）+ 动态段（cwd/git/记忆，每会话）；
静态前缀打 cache_control:ephemeral。边界切分的"完整逻辑"归 context-governance（M0贯穿），
本处只给网关组装请求所需的最小封装。规格：docs/modules/gateway.md §3、context-governance.md。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SystemPrompt:
    static: list[str] = field(default_factory=list)    # 静态段：多轮间字节稳定，喂缓存
    dynamic: list[str] = field(default_factory=list)   # 动态段：每会话变

    def to_blocks(self) -> list[dict]:
        """组装成 provider 的 system blocks；静态前缀末块打 ephemeral 缓存断点（HR-9）。"""
        blocks: list[dict] = [{"type": "text", "text": s} for s in self.static]
        if blocks:
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        blocks += [{"type": "text", "text": d} for d in self.dynamic]
        return blocks
