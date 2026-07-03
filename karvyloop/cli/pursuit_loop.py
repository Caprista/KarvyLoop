"""薄 pursue() 循环 —— role 作为尽责下属(docs/02 §15)。

**不是**胖 replan 状态机:阶梯的"换 plan / 搜 skill / 造 skill"由**模型 carry**(尽责契约已
seed 进每个 role 的 COMMITMENT,§15.1.5)。这里只给确定性**地板 + 薄外层**:

  - **一份统一预算**(`ReplanBudget.max_attempts`)兜住所有重试 —— **吸收** `verify_and_fix`
    的 fix-round。每次 attempt = drive + 独立验收;重试触发两种信号:
      ① abnormal terminal(max_turns / circuit_open = 没跑完)→ replan;
      ② verdict 不过(跑完了但结果不对)→ 带验收意见修。
  - **infra-dead**(模型 / 网络 / 沙箱调不通)→ **立即停 fail-loud,不重试**:基础能力没了,
    replan 同一条路也没用(docs/02 §15.3,Code① 的 `is_infra_dead` 判)。
  - 预算耗尽仍没成 → 标 `infeasible`(调用方据此发**不可行报告卡**,带真实尝试轨迹)。

pursue() 是**纯逻辑**(不碰 app / registry / 决策卡)→ 可单测;发卡 / 回执由 handler 接(它有 app)。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, List, Optional

from karvyloop.atoms.terminal import is_infra_dead
from karvyloop.coding.checker import CheckedResult, Verdict, independent_check


@dataclass
class ReplanBudget:
    """role 级重规划预算 = 确定性地板(模板覆盖不掉,§15.1.5)。

    max_attempts 兜住 replan(没跑完)+ fix-round(验收不过)的**总和** —— 一份预算,不嵌套。
    """
    max_attempts: int = 3

    def __post_init__(self) -> None:
        # 地板至少跑一次:max_attempts<1 会让循环体不执行 → 空轨迹的"假"不可行报告(违 §15.7)。
        if self.max_attempts < 1:
            self.max_attempts = 1


@dataclass
class PursuitOutcome:
    checked: CheckedResult
    attempts: List[dict] = field(default_factory=list)  # 真实轨迹 [{attempt, terminal, note}]
    infeasible: bool = False   # 预算耗尽仍没成 → 调用方该升不可行报告卡
    infra_dead: bool = False   # 基础能力失效 → fail-loud(**不是** infeasible,别发卡)


def _check_kwargs(rk: dict) -> Optional[dict]:
    """缺验收能力(token/sandbox/gateway 任一为空)→ None:诚实退回"不验"(不假装验过)。"""
    if not all(rk.get(k) is not None for k in ("token", "sandbox", "gateway")):
        return None
    return dict(
        token=rk["token"], sandbox=rk["sandbox"], gateway=rk["gateway"],
        workspace_root=rk.get("workspace_root", "/"), model_ref=rk.get("model_ref", ""),
    )


def _record_verdict(ml: Any, result: Any, verdict: "Verdict") -> None:
    """独立验收 verdict 回流(docs/44 断⑭):此前 verdict 只用于 replan,VerifyStore/eval_fact
    全不知情 —— 结晶"验证门"名实不符(只有执行器自报)。PASS/FAIL 都回流;inconclusive
    不回(没证据≠差评)。duck-type 防御:测试桩/旧 ml 没有 record_verdict → 静默跳过。"""
    if verdict.inconclusive:
        return
    rec = getattr(ml, "record_verdict", None)
    if not callable(rec):
        return
    sig = getattr(result, "sig", "") or ""
    if not sig:
        return
    try:
        rec(sig, passed=bool(verdict.passed), feedback=verdict.feedback or "",
            task_id=getattr(result, "task_id", "") or "")
    except Exception:
        pass   # 回流失败不阻断 pursue(记账是旁路,不是执行路径)


def pursue(
    goal: str,
    *,
    ml: Any,
    slow_brain: Any,
    rk: dict,
    budget: Optional[ReplanBudget] = None,
) -> PursuitOutcome:
    """让 role 在预算内尽责追求 goal —— drive + 验收,失败 replan / 修,infra-dead 停,耗尽标 infeasible。

    同步(供 handler 在线程内调);`asyncio.run(independent_check)` 与 `ml.drive` 顺序执行、
    不嵌套事件循环(同 `verify_and_fix` 的约定)。
    """
    budget = budget or ReplanBudget()
    ck = _check_kwargs(rk)
    attempts: List[dict] = []
    intent = goal
    last_result: Any = None
    last_verdict = Verdict(passed=False, feedback="(未执行)")

    for i in range(1, budget.max_attempts + 1):
        result = ml.drive(intent, slow_brain=slow_brain)
        last_result = result
        terminal = (getattr(result, "terminal", "") or "")
        is_last = i >= budget.max_attempts

        # 防御性:作者执行抛错被上层兜成 result.error(同 verify_and_fix 约定)→ 当"没跑完"
        if getattr(result, "error", ""):
            attempts.append({"attempt": i, "terminal": "error", "note": "执行出错"})
            last_verdict = Verdict(passed=False, feedback="作者执行出错", raw="")
            if is_last:
                break
            intent = f"{goal}\n\n【上次执行出错】请换个更稳的方法重做。"
            continue

        # ① infra-dead → 立即停 fail-loud,不重试(基础能力没了,replan 没用)
        if is_infra_dead(terminal):
            attempts.append({"attempt": i, "terminal": terminal, "note": "基础能力失效"})
            return PursuitOutcome(
                checked=CheckedResult(
                    result=result,
                    verdict=Verdict(passed=False, feedback="基础能力失效(模型/网络/沙箱调不通)",
                                    inconclusive=True),
                    rounds=i - 1),
                attempts=attempts, infra_dead=True)

        # ② abnormal terminal(没跑完)→ replan(还有预算)
        if terminal and terminal != "completed":
            attempts.append({"attempt": i, "terminal": terminal, "note": "没跑完"})
            if is_last:
                last_verdict = Verdict(passed=False, feedback=f"多次未跑完({terminal})")
                break
            intent = f"{goal}\n\n【上次没跑完:{terminal}】请拆成更小的步骤、换个方法重做。"
            continue

        # 跑完了 → 验收门
        if ck is None:
            # 未接验收能力 → 诚实 inconclusive 收(不假装验过)
            attempts.append({"attempt": i, "terminal": "completed", "note": "未接验收"})
            return PursuitOutcome(
                checked=CheckedResult(
                    result=result,
                    verdict=Verdict(passed=True, feedback="(未接验收能力)", inconclusive=True),
                    rounds=i - 1),
                attempts=attempts)

        verdict = asyncio.run(independent_check(goal, getattr(result, "text", "") or "", **ck))
        last_verdict = verdict
        _record_verdict(ml, result, verdict)   # docs/44 断⑭:verdict 回流验证门/eval_fact
        if verdict.passed or verdict.inconclusive:
            attempts.append({"attempt": i, "terminal": "completed",
                             "note": "验收过" if verdict.passed else "验收 inconclusive"})
            return PursuitOutcome(
                checked=CheckedResult(result=result, verdict=verdict, rounds=i - 1),
                attempts=attempts)

        # ③ verdict 不过 → 带意见修(fix-round,同一预算)
        attempts.append({"attempt": i, "terminal": "completed",
                         "note": f"验收未过:{(verdict.feedback or '')[:40]}"})
        if is_last:
            break
        intent = (f"{goal}\n\n【独立验收未通过】{verdict.feedback}\n"
                  f"请针对该问题修正后重做,别重复上次的错。")

    # 预算耗尽仍没成 → infeasible(带轨迹给不可行报告卡)。
    # 防御:infeasible 只在**有真实轨迹**时为真(§15.7:无轨迹=假报告;max_attempts≥1 已保证非空)。
    return PursuitOutcome(
        checked=CheckedResult(result=last_result, verdict=last_verdict, rounds=budget.max_attempts),
        attempts=attempts, infeasible=bool(attempts))


__all__ = ["ReplanBudget", "PursuitOutcome", "pursue"]
