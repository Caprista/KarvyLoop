"""权限 ask / 结晶确认交互（cli/prompt_ui.py）。

规格：docs/modules/workbench-cli.md §3 prompt_ui.py + §4 UX 三原则。
- M0 简化:stdin/stdout 行协议,非 TTY 时给默认决策(deny/yes/auto)
- 权限 ask → 用户选 y/n(也可 a = always-allow for this session)
- 结晶 confirm → 用户选 y/N(默认 N,不偷偷固化)
"""

from __future__ import annotations

import sys
from typing import Any, Optional, TextIO


# 决策常量(spec §3 run.py 提到 Decision,这里也用同一份)
DECISION_ALLOW = "allow"
DECISION_ALLOW_ALWAYS = "allow_always"
DECISION_DENY = "deny"


def _isatty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def ask_permission(
    tool: str,
    subject: str,
    *,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    default: str = DECISION_DENY,
) -> str:
    """渲染权限请求并读取用户决策。

    非 TTY 环境(non-interactive shell)→ 用 default(默认 deny,HR-1 fail-closed)
    """
    out = stdout or sys.stdout
    inp = stdin or sys.stdin
    if not _isatty():
        return default
    out.write(f"  ⚠ 权限请求:{tool} · {subject}\n")
    out.write("    [y] 允许  [n] 拒绝  [a] 本会话始终允许  > ")
    out.flush()
    try:
        line = inp.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if line in ("y", "yes"):
        return DECISION_ALLOW
    if line in ("a", "always"):
        return DECISION_ALLOW_ALWAYS
    return DECISION_DENY


def confirm_crystallize(
    sig_summary: str,
    *,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    default: bool = False,
) -> bool:
    """结晶前确认。默认 N(不偷偷固化;UX 三原则之三)。

    非 TTY → 用 default(M0 = False,不偷偷结晶)
    """
    out = stdout or sys.stdout
    inp = stdin or sys.stdin
    if not _isatty():
        return default
    out.write(f"  ◆ 建议结晶:技能稳定运行 {sig_summary} 次,是否固化?[y/N] ")
    out.flush()
    try:
        line = inp.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    return line in ("y", "yes")


__all__ = [
    "ask_permission",
    "confirm_crystallize",
    "DECISION_ALLOW",
    "DECISION_ALLOW_ALWAYS",
    "DECISION_DENY",
]
