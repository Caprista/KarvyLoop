"""sandbox — 隔离执行 + PAL。

规格（函数级实现架构 + 签名级接口 + 验收标准）：docs/modules/sandbox.md
里程碑：M0（bubblewrap Linux）。状态：实现 + 通过 self-acceptance。
PAL：核心层只暴露 Sandbox 协议与 selector；平台实现关进 karvyloop.platform.*。
"""

from __future__ import annotations

from .base import Sandbox
from .exec_result import ExecResult
from .mounts import has_net, mounts_from_token
from .selector import default_sandbox

__all__ = [
    "Sandbox",
    "ExecResult",
    "default_sandbox",
    # 工具函数（决策链/令牌层也会用）
    "mounts_from_token",
    "has_net",
]
