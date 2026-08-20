"""Sandbox 工厂（sandbox/selector.py）—— PAL 选实现。

规格：docs/modules/sandbox.md §PAL / #1 §6.2。
规则：Linux bwrap 可用 → BubblewrapSandbox；macOS sandbox-exec 可用 → SeatbeltSandbox；
Windows → 探测降级链：Tier 3 RestrictedTokenSandbox（写隔离 + Job 资源上限；带 `net:`
的 token fail-closed 拒跑）→ Tier 4 DegradedWindowsSandbox（第一方 workspace 读写/exec
直通、诚实无隔离；第三方技能脚本 fail-closed 拒跑）。
其余平台 / Linux 无 bwrap / macOS 无 sandbox-exec → StubSandbox（fail-closed 明确拒绝，
绝不静默无隔离执行）。
调试：env `KARVYLOOP_SANDBOX=restricted|degraded|stub` 强制指定（仅 win32 分支识别）。
注入点：测试可传 `override=...`。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from .base import Sandbox


def default_sandbox(override: Optional[Sandbox] = None) -> Sandbox:
    """选实现。

    顺序：
      1) override（测试 / 上层定制）
      2) Linux → 探 bwrap；可用则用，不可用降级到 StubSandbox（**不**静默改用无隔离）
      3) macOS → 探 sandbox-exec（Seatbelt）；可用则用，不可用降级到 StubSandbox
      4) Windows → 探 RestrictedToken（Tier 3）；探测失败降 DegradedWindowsSandbox
         （Tier 4：只拦第三方技能脚本，其余全功能——第一方直通如实标无隔离）
      5) 其他平台 → StubSandbox

    能力缝(capability/seams):选出的实现注册为全局 SEAMS 的 sandbox provider —
    换后端=改这一处注册,消费侧(coding/tools)从缝解析,不逐工具改注入。
    注册失败不阻断(本函数的核心是"返回一个可用沙箱",注册是附加动作)。
    """
    if override is not None:
        _register(override)
        return override
    from karvyloop.platform._stub import StubSandbox
    if sys.platform.startswith("linux"):
        from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox
        if BubblewrapSandbox.available():
            return _register(BubblewrapSandbox())
        return _register(StubSandbox())
    if sys.platform == "darwin":
        from karvyloop.platform.darwin.seatbelt import SeatbeltSandbox
        if SeatbeltSandbox.available():
            return _register(SeatbeltSandbox())
        return _register(StubSandbox())
    if sys.platform == "win32":
        from karvyloop.platform.win.degraded import DegradedWindowsSandbox
        forced = os.environ.get("KARVYLOOP_SANDBOX", "").strip().lower()
        if forced == "stub":
            return _register(StubSandbox())
        if forced == "degraded":
            return _register(DegradedWindowsSandbox())
        try:
            from karvyloop.platform.win.restricted import RestrictedTokenSandbox
            if forced == "restricted" or RestrictedTokenSandbox.available():
                return _register(RestrictedTokenSandbox())
        except Exception:
            pass   # 探测/导入失败 → 降级,fail-closed 语义由 Degraded/第三方拒跑保住
        return _register(DegradedWindowsSandbox())
    return _register(StubSandbox())


def _register(sandbox: Sandbox) -> Sandbox:
    """把选出的沙箱登记为 sandbox 能力缝的 provider(同时标 fs/shell 同源)。

    fs/shell 的 provider 本质就是"一个 sandbox 实例 + 它的读写/exec 能力"——
    三者注册同一实例,消费侧按需取。注册失败(导入/异常)静默:不影响返回可用沙箱。
    """
    try:
        from karvyloop.capability.seams import SEAMS, SLOT_FS, SLOT_SANDBOX, SLOT_SHELL
        SEAMS.register_provider(SLOT_SANDBOX.name, sandbox)
        SEAMS.register_provider(SLOT_FS.name, sandbox)
        SEAMS.register_provider(SLOT_SHELL.name, sandbox)
    except Exception:
        pass
    return sandbox
