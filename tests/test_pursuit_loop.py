"""docs/02 §15 — Code ②b-2:薄 pursue() 循环验收。

一份统一预算兜住 replan(没跑完)+ fix-round(验收不过);infra-dead 立即停不重试;
耗尽标 infeasible(带轨迹)。逐条锚 §15.7 不变量。
"""

from __future__ import annotations

import types

import pytest

from karvyloop.cli.pursuit_loop import ReplanBudget, pursue
from karvyloop.coding.checker import Verdict


def _res(terminal: str = "completed", text: str = "ok"):
    return types.SimpleNamespace(terminal=terminal, text=text)


class FakeML:
    """按 attempt 顺序吐 DriveResult(用尽后重复最后一个);记录每次 intent。"""
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def drive(self, intent, slow_brain=None):
        self.calls.append(intent)
        idx = min(len(self.calls) - 1, len(self._results) - 1)
        return self._results[idx]


_RK_CHECK = {"token": object(), "sandbox": object(), "gateway": object(),
             "workspace_root": "/", "model_ref": "m"}
_RK_NOCHECK: dict = {}  # 缺 token/sandbox/gateway → 不验收


def _patch_check(monkeypatch, verdicts):
    seq = list(verdicts)
    n = {"i": 0}

    async def _stub(goal, text, **kw):
        v = seq[min(n["i"], len(seq) - 1)]
        n["i"] += 1
        return v

    monkeypatch.setattr("karvyloop.cli.pursuit_loop.independent_check", _stub)


# ============ 成功路径:一次过 ============

def test_success_first_attempt(monkeypatch):
    _patch_check(monkeypatch, [Verdict(passed=True, feedback="good")])
    ml = FakeML([_res("completed")])
    out = pursue("写报告", ml=ml, slow_brain=None, rk=_RK_CHECK)
    assert out.infeasible is False and out.infra_dead is False
    assert len(ml.calls) == 1
    assert len(out.attempts) == 1 and out.attempts[0]["note"] == "验收过"


# ============ ① infra-dead:立即停,不重试,不 infeasible ============

def test_infra_dead_stops_immediately_no_retry(monkeypatch):
    ml = FakeML([_res("infra_dead")])
    out = pursue("写报告", ml=ml, slow_brain=None, rk=_RK_CHECK)
    assert out.infra_dead is True
    assert out.infeasible is False           # infra-dead ≠ infeasible(别发卡)
    assert len(ml.calls) == 1                # 没有白爬阶梯:就跑了一次
    assert out.attempts[0]["terminal"] == "infra_dead"


# ============ ② abnormal terminal → replan ============

def test_abnormal_terminal_then_success(monkeypatch):
    _patch_check(monkeypatch, [Verdict(passed=True)])
    ml = FakeML([_res("max_turns"), _res("completed")])
    out = pursue("写报告", ml=ml, slow_brain=None, rk=_RK_CHECK)
    assert out.infeasible is False
    assert len(ml.calls) == 2                # 第一次没跑完 → replan,第二次成
    assert out.attempts[0]["terminal"] == "max_turns" and out.attempts[0]["note"] == "没跑完"
    # replan 的 intent 带了"没跑完"反馈
    assert "没跑完" in ml.calls[1]


def test_all_abnormal_exhausts_to_infeasible(monkeypatch):
    ml = FakeML([_res("circuit_open")])  # 永远没跑完
    out = pursue("写报告", ml=ml, slow_brain=None, rk=_RK_CHECK)
    assert out.infeasible is True
    assert len(ml.calls) == 3                            # 默认预算 3
    assert len(out.attempts) == 3


# ============ ③ verdict 不过 → 带意见修(同一预算)============

def test_verdict_fail_then_pass(monkeypatch):
    _patch_check(monkeypatch, [Verdict(passed=False, feedback="少了结论"), Verdict(passed=True)])
    ml = FakeML([_res("completed"), _res("completed")])
    out = pursue("写报告", ml=ml, slow_brain=None, rk=_RK_CHECK)
    assert out.infeasible is False
    assert len(ml.calls) == 2
    assert "少了结论" in ml.calls[1]                      # fix-round 带了验收意见


def test_verdict_always_fail_exhausts_to_infeasible(monkeypatch):
    _patch_check(monkeypatch, [Verdict(passed=False, feedback="还是不对")])
    ml = FakeML([_res("completed")])
    out = pursue("写报告", ml=ml, slow_brain=None, rk=_RK_CHECK)
    assert out.infeasible is True
    assert len(out.attempts) == 3
    assert all(a["terminal"] == "completed" for a in out.attempts)  # 跑完了但反复验收不过


# ============ 统一预算:replan 与 fix-round 共用一份 ============

def test_budget_caps_total_attempts(monkeypatch):
    ml = FakeML([_res("max_turns")])
    out = pursue("写报告", ml=ml, slow_brain=None, rk=_RK_CHECK,
                 budget=ReplanBudget(max_attempts=2))
    assert len(ml.calls) == 2                # 严格被地板封顶
    assert out.infeasible is True


# ============ 未接验收能力:诚实 inconclusive 收,不假装 ============

def test_no_check_capability_accepts_inconclusive(monkeypatch):
    ml = FakeML([_res("completed")])
    out = pursue("写报告", ml=ml, slow_brain=None, rk=_RK_NOCHECK)
    assert out.infeasible is False and out.infra_dead is False
    assert out.checked.verdict.inconclusive is True
    assert len(ml.calls) == 1               # 没验收能力不该硬重试


# ============ 轨迹真实(给不可行报告卡当证据)============

def test_budget_clamps_to_at_least_one(monkeypatch):
    """对抗验收 Finding 1:max_attempts<1 会让循环不跑 → 空轨迹"假"infeasible(违 §15.7)。
    地板钳到 ≥1 + infeasible 只在有真实轨迹时为真。"""
    from karvyloop.cli.pursuit_loop import ReplanBudget
    b = ReplanBudget(max_attempts=0)
    assert b.max_attempts == 1
    ml = FakeML([_res("max_turns")])
    out = pursue("写报告", ml=ml, slow_brain=None, rk=_RK_CHECK, budget=b)
    assert len(ml.calls) == 1                    # 至少跑一次
    assert out.infeasible is True
    assert len(out.attempts) >= 1                # 永不空轨迹假报告


def test_attempts_trail_is_real_for_card(monkeypatch):
    _patch_check(monkeypatch, [Verdict(passed=False, feedback="x")])
    ml = FakeML([_res("max_turns"), _res("completed"), _res("completed")])
    out = pursue("写报告", ml=ml, slow_brain=None, rk=_RK_CHECK)
    assert out.infeasible is True
    # 轨迹反映真实发生:先 max_turns(没跑完),后两次 completed 但验收不过
    terminals = [a["terminal"] for a in out.attempts]
    assert terminals == ["max_turns", "completed", "completed"]
