"""value.md → per-role 编译器接缝验收(9.5 loop-step1)。

锁:角色灵魂(7文件)+ 域 value.md/deontic 真被 paradigm_loader 编译进 system prompt;
缺角色目录则回退(None)。
"""
from __future__ import annotations

from karvyloop.coding.paradigm_prompt import build_role_paradigm_prompt
from karvyloop.coding.prompt import CodingPrompt
from karvyloop.roles.registry import RoleRegistry
from karvyloop.domain.registry import BusinessDomainRegistry
from karvyloop.domain.deontic import Deontic


def _domain_with_value(tmp_path):
    reg = BusinessDomainRegistry()
    return reg.create(
        name="装修域",
        created_by="user:ch",
        value_md_raw="# 价值观\n- 诚实第一\n- 用户利益至上",
        deontic=Deontic(forbid=("禁止删库",), oblige=("先读后写",)),
        member_query="user:ch AND agent:designer",
    )


def test_compiles_soul_and_value_into_prompt(tmp_path):
    roles = RoleRegistry(tmp_path / "roles")
    rv = roles.create("designer", identity="我是资深设计师", soul="克制、用户至上", atom_ids=[])
    domain = _domain_with_value(tmp_path)
    cp = build_role_paradigm_prompt(rv, domain, intent="帮我画个图", cwd="/home/ws")
    assert isinstance(cp, CodingPrompt)
    text = cp.to_text()
    # 灵魂被编译进来
    assert "资深设计师" in text          # IDENTITY
    assert "克制、用户至上" in text       # SOUL
    # 域的 value.md / deontic 被编译进来(per-role 治理)
    assert "诚实第一" in text or "用户利益至上" in text   # value.md
    assert "禁止删库" in text or "先读后写" in text        # deontic 护栏
    # 工作区块在
    assert "/home/ws" in text


def test_compiled_prompt_marks_domain_governance_covered(tmp_path):
    """P2-a 去重(对抗验收):编译成功的 per-role prompt 已含域治理(value.md+deontic)→
    带 covers_domain_governance 标记,直聊路径据此**不再**把 governance_text 域块重复注入。"""
    roles = RoleRegistry(tmp_path / "roles")
    rv = roles.create("designer", identity="我是资深设计师", soul="克制", atom_ids=[])
    domain = _domain_with_value(tmp_path)
    cp = build_role_paradigm_prompt(rv, domain, intent="x", cwd="/w")
    assert getattr(cp, "covers_domain_governance", False) is True
    # 直聊两条路径(ws + routes)都做了尾段剥除(接线在位;逻辑由本标记驱动)
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for rel in ("karvyloop/console/ws.py", "karvyloop/console/routes.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "covers_domain_governance" in src, f"{rel} 缺双注入去重接线"


def test_role_name_anchored_to_nickname_not_leaked_karvy(tmp_path):
    """bug 修:私聊角色自称成"小卡"。IDENTITY.md 文本(共创起草器 LLM 生成,system 通篇
    小卡)可能把角色名漏成"小卡";系统提示必须用**用户设的花名**钉权威身份、划清与小卡的界,
    不管 IDENTITY 文本写了啥。"""
    roles = RoleRegistry(tmp_path / "roles")
    # 模拟坏 IDENTITY:文本里烙了"小卡",但用户设的花名是"小美"
    rv = roles.create("pcb-eng", identity="我是小卡,KarvyLoop 里的 PCB 设计工程师",
                      soul="严谨", nickname="小美", title="PCB设计工程师")
    cp = build_role_paradigm_prompt(rv, None, intent="你是谁", cwd="/w")
    text = cp.to_text()
    assert "你叫「小美」" in text                      # 权威花名钉进提示
    assert "绝不要自称小卡" in text                    # 显式划清与全局助手的界
    # 花名在权威锚里,压在 IDENTITY 文本之前(先声明真名)
    assert text.index("你叫「小美」") < text.index("PCB 设计工程师")


def test_name_anchor_falls_back_to_id_when_no_nickname(tmp_path):
    """没花名(nickname 空)→ 用 role_id 当名字锚,不炸、不空锚。"""
    roles = RoleRegistry(tmp_path / "roles")
    rv = roles.create("analyst", identity="资深分析师", soul="求真")   # 无 nickname
    cp = build_role_paradigm_prompt(rv, None, intent="x", cwd="/w")
    text = cp.to_text()
    assert "你叫「analyst」" in text


def test_no_role_dir_returns_none(tmp_path):
    """不是 materialized 角色目录 → 返 None(调用方回退 persona)。"""
    class _Fake:
        id = "ghost"
        path = tmp_path / "nope"
    assert build_role_paradigm_prompt(_Fake(), None) is None


def test_works_without_domain(tmp_path):
    """无域(个人场)也能编译角色灵魂,不炸。"""
    roles = RoleRegistry(tmp_path / "roles")
    rv = roles.create("pm", identity="产品经理", soul="求真")
    cp = build_role_paradigm_prompt(rv, None, intent="x", cwd="/w")
    assert isinstance(cp, CodingPrompt)
    assert "产品经理" in cp.to_text()
