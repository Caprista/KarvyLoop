"""coding/checker.py — 独立验收者(loop step3:把 maker/checker 分离落进 forge)。

**为什么**:P3 M1 的"自检"是 maker 自己验自己(persona 提示"跑一遍验证")——
作者既当运动员又当裁判,会把"我 cat 了一遍 heredoc 所以没问题"当验证。loop engineering
(docs/00 §0.6 loop 层)要求**独立** checker:全新 LLM 上下文、只读工具、不信作者自述,
去**实际**读工作区/跑测试,判定是否真的达成需求;不过则把验收意见回灌作者修一轮再验。

锚:docs/00 §0.6 工程范式四层栈(loop 层 maker/checker + state-on-disk);《产品之书》ch6 step3。

诚实边界:
- checker 拿 read_file + run_command(能读能跑测试,**不给** write/edit → 维持作者/验收者分离)。
  **✅ 安全硬化(2026-06-21)**:此前"bash 仍能写"是 loophole;现给 checker 一个 `read_only_token`
  (去掉 fs 写权限)→ bubblewrap `--ro-bind` 工作区 → **bash 也写不动**(能力层堵死,非靠 prompt)。
- 无验收能力(--no-llm / 缺 gateway)→ 诚实退回单跑,**不假装**验收(标 inconclusive)。
- checker 没给明确 VERDICT → inconclusive,**不阻塞**(避免假阴性把用户卡进重做循环)。
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from karvyloop.coding.prompt import CodingPrompt, build_coding_prompt

logger = logging.getLogger(__name__)

# 验收判定:结尾一行 `VERDICT: PASS` 或 `VERDICT: FAIL — 原因`(中英文冒号/破折号都认)
_VERDICT_RE = re.compile(r"VERDICT\s*[:：]\s*(PASS|FAIL)", re.IGNORECASE)


@dataclass
class Verdict:
    """独立验收结论。"""
    passed: bool
    feedback: str = ""
    raw: str = ""
    inconclusive: bool = False  # checker 没给明确判定 / 无验收能力 → 不据此阻塞


@dataclass
class CheckedResult:
    """maker→checker(→修)后的最终结果。"""
    result: Any        # 最后一次 maker 的 DriveOutcome(.text / .error)
    verdict: Verdict
    rounds: int = 0    # 验收驱动的修正轮数(0 = 一次过 / 未触发修正)


def build_checker_prompt(cwd: str) -> CodingPrompt:
    """验收者 system prompt:复用 build_coding_prompt 的动态上下文(cwd/git/指令文件),
    换掉静态角色段 —— 强调"你是裁判不是作者、只读核验、结尾必须给 VERDICT"。"""
    base = build_coding_prompt(cwd)
    static = [
        "你是 KarvyLoop 的**独立验收者(checker)**,不是作者。某作者已声称完成了一个需求。",
        "职责:**不要相信作者的自述**。用 read_file 读实际产物、用 run_command 实际跑"
        "(测试/脚本/命令/检查输出),独立判断是否**真的**达成了需求。",
        "纪律:你只负责核验,**绝不修改任何文件**(你没有写工具)。先看证据再下结论。",
        "工具集:read_file / run_command(只读核验)。",
        "结尾**必须**单独一行给出判定,二选一:\n"
        "  `VERDICT: PASS`  —— 确已达成\n"
        "  `VERDICT: FAIL — <一句话说清差在哪、怎么修>`  —— 未达成",
    ]
    return CodingPrompt(static=static, dynamic_blocks=base.dynamic_blocks)


def parse_verdict(text: str) -> Verdict:
    """从验收者输出里解析 VERDICT。没有明确判定 → inconclusive(不阻塞)。

    取**最后**一处 VERDICT(prompt 要求判定在结尾单独一行):验收者常会先复述作者自述
    (其中可能含 "VERDICT: PASS")再下自己的结论,first-match 会把作者的声称误当成验收
    结果 → 假 PASS,直接架空整个独立验收。故必须 last-match。
    """
    t = text or ""
    matches = list(_VERDICT_RE.finditer(t))
    if not matches:
        return Verdict(passed=True, feedback="(验收未给出明确判定)", raw=t, inconclusive=True)
    m = matches[-1]
    passed = m.group(1).upper() == "PASS"
    # 取 VERDICT 所在行 PASS/FAIL 之后的原因(去掉前导分隔符)
    line = t[m.start():].splitlines()[0]
    after = line[(m.end() - m.start()):]
    feedback = after.lstrip(" —-–:：\t").strip() or line.strip()
    return Verdict(passed=passed, feedback=feedback, raw=t)


async def independent_check(
    intent: str,
    maker_text: str,
    *,
    token: Any,
    sandbox: Any,
    gateway: Any,
    workspace_root: str = "/",
    model_ref: str = "",
    max_turns: int = 6,
) -> Verdict:
    """跑一次**独立**只读 forge run 核验作者产物,返回 Verdict。token 账本归属 source=checker。"""
    from karvyloop.coding.forge import generate_and_run
    from karvyloop.llm.token_ledger import token_source
    from karvyloop.sandbox.mounts import read_only_token

    check_intent = (
        f"【需要核验的原始需求】\n{intent}\n\n"
        f"【作者的自述(仅供参考,不可尽信)】\n{(maker_text or '(无自述)').strip()}\n\n"
        f"请独立核验作者是否真的达成了上述需求,结尾给出 VERDICT。"
    )
    # 安全硬化(§0.6):给 checker 一个**去掉 fs 写权限**的 token → 工作区 ro-bind → bash 也写不动
    # (堵 read_only loophole;此前只砍 write/edit 工具,bash 仍能写)。
    ro_token = read_only_token(token)
    with token_source("checker"):
        rr = await generate_and_run(
            check_intent, ro_token, sandbox,
            gateway=gateway, workspace_root=workspace_root,
            model_ref=model_ref, max_turns=max_turns,
            read_only=True,
            system_prompt=build_checker_prompt(workspace_root),
        )
    return parse_verdict(rr.text or "")


def verify_and_fix(
    intent: str,
    *,
    drive_fn: Callable[[str], Any],
    check_kwargs: dict,
    max_fix_rounds: int = 1,
) -> CheckedResult:
    """maker → 独立 checker →(不过则回灌修一轮)→ 再 checker。同步(供 handler 在线程内调)。

    `drive_fn(intent_str)` 跑一次 maker,返回 DriveOutcome(.text/.error)。
    `check_kwargs` = independent_check 的 token/sandbox/gateway/workspace_root/model_ref。
    nested asyncio.run 安全:drive_fn 与 independent_check 顺序执行,不嵌套事件循环。
    """
    result = drive_fn(intent)
    # 注:生产里 maker 真失败是**抛异常**(ml.drive 不吞,DriveResult 无 error 字段),
    # 由 handler 外层 try 兜(task_reg.finish(error=...))→ 那条路根本到不了 checker。
    # 这里的 .error 分支是防御性的(与 handler 既有 `getattr(result,"error")` 约定一致):
    # 万一某 outcome 类型带 error 字段,也不去验一个失败产物。
    if getattr(result, "error", ""):
        return CheckedResult(result=result,
                             verdict=Verdict(passed=False, feedback="作者执行出错", raw=""),
                             rounds=0)
    verdict = asyncio.run(independent_check(intent, getattr(result, "text", "") or "", **check_kwargs))
    rounds = 0
    while (not verdict.passed) and (not verdict.inconclusive) and rounds < max_fix_rounds:
        rounds += 1
        logger.info(f"[checker] 验收未过(第{rounds}轮回灌修正): {verdict.feedback[:80]}")
        fix_intent = (
            f"{intent}\n\n"
            f"【独立验收未通过,验收意见】{verdict.feedback}\n"
            f"请针对该问题修正后重做,不要重复上次的错误。"
        )
        result = drive_fn(fix_intent)
        if getattr(result, "error", ""):
            break
        verdict = asyncio.run(independent_check(intent, getattr(result, "text", "") or "", **check_kwargs))
    return CheckedResult(result=result, verdict=verdict, rounds=rounds)


def verify_and_fix_with_rk(
    intent: str,
    *,
    ml: Any,
    slow_brain: Any,
    rk: dict,
    max_fix_rounds: int = 1,
) -> CheckedResult:
    """handler 便捷封装:从 runtime_kwargs(rk)取验收能力,缺则**诚实退回单跑**(不假装验收)。"""
    def drive_fn(i: str) -> Any:
        return ml.drive(i, slow_brain=slow_brain)

    have_check = all(rk.get(k) is not None for k in ("token", "sandbox", "gateway"))
    if not have_check:
        return CheckedResult(
            result=drive_fn(intent),
            verdict=Verdict(passed=True, feedback="(未接验收能力)", inconclusive=True),
            rounds=0,
        )
    check_kwargs = dict(
        token=rk["token"], sandbox=rk["sandbox"], gateway=rk["gateway"],
        workspace_root=rk.get("workspace_root", "/"), model_ref=rk.get("model_ref", ""),
    )
    checked = verify_and_fix(intent, drive_fn=drive_fn, check_kwargs=check_kwargs,
                             max_fix_rounds=max_fix_rounds)
    # docs/44 断⑭:最终 verdict 回流验证门/eval_fact(此前只用于修一轮,结晶闸门全不知情)。
    # inconclusive 不回(没证据≠差评);duck-type 防御:测试桩 ml 没有 record_verdict → 跳过。
    v = checked.verdict
    rec = getattr(ml, "record_verdict", None)
    sig = getattr(checked.result, "sig", "") or ""
    if callable(rec) and sig and not v.inconclusive:
        try:
            rec(sig, passed=bool(v.passed), feedback=v.feedback or "",
                task_id=getattr(checked.result, "task_id", "") or "")
        except Exception:
            logger.warning("[checker] verdict 回流失败(sig=%s);验收结论不受影响", sig[:8], exc_info=True)
    return checked


def verdict_suffix(checked: CheckedResult) -> str:
    """把验收结论压成一句可拼进回执的后缀(给用户看 loop 真的验过了)。"""
    v = checked.verdict
    if v.inconclusive:
        return ""
    if v.passed:
        if checked.rounds:
            return f"(独立验收✓,修正{checked.rounds}轮后通过)"
        return "(独立验收✓)"
    return f"(独立验收⚠ 仍未过:{v.feedback[:60]})"


__all__ = [
    "Verdict", "CheckedResult",
    "build_checker_prompt", "parse_verdict",
    "independent_check", "verify_and_fix", "verify_and_fix_with_rk", "verdict_suffix",
]
