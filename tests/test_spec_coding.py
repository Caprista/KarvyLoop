"""Spec-coding 5 段流水线验收测试（tests/test_spec_coding.py）。

**M2.0 拍 2**。8 AC + 1 端到端。设计:`docs/12-spec-driven-coding.md`。
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from karvyloop.spec_coding import (
    PipelineContext,
    SpecCodingConfig,
    SpecCodingError,
    spec_coding_pipeline,
)
from karvyloop.spec_coding.crystallize import (
    SKILL_FRONTMATTER_FIELDS,
    SkillCandidate,
    crystallize,
)
from karvyloop.spec_coding.implement import implement
from karvyloop.spec_coding.intent import (
    INTENT_TRIGGERS,
    NEGATION_MARKERS,
    Intent,
    extract_intent,
    has_intent_marker,
)
from karvyloop.spec_coding.pipeline import PipelineResult
from karvyloop.spec_coding.spec import (
    SPEC_REQUIRED_SECTIONS,
    Spec,
    compose_spec,
)
from karvyloop.spec_coding.tech_select import TechStack, select_tech


# ---- helpers ----------

REGISTERED_ATOMS = ("write_ppt", "read_doc", "prd_review", "data_analysis")


def _make_ctx(messages, atoms=REGISTERED_ATOMS, base_dir=None) -> PipelineContext:
    return PipelineContext(
        messages=messages,
        registered_atoms=atoms,
        base_dir=base_dir or tempfile.mkdtemp(prefix="karvyloop_test_"),
    )


# ============ AC1:Stage 1 提取"用户想做 X" intent ============
def test_ac1_extract_intent_from_messages():
    """AC1: 给定对话 context → extract_intent 提取出"用户想做 X" goal。"""
    # 触发
    i1 = extract_intent(["今天天气不错", "我想做一个生成 PPT 的 skill"])
    assert i1 is not None
    assert "PPT" in i1.goal or "ppt" in i1.goal.lower()
    assert i1.confidence > 0
    # 否定 → None
    i2 = extract_intent(["我不想做"])
    assert i2 is None
    # 无触发词 → None
    i3 = extract_intent(["hello world"])
    assert i3 is None


# ============ AC2:spec.md 含至少 4 段 ============
def test_ac2_spec_has_required_sections():
    """AC2: intent → spec.md 含目标/输入/输出/verify 4 段。"""
    intent = Intent(raw="我想做 X", goal="做一个测试 skill", confidence=0.8)
    spec = compose_spec(intent)
    assert spec.has_required_sections()
    for required in SPEC_REQUIRED_SECTIONS:
        assert required in spec.md_text


# ============ AC3:tech 选型不选不存在的 atom ============
def test_ac3_tech_selection_validates_against_registry():
    """AC3: select_tech 返回的 TechStack.is_valid(registered) 必须 True。"""
    # 含 PPT 关键词 → 推断用 write_ppt
    tech = select_tech(
        "我想做生成 PPT 大纲的 skill",
        registered_atoms=REGISTERED_ATOMS,
    )
    assert tech.is_valid(REGISTERED_ATOMS)
    assert "write_ppt" in tech.atoms

    # 显式引用 + 关键词推断
    tech2 = select_tech(
        "atom: prd_review + 关键词 doc",
        registered_atoms=REGISTERED_ATOMS,
    )
    assert tech2.is_valid(REGISTERED_ATOMS)
    assert "prd_review" in tech2.atoms
    assert "read_doc" in tech2.atoms

    # 反例:空 registered
    tech3 = select_tech("atom: write_ppt", registered_atoms=())
    assert not tech3.is_valid(())
    assert tech3.atoms == ()


# ============ AC4:实现不写到主目录 ============
def test_ac4_implement_writes_to_sandbox_only(tmp_path: pathlib.Path):
    """AC4: implement() 写到 base_dir(注入测试 tmp),**不**写到 cwd/。"""
    spec = Spec(
        md_text="## 目标\n做测试",
        goal="做测试",
        sections=SPEC_REQUIRED_SECTIONS,
    )
    tech = TechStack(atoms=("write_ppt",))
    impl = implement(spec, tech, base_dir=tmp_path)
    # 写到 tmp_path(不是 cwd)
    assert impl.artifact_path.exists()
    assert str(impl.artifact_path).startswith(str(tmp_path))
    # 文件**含**技术选型引用
    code = impl.artifact_path.read_text(encoding="utf-8")
    assert "write_ppt" in code


# ============ AC5:5 段全程不修改用户 .md ============
def test_ac5_pipeline_does_not_modify_user_md(tmp_path: pathlib.Path):
    """AC5: 跑完整 pipeline 不会改 Wizard 拍 1 写的 .md。"""
    # 预先放一个用户 .md(模拟 Wizard 拍 1 产物)
    user_md = tmp_path / "IDENTITY.md"
    original_content = "我是 pm,不会改"
    user_md.write_text(original_content, encoding="utf-8")

    ctx = _make_ctx(["我想做一个生成 PPT 的 skill"], base_dir=str(tmp_path))
    result = spec_coding_pipeline(ctx)
    assert result is not None
    # 用户 .md 仍未动
    assert user_md.read_text(encoding="utf-8") == original_content


# ============ AC6:enabled=False → 返 None ============
def test_ac6_disabled_returns_none():
    """AC6: SpecCodingConfig.enabled=False → pipeline 返 None(完全**不**触发)。"""
    ctx = _make_ctx(["我想做一个生成 PPT 的 skill"])
    cfg = SpecCodingConfig(enabled=False)
    result = spec_coding_pipeline(ctx, config=cfg)
    assert result is None


# ============ AC7:任一段失败 → 已生成文件不回滚(本拍不实际生成)============
def test_ac7_failure_raises_spec_coding_error():
    """AC7: 任一段失败 → 抛 SpecCodingError(已生成文件**不**回滚 = 本拍不实际生成)。

    测试场景:registered_atoms 空 → Stage 3 is_valid 失败。
    """
    ctx = _make_ctx(["我想做 X"], atoms=())  # 空 registered
    with pytest.raises(SpecCodingError) as exc_info:
        spec_coding_pipeline(ctx)
    msg = str(exc_info.value)
    # 错误信息含 Stage 3
    assert "Stage 3" in msg


# ============ AC8:完整跑通 → 产出可被 Paradigm Loader 加载的 SKILL.md ============
def test_ac8_end_to_end_produces_agentskills_io_compatible_skill(tmp_path: pathlib.Path):
    """AC8: 完整 5 段跑通 → SKILL.md 含 agentskills.io 必填 frontmatter。"""
    ctx = _make_ctx(
        ["我想做一个生成 PPT 大纲的 skill"],
        base_dir=str(tmp_path),
    )
    result = spec_coding_pipeline(ctx)
    assert result is not None
    assert isinstance(result, PipelineResult)

    # skill 产物
    skill = result.skill
    assert isinstance(skill, SkillCandidate)
    # AC8: agentskills.io frontmatter 必填
    assert skill.has_agentskills_io_frontmatter()
    for field in SKILL_FRONTMATTER_FIELDS:
        assert f"{field}:" in skill.skill_md_text

    # 实际写到 tmp
    assert skill.skill_path.exists()
    assert str(skill.skill_path).startswith(str(tmp_path))


# ============ 协议不变量:5 段顺序 ============
def test_protocol_five_stages_in_order():
    """锁住 5 段顺序 + 各自的不可变量。"""
    # Intent: 触发词
    assert len(INTENT_TRIGGERS) > 0
    assert len(NEGATION_MARKERS) > 0
    # Spec: 4 段
    assert len(SPEC_REQUIRED_SECTIONS) == 4
    assert "## 目标" in SPEC_REQUIRED_SECTIONS
    # Crystallize: agentskills.io 2 字段
    assert "name" in SKILL_FRONTMATTER_FIELDS
    assert "description" in SKILL_FRONTMATTER_FIELDS


# ============ 边界:否定词 + 模糊意图 ============
def test_negation_and_no_intent_skip_pipeline(tmp_path: pathlib.Path):
    """边界:用户**不**想做 或 无 intent → pipeline 静默返 None,**不**抛异常。"""
    # 否定
    ctx1 = _make_ctx(["我不想做"], base_dir=str(tmp_path))
    assert spec_coding_pipeline(ctx1) is None
    # 无 intent
    ctx2 = _make_ctx(["hello world"], base_dir=str(tmp_path))
    assert spec_coding_pipeline(ctx2) is None
    # 空 messages
    ctx3 = _make_ctx([], base_dir=str(tmp_path))
    assert spec_coding_pipeline(ctx3) is None
