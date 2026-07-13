"""receipt_pipeline — 报销识别的"确定性地板"引擎:OCR文本 → LLM结构化抽取 → **确定性算术求解** → 结果。

三步各司其职,把"靠模型"的面积压到最小(Hardy 防降级架构):
1. **读**(OCR,已在 ocr_recognize:行重建 + 逐段置信度 ⟦?score⟧);
2. **抽**(LLM,response_schema 约束解码;**规矩:带 ⟦?低把握⟧ 或读不准的数 → 输出 null,别把蒙的数传下来**);
3. **算**(receipt_solver.arithmetic_reconcile,**纯算术、不烧 token、模型再弱也不塌** —— 用票据自身冗余
   把第 2 步留 null 的数反解回来,唯一确定才纠、欠定/冲突只 flag)。

这一层是"识别+结构化输出"的引擎;报销员(chat)是它的对话脸。抽取严格(宁空勿毒:JSON 解析失败返错,
不 prose 硬抽),算术兜底(确定性)。① 逐段置信度 + ② 算术冗余 = 两个独立信号,叠起来定位/纠错才准。
"""
from __future__ import annotations

import json
from typing import Any

from karvyloop.receipt_solver import arithmetic_reconcile

_NUM = ["number", "null"]
_STR = ["string", "null"]

#: 约束解码 schema(数字字段一律 nullable —— 低把握就该留 null 交给求解器,别硬填)。
RECEIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string"},
        "merchant": {"type": _STR},
        "date": {"type": _STR},
        "currency": {"type": _STR},
        "tax_id": {"type": _STR},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": _STR},
                    "qty": {"type": _NUM},
                    "unit_price": {"type": _NUM},
                    "amount": {"type": _NUM},
                },
                "required": ["name", "qty", "unit_price", "amount"],
            },
        },
        "subtotal": {"type": _NUM},
        "tax": {"type": _NUM},
        "total": {"type": _NUM},
    },
    "required": ["doc_type", "merchant", "date", "currency", "tax_id",
                 "line_items", "subtotal", "tax", "total"],
}

_EXTRACT_SYSTEM = (
    "你是票据抽取器。把 OCR 文本抽成给定 JSON schema,只输出 JSON。铁律:\n"
    "1) **带 ⟦?0.NN⟧ 标记的数字(OCR 低把握)或你读不准的数字 → 一律输出 null**,"
    "   绝不把蒙的/存疑的数字填进去 —— 后面有确定性算术求解器会用票据自身的数学关系把能定的反解回来。\n"
    "2) 文字(商户/品名)可按上下文纠明显 OCR 错字;数字宁 null 勿编。\n"
    "3) 不是发票(结账单/小票)→ tax_id 用 null。免费项金额 0。数量取整数份数。\n"
    "只回 JSON,不要解释。"
)


def _strict_json(text: str) -> Any:
    """严格解析 LLM 返回的 JSON(宁空勿毒):只剥最外层 ```fence```,解析失败抛 ValueError。"""
    s = (text or "").strip()
    if s.startswith("```"):
        nl = s.find("\n")
        s = s[nl + 1:] if nl >= 0 else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return json.loads(s)


async def reconcile_from_ocr(ocr_text: str, *, gateway, model_ref: str = "") -> dict[str, Any]:
    """OCR 文本 → 结构化抽取(约束解码)→ 确定性算术求解。返回 solver 输出(含 reconciled/flags/balanced)
    + doc_type/merchant/date 等透传;抽取/解析失败 → {"ok": False, "error": ...},绝不投毒。"""
    from karvyloop.gateway.events import TextDelta
    from karvyloop.gateway.system import SystemPrompt
    sp = SystemPrompt(static=[_EXTRACT_SYSTEM])
    msgs = [{"role": "user", "content": [{"type": "text", "text": ocr_text or ""}]}]
    from karvyloop.gateway.structured import harvest_structured
    try:
        try:
            agen = gateway.complete(msgs, [], model_ref, system=sp, response_schema=RECEIPT_SCHEMA)
        except TypeError:                       # 老 gateway 不支持 response_schema → 无约束兜底
            agen = gateway.complete(msgs, [], model_ref, system=sp)
        # 约束解码正身可能在工具入参(anthropic 方言强制 tool-use)→ 统一收割
        raw = await harvest_structured(agen)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"抽取调用失败:{type(e).__name__}: {e}"}
    try:
        data = _strict_json(raw)
        if not isinstance(data, dict):
            raise ValueError("不是 JSON 对象")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"结构化抽取解析失败(宁空勿毒,不硬抽):{type(e).__name__}"}
    solved = arithmetic_reconcile(data)     # ③ 确定性求解
    solved["ok"] = True
    return solved


__all__ = ["reconcile_from_ocr", "RECEIPT_SCHEMA", "arithmetic_reconcile"]
