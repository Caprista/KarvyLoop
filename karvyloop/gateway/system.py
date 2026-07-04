"""System prompt 静/动态边界 + 缓存（gateway/system.py）。

HR-9：system prompt 拆静态段（角色/规范，全局可缓存）+ 动态段（cwd/git/记忆，每会话）；
静态前缀打 cache_control:ephemeral。边界切分的"完整逻辑"归 context-governance（M0贯穿），
本处只给网关组装请求所需的最小封装。规格：docs/modules/gateway.md §3、context-governance.md。

为什么只给「静态段」打断点、动态段不打:
- **static** = 角色/规范/工具用法这类**多轮间字节稳定**的前缀 → 打断点重复调用命中 cache_read,
  省 ~90% 前缀 input 成本。
- **dynamic** = cwd/git/记忆召回/治理注入这类**每会话/每轮都变**的段 → 打了反而每轮触发一次
  cache_write(~1.25x input),纯浪费。所以断点只落在 static 尾块、绝不落 dynamic。
- **最小可缓存长度**:Anthropic 对可缓存内容有最小 token 下限(约 1024);前缀太小时写缓存的
  成本 > 省下的,不划算 —— 低于 `_MIN_CACHE_TOKENS` 时不打断点(provider 本身也会静默不缓存)。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 最小可缓存前缀 token 粗估下限(Anthropic Messages 缓存最小长度约 1024 tok)。
# 低于此不打断点:小前缀写缓存的成本 > 省下的,加了只白付 cache_write。
_MIN_CACHE_TOKENS = 1024


def _rough_tokens(text: str) -> int:
    """CJK 感知的极简 token 粗估(仅用于"够不够最小缓存长度"的门槛判定,不进记账)。
    CJK ≈ 1 tok/字,其余 ≈ len//4 —— 与 gateway/client._text_tokens 同法(不 import 避免耦合)。"""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿" or "　" <= ch <= "ヿ" or "＀" <= ch <= "￯")
    return cjk + (len(text) - cjk) // 4


@dataclass
class SystemPrompt:
    static: list[str] = field(default_factory=list)    # 静态段：多轮间字节稳定，喂缓存
    dynamic: list[str] = field(default_factory=list)   # 动态段：每会话变

    def to_blocks(self, cache: bool = True) -> list[dict]:
        """组装成 provider 的 system blocks；静态前缀末块打 ephemeral 缓存断点（HR-9）。

        cache=False → 不打任何断点(开关关 / 不支持缓存的路径)。
        断点只落静态段尾块,且静态段总量须 ≥ 最小可缓存长度(否则打了只白付 cache_write)。
        动态段永不打(每轮变,缓存必 miss)。
        """
        blocks: list[dict] = [{"type": "text", "text": s} for s in self.static]
        if blocks and cache:
            static_tokens = sum(_rough_tokens(s) for s in self.static)
            if static_tokens >= _MIN_CACHE_TOKENS:
                blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        blocks += [{"type": "text", "text": d} for d in self.dynamic]
        return blocks
