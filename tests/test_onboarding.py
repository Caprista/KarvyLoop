"""Onboarding 常驻引导验收测试(tests/test_onboarding.py)。

**M2.0 拍 3**。7 AC + 1 协议 = 8 测试。设计:docs/13-resident-onboarding.md。
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from karvyloop.onboarding import (
    HINTS,
    OnboardingPolicy,
    PolicyDecision,
    classify_intent,
    doc_rag_search,
    endpoint_registry,
    observe_message,
)
from karvyloop.onboarding.hints import (
    ALL_FLAGS,
    FIRST_ATOM_COMPOSE,
    FIRST_LONG_TOOL,
    FIRST_PURSUIT,
    FIRST_SKILL_USE,
    NO_ROLE_YET,
)
from karvyloop.onboarding.registry import EndpointEntry


# ============ AC1:EndpointRegistry 任意 endpoint 抽象 ============
def test_ac1_endpoint_registry_register_and_create():
    """AC1: 注册 3 个 endpoint + create(cli) 返非 None + create(unknown) 返 None。"""
    # cli / im / silent 已在 import 时自动注册
    assert endpoint_registry.is_registered("cli")
    assert endpoint_registry.is_registered("im")
    assert endpoint_registry.is_registered("silent")
    cli = endpoint_registry.create("cli")
    assert cli is not None
    assert isinstance(cli, EndpointEntry)
    assert cli.name == "cli"
    # unknown
    assert endpoint_registry.create("telegram") is None
    assert endpoint_registry.create("") is None
    # 全部 entries 至少 3 个
    assert len(endpoint_registry.all_entries()) >= 3


# ============ AC2:should_show + I1 seen 持久化 ============
def test_ac2_should_show_first_true_second_false(tmp_path: pathlib.Path):
    """AC2: 第一次 should_show(flag) = True,标记 seen 后第二次 = False。"""
    seen_path = str(tmp_path / "seen.yaml")
    p = OnboardingPolicy(seen_path=seen_path)
    flag = NO_ROLE_YET
    # 第一次
    assert p.should_show(flag) is True
    p.record_response(flag, "shown")
    # 第二次
    assert p.should_show(flag) is False
    # seen.yaml 已写
    assert pathlib.Path(seen_path).exists()
    # 持久化跨实例:新 policy 读同一文件
    p2 = OnboardingPolicy(seen_path=seen_path)
    assert p2.should_show(flag) is False


# ============ AC3:5 类 hint 都存在 ============
def test_ac3_five_hint_categories_present():
    """AC3: HINTS 字典含 5 个 flag,ALL_FLAGS 锁住协议。"""
    assert len(ALL_FLAGS) == 5
    for flag in ALL_FLAGS:
        assert flag in HINTS
        assert len(HINTS[flag]) > 0
    # 5 个具体 flag
    assert NO_ROLE_YET in HINTS
    assert FIRST_SKILL_USE in HINTS
    assert FIRST_PURSUIT in HINTS
    assert FIRST_ATOM_COMPOSE in HINTS
    assert FIRST_LONG_TOOL in HINTS


# ============ AC4:异步投递异常不外传 ============
def test_ac4_delivery_exception_swallowed(tmp_path: pathlib.Path):
    """AC4: delivery_fn 抛异常 → show() 返 show=False 不抛,reason 含 'delivery failed'。"""
    # 注入一个"必崩"的 endpoint
    def boom(_ep: str, _text: str) -> None:
        raise RuntimeError("intentional")

    bad = EndpointEntry(
        name="boom",
        label="Boom",
        delivery_fn=boom,
        is_available_fn=lambda: True,
    )
    endpoint_registry.register(bad)

    p = OnboardingPolicy(seen_path=str(tmp_path / "seen.yaml"))
    decision = p.show(NO_ROLE_YET, endpoint_name="boom")
    # 不抛 + show=False + reason 标记
    assert isinstance(decision, PolicyDecision)
    assert decision.show is False
    assert "delivery failed" in decision.reason


# ============ AC5:endpoint 不可用 → 静默跳过 ============
def test_ac5_endpoint_offline_silently_skipped(tmp_path: pathlib.Path):
    """AC5: is_available_fn() = False → show() 返 show=False,reason='endpoint offline'。"""
    p = OnboardingPolicy(seen_path=str(tmp_path / "seen.yaml"))
    # im endpoint 已注册,is_available 永远 False(stub)
    decision = p.show(FIRST_SKILL_USE, endpoint_name="im")
    assert decision.show is False
    assert decision.reason == "endpoint offline"


# ============ AC6:guardrails 沿用 ============
def test_ac6_guardrails_legacy_from_paradigm_loader():
    """AC6: 默认 guardrails 5 条齐全(对照 Paradigm Loader L0)。"""
    p = OnboardingPolicy()
    assert p.has_guardrails() is True
    # 显式空 guardrails → 不通过
    p2 = OnboardingPolicy(guardrails=())
    assert p2.has_guardrails() is False
    # 缺 1 条 → 不通过
    p3 = OnboardingPolicy(guardrails=("no rm -rf",))
    assert p3.has_guardrails() is False


# ============ AC7:用户响应持久化 + 不覆盖其他 flag ============
def test_ac7_response_persistence_isolated(tmp_path: pathlib.Path):
    """AC7: record_response 写进 seen.yaml,不污染其他 flag(I6)。"""
    seen_path = str(tmp_path / "seen.yaml")
    p = OnboardingPolicy(seen_path=seen_path)
    p.record_response(NO_ROLE_YET, "accepted")
    p.record_response(FIRST_SKILL_USE, "rejected")
    # 两次都进
    assert p.get_response(NO_ROLE_YET) == "accepted"
    assert p.get_response(FIRST_SKILL_USE) == "rejected"
    # 其他 flag 未污染
    assert p.get_response(FIRST_PURSUIT) is None
    # 持久化
    p2 = OnboardingPolicy(seen_path=seen_path)
    assert p2.get_response(NO_ROLE_YET) == "accepted"
    assert p2.get_response(FIRST_SKILL_USE) == "rejected"


# ============ 协议不变量:5 hint + 3 endpoint + observe + RAG 最小可用 ============
def test_protocol_invariants_and_observers(tmp_path: pathlib.Path):
    """协议不变量 + observe_message + doc_rag_search 最小可用。"""
    # 5 hint
    assert len(HINTS) == 5
    # 3 endpoint(min)
    assert len(endpoint_registry.all_entries()) >= 3
    # observe_message 关键词
    obs1 = observe_message(["我想加一个角色"])
    assert obs1 in ALL_FLAGS
    # 显式上下文信号优先
    obs2 = observe_message([], role_files_present=False)
    assert obs2 == NO_ROLE_YET
    obs3 = observe_message([], skill_used_recently=True)
    assert obs3 == FIRST_SKILL_USE
    # classify_intent fallback
    assert classify_intent(["hello world"]) is None
    # RAG 最小可用
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "a.md").write_text("# 标题\n\nKarvyLoop 是 AI-native 操作系统。", encoding="utf-8")
    (docs_dir / "b.md").write_text("# 其他\n\n跟 KarvyLoop 无关的内容。", encoding="utf-8")
    hits = doc_rag_search("KarvyLoop", docs_dir=str(docs_dir))
    assert len(hits) >= 1
    assert hits[0].path.endswith("a.md")
    assert hits[0].score > 0
    # 0 命中不抛
    assert doc_rag_search("xyz_no_match_xyz", docs_dir=str(docs_dir)) == []
    # docs_dir 不存在 → 空
    assert doc_rag_search("anything", docs_dir=str(tmp_path / "no_such_dir")) == []
