"""test_decision_card — 决策卡四条硬不变量(接地/逼判断/价值闸/反投降)。"""
from __future__ import annotations
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from karvyloop.cognition.decision_card import (  # noqa: E402
    build_decision_card, should_surface, SurfaceTracker, Criterion)


# ---- 不变量2:接地,不诱信任 ----
def test_no_gate_is_unverifiable_never_solved():
    c = build_decision_card(problem="P", approach="A", gate_results=None)
    assert c.resolvable == "unverifiable" and c.grounded is False
    assert c.to_dict()["narrated_warning"] is True   # UI 必须标"未核验"
    assert c.verified_criteria() == []               # 无接地依据,绝不伪装

def test_all_gates_pass_is_grounded_solved():
    c = build_decision_card(problem="P", approach="A",
                            gate_results=[("测试通过", True), ("文件已生成", True)])
    assert c.resolvable == "solved" and c.grounded is True
    assert len(c.verified_criteria()) == 2
    assert all(x.source == "verify_gate" and x.status == "passed" for x in c.criteria)

def test_some_fail_is_partial_all_fail_is_failed():
    assert build_decision_card(problem="P", approach="A",
                               gate_results=[("a", True), ("b", False)]).resolvable == "partial"
    assert build_decision_card(problem="P", approach="A",
                               gate_results=[("a", False), ("b", False)]).resolvable == "failed"


# ---- 不变量4:价值闸 —— 过度判断=没判断,纯执行成功不浮 ----
def test_pure_grounded_success_does_not_surface():
    c = build_decision_card(problem="P", approach="A", gate_results=[("ok", True)])
    assert should_surface(c) is False                      # solved+接地+非高价值 → 自动,不打扰

def test_failure_partial_unverifiable_surface():
    assert should_surface(build_decision_card(problem="P", approach="A",
                          gate_results=[("x", False)])) is True            # failed
    assert should_surface(build_decision_card(problem="P", approach="A",
                          gate_results=[("a", True), ("b", False)])) is True  # partial
    assert should_surface(build_decision_card(problem="P", approach="A")) is True  # unverifiable

def test_high_value_or_deontic_forces_surface_even_if_solved():
    c = build_decision_card(problem="P", approach="A", gate_results=[("ok", True)])
    assert should_surface(c, high_value=True) is True
    assert should_surface(c, deontic_requires=True) is True


# ---- 不变量3:逼判断 —— engaged 只有改/删才算 ----
def test_engaged_only_when_edited_or_dropped():
    c = build_decision_card(problem="P", approach="A", gate_results=[("a", True), ("b", True)])
    assert c.engaged() is False                       # 没动 = rubber-stamp
    c.criteria[0].dropped = True
    assert c.engaged() is True                        # 删了一条 = 真判断
    c2 = build_decision_card(problem="P", approach="A", gate_results=[("a", True)])
    c2.criteria[0].edited_from = "原依据"
    assert c2.engaged() is True                       # 改写过 = 真判断


# ---- 不变量4(另一半):反投降闸 ----
def test_anti_surrender_gate_trips_after_threshold_and_resets_on_engage():
    t = SurfaceTracker(threshold=3)
    for _ in range(3):
        t.record(accepted=True, engaged=False)        # 连续无脑认
    assert t.needs_recheck() is True                  # 3 次 → 拦一次
    t.record(accepted=True, engaged=True)             # 这次真判断了
    assert t.consecutive_blind_accepts == 0 and t.needs_recheck() is False
    # 拒/DEFER 也重置
    t2 = SurfaceTracker(threshold=2)
    t2.record(accepted=True, engaged=False); t2.record(accepted=False, engaged=False)
    assert t2.consecutive_blind_accepts == 0
