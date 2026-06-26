"""Paradigm Loader 验收测试（tests/test_paradigm_loader.py）。

**M2.0 拍 0**。6 AC 锁住机制不变量(防漂移)。设计:`docs/10-paradigm-loader.md`。
"""

from __future__ import annotations

import pytest

from karvyloop.paradigm import (
    LAYER_ORDER,
    R1_FULL_SCENE,
    R2_PURSUIT_HIT,
    R3_VERIFY_STEP,
    R4_DOMAIN_LAYER,
    SOUL_FILES,
    ParadigmContext,
    load_paradigm,
)
from karvyloop.paradigm.budget import Budget, TokenCounter
from karvyloop.paradigm.loader import (
    DomainView,
    LayerContent,
    LoadedParadigm,
    PursuitView,
    RoleInstance,
)


# ---- helpers ----------

def _make_ctx(
    *,
    role_id: str = "pm",
    identity: str = "I am PM",
    soul: str = "I value simplicity",
    composition: str = "use write_ppt",
    guardrails: list[str] | None = None,
    user_message: str = "写一个 Q3 OKR",
    pursuit: PursuitView | None = None,
    environment: dict | None = None,
    soul_refs: dict | None = None,
) -> ParadigmContext:
    return ParadigmContext(
        role_instance=RoleInstance(
            role_id=role_id,
            identity_text=identity,
            soul_text=soul,
            composition_text=composition,
            soul_refs=soul_refs or {},
        ),
        domain=DomainView(domain_id="karvyloop", guardrails=guardrails or []),
        user_message=user_message,
        current_pursuit=pursuit,
        environment=environment or {},
    )


# ============ AC1:7 layer 顺序正确 ============
def test_ac1_full_pm_context_returns_7_layers_in_order():
    """AC1: role `pm` + domain `karvyloop` + pursuit 命中 → 加载 Layer 0/1/2/3/5
    (注:Layer 4 VERIFY 不加载——没进入判定步骤;Layer 6 没 environment)
    且按 LAYER_ORDER 顺序加载。
    """
    pursuit = PursuitView(
        id="p1",
        statement="Q3 OKR",
        verify_gate={"metric": "ship by Sept"},
        commitment_text="ship 3 features",
    )
    ctx = _make_ctx(pursuit=pursuit, environment={"tools": ["write_ppt"]})

    result = load_paradigm(ctx)

    # 必加载(AC2)
    assert 0 in result.layers
    assert 1 in result.layers
    assert 2 in result.layers
    # R2 命中 → Layer 3 在
    assert 3 in result.layers
    # Layer 4 不在(没进入判定步骤)
    assert 4 not in result.layers
    # Layer 5 在(R1)
    assert 5 in result.layers
    # Layer 6 不在(没 environment? -- 实际上 environment={"tools":...} → 6 会在)
    # 修正:有 environment → 6 在
    assert 6 in result.layers

    # 顺序正确
    assert result.loaded_layers == sorted(result.loaded_layers)
    # log_line 包含 loaded 顺序
    assert "loaded=" in result.log_line


