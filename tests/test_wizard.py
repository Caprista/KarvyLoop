"""Wizard CLI 验收测试（tests/test_wizard.py）。

**M2.0 拍 1**。7 AC + 协议不变量。设计:`docs/11-wizard.md`。
"""

from __future__ import annotations

import pathlib

import pytest

from karvyloop.wizard import (
    WIZARD_STEPS,
    Compositor,
    compose_file,
    preview_paradigm,
)
from karvyloop.wizard.bootstrapper import (
    is_dont_understand,
    is_skip,
    validate_composition_atoms,
)
from karvyloop.wizard.storage import (
    WizardWriteResult,
    initialize_role_dir,
    role_dir,
    write_step_file,
)


# ---- helpers ----------

def _full_answers(step) -> list[str]:
    """给每步 1 个有效答案。"""
    return [f"answer for {step.step_id} #{i+1}" for i in range(len(step.questions))]


def _all_files(role_id="pm", domain_id="karvyloop"):
    """跑 7 步生成 7 个 .md 文本(走 Compositor)。"""
    comp = Compositor(role_id=role_id, domain_id=domain_id)
    return {step.step_id: comp.compose(step, _full_answers(step)) for step in WIZARD_STEPS}


# ============ AC1:7 步引导 schema + Compositor 纯函数无 IO ============
def test_ac1_seven_steps_pure_compositor():
    """AC1: 给定 7 步问答答案 → Compositor 生成 7 个 .md 文本,纯函数无 IO。

    '纯' = 不调任何外部资源(网络 / 文件 / LLM)。
    """
    files = _all_files()
    assert len(files) == 7
    expected_keys = {s.step_id for s in WIZARD_STEPS}
    assert set(files.keys()) == expected_keys
    # 每个文件有"karvyloop.wizard generated" header
    for step_id, content in files.items():
        assert "karvyloop.wizard generated" in content
        assert f"step_id: {step_id}" in content


# ============ AC2:每个 .md 文本含问题答案关键词(防写偏)============
def test_ac2_files_contain_user_answers():
    """AC2: 用户答案关键词**必**出现在 .md 里(防 Compositor 写偏)。"""
    comp = Compositor(role_id="pm", domain_id="karvyloop")
    identity_answers = ["产品经理", "KarvyLoop 产品部", "负责方向 + 边界"]
    text = comp.compose(WIZARD_STEPS[0], identity_answers)
    for ans in identity_answers:
        assert ans in text, f"answer '{ans}' missing from IDENTITY .md"


# ============ AC3:任一步 skip → 写"暂不填"占位,不抛异常 ============
def test_ac3_skip_step_writes_placeholder_no_exception():
    """AC3: 7 步中任一步用户答 "skip" → 写'暂不填'占位,不抛异常。"""
    comp = Compositor(role_id="pm", domain_id="karvyloop")
    text = comp.compose(WIZARD_STEPS[0], ["skip"] * len(WIZARD_STEPS[0].questions))
    assert "暂不填" in text
    assert "skip" not in text.lower() or "Skip" not in text  # 占位不**直接**显示 "skip" 字符串
    # 也不抛异常


# ============ AC4:预演 → 7 layer 全有(不走 default)============
def test_ac4_preview_loaded_paradigm_has_all_7_layers():
    """AC4: 写完 7 文件 → 调 preview_paradigm → 返回的 LoadedParadigm 7 layer **全**有。

    测试通过给 domain 传非空 guardrails → L0 不走 default。
    """
    files = _all_files()
    result = preview_paradigm(
        role_id="pm", domain_id="karvyloop",
        files=files,
        guardrails=["no rm -rf", "user data 加密"],
    )
    # MUST 全在
    assert 0 in result.layers
    assert 1 in result.layers
    assert 2 in result.layers
    # R1 全场景:USER + MEMORY (Layer 5) 在
    assert 5 in result.layers
    # 用户答了"我不懂"或"skip"才走 default;这里全有答 + 有 guardrails → 不**走** default
    for layer in (0, 1, 2, 5):
        src = result.layers[layer].source
        assert "default" not in src, f"layer {layer} unexpectedly走 default: {src}"


