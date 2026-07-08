"""test_receipt_extract — 发票/小票 → 结构化输出(报销识别核)。

不用 key:stub gateway 喂定死回复,验约束解码接线 + 严格解析(宁空勿毒)+ **确定性算术对账**
(世界雷达 anti-hallucination:行项之和≟总额,不信 LLM 自报)。OCR 走 graceful degrade 验。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.receipt_extract import arithmetic_flags, extract_receipt, RECEIPT_SCHEMA  # noqa: E402


class TextDelta:               # 名字必须是 TextDelta(抽取器按 type().__name__ 认)
    def __init__(self, text): self.text = text


class _StubGW:
    """假 gateway:complete 吐定死回复;accepts_schema=False → 模拟老 gateway 触发 TypeError 兜底。"""
    def __init__(self, reply: str, accepts_schema: bool = True):
        self._reply = reply
        self._accepts = accepts_schema

    def resolve_model(self, scope):  # noqa: ANN001
        return "stub-model"

    def complete(self, msgs, tools, ref, system=None, response_schema=None):  # noqa: ANN001
        if response_schema is not None and not self._accepts:
            raise TypeError("old gateway: no response_schema")   # 触发调用方 except TypeError 退普通

        async def _gen():
            yield TextDelta(self._reply)
        return _gen()


# ---- 1. 算术对账(确定性,不碰 LLM)----

def test_arithmetic_flags_sum_ok():
    d = {"merchant": "沃尔玛", "date": "2026-03-01", "total": 30.0,
         "line_items": [{"name": "牛奶", "amount": 12.0}, {"name": "面包", "amount": 18.0}]}
    assert "sum_mismatch" not in arithmetic_flags(d)


def test_arithmetic_flags_sum_mismatch():
    d = {"merchant": "沃尔玛", "date": "2026-03-01", "total": 100.0,
         "line_items": [{"name": "牛奶", "amount": 12.0}, {"name": "面包", "amount": 18.0}]}
    assert "sum_mismatch" in arithmetic_flags(d)   # 12+18=30 ≠ 100 → 疑似 OCR 抄错/漏项


def test_arithmetic_flags_missing_fields():
    f = arithmetic_flags({"total": None, "line_items": [], "merchant": "", "date": None})
    assert "missing_total" in f and "no_line_items" in f
    assert "missing_merchant" in f and "missing_date" in f


# ---- 2. extract_receipt(约束解码接线 + 宁空勿毒解析)----

_GOOD = json.dumps({
    "doc_type": "receipt", "merchant": "星巴克", "date": "2026-03-15", "currency": "CNY",
    "total": 66.0, "tax_id": None, "payee": None,
    "line_items": [{"name": "拿铁", "qty": 2, "amount": 66.0}], "category_hint": "餐饮",
}, ensure_ascii=False)


def test_extract_good():
    gw = _StubGW(_GOOD)
    d = asyncio.run(extract_receipt("星巴克 拿铁x2 66.00", gateway=gw))
    assert d["ok"] is True and d["doc_type"] == "receipt" and d["merchant"] == "星巴克"
    assert d["total"] == 66.0 and d["flags"] == []          # 66==66,零 flag


def test_extract_fenced_json():
    gw = _StubGW("```json\n" + _GOOD + "\n```")
    d = asyncio.run(extract_receipt("x", gateway=gw))
    assert d["ok"] is True and d["merchant"] == "星巴克"      # 剥围栏


def test_extract_garbage_is_empty_not_poison():
    gw = _StubGW("这不是 JSON,是一段废话")
    d = asyncio.run(extract_receipt("x", gateway=gw))
    assert d["ok"] is False and d["error"] == "unparseable"   # 宁空勿毒:不硬编


def test_extract_arithmetic_flag_wired():
    bad = json.dumps({"doc_type": "receipt", "merchant": "A", "date": "2026-01-01",
                      "currency": "CNY", "total": 999.0, "tax_id": None, "payee": None,
                      "line_items": [{"name": "x", "qty": 1, "amount": 10.0}],
                      "category_hint": None}, ensure_ascii=False)
    d = asyncio.run(extract_receipt("x", gateway=_StubGW(bad)))
    assert d["ok"] is True and "sum_mismatch" in d["flags"]   # 对账覆盖 LLM 自报


def test_extract_old_gateway_typeerror_fallback():
    gw = _StubGW(_GOOD, accepts_schema=False)                # 老 gateway 不认 response_schema
    d = asyncio.run(extract_receipt("x", gateway=gw))
    assert d["ok"] is True and d["merchant"] == "星巴克"      # 退普通 complete,照样跑


def test_extract_no_gateway():
    d = asyncio.run(extract_receipt("x", gateway=None))
    assert d["ok"] is False and d["error"] == "no_gateway"


def test_schema_shape():
    assert RECEIPT_SCHEMA["type"] == "object"
    for k in ("doc_type", "merchant", "total", "line_items", "category_hint"):
        assert k in RECEIPT_SCHEMA["properties"]


# ---- 3. OCR graceful degrade(没装 paddleocr 也不崩)----

def test_ocr_bad_magic_rejected():
    from karvyloop.ocr_recognize import recognize_image
    r = recognize_image(b"not-an-image")
    assert r.ok is False and r.error == "bad_file"           # 伪造/坏图 → 拒,不吐垃圾


def test_ocr_valid_image_missing_dep_is_honest():
    """真 JPG magic 但没装 PaddleOCR → 诚实 missing_dependency + 安装提示,绝不崩。"""
    from karvyloop.ocr_recognize import recognize_image
    fake_jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 64            # JPG magic 头
    r = recognize_image(fake_jpg)
    assert r.ok is False and r.error in ("missing_dependency", "ocr_failed", "ocr_empty")
    if r.error == "missing_dependency":
        assert "karvyloop[ocr]" in r.hint
