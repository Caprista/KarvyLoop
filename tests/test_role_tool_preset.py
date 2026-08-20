"""test_role_tool_preset — role 级工具可见性预设(B 方向最小一刀)。

设计:COMPOSITION.yaml 可选 `tools:` 段(组名/显式工具名)→ RoleView.tool_ids →
build_role_paradigm_prompt 挂 persona.tool_preset → forge 合并后 apply_tool_preset 过滤。

AC:
- AC1: COMPOSITION tools 段 round-trip(create 写 → get 解析);缺省 = 空(全量,0 回归)
- AC2: update/rewrite_atom_refs/add_atom 重写 COMPOSITION 时 tools 段不丢
- AC3: apply_tool_preset 组展开(coding/network/mcp/computer/create_atom)+ 显式名 + 未知名 fail-soft
- AC4: build_role_paradigm_prompt 挂 tool_preset;无 tools 段 → 不挂(None)
- AC5: forge 合并后过滤生效(带 preset → 只剩白名单;不带 → 全量)
"""
from __future__ import annotations

import time

from karvyloop.coding.tools import apply_tool_preset, make_coding_tools
from karvyloop.coding.filestate import FileState
from karvyloop.roles.registry import RoleRegistry
from karvyloop.schemas import Capability, CapabilityToken


def _tok() -> CapabilityToken:
    return CapabilityToken(
        task_id="t",
        grants=[Capability(resource="fs:/tmp", ops=["read", "write"])],
        expiry=time.time() + 3600,
    )


def _registry(tmp_path) -> RoleRegistry:
    return RoleRegistry(tmp_path / "roles")


# ---- AC1: 声明 + 解析 round-trip ----
def test_create_with_tool_preset_roundtrip(tmp_path):
    reg = _registry(tmp_path)
    rv = reg.create("资料员", identity="管资料的", tool_ids=["coding", "network"])
    assert rv.tool_ids == ["coding", "network"]
    got = reg.get("资料员")
    assert got is not None and got.tool_ids == ["coding", "network"]
    # to_dict 也带(API 出参可见可编)
    assert got.to_dict()["tool_ids"] == ["coding", "network"]


def test_create_without_preset_defaults_empty(tmp_path):
    reg = _registry(tmp_path)
    rv = reg.create("普通角色", identity="x")
    assert rv.tool_ids == []           # 缺省 = 无预设(全量,0 回归)
    comp = (tmp_path / "roles" / "普通角色" / "COMPOSITION.yaml").read_text(encoding="utf-8")
    assert "tools:" not in comp        # 无预设不写 tools 段


def test_update_tool_ids(tmp_path):
    reg = _registry(tmp_path)
    reg.create("r1", identity="x")
    reg.update("r1", tool_ids=["coding"])
    assert reg.get("r1").tool_ids == ["coding"]
    reg.update("r1", tool_ids=[])      # 显式清空 → 回全量
    assert reg.get("r1").tool_ids == []


# ---- AC2: 重写保留 tools 段 ----
def test_rewrite_atom_refs_preserves_tools(tmp_path):
    reg = _registry(tmp_path)
    reg.create("r2", identity="x", tool_ids=["network"])
    reg.rewrite_atom_refs("r2", {"old_atom": "new_atom"})
    assert reg.get("r2").tool_ids == ["network"]


def test_add_atom_preserves_tools(tmp_path):
    reg = _registry(tmp_path)
    reg.create("r3", identity="x", tool_ids=["coding", "mcp"])
    reg.add_atom("r3", "some_atom")
    assert reg.get("r3").tool_ids == ["coding", "mcp"]


def test_update_identity_preserves_tools(tmp_path):
    reg = _registry(tmp_path)
    reg.create("r4", identity="旧人格", tool_ids=["computer"])
    reg.update("r4", identity="新人格")
    assert reg.get("r4").tool_ids == ["computer"]


# ---- AC3: apply_tool_preset ----
def _full_tools():
    return make_coding_tools(sandbox=None, file_state=FileState(),
                             workspace_root="/tmp", token=_tok())


