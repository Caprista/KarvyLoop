"""test_receipt_pipeline — OCR文本 → 约束解码抽取 → 确定性算术求解 引擎(①+② 合流)。

锁:低把握数字被抽取成 null(交给求解器)→ 求解器用票据冗余反解;垃圾返回宁空勿毒;
老 gateway(不支持 response_schema)TypeError 优雅兜底。stub gateway,不烧真模型。
"""
from __future__ import annotations

import asyncio
import json

from karvyloop.receipt_pipeline import RECEIPT_SCHEMA, reconcile_from_ocr


class _TextDelta:  # 结构与 gateway.events.TextDelta 一致(isinstance 在真类上;这里塞真类)
    pass


from karvyloop.gateway.events import TextDelta  # noqa: E402


class _StubGW:
    """回一段预置文本;accepts_schema=False 时对 response_schema 抛 TypeError(模拟老 gateway)。"""
    def __init__(self, payload: str, *, accepts_schema: bool = True):
        self.payload = payload
        self.accepts_schema = accepts_schema
        self.saw_schema = False

    def complete(self, messages, tools, model_ref, *, system=None, response_schema=None, **kw):
        if response_schema is not None:
            if not self.accepts_schema:
                raise TypeError("old gateway: no response_schema")
            self.saw_schema = True

        async def _gen():
            yield TextDelta(text=self.payload)
        return _gen()


_GOOD = json.dumps({
    "doc_type": "receipt", "merchant": "星巴克", "date": None, "currency": "CNY", "tax_id": None,
    "line_items": [
        {"name": "拿铁", "qty": 2, "unit_price": None, "amount": None},   # 低把握 → null
        {"name": "美式", "qty": 1, "unit_price": 30.0, "amount": 30.0},
    ],
    "subtotal": 96.0, "tax": None, "total": 96.0,
})


def test_pipeline_extracts_then_solver_reverse_solves():
    gw = _StubGW(_GOOD)
    out = asyncio.run(reconcile_from_ocr("拿铁⟦?0.4⟧ 美式 30 合计 96", gateway=gw, model_ref="m"))
    assert out["ok"] is True and gw.saw_schema, "应走约束解码"
    assert out["line_items"][0]["amount"] == 66.0, "求解器该反解出拿铁 66"
    assert out["balanced"] is True


def test_pipeline_strips_fence():
    gw = _StubGW("```json\n" + _GOOD + "\n```")
    out = asyncio.run(reconcile_from_ocr("x", gateway=gw, model_ref="m"))
    assert out["ok"] is True and out["line_items"][0]["amount"] == 66.0


def test_pipeline_refuses_garbage_not_poison():
    gw = _StubGW("对不起我不是 JSON,这是一段解释文字")
    out = asyncio.run(reconcile_from_ocr("x", gateway=gw, model_ref="m"))
    assert out["ok"] is False and "解析失败" in out["error"]


def test_pipeline_old_gateway_fallback():
    gw = _StubGW(_GOOD, accepts_schema=False)
    out = asyncio.run(reconcile_from_ocr("x", gateway=gw, model_ref="m"))
    assert out["ok"] is True and out["line_items"][0]["amount"] == 66.0


def test_schema_shape():
    props = RECEIPT_SCHEMA["properties"]
    assert props["subtotal"]["type"] == ["number", "null"], "数字字段必须 nullable(低把握留 null)"
    assert "line_items" in props and props["line_items"]["type"] == "array"
