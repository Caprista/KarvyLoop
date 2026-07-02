"""兼容 shim — 核心循环已搬 karvyloop/runtime,此处仅兼容旧导入路径（P2-f）。

Compatibility shim after P2-f: the core loop moved to
``karvyloop/runtime/main_loop.py`` (console/ and workbench/ importing the core
runtime from a UI package was a reverse dependency edge). This module only
re-exports the same names so ``from karvyloop.cli.main_loop import X`` keeps
working. New code should import from ``karvyloop.runtime.main_loop``.

注意:monkeypatch 请打到 ``karvyloop.runtime.main_loop``(真模块)——
patch 本 shim 对生产路径是无效空操作。
"""

from __future__ import annotations

from karvyloop.runtime.main_loop import (
    DEFAULT_CTX_TOKEN_BUDGET,
    TRACE_RAW_MIN,
    TRACE_RAW_PER_SIG,
    Brain,
    DriveResult,
    DriveStats,
    MainLoop,
    SlowBrain,
    _annotate_terminal,
    _method_body,
    _render_ctx_prefix,
    _slow_brain_accepts_ctx,
    forge_slow_brain_factory,
    recall,
)

__all__ = [
    "Brain", "DriveResult", "DriveStats", "MainLoop", "SlowBrain",
    "forge_slow_brain_factory",
]
