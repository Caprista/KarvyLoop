"""test_forge_tool_merge — 搜索源偏好:复用-key 的 MCP 搜索在场时,keyless DDG web_search 让位。

只测纯合并逻辑 `_merge_extra_tools`(不跑真 agent):
- 无 extra → 原样;
- 注入普通 MCP 工具(非搜索)→ 并入,内置 web_search 保留;
- 注入 MCP web_search(复用你 key 的更好搜索)→ keyless web_search 让位,web_fetch 保留。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.coding.forge import _merge_extra_tools  # noqa: E402


def _builtin():
    # 模拟 make_coding_tools 的键集合(值无所谓,这里只看键的取舍)
    return {k: object() for k in
            ["read_file", "run_command", "web_search", "web_fetch", "write_file", "edit_file"]}


def test_no_extra_keeps_builtin():
    t = _builtin()
    _merge_extra_tools(t, None)
    assert "web_search" in t and "web_fetch" in t
    _merge_extra_tools(t, {})
    assert "web_search" in t


def test_non_search_mcp_tool_keeps_keyless_web_search():
    t = _builtin()
    _merge_extra_tools(t, {"mcp_minimax_understand_image": object()})
    assert "mcp_minimax_understand_image" in t
    assert "web_search" in t          # 非搜索 MCP 工具不影响内置搜索
    assert "web_fetch" in t


def test_mcp_web_search_demotes_keyless():
    t = _builtin()
    _merge_extra_tools(t, {"mcp_minimax_web_search": object(),
                           "mcp_minimax_understand_image": object()})
    assert "mcp_minimax_web_search" in t
    assert "web_search" not in t      # 复用-key 搜索在场 → keyless DDG 让位
    assert "web_fetch" in t           # 读网页工具保留
