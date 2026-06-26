"""registry 验收测试 —— 逐条对应 docs/modules/registry.md §5 验收标准。

8 条 AC:HR-1 factory-only / fail-closed 默认 / 配置期收窄 /
重名拒 / 未知工具 dispatch / 字母序稳定 / SKILL.md 渐进披露 /
搜索阈值未触零开销。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from karvyloop.capability import Mode
from karvyloop.registry import (
    TOOL_DEFAULTS,
    Tool,
    ToolRegistry,
    build_tool,
    is_factory_built,
    load_skill,
    load_skills_dir,
    parse_frontmatter,
)
from karvyloop.schemas import Capability, CapabilityToken


def _tok(*, mode: Mode = Mode.FULL, fs_write: bool = True) -> CapabilityToken:
    grants = [Capability(resource="fs:/ws", ops=["read"])]
    if fs_write:
        grants.append(Capability(resource="fs:/ws", ops=["read", "write"]))
    return CapabilityToken(task_id="t", grants=grants, expiry=time.time() + 3600)


# ============ AC1:HR-1 —— 不经 build_tool 注册被拒 ============
def test_ac1_bare_tool_register_rejected():
    reg = ToolRegistry()

    async def _call(inp, token, sandbox):
        return inp

    # 裸 Tool
    bare = Tool(name="x", description="x", input_schema={"type": "object"}, call=_call)
    assert is_factory_built(bare) is False
    with pytest.raises(ValueError, match="HR-1"):
        reg.register(bare)
    assert "x" not in reg


# ============ AC2:HR-1 —— 忘声明 is_read_only 默认当写 ============
def test_ac2_missing_is_read_only_defaults_to_write():
    async def _call(inp, token, sandbox):
        return inp

    t = build_tool(name="x", input_schema={"type": "object"}, call=_call)
    # 显式未声明 is_read_only → 用 TOOL_DEFAULTS 的 lambda
    assert t.is_read_only({"anything": 1}) is False
    assert t.is_concurrency_safe({}) is False
    # required_mode 默认 FULL
    assert t.required_mode == Mode.FULL


# ============ AC3:配置期收窄 —— READ_ONLY 模式不含 write/exec 工具 ============
def test_ac3_exposed_tools_filters_by_mode():
    reg = ToolRegistry()

    async def _call(inp, token, sandbox):
        return inp

    # read_file: required_mode = READ_ONLY
    reg.register(build_tool(
        name="read_file", input_schema={"type": "object"}, call=_call,
        is_read_only=lambda inp: True, required_mode=Mode.READ_ONLY,
    ))
    # write_file: required_mode = WORKSPACE_WRITE
    reg.register(build_tool(
        name="write_file", input_schema={"type": "object"}, call=_call,
        required_mode=Mode.WORKSPACE_WRITE,
    ))
    # bash: required_mode = WORKSPACE_WRITE
    reg.register(build_tool(
        name="run_command", input_schema={"type": "object"}, call=_call,
        required_mode=Mode.WORKSPACE_WRITE,
    ))
    # net: required_mode = FULL
    reg.register(build_tool(
        name="network", input_schema={"type": "object"}, call=_call,
        required_mode=Mode.FULL,
    ))

    # READ_ONLY 模式
    exposed_ro = reg.exposed_tools(token=_tok(), mode=Mode.READ_ONLY)
    names_ro = [t["name"] for t in exposed_ro]
    assert "read_file" in names_ro
    assert "write_file" not in names_ro
    assert "run_command" not in names_ro
    assert "network" not in names_ro

    # WORKSPACE_WRITE 模式
    exposed_ww = reg.exposed_tools(token=_tok(), mode=Mode.WORKSPACE_WRITE)
    names_ww = [t["name"] for t in exposed_ww]
    assert "read_file" in names_ww
    assert "write_file" in names_ww
    assert "run_command" in names_ww
    assert "network" not in names_ww

    # FULL 模式
    exposed_full = reg.exposed_tools(token=_tok(), mode=Mode.FULL)
    names_full = [t["name"] for t in exposed_full]
    assert set(names_full) == {"read_file", "write_file", "run_command", "network"}


# ============ AC4:重名拒 ============
def test_ac4_duplicate_name_rejected():
    reg = ToolRegistry()

    async def _call(inp, token, sandbox):
        return inp

    reg.register(build_tool(name="x", input_schema={"type": "object"}, call=_call))
    with pytest.raises(ValueError, match="duplicate tool name"):
        reg.register(build_tool(name="x", input_schema={"type": "object"}, call=_call))
    assert len(reg) == 1


# ============ AC5:未知工具 dispatch 返回 error(不抛)============
@pytest.mark.asyncio
async def test_ac5_dispatch_unknown_tool_returns_error():
    reg = ToolRegistry()
    r = await reg.dispatch("does_not_exist", {}, token=_tok())
    assert r.is_error is True
    assert "unknown tool" in r.error_reason
    # 调用不抛
    # 二次确认
    r2 = await reg.dispatch("does_not_exist", {}, token=_tok())
    assert r2.is_error is True


# ============ AC6:字母序稳定(同输入字节一致 → 喂缓存)============
def test_ac6_exposed_tools_alphabetically_stable():
    reg = ToolRegistry()

    async def _call(inp, token, sandbox):
        return inp

    for n in ["zebra", "apple", "mango", "banana"]:
        reg.register(build_tool(
            name=n, input_schema={"type": "object"}, call=_call,
            is_read_only=lambda inp: True, required_mode=Mode.READ_ONLY,
        ))
    a = reg.exposed_tools(token=_tok(), mode=Mode.READ_ONLY)
    b = reg.exposed_tools(token=_tok(), mode=Mode.READ_ONLY)
    assert [t["name"] for t in a] == [t["name"] for t in b] == ["apple", "banana", "mango", "zebra"]


# ============ AC7:SKILL.md 渐进披露(frontmatter 进 schema,正文仅 call 时读)============
def test_ac7_skill_progressive_disclosure(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: my-skill\n"
        "description: A demo skill\n"
        "when_to_use: when user asks X\n"
        "scope: user\n"
        "arguments:\n"
        "  - name: target\n"
        "    type: string\n"
        "    required: true\n"
        "  - name: count\n"
        "    type: integer\n"
        "    required: false\n"
        "allowed_tools: [read_file]\n"
        "---\n"
        "This is the body. Should not be loaded until call() time.\n"
        "## Steps\n"
        "1. Read the file\n"
        "2. Process it\n",
        encoding="utf-8",
    )

    # 解析 frontmatter
    fm, body = parse_frontmatter(skill_md)
    assert fm.name == "my-skill"
    assert fm.description == "A demo skill"
    assert fm.allowed_tools == ["read_file"]
    assert "body" in body  # body 仍在

    # 加载为 Tool
    t = load_skill(skill_md)
    assert t.name == "my-skill"
    # schema: frontmatter 的 arguments 进了 input_schema
    assert "target" in t.input_schema["properties"]
    assert t.input_schema["properties"]["target"]["type"] == "string"
    assert "target" in t.input_schema.get("required", [])

    # 渐进披露:调 call() 时才读 body
    async def _run():
        result = await t.call({"target": "x"}, token=_tok(), sandbox=None)
        return result

    out = asyncio.run(_run())
    assert "This is the body" in out["body"]
    assert "Steps" in out["body"]
    assert out["input"] == {"target": "x"}


# ============ AC8:schema 体量 < 阈值不启用搜索(零开销)============
def test_ac8_search_threshold_not_triggered_for_small_set():
    reg = ToolRegistry()

    async def _call(inp, token, sandbox):
        return inp

    # 注册 5 个小工具(应不触发)
    for n in ["a", "b", "c", "d", "e"]:
        reg.register(build_tool(
            name=n, input_schema={"type": "object"}, call=_call,
            is_read_only=lambda inp: True, required_mode=Mode.READ_ONLY,
        ))
    exposed = reg.exposed_tools(token=_tok(), mode=Mode.READ_ONLY)
    # 正常返回(无 _tool_search 标记)
    assert all("_tool_search" not in t for t in exposed)
    assert len(exposed) == 5

    # 注册 100 个大工具(应触发)—— 但 v1 不实现 search,只标 _tool_search
    reg2 = ToolRegistry()
    big_schema = {
        "type": "object",
        "properties": {
            f"p{i}": {
                "type": "string",
                "description": "x" * 500,  # 触发体量
            }
            for i in range(100)
        },
    }
    for n in [f"big{i}" for i in range(100)]:
        reg2.register(build_tool(
            name=n, input_schema=big_schema, call=_call,
            is_read_only=lambda inp: True, required_mode=Mode.READ_ONLY,
        ))
    exposed2 = reg2.exposed_tools(token=_tok(), mode=Mode.READ_ONLY)
    # 超阈值 → 返回 1 个标记项
    assert len(exposed2) == 1
    assert exposed2[0].get("_tool_search") is True
    assert exposed2[0].get("total") == 100


# ============ 额外:is_enabled 过滤 ============
def test_extra_is_enabled_filter():
    reg = ToolRegistry()

    async def _call(inp, token, sandbox):
        return inp

    # 用一个可变计数器模拟 is_enabled
    counter = {"on": True}

    def _enabled():
        return counter["on"]

    reg.register(build_tool(
        name="a", input_schema={"type": "object"}, call=_call,
        is_read_only=lambda inp: True, is_enabled=_enabled,
        required_mode=Mode.READ_ONLY,
    ))
    reg.register(build_tool(
        name="b", input_schema={"type": "object"}, call=_call,
        is_read_only=lambda inp: True, required_mode=Mode.READ_ONLY,
    ))

    exposed = reg.exposed_tools(token=_tok(), mode=Mode.READ_ONLY)
    assert {t["name"] for t in exposed} == {"a", "b"}

    counter["on"] = False
    exposed2 = reg.exposed_tools(token=_tok(), mode=Mode.READ_ONLY)
    assert {t["name"] for t in exposed2} == {"b"}


# ============ 额外:capability 收窄(无 fs write grant 时 write 工具不暴露)============
def test_extra_capability_narrowing():
    reg = ToolRegistry()

    async def _call(inp, token, sandbox):
        return inp

    reg.register(build_tool(
        name="read_file", input_schema={"type": "object"}, call=_call,
        is_read_only=lambda inp: True, required_mode=Mode.READ_ONLY,
    ))
    reg.register(build_tool(
        name="write_file", input_schema={"type": "object"}, call=_call,
        required_mode=Mode.WORKSPACE_WRITE,
    ))

    # token 无 fs:write grant
    tok_ro = _tok(fs_write=False)
    exposed = reg.exposed_tools(token=tok_ro, mode=Mode.FULL)
    names = [t["name"] for t in exposed]
    assert "read_file" in names
    assert "write_file" not in names  # 无 write grant → 不暴露


# ============ AC9:agentskills.io `version` 字段(v1.5 新增)============
def test_ac9_skill_frontmatter_parses_agentskills_version(tmp_path: Path):
    """**M1.5** agentskills.io 标准 `version` 字段对齐测试。

    见 `CONTEXT/03-feature-knowledge-base.md` §五 agentskills.io vs 我们 frontmatter
    字段对照表。`version` 是 agentskills.io 可选字段,v1.5 我们解析并常驻。
    """
    skill_dir = tmp_path / "skills" / "versioned-skill"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: versioned-skill\n"
        "description: A skill with version per agentskills.io\n"
        "version: 1.2.3\n"
        "scope: user\n"
        "---\n"
        "Body content.\n",
        encoding="utf-8",
    )
    fm, _body = parse_frontmatter(skill_md)
    assert fm.name == "versioned-skill"
    assert fm.version == "1.2.3", (
        f"version 字段应从 frontmatter 解析,实际: {fm.version!r}"
    )


# ============ AC10:旧 SKILL.md(无 version)向后兼容 =============
def test_ac10_skill_frontmatter_version_default_empty_for_legacy(tmp_path: Path):
    """**M1.5** 向后兼容:旧 SKILL.md 没有 `version` 字段时,缺省空串(不抛)。

    防止 v1.5 升级把既有 skill 一刀切挂掉 —— M1.5 起的代码应该既能
    读新格式,也能读旧格式(load_skill 不要求 version 必填)。
    """
    skill_dir = tmp_path / "skills" / "legacy-skill"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: legacy-skill\n"
        "description: Pre-agentskills.io skill (no version field)\n"
        "scope: user\n"
        "---\n"
        "Legacy body.\n",
        encoding="utf-8",
    )
    fm, _body = parse_frontmatter(skill_md)
    assert fm.name == "legacy-skill"
    assert fm.version == "", (
        f"缺 version 字段时,应缺省空串(向后兼容);实际: {fm.version!r}"
    )
    # 加载为 Tool 也不该挂
    t = load_skill(skill_md)
    assert t.name == "legacy-skill"


# ============ AC11:load_skill 保留 version 字段(raw 透传,loadable)============
def test_ac11_load_skill_preserves_version_metadata(tmp_path: Path):
    """**M1.5** load_skill 路径完整:version 字段从 frontmatter 一路走到 raw dict,
    供 SkillIndex / registry 后续按 version 索引/排序/弃用检查用。

    现在的 Tool 对象没把 version 暴露给模型(只暴露 name/description/input_schema,
    是 progressive disclosure 的 part 1);但 raw 里必须有,这是 v1.5 给后续 v2 留的
    接口(v2 计划:Tool 暴露 version 字段供模型决定"是否升级到新版本"或
    SkillIndex 按 version 建索引)。
    """
    skill_dir = tmp_path / "skills" / "v-aware"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: v-aware\n"
        "description: Skill that participates in version-aware indexing\n"
        "version: 2.0.0\n"
        "scope: user\n"
        "---\n"
        "Body.\n",
        encoding="utf-8",
    )
    fm, _body = parse_frontmatter(skill_md)
    assert fm.version == "2.0.0"
    assert fm.raw is not None
    assert fm.raw.get("version") == "2.0.0", (
        f"raw dict 应透传 version 字段(给 SkillIndex 等后续用);"
        f"实际 raw: {fm.raw}"
    )
    # 加载为 Tool 也不该挂(确认 version 不破坏 build_tool)
    t = load_skill(skill_md)
    assert t.name == "v-aware"
