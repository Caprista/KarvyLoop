"""test_reconcile_tool — 报销确定性算术 = 一个 **tool**(role-atom-skill-tool 定位)。

Hardy 纠偏:确定性求解不是"单独功能",是执行 loop 的 tool —— expense **skill** 声明、报销员 **role**
组合即得。本测锁:工具本身能算、被认作真工具(非 unresolved)、make_coding_tools provisions、
expense skill 在 allowed-tools 里声明它。
"""
from __future__ import annotations

import asyncio

from karvyloop.atoms.tool_catalog import BUILTIN_TOOL_NAMES, classify_atom_tools
from karvyloop.coding.tools import ReconcileReceiptTool, make_coding_tools


def test_tool_runs_arithmetic_reconcile():
    """__call__ 就是把抽出的数字丢给确定性求解器,反解 + flags。"""
    tool = ReconcileReceiptTool()
    out = asyncio.run(tool({
        "line_items": [{"name": "拿铁", "qty": 2, "unit_price": None, "amount": None},
                       {"name": "美式", "qty": 1, "unit_price": 30.0, "amount": 30.0}],
        "subtotal": 96.0, "tax": None, "total": 96.0,
    }))
    assert out["line_items"][0]["amount"] == 66.0 and out["balanced"] is True


def test_tool_never_crashes():
    out = asyncio.run(ReconcileReceiptTool()({"line_items": [{"amount": "x"}], "total": None}))
    assert "balanced" in out


def test_recognized_as_real_tool_not_unresolved():
    """报销员/expense 的 tools 里含 reconcile_receipt → executable,不进 unresolved(否则=假工具)。"""
    assert "reconcile_receipt" in BUILTIN_TOOL_NAMES
    cls = classify_atom_tools(["read_file", "reconcile_receipt"])
    assert cls["executable"] is True and "reconcile_receipt" not in cls["unresolved_tools"]


def test_provisioned_in_execution_toolset():
    """make_coding_tools(role 执行的工具集)真的注入了它 —— 声明了就调得到(不是空声明)。"""
    tools = make_coding_tools(sandbox=None, file_state=None, workspace_root=".",
                              token=None, read_only=True)
    assert "reconcile_receipt" in tools and tools["reconcile_receipt"].name == "reconcile_receipt"


def test_capability_read_only_not_denied():
    """能力门:新工具默认 FULL 会被拦 → 报销员一调就 capability_denied。纯算术必须 READ_ONLY + 永不拦。"""
    from karvyloop.capability.deontic_gate import _READ_ONLY_TOOLS
    from karvyloop.capability.policy import DEFAULT_TOOL_REQUIREMENTS, Mode
    assert DEFAULT_TOOL_REQUIREMENTS.get("reconcile_receipt") == Mode.READ_ONLY
    assert "reconcile_receipt" in _READ_ONLY_TOOLS


def test_expense_skill_declares_the_tool():
    """skill 层:expense 的 allowed-tools 声明 reconcile_receipt(role 组合 skill 即得该 tool)。"""
    from karvyloop.registry.skills import parse_frontmatter, system_skills_dir
    fm, body = parse_frontmatter(system_skills_dir() / "expense" / "SKILL.md")
    allowed = (fm.raw or {}).get("allowed-tools") or []
    assert "reconcile_receipt" in allowed, "expense skill 必须在 allowed-tools 声明该 tool"
    assert "reconcile_receipt" in body, "方法里必须教何时调它"
