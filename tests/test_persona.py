"""persona 层验收(方案 A,9.4e)—— 对话默认好好说话、要动手才用工具。

病根:console 每句话都套 coding 提示吐 CodingResult。方案 A:小卡/业务角色是有人格、
带工具的对话体。这里锁人格 prompt 的关键不变量 + generate_and_run 的 system_prompt 覆盖。
"""
from __future__ import annotations

import inspect

from karvyloop.coding.persona import (
    KARVY_PERSONA,
    build_karvy_persona_prompt,
    build_role_persona_prompt,
)
from karvyloop.coding.prompt import CodingPrompt


def test_karvy_persona_is_conversational_not_coding():
    p = build_karvy_persona_prompt(cwd="/tmp/ws")
    assert isinstance(p, CodingPrompt)
    text = p.to_text()
    # 身份:小卡 / 卡皮巴拉
    assert "小卡" in text and "卡皮巴拉" in text
    # 明令禁止把每句当编码任务 / 吐 CodingResult
    assert "CodingResult" in text  # 出现在"绝不输出 CodingResult"的禁令里
    assert "绝不" in text
    # 不是 coding 原子提示(没有"你是 KarvyLoop 的 coding 原子"那句)
    assert "coding 原子" not in text
    # cwd 透传(动手时工具要用)
    assert "/tmp/ws" in text


def test_role_persona_has_role_and_domain():
    p = build_role_persona_prompt("设计师", domain_name="装修域", cwd="/w")
    text = p.to_text()
    assert "设计师" in text and "装修域" in text
    assert "CodingResult" in text  # 同样禁令
    assert "coding 原子" not in text


def test_role_persona_without_domain_name():
    p = build_role_persona_prompt("工程师")
    text = p.to_text()
    assert "工程师" in text  # 没域名也不炸


def test_karvy_persona_static_nonempty():
    assert len(KARVY_PERSONA) >= 5


def test_coding_self_verify_discipline_in_personas():
    """P3 M1:写代码自检纪律 —— 两种人格都得有「跑一遍验证 + 别假装上线」。"""
    for text in (build_karvy_persona_prompt().to_text(),
                 build_role_persona_prompt("设计师").to_text()):
        assert "验收" in text
        assert "跑一遍验证" in text
        assert "别让用户当测试工程师" in text
        assert "上线" in text  # "绝不假装上线"


def test_destructive_op_confirm_rule_in_personas():
    """破坏性/不可逆操作动手前先确认 —— 小卡人格也要有(shakeout 抓到:小卡直接删了文件没确认)。"""
    for text in (build_karvy_persona_prompt().to_text(),
                 build_role_persona_prompt("设计师").to_text()):
        assert "破坏性" in text and "不可逆" in text
        assert "先" in text and "确认" in text          # 动手前先确认
        assert "rm -rf" in text or "清空" in text


def test_generate_and_run_accepts_system_prompt_override():
    """forge.generate_and_run 必须暴露 system_prompt 覆盖参数(方案 A 注入点)。"""
    from karvyloop.coding.forge import generate_and_run
    sig = inspect.signature(generate_and_run)
    assert "system_prompt" in sig.parameters
    assert sig.parameters["system_prompt"].default is None  # 缺省=旧 coding 行为


def test_slow_brain_factory_accepts_persona():
    """forge_slow_brain_factory 必须接 persona 并透传(链路不断)。"""
    from karvyloop.cli.main_loop import forge_slow_brain_factory
    sig = inspect.signature(forge_slow_brain_factory)
    assert "persona" in sig.parameters


def test_drive_in_tui_accepts_persona():
    from karvyloop.workbench.main_loop_bridge import drive_in_tui
    sig = inspect.signature(drive_in_tui)
    assert "persona" in sig.parameters
