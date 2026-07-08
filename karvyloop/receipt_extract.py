"""receipt_extract — 发票 / 小票 → **结构化输出**(报销工具的识别核;Hardy 2026-07-08 定范围)。

范围就到"识别 + 结构化"(不是完整报销助手:提交/追踪/对账/提醒都是后话)。

流水线(Hardy 的模型 + 世界雷达):
  图片/PDF扫描 → **OCR 工具**(ocr_recognize,PaddleOCR `[ocr]` 可选件)→ 脏文本
                                                              ↘ 文字稿/PDF文字层/粘贴 直接进
  脏文本 → **LLM 结构化抽取**(约束解码 response_schema)—— 顺手用上下文纠 OCR 错字(O↔0 / l↔1 /
           小数点错位 / 行乱序),**拿不准留 null 绝不编**(宁空勿毒:错金额比空更坏)
  抽完 → **算术对账**(世界雷达 anti-hallucination 招:行项之和 ≟ 总额,对不上打 flag)——确定性,
         不信 LLM 自报的对错

诚实边界:①图片依赖 `[ocr]`(PaddleOCR,on-device)或用户视觉模型,不吹"本地万能";②这里**不判报销
政策/科目**(那要用户的政策库,是 assistant 层);③web_search 核实存疑实体(商户/地址)留作可选钩子。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

#: 抽取 schema(约束解码目标)。可空字段用 [type,null];宁空勿毒 = 拿不准填 null,不编。
RECEIPT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "doc_type": {"type": "string",
                     "enum": ["receipt", "invoice", "shopping_list", "itinerary", "other"]},
        "merchant": {"type": ["string", "null"]},      # 商户 / 开票方
        "date": {"type": ["string", "null"]},           # 票面日期(原样或 ISO;拿不准 null)
        "currency": {"type": ["string", "null"]},       # CNY / USD / …
        "total": {"type": ["number", "null"]},          # 价税合计 / 总额
        "tax_id": {"type": ["string", "null"]},         # 税号
        "payee": {"type": ["string", "null"]},          # 抬头
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "qty": {"type": ["number", "null"]},
                    "amount": {"type": ["number", "null"]},
                },
                "required": ["name", "qty", "amount"],
            },
        },
        "category_hint": {"type": ["string", "null"]},  # 票面看着像什么(交通/餐饮/办公…),**非**报销政策裁决
    },
    "required": ["doc_type", "merchant", "date", "currency", "total",
                 "tax_id", "payee", "line_items", "category_hint"],
}

_SYSTEM = (
    "你是发票/小票识别器。输入是一段票据文本,**常来自 OCR,可能有错字、字符混淆、行乱序、"
    "金额错位**。你的活:\n"
    "1) 判 doc_type:receipt(小票)/ invoice(发票)/ shopping_list(购物清单)/ itinerary(行程单)/ other;\n"
    "2) 抽字段:商户、日期、币种、总额、税号、抬头、行项(名称/数量/金额);\n"
    "3) **用上下文纠常见 OCR 错**(O↔0、l/I↔1、乱序数字、小数点错位)—— 但**只在有把握时纠**;\n"
    "4) **拿不准的字段一律 null,绝不编造金额/税号/日期**(宁空勿毒:一个错金额比留空更坏);\n"
    "5) category_hint 只写票面看着像什么(交通/餐饮/办公…),**不判能不能报销**(那不是你的活)。\n"
    "严格按 schema 输出 JSON,不要 JSON 以外任何字。"
)


def _strict_json(text: str) -> Optional[dict]:
    """严格解析 LLM 输出 → dict;剥围栏,失败返 None(宁空勿毒,绝不半抽)。"""
    t = (text or "").strip()
    if not t:
        return None
    lines = t.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    if not t.startswith("{"):
        m = re.search(r"\{.*\}", t, re.S)   # 散文里捞第一个完整对象
        if not m:
            return None
        t = m.group(0)
    try:
        d = json.loads(t)
        return d if isinstance(d, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def arithmetic_flags(data: dict) -> list[str]:
    """确定性对账(世界雷达 anti-hallucination):行项之和 ≟ 总额 + 缺字段。**不信 LLM 自报对错**。"""
    flags: list[str] = []
    total = _num(data.get("total"))
    items = data.get("line_items") if isinstance(data.get("line_items"), list) else []
    amounts = [a for a in (_num(it.get("amount")) for it in items if isinstance(it, dict)) if a is not None]
    if total is None:
        flags.append("missing_total")
    if not items:
        flags.append("no_line_items")
    if total is not None and amounts:
        s = sum(amounts)
        # 允许 1% 或 0.02 的容差(税/四舍五入);超出 = 疑似 OCR 抄错或漏项
        if abs(s - total) > max(0.02, abs(total) * 0.01):
            flags.append("sum_mismatch")
    merch = data.get("merchant")
    if not (isinstance(merch, str) and merch.strip()):
        flags.append("missing_merchant")
    if not data.get("date"):
        flags.append("missing_date")
    return flags


async def extract_receipt(text: str, *, gateway: Any, model_ref: str = "") -> dict:
    """脏票据文本 → 结构化 dict(约束解码抽取 + 确定性算术对账 flag)。

    返回 {..schema字段.., "flags": [...], "ok": bool}。ok=False = LLM 没吐出可解析结构(宁空勿毒:
    返回空壳 + error,绝不硬编)。**不做报销政策裁决**(范围外)。
    """
    if gateway is None:
        return {"ok": False, "error": "no_gateway", "flags": ["no_gateway"]}
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    from karvyloop.llm.token_ledger import token_source
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref or ""
    msgs = [{"role": "user", "content": (text or "")[:16000]}]
    sp = SystemPrompt(static=[_SYSTEM])
    out = ""
    try:
        with token_source("receipt_extract"):
            try:
                stream = gateway.complete(msgs, [], ref, system=sp, response_schema=RECEIPT_SCHEMA)
            except TypeError:
                stream = gateway.complete(msgs, [], ref, system=sp)   # 老 gateway 不认 → 退普通
            async for ev in stream:
                if type(ev).__name__ == "TextDelta":
                    out += getattr(ev, "text", "")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"llm_failed: {e}", "flags": ["llm_failed"]}
    data = _strict_json(out)
    if data is None:
        return {"ok": False, "error": "unparseable", "flags": ["unparseable"]}
    data["flags"] = arithmetic_flags(data)   # 确定性对账,覆盖任何 LLM 自报
    data["ok"] = True
    return data


__all__ = ["RECEIPT_SCHEMA", "extract_receipt", "arithmetic_flags"]
