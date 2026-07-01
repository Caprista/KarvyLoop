"""test_atom_tool_catalog — 原子工具真实性诚实标注(docs/14 §11.1).

round2 裁判逮到:导入纯人设 agent 合成的"工具名"(validate-geographic-coherence)对不上真实工具
注册表 → 原子看着有工具其实调不动。这里验:核对真实目录 → executable/advisory + unresolved_tools。
"""
from __future__ import annotations

from karvyloop.atoms.registry import AtomRegistry, AtomStore
from karvyloop.atoms.tool_catalog import classify_atom_tools


# ---- 判定函数 ----
def test_classify_resolves_builtins_flags_synthesized():
    r = classify_atom_tools(["web_search", "read_file", "validate-geographic-coherence"])
    assert r["executable"] is True                       # 有真工具
    assert r["unresolved_tools"] == ["validate-geographic-coherence"]


def test_classify_all_synthesized_is_advisory():
    r = classify_atom_tools(["validate-geographic-coherence", "climate-system-design"])
    assert r["executable"] is False                      # 一个真工具都没有 = 顾问
    assert len(r["unresolved_tools"]) == 2


def test_classify_empty_tools_is_advisory():
    r = classify_atom_tools([])
    assert r["executable"] is False and r["unresolved_tools"] == []


def test_classify_normalizes_names():
    # web-search / Web Search → web_search;run-command → run_command
    r = classify_atom_tools(["web-search", "Run Command"])
    assert r["executable"] is True and r["unresolved_tools"] == []


def test_classify_extra_known_for_mcp():
    r = classify_atom_tools(["my_mcp_tool"], extra_known=["my_mcp_tool"])
    assert r["executable"] is True and r["unresolved_tools"] == []
    r2 = classify_atom_tools(["my_mcp_tool"])            # 不给 extra_known → 对不上
    assert r2["executable"] is False


# ---- 落库时自动标注 ----
def test_create_labels_executable_atom(tmp_path):
    reg = AtomRegistry(store=AtomStore(tmp_path / "atoms.json"))
    a = reg.create("real_worker", "task", "干活", tools=["web_search", "write_file"])
    assert a.executable is True and a.unresolved_tools == []


def test_create_labels_advisory_atom(tmp_path):
    reg = AtomRegistry(store=AtomStore(tmp_path / "atoms.json"))
    a = reg.create("geo_advisor", "task", "校验地理一致性",
                   tools=["validate-geographic-coherence", "climate-system-design"])
    assert a.executable is False, "纯合成工具名的原子该标 advisory"
    assert set(a.unresolved_tools) == {"validate-geographic-coherence", "climate-system-design"}
    # 重载回来仍是 advisory(落盘带上了)
    reg2 = AtomRegistry(store=AtomStore(tmp_path / "atoms.json"))
    assert reg2.get("geo_advisor").executable is False


def test_legacy_atom_without_field_defaults_executable():
    """旧 atoms.json 没这俩字段 → 默认 executable=True(不把历史原子误标 advisory)。"""
    from karvyloop.schemas.atom import AtomSpec
    spec = AtomSpec(id="old", kind="task", prompt="x", input_schema={}, output_schema={})
    assert spec.executable is True and spec.unresolved_tools == []
