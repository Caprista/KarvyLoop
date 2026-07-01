"""test_capability_web_mcp — #3 修:只读联网=基础能力,MCP=可信注入工具,别被默认 FULL 一票拒。

回归(Hardy 报):agent 联网搜被 capability_denied + 沙箱无外网。根因:web_search/web_fetch/mcp_*
不在 DEFAULT_TOOL_REQUIREMENTS → 默认 FULL → forge(WORKSPACE_WRITE)下被拒。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.capability import authorize, PermissionContext as PC, Mode, Allow, Deny  # noqa: E402
from karvyloop.capability.policy import required_mode  # noqa: E402


def _ok(tool, mode):
    return isinstance(authorize(PC(tool=tool, input={"query": "x", "url": "https://x"},
                                   mode=mode, workspace_root=None)), Allow)


def test_web_tools_are_readonly_floor():
    assert required_mode("web_search") == Mode.READ_ONLY
    assert required_mode("web_fetch") == Mode.READ_ONLY
    # maker(forge) 和只读 checker 都能搜(基础能力)
    assert _ok("web_search", Mode.WORKSPACE_WRITE) and _ok("web_search", Mode.READ_ONLY)
    assert _ok("web_fetch", Mode.WORKSPACE_WRITE) and _ok("web_fetch", Mode.READ_ONLY)


def test_mcp_tools_allowed_at_maker_denied_at_readonly_checker():
    assert required_mode("mcp_minimax_web_search") == Mode.WORKSPACE_WRITE
    # forge(WORKSPACE_WRITE)放行 —— 配了 MCP 就能用
    assert _ok("mcp_minimax_web_search", Mode.WORKSPACE_WRITE)
    assert _ok("mcp_anything_else", Mode.WORKSPACE_WRITE)
    # 只读 checker 仍拦(maker/checker 分离)
    assert isinstance(authorize(PC(tool="mcp_minimax_web_search", input={}, mode=Mode.READ_ONLY,
                                   workspace_root=None)), Deny)


def test_unknown_tool_still_full_by_default():
    # 没动其它:未知工具仍 FULL(最严)
    assert required_mode("rm_minus_rf") == Mode.FULL