# ============ AC2:MUST 永在 + 条件层按规则 ============
def test_ac2_must_layers_always_present_conditional_only_when_rule_hits():
    """AC2: Layer 0/1/2 任何输入下**必**出现;Layer 3 仅当 pursuit 命中;Layer 4 仅当判定步骤。"""
    # --- 1. 无 pursuit + 无 verify → 仅 MUST
    r1 = load_paradigm(_make_ctx(pursuit=None, user_message="hello"))
    assert 0 in r1.layers
    assert 1 in r1.layers
    assert 2 in r1.layers
    assert 3 not in r1.layers
    assert 4 not in r1.layers
    assert 5 in r1.layers  # R1

    # --- 2. 有 pursuit → Layer 3 在
    p = PursuitView(id="p1", statement="X", verify_gate={})
    r2 = load_paradigm(_make_ctx(pursuit=p, user_message="hello"))
    assert 3 in r2.layers
    assert 4 not in r2.layers  # 还是没判定

    # --- 3. pursuit 标 entering_verification → Layer 4 在
    p_verify = PursuitView(id="p2", statement="Y", verify_gate={}, entering_verification=True)
    r3 = load_paradigm(_make_ctx(pursuit=p_verify, user_message="继续"))
    assert 3 in r3.layers
    assert 4 in r3.layers

    # --- 4. user_message 含 'verify' 关键词 → Layer 4 在
    r4 = load_paradigm(_make_ctx(pursuit=p, user_message="let's verify the result"))
    assert 4 in r4.layers

    # --- 5. user_message 含中文 '判定' → Layer 4 在
    r5 = load_paradigm(_make_ctx(pursuit=p, user_message="该做最终判定了"))
    assert 4 in r5.layers


# ============ AC3:budget overflow → 按降级顺序砍 ============
def test_ac3_overflow_drops_layer_6_first_then_5():
    """AC3: budget 满 → 优先砍 Layer 6 → 再砍 Layer 5 → 永不砍 Layer 0/1/2。"""
    # 制造一个超长 Layer 6 内容(用环境+大量 tools 名)
    big_tools = [f"tool_{i}_" + "x" * 50 for i in range(2000)]
    ctx = _make_ctx(
        user_message="hello",
        environment={"tools": big_tools, "channel": "default"},
    )

    # 给一个非常小的 budget(只够 MUST)
    small_budget = Budget(cap=200, counter=TokenCounter())
    result = load_paradigm(ctx, budget=small_budget)

    # MUST 都在
    assert 0 in result.layers
    assert 1 in result.layers
    assert 2 in result.layers
    # 6 必砍(最不稳)
    assert 6 not in result.layers
    assert 6 in result.dropped_layers
    # 5 可能也砍了(看 token 数),但 MUST 一定在
    # 不变量:0/1/2 永远在
    for must in (0, 1, 2):
        assert must in result.layers, f"MUST layer {must} 被砍了,违反不变量"

    # 极端:把 budget 设到 MUST 装得下但 MUST+L5 装不下的程度
    # 用大 identity 让 MUST 总 token 达到 ~80,cap=85 → MUST 装得下(80),加 L5(5)→ 85 = 装得下
    # 用 cap=83 → 80 装得下,加 L5(5)→ 85 > 83,5 必砍
    big_ctx = _make_ctx(
        identity="X" * 200,  # ~50 token
        soul="Y" * 200,      # ~50 token
        user_message="hello",
        environment={"tools": ["t1", "t2", "t3"] * 100},  # big L6
    )
    tiny_budget = Budget(cap=83, counter=TokenCounter())
    result2 = load_paradigm(big_ctx, budget=tiny_budget)
    # MUST 必须还在
    for must in (0, 1, 2):
        assert must in result2.layers
    # 5 必砍(MUST 80 token + 5 default 占位 ≈ 5 token,总 85 > 83)
    assert 5 not in result2.layers, f"L5 没被砍,反例: {result2.layers.keys()}"
    # 6 早砍
    assert 6 not in result2.layers
    # dropped 必须包含 5 和 6
    assert 5 in result2.dropped_layers
    assert 6 in result2.dropped_layers


