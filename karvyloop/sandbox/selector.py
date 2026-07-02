"""Sandbox 工厂（sandbox/selector.py）—— PAL 选实现。

规格：docs/modules/sandbox.md §PAL / #1 §6.2。
规则：Linux bwrap 可用 → BubblewrapSandbox；macOS sandbox-exec 可用 → SeatbeltSandbox；
否则（含 Windows）→ StubSandbox（fail-closed 明确拒绝，绝不静默无隔离执行）。
Windows = 降级模式（degraded）：运行时/控制台/自有结晶技能全功能，仅第三方技能脚本被禁用。
注入点：测试可传 `override=...`。
"""

from __future__ import annotations

import sys
from typing import Optional

from .base import Sandbox


def default_sandbox(override: Optional[Sandbox] = None) -> Sandbox:
    """选实现。

    顺序：
      1) override（测试 / 上层定制）
      2) Linux → 探 bwrap；可用则用，不可用降级到 StubSandbox（**不**静默改用无隔离）
      3) macOS → 探 sandbox-exec（Seatbelt）；可用则用，不可用降级到 StubSandbox
      4) 其他平台（Windows 等）→ StubSandbox（降级模式：只拦第三方技能脚本，其余全功能）
    """
    if override is not None:
        return override
    from karvyloop.platform._stub import StubSandbox
    if sys.platform.startswith("linux"):
        from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox
        if BubblewrapSandbox.available():
            return BubblewrapSandbox()
        return StubSandbox()
    if sys.platform == "darwin":
        from karvyloop.platform.darwin.seatbelt import SeatbeltSandbox
        if SeatbeltSandbox.available():
            return SeatbeltSandbox()
        return StubSandbox()
    return StubSandbox()
