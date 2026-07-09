"""test_ocr_recognize — 图片 OCR 前置(RapidOCR;报销识别的图片腿)。

选型 2026-07-09 从 PaddleOCR 换 rapidocr-onnxruntime(paddlepaddle 追不上 Python:3.14 无 wheel)。
本机通常没装 [ocr] → 锁的是**没装时的诚实降级**(宁空勿毒 + 明确安装提示,绝不崩),不真跑引擎。
真识别在 VM/装了 [ocr] 的机器上真机验(已在 Python 3.14 上 rapidocr 读通 'Starbucks/96.00')。
"""
from __future__ import annotations

from karvyloop.ocr_recognize import OCR_INSTALL_HINT, recognize_image

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_rejects_non_image_magic():
    """伪造扩展名/坏图(magic 不符)→ bad_file + 空文本(宁空勿毒,绝不吐垃圾)。"""
    r = recognize_image(b"this is definitely not an image")
    assert r.ok is False and r.text == "" and r.error == "bad_file"


def test_empty_data_rejected():
    r = recognize_image(b"")
    assert r.ok is False and r.text == "" and r.error == "bad_file"


def test_missing_dep_is_honest_not_crash():
    """真图 magic 但没装 rapidocr → missing_dependency + 指向 [ocr] 安装提示,绝不崩、绝不假装识别。

    (装了 [ocr] 的机器上这条会真跑引擎;本机没装时走这条诚实降级 —— 用 importorskip 语义:
     若恰好装了,断言就放宽为'要么真识别要么诚实错',反正不许崩。)"""
    fake_png = _PNG_MAGIC + b"\x00" * 64
    r = recognize_image(fake_png)
    assert r.ok is False, "坏 PNG 体不该 ok"
    assert r.error in ("missing_dependency", "ocr_failed", "ocr_empty", "bad_file")
    if r.error == "missing_dependency":
        assert "karvyloop[ocr]" in OCR_INSTALL_HINT and "karvyloop[ocr]" in r.hint