# ============ AC4:pursuit 节流 + VERIFY 条件 ============
def test_ac4_pursuit_throttle_and_verify_condition():
    """AC4: 同一 pursuit 重复加载 COMMITMENT 没问题(纯函数无副作用);
    VERIFY 只在判定步骤出现(已在 AC2 覆盖)。这里验证:
    1. 多次 load_paradigm 同一 ctx → 每次结果一致
    2. current_pursuit.commitment_text 给定时,Layer 3 文本就是它(不走 default)
    """
    p = PursuitView(
        id="p_throttle",
        statement="ship X",
        verify_gate={"k": "v"},
        commitment_text="explicit commitment text",
    )
    ctx = _make_ctx(pursuit=p, user_message="normal")

    # 同一 ctx 多次加载 → 结果一致
    r1 = load_paradigm(ctx)
    r2 = load_paradigm(ctx)
    assert r1.loaded_layers == r2.loaded_layers
    assert r1.layers[3].text == r2.layers[3].text
    # COMMITMENT 用了显式文本
    assert "explicit commitment text" in r1.layers[3].text

    # pursuit 没 commitment_text → 走 default(从 statement 生成)
    p_no_text = PursuitView(id="p2", statement="默认承诺", verify_gate={})
    ctx2 = _make_ctx(pursuit=p_no_text, user_message="normal")
    r3 = load_paradigm(ctx2)
    assert "默认承诺" in r3.layers[3].text


# ============ AC5:日志格式稳定 ============
def test_ac5_log_line_format_stable():
    """AC5: 日志格式 = `[ParadigmLoader] role=X domain=Y loaded=[...] dropped=[...] budget=N/M`"""
    ctx = _make_ctx()
    result = load_paradigm(ctx)

    log = result.log_line
    assert log.startswith("[ParadigmLoader] ")
    assert "role=pm" in log
    assert "domain=karvyloop" in log
    assert "loaded=" in log
    assert "dropped=" in log
    assert "budget=" in log
    # budget 形如 "N/M"
    budget_part = log.split("budget=")[-1]
    assert "/" in budget_part
    n, m = budget_part.split("/")
    assert n.isdigit()
    assert m.isdigit()


# ============ AC6:完全无 .md 也能跑 ============
def test_ac6_empty_md_files_fall_back_to_defaults():
    """AC6: 完全无 .md 文件时,Layer 0/1 走 default,Layer 2-5 也走 default,
    Loader **不**抛异常,正常返回 LoadedParadigm。"""
    ctx = _make_ctx(
        identity="",          # 缺 IDENTITY
        soul="",              # 缺 SOUL
        composition="",       # 缺 COMPOSITION
        guardrails=[],        # 缺 guardrails
        soul_refs={},         # 缺 USER/MEMORY
    )
    result = load_paradigm(ctx)

    # 不抛异常
    assert isinstance(result, LoadedParadigm)
    # MUST 都在
    assert 0 in result.layers
    assert 1 in result.layers
    assert 2 in result.layers
    # 0 走 default(没 guardrails)
    assert "default" in result.layers[0].source
    # 1 走 default(identity 和 soul 都缺)
    assert "default" in result.layers[1].source
    # 2 走 default
    assert "default" in result.layers[2].source

    # system prompt 拼出来不崩
    prompt = result.to_system_prompt()
    assert "layer 0" in prompt
    assert "layer 1" in prompt
    assert "layer 2" in prompt
    # 顺序正确(layer 数字从 0 升序)
    idx0 = prompt.index("layer 0")
    idx1 = prompt.index("layer 1")
    idx2 = prompt.index("layer 2")
    assert idx0 < idx1 < idx2


# ============ 协议不变量(锁住 #0 §2.4 4 规则) ============
def test_policy_constants_match_constitution():
    """锁住与 #0 §2.4 的一致性:SOUL_FILES 6 个 + LAYER_ORDER (0..6) + 4 规则 ID。"""
    assert len(SOUL_FILES) == 6
    assert set(SOUL_FILES) == {"IDENTITY", "SOUL", "USER", "COMMITMENT", "VERIFY", "MEMORY"}
    assert LAYER_ORDER == (0, 1, 2, 3, 4, 5, 6)
    # 4 规则 ID 顺序
    assert R1_FULL_SCENE.id == "R1"
    assert R2_PURSUIT_HIT.id == "R2"
    assert R3_VERIFY_STEP.id == "R3"
    assert R4_DOMAIN_LAYER.id == "R4"