def test_preset_group_coding():
    tools = _full_tools()
    out = apply_tool_preset(tools, ["coding"])
    assert set(out) == {"read_file", "write_file", "edit_file", "run_command", "reconcile_receipt"}
    assert "web_search" not in out and "web_fetch" not in out


def test_preset_group_network():
    out = apply_tool_preset(_full_tools(), ["network"])
    assert set(out) == {"web_search", "web_fetch"}


def test_preset_mcp_and_computer_groups():
    tools = _full_tools()
    tools["mcp_minimax_web_search"] = object()
    tools["mcp_computer_use_screenshot"] = object()
    tools["mcp_computer_use_click"] = object()
    # mcp 组 = 所有 mcp_*
    out = apply_tool_preset(tools, ["mcp"])
    assert set(out) == {"mcp_minimax_web_search", "mcp_computer_use_screenshot", "mcp_computer_use_click"}
    # computer 组单列 = 只桌面控制(不带其他 mcp)
    out2 = apply_tool_preset(tools, ["computer"])
    assert set(out2) == {"mcp_computer_use_screenshot", "mcp_computer_use_click"}


def test_preset_explicit_name_and_union():
    tools = _full_tools()
    out = apply_tool_preset(tools, ["network", "read_file"])
    assert set(out) == {"web_search", "web_fetch", "read_file"}


def test_preset_unknown_name_failsafe():
    tools = _full_tools()
    out = apply_tool_preset(tools, ["coding", "typo_group", "nonexistent_tool"])
    # 未知名被忽略(不把 role 搞成零工具砖——coding 组仍在)
    assert "read_file" in out and "typo_group" not in out


def test_preset_empty_or_none_passthrough():
    tools = _full_tools()
    assert apply_tool_preset(tools, None) is tools
    assert apply_tool_preset(tools, []) is tools


# ---- AC4: persona 挂载 ----
def test_paradigm_prompt_attaches_preset(tmp_path):
    reg = _registry(tmp_path)
    rv = reg.create("挂载员", identity="x", tool_ids=["coding"])
    from karvyloop.coding.paradigm_prompt import build_role_paradigm_prompt
    cp = build_role_paradigm_prompt(rv, None, intent="干活", cwd="/tmp")
    assert cp is not None
    assert getattr(cp, "tool_preset", None) == ["coding"]


def test_paradigm_prompt_no_preset_when_absent(tmp_path):
    reg = _registry(tmp_path)
    rv = reg.create("全量员", identity="x")
    from karvyloop.coding.paradigm_prompt import build_role_paradigm_prompt
    cp = build_role_paradigm_prompt(rv, None, intent="干活", cwd="/tmp")
    assert cp is not None
    assert getattr(cp, "tool_preset", None) is None   # 无 tools 段 → 不挂(0 回归)


# ---- AC5: forge 过滤(经 generate_and_run 的组合点)----
def test_forge_filters_by_persona_preset():
    """persona 带 tool_preset → 合并后工具被过滤;mcp 组收窄也生效。"""
    from karvyloop.coding.persona import build_role_persona_prompt
    from karvyloop.coding.tools import apply_tool_preset as _apt
    # 直接验证 forge 内的过滤逻辑路径:base + extras 合并后过 preset
    tools = _full_tools()
    tools["create_atom"] = object()
    tools["mcp_x"] = object()
    persona = build_role_persona_prompt("r", cwd="/tmp")
    persona.tool_preset = ["coding", "create_atom"]
    preset = getattr(persona, "tool_preset", None)
    out = _apt(tools, preset) if preset else tools
    assert set(out) == {"read_file", "write_file", "edit_file", "run_command",
                        "reconcile_receipt", "create_atom"}
    assert "mcp_x" not in out


def test_forge_no_preset_keeps_all():
    from karvyloop.coding.persona import build_role_persona_prompt
    persona = build_role_persona_prompt("r", cwd="/tmp")
    assert getattr(persona, "tool_preset", None) is None   # 轻量 persona 无预设 → forge 不过滤
