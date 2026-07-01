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