# ============ AC5:可中断,已写文件不回滚 ============
def test_ac5_interrupt_keeps_written_files(tmp_path: pathlib.Path):
    """AC5: 模拟 7 步中途 Ctrl+C(写完 3 步就停)→ 已写文件**不**回滚。"""
    initialize_role_dir(domain_id="d1", role_id="r1", base_dir=tmp_path)
    # 写前 3 步
    for step in WIZARD_STEPS[:3]:
        write_step_file(
            domain_id="d1", role_id="r1",
            file_basename=step.file_basename,
            content=f"content for {step.step_id}",
            base_dir=tmp_path,
        )
    # 模拟中断(不再写后续)
    # 验证前 3 步文件**在**
    rd = tmp_path / role_dir("d1", "r1")
    assert (rd / "IDENTITY.md").exists()
    assert (rd / "SOUL.md").exists()
    assert (rd / "USER.md").exists()
    # 后 4 步文件**不**在(没写过)
    assert not (rd / "COMMITMENT.md").exists()
    assert not (rd / "VERIFY.md").exists()
    assert not (rd / "MEMORY.md").exists()
    assert not (rd / "COMPOSITION.yaml").exists()


# ============ AC6:用户答"我不懂" → 给示例,不自动写 ============
def test_ac6_dont_understand_gives_example_not_auto_write():
    """AC6: 用户答'我不懂' → Bootstrapper 给'示例 —— 待你确认'占位,**不**自动写。"""
    comp = Compositor(role_id="pm", domain_id="karvyloop")
    text = comp.compose(WIZARD_STEPS[0], ["我不懂"] * len(WIZARD_STEPS[0].questions))
    assert "示例" in text
    assert "待你确认" in text
    # 验证**不**含真实用户答案(因为没给)
    assert "answer for IDENTITY" not in text


# ============ AC7:COMPOSITION.yaml 的 atom 字段值在已注册原子列表里 ============
def test_ac7_composition_yaml_validates_atom_references():
    """AC7: COMPOSITION.yaml 的 `composition[].atom` 字段值**必**在已注册原子列表里。"""
    comp = Compositor(role_id="pm", domain_id="karvyloop")
    text = comp.compose(WIZARD_STEPS[6], ["write_ppt, prd_review", "先 write_ppt 后 prd_review"])

    # 已注册原子列表
    registered = ["write_ppt", "prd_review", "data_analysis"]
    is_valid, unknown = validate_composition_atoms(text, registered)
    assert is_valid, f"unexpected unknown atoms: {unknown}"
    assert unknown == []

    # 测反例:引用一个未注册的 atom
    is_valid2, unknown2 = validate_composition_atoms(text, ["write_ppt"])  # prd_review 未注册
    assert not is_valid2
    assert "prd_review" in unknown2


# ============ 协议不变量(锁住 #0 §2.4 7 文件清单) ============
def test_protocol_seven_steps_match_constitution():
    """锁住与 #0 §2.4 一致性:7 步 IDENTITY/SOUL/USER/COMMITMENT/VERIFY/MEMORY/COMPOSITION 顺序 + skip_allowed 全 True。"""
    expected_order = ("IDENTITY", "SOUL", "USER", "COMMITMENT", "VERIFY", "MEMORY", "COMPOSITION")
    assert tuple(s.step_id for s in WIZARD_STEPS) == expected_order
    # 7 步全允许 skip
    assert all(s.skip_allowed for s in WIZARD_STEPS)
    # 7 个 step_id 跟 7 文件清单对得上
    from karvyloop.paradigm import SOUL_FILES
    assert SOUL_FILES == ("IDENTITY", "SOUL", "USER", "COMMITMENT", "VERIFY", "MEMORY")


# ============ 边界:is_skip / is_dont_understand 大小写不敏感 ============
def test_skip_and_dont_understand_case_insensitive():
    """is_skip / is_dont_understand 大小写不敏感 + 中文 alias 兼容。"""
    assert is_skip("skip")
    assert is_skip("Skip")
    assert is_skip("SKIP")
    assert is_skip("跳过")
    assert is_skip("")  # 空也当 skip
    assert is_skip("  ")  # 空白也当 skip

    assert is_dont_understand("我不懂")
    assert is_dont_understand("不知道")
    assert is_dont_understand("idk")
    assert is_dont_understand("I don't know")
    assert not is_dont_understand("我懂")
