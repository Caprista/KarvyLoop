"""ops_agent — 自愈运维 agent(Layer 1,LLM)。诊断超出 doctor 固定检查的真问题。

垫在确定性 doctor(L0)之上(bootstrap 悖论:模型挂了它也挂,那时 L0 顶上)。三条诚实铁律:
1. **接地**:只吃**真问题信号**(doctor 真发现的问题 / 真实错误),不自由臆测没给的东西。
2. **宁空勿毒**(镜像 `llm-output-parser-must-refuse-garbage`):严格 JSON,解析失败/无实质 → ok=False,
   绝不把幻觉当诊断。
3. **只诊断+提议,绝不执行**:LLM 输出**永不被执行**——只展示给你看 / 作为提议;唯一自动执行
   的是确定性 `doctor.repair_finding`(可逆子集)。"自动修"绝不等于"让 LLM 改你系统"。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

_RISKS = ("reversible", "needs_approval")

OPS_SYSTEM = (
    "你是 KarvyLoop 的运维诊断器。下面给你一个**真实的系统问题信号**(自检发现的问题 / 真实报错)。"
    "用**用户能懂的话**(不要黑话)说清:① 这是什么问题 ② 大概为什么 ③ 具体怎么修(分步、可照着做)。"
    "**只基于给定的信号**,别臆测没给的东西;拿不准就说不确定。"
    "再判断这个修法的风险:**reversible**(可逆/低风险,如重置一个缓存文件)还是 "
    "**needs_approval**(改配置 / 装东西 / 删数据 / 动网络 —— 须用户批准)。\n"
    '严格输出 JSON 对象:{"summary":"<一句话问题>","cause":"<可能原因>",'
    '"fix":"<分步怎么修>","risk":"reversible|needs_approval"}。'
    "不要输出 JSON 以外任何文字。无法诊断就输出 "
    '{"summary":"","cause":"","fix":"","risk":"needs_approval"}。'
)


@dataclass
class OpsDiagnosis:
    ok: bool                  # 是否成功诊断(有实质 summary+fix)
    summary: str = ""
    cause: str = ""
    fix: str = ""
    risk: str = "needs_approval"   # reversible | needs_approval(默认保守:要批准)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "summary": self.summary, "cause": self.cause,
                "fix": self.fix, "risk": self.risk}


def _strip_fence(t: str) -> str:
    t = (t or "").strip()
    if t.startswith("```"):
        t = t[3:]
        if t[:4].lower() == "json":
            t = t[4:]
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()


def parse_diagnosis(text: str) -> Optional[dict]:
    """严格解析运维诊断 JSON。宁空勿毒:只剥外层 fence → json.loads;不像对象 / 解析失败 /
    无实质(summary 或 fix 空)→ None(绝不把垃圾或幻觉当诊断)。"""
    t = _strip_fence(text)
    if not t.startswith("{"):
        return None
    try:
        obj = json.loads(t)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    summary = str(obj.get("summary", "") or "").strip()
    fix = str(obj.get("fix", "") or "").strip()
    if not summary or not fix:
        return None
    risk = obj.get("risk", "needs_approval")
    if risk not in _RISKS:
        risk = "needs_approval"   # 拿不准 → 保守要批准
    return {"summary": summary, "cause": str(obj.get("cause", "") or "").strip(),
            "fix": fix, "risk": risk}


async def diagnose(signal: str, *, gateway: Any, model_ref: str = "") -> OpsDiagnosis:
    """对一个真问题信号跑一次受限 LLM 诊断(无工具)。失败/空/无 gateway → ok=False(退回确定性)。

    **LLM 输出只回给你看 / 作为提议,绝不执行。**
    """
    sig = (signal or "").strip()
    if not sig or gateway is None:
        return OpsDiagnosis(ok=False)
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    out = ""
    try:
        async for ev in gateway.complete(
            [{"role": "user", "content": sig}], [], ref,
            system=SystemPrompt(static=[OPS_SYSTEM]),
        ):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception:
        return OpsDiagnosis(ok=False)
    parsed = parse_diagnosis(out)
    if parsed is None:
        return OpsDiagnosis(ok=False)
    return OpsDiagnosis(ok=True, **parsed)


__all__ = ["OpsDiagnosis", "parse_diagnosis", "diagnose", "OPS_SYSTEM"]
