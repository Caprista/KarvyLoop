"""Adapter 外部 agent 校验器验收测试(tests/test_adapter.py)。

**M2.0 拍 4**。7 AC + 1 协议 = 8 测试。设计:docs/14-external-agent-adapter.md。
"""
from __future__ import annotations

import pathlib

import pytest

from karvyloop.adapter import (
    EXTERNAL_SOURCES,
    AdapterRegistry,
    AdapterPlan,
    ApplyResult,
    ExternalManifest,
    ManifestError,
    PlanError,
    SLOT_NAMES,
    SlotAction,
    SlotPlan,
    adapter_registry,
    apply_plan,
    build_plan,
    discover_manifest,
    parse_claude_manifest,
    parse_codex_manifest,
    parse_generic_manifest,
    parse_openclaw_hermes_manifest,
    validate_with_loader,
)


# ============ helpers ============

def _claude_payload(sp: str = "I am Claude. I am helpful.") -> dict:
    return {
        "system_prompt": sp,
        "tools": [{"name": "Read", "schema": {}}, {"name": "Write", "schema": {}}],
        "skills": [{"name": "doc-review"}],
        "agent_name": "test-claude",
    }


def _codex_payload(sp: str = "I am Codex. I code.") -> dict:
    return {
        "system_prompt": sp,
        "tools": [{"name": "shell"}],
        "agent_name": "test-codex",
    }


def _openclaw_payload(sp: str = "Hermes here.") -> dict:
    return {
        "system_prompt": sp,
        "tools": [{"name": "memory_search"}],
        "soul_files": ["/tmp/should_not_exist/SOUL.md"],   # 故意不存在 → WARN
    }


# ============ AC1:Manifest is_minimal 校验 ============
def test_ac1_manifest_is_minimal_rejects_missing_required():
    """AC1: 缺 system_prompt / tools → 拒收(M1+discover_manifest 抛 ManifestError)。"""
    # 缺 system_prompt
    bad1 = {"tools": [{"name": "x"}]}
    with pytest.raises(ManifestError):
        discover_manifest("claude", bad1)
    # 缺 tools
    bad2 = {"system_prompt": "hi"}
    with pytest.raises(ManifestError):
        discover_manifest("claude", bad2)
    # 未知 source_id
    with pytest.raises(ManifestError):
        discover_manifest("unknown-source", _claude_payload())

    # 完整 → 返 ExternalManifest
    m = discover_manifest("claude", _claude_payload())
    assert m.is_minimal()
    assert m.source_id == "claude"
    assert len(m.tools) == 2


# ============ AC2:Planner 7 槽位齐全 ============
def test_ac2_planner_produces_seven_slots(tmp_path: pathlib.Path):
    """AC2: 给合法 manifest → plan.slots 必**须** = 7。"""
    m = discover_manifest("claude", _claude_payload())
    plan = build_plan(m, target_agent_dir=str(tmp_path / "agent_a"))
    assert len(plan.slots) == 7
    assert len(SLOT_NAMES) == 7
    for slot_name in SLOT_NAMES:
        sp = plan.slot_for(slot_name)
        assert sp.slot == slot_name
        assert sp.target  # 非空


# ============ AC3:openclaw-hermes 有 SOUL.md → COPY;无 USER.md → SYNTHESIZE ============
def test_ac3_plan_skip_and_synthesize_strategy(tmp_path: pathlib.Path):
    """AC3: 异构同源表策略 — openclaw-hermes 有 soul_files → SOUL=COPY;无 user_files → USER=SYNTHESIZE。"""
    # 准备真 SOUL.md 文件
    soul_file = tmp_path / "SOUL.md"
    soul_file.write_text("I am soul. Honest and direct.", encoding="utf-8")
    payload = {
        "system_prompt": "I am Hermes from openclaw.",
        "tools": [{"name": "memory"}],
        "soul_files": [str(soul_file)],
        # 无 user_files
    }
    m = discover_manifest("openclaw-hermes", payload)
    plan = build_plan(m, target_agent_dir=str(tmp_path / "agent_b"))

    soul_slot = plan.slot_for("SOUL")
    user_slot = plan.slot_for("USER")
    memory_slot = plan.slot_for("MEMORY")
    composition_slot = plan.slot_for("COMPOSITION")
    identity_slot = plan.slot_for("IDENTITY")
    commitment_slot = plan.slot_for("COMMITMENT")
    verify_slot = plan.slot_for("VERIFY")

    # SOUL 有源 → COPY
    assert soul_slot.action == SlotAction.COPY
    assert soul_slot.source == str(soul_file)
    assert "soul" in soul_slot.content_preview.lower()

    # USER 无源 → SYNTHESIZE
    assert user_slot.action == SlotAction.SYNTHESIZE

    # MEMORY 无源 → SYNTHESIZE
    assert memory_slot.action == SlotAction.SYNTHESIZE

    # IDENTITY / COMMITMENT / VERIFY / COMPOSITION 全部 SYNTHESIZE(拍 4 v0)
    assert identity_slot.action == SlotAction.SYNTHESIZE
    assert commitment_slot.action == SlotAction.SYNTHESIZE
    assert verify_slot.action == SlotAction.SYNTHESIZE
    assert composition_slot.action == SlotAction.SYNTHESIZE


