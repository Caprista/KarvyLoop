"""cli — 工作台 CLI：init/run + 流式渲染 + 结晶确认

规格（函数级实现架构 + 签名级接口 + 验收标准）：docs/modules/workbench-cli.md
里程碑：M0。状态：实现 + 通过 self-acceptance。
"""

from __future__ import annotations

from .init import CONFIG_PATH, DEFAULT_CONFIG_YAML, cmd_init, default_config_path
from .main import VERSION, main
from karvyloop.runtime.main_loop import (  # P2-f:核心循环已搬 runtime/
    Brain,
    DriveResult,
    DriveStats,
    MainLoop,
    SlowBrain,
    forge_slow_brain_factory,
)
from .prompt_ui import (
    DECISION_ALLOW,
    DECISION_ALLOW_ALWAYS,
    DECISION_DENY,
    ask_permission,
    confirm_crystallize,
)
from .render import Renderer, RenderStats
from .run import cmd_run, cmd_run_async

__all__ = [
    "main", "VERSION",
    # init
    "cmd_init", "default_config_path", "CONFIG_PATH", "DEFAULT_CONFIG_YAML",
    # run
    "cmd_run", "cmd_run_async",
    # main_loop
    "Brain", "DriveResult", "DriveStats", "MainLoop", "SlowBrain",
    "forge_slow_brain_factory",
    # render
    "Renderer", "RenderStats",
    # prompt_ui
    "ask_permission", "confirm_crystallize",
    "DECISION_ALLOW", "DECISION_ALLOW_ALWAYS", "DECISION_DENY",
]
