"""runtime — 核心主循环运行时（P2-f 反向依赖修复：核心 loop 不再住 UI 包）。

The core loop (MainLoop / Brain / forge_slow_brain_factory) used to live in
``karvyloop/cli/main_loop.py``; console/ and workbench/ importing from a UI
package was a reverse dependency edge. P2-f moves it here verbatim.
``karvyloop.cli.main_loop`` remains as a compatibility shim.
"""

from __future__ import annotations

from .main_loop import (
    Brain,
    DriveResult,
    DriveStats,
    MainLoop,
    SlowBrain,
    forge_slow_brain_factory,
)

__all__ = [
    "Brain",
    "DriveResult",
    "DriveStats",
    "MainLoop",
    "SlowBrain",
    "forge_slow_brain_factory",
]