# ============ AC4:can_apply 默认 False(WARN slot 触发)============
def test_ac4_warn_slot_blocks_can_apply(tmp_path: pathlib.Path):
    """AC4: 有 WARN slot(本源文件读不到)→ has_warnings=True + can_apply=False。"""
    payload = {
        "system_prompt": "Hermes",
        "tools": [{"name": "x"}],
        "soul_files": ["/this/path/does/not/exist/SOUL.md"],   # 故意不存在
    }
    m = discover_manifest("openclaw-hermes", payload)
    plan = build_plan(m, target_agent_dir=str(tmp_path / "agent_c"))
    assert plan.has_warnings is True
    assert plan.can_apply is False
    soul_slot = plan.slot_for("SOUL")
    assert soul_slot.action == SlotAction.WARN
    assert any("SOUL.md" in w for w in soul_slot.warnings)

    # 干净 claude manifest → can_apply=True
    m2 = discover_manifest("claude", _claude_payload())
    plan2 = build_plan(m2, target_agent_dir=str(tmp_path / "agent_d"))
    assert plan2.can_apply is True
    assert plan2.has_warnings is False


# ============ AC5:Apply 强制不覆盖 ============
def test_ac5_apply_skips_existing_target(tmp_path: pathlib.Path):
    """AC5: 目标文件**已**存在 → 写**不**了,归 skipped_exists,written 不会含它。"""
    m = discover_manifest("claude", _claude_payload())
    target_dir = tmp_path / "agent_e"
    plan = build_plan(m, target_agent_dir=str(target_dir))

    # 预先创建**一**个 target 文件
    pre = target_dir / "IDENTITY.md"
    pre.parent.mkdir(parents=True, exist_ok=True)
    original = "DO NOT OVERWRITE"
    pre.write_text(original, encoding="utf-8")

    result = apply_plan(plan, m, target_agent_dir=str(target_dir))
    assert isinstance(result, ApplyResult)
    # IDENTITY.md 在 skipped
    assert any("IDENTITY.md" in p for p in result.skipped_exists)
    assert not any("IDENTITY.md" in p for p in result.written)
    # 内容**未**改
    assert pre.read_text(encoding="utf-8") == original
    # 其他 6 个文件**已**写
    assert len(result.written) == 6


# ============ AC6:Apply 写到目标 dir (J4)============
def test_ac6_apply_writes_to_target_dir_only(tmp_path: pathlib.Path):
    """AC6: apply_plan 写**到** tmp_path 注**入**的 target,**不**写到 cwd。"""
    import os
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))   # cwd = tmp,但**不**是 target
        m = discover_manifest("codex", _codex_payload())
        target = tmp_path / "real_target"
        plan = build_plan(m, target_agent_dir=str(target))
        result = apply_plan(plan, m, target_agent_dir=str(target))
        # 写到 target
        assert (target / "IDENTITY.md").exists()
        assert (target / "SOUL.md").exists()
        assert (target / "COMPOSITION.yaml").exists()
        # cwd **没**有 IDENTITY.md
        assert not (tmp_path / "IDENTITY.md").exists()
        # 7 文件全写
        assert len(result.written) == 7
        assert len(result.failed) == 0
    finally:
        os.chdir(old_cwd)


# ============ AC7:Validator 烟测 ============
def test_ac7_validator_smoke(tmp_path: pathlib.Path):
    """AC7: 7 文件齐 + COMPOSITION 有 step_id → 0 错;缺 SOUL.md → validation_errors 含 'SOUL'。"""
    m = discover_manifest("claude", _claude_payload())
    target = tmp_path / "agent_v"
    plan = build_plan(m, target_agent_dir=str(target))
    result = apply_plan(plan, m, target_agent_dir=str(target))
    assert len(result.written) == 7

    # 完整 → valid
    v1 = validate_with_loader(plan, agent_dir=str(target))
    assert v1.is_valid
    assert v1.errors == ()

    # 删 SOUL.md → invalid + 错含 SOUL
    (target / "SOUL.md").unlink()
    v2 = validate_with_loader(plan, agent_dir=str(target))
    assert not v2.is_valid
    assert any("SOUL" in e for e in v2.errors)

    # 不存在 dir → invalid
    v3 = validate_with_loader(plan, agent_dir=str(tmp_path / "no_such_dir"))
    assert not v3.is_valid


# ============ 协议不变量:7 槽位 + 4 source adapter + dataclass 字段锁住 ============
def test_protocol_invariants_and_parsers():
    """协议不变量:7 槽位 + 4 内置 source + 字段锁住。"""
    # 7 槽位
    assert len(SLOT_NAMES) == 7
    assert "COMPOSITION" in SLOT_NAMES
    # 4 source adapter
    assert len(EXTERNAL_SOURCES) >= 4
    for sid in ("claude", "codex", "openclaw-hermes", "generic-json"):
        assert sid in EXTERNAL_SOURCES
    # registry 注**册**
    assert adapter_registry.is_registered("claude")
    assert adapter_registry.is_registered("codex")
    assert adapter_registry.is_registered("openclaw-hermes")
    assert adapter_registry.is_registered("generic-json")
    assert len(adapter_registry.all_entries()) >= 4
    # 4 parser 都能产**合**法 manifest
    for parser, payload in [
        (parse_claude_manifest, _claude_payload()),
        (parse_codex_manifest, _codex_payload()),
        (parse_openclaw_hermes_manifest, _openclaw_payload()),
        (parse_generic_manifest, _claude_payload()),
    ]:
        m = parser(payload, "<test>")
        assert isinstance(m, ExternalManifest)
        assert m.is_minimal()
    # SlotAction 5 个**字**段锁**住**
    assert SlotAction.COPY == "copy"
    assert SlotAction.SYNTHESIZE == "synthesize"
    assert SlotAction.SKIP == "skip"
    assert SlotAction.SKIP_EXISTS == "skip_exists"
    assert SlotAction.WARN == "warn"
