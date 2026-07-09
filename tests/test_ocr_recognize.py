"""test_ocr_recognize — 图片 OCR 前置(RapidOCR;报销识别的图片腿)。

选型 2026-07-09 从 PaddleOCR 换 rapidocr-onnxruntime(paddlepaddle 追不上 Python:3.14 无 wheel)。
本机通常没装 [ocr] → 锁的是**没装时的诚实降级**(宁空勿毒 + 明确安装提示,绝不崩),不真跑引擎。
真识别在 VM/装了 [ocr] 的机器上真机验(已在 Python 3.14 上 rapidocr 读通 'Starbucks/96.00')。
"""
from __future__ import annotations

from karvyloop.ocr_recognize import OCR_INSTALL_HINT, _layout_text, recognize_image

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _box(x, y, w=40, h=20):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


# ---- ①「别丢 box/score」:行重建 + 逐段置信度标注(解 LLM 盲信 OCR 的根)----

def test_layout_reconstructs_rows_item_and_price_on_one_line():
    """品名与价格本在同一视觉行(y 相近、x 分开),RapidOCR 常拆两段 → 重建后拼一行,
    下游 LLM 才能按行关联"项↔价",不靠猜。"""
    result = [
        [_box(10, 8), "养生海参盅", 0.97],
        [_box(200, 8), "348.00", 0.96],       # 同一行(y≈)、更靠右
        [_box(10, 60), "合计", 0.95],
        [_box(200, 60), "933.00", 0.95],      # 下一行
    ]
    out = _layout_text(result)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert "养生海参盅" in lines[0] and "348.00" in lines[0], f"项与价该同一行: {lines}"
    assert "合计" in lines[1] and "933.00" in lines[1], f"下一行该是合计: {lines}"


def test_layout_marks_low_confidence_and_adds_legend():
    """把握 < 阈值的段包 ⟦?0.NN⟧ + 顶部 legend;高把握段不标 —— 让 LLM 按把握信任,不盲信。"""
    result = [
        [_box(10, 8), "拿铁", 0.98],
        [_box(200, 8), "865", 0.55],          # 低把握的价 → 该被标
    ]
    out = _layout_text(result)
    assert "865⟦?0.55⟧" in out, "低把握段必须带置信度标注"
    assert "拿铁" in out and "拿铁⟦" not in out, "高把握段不该被标"
    assert "OCR 置信度提示" in out, "有低把握段时必须给 legend 让 LLM 懂标记含义"


def test_layout_no_legend_when_all_confident():
    """全高把握 → 干净文本,不加噪(向后兼容清晰图)。"""
    out = _layout_text([[_box(10, 8), "TOTAL", 0.99], [_box(200, 8), "12.65", 0.98]])
    assert "OCR 置信度提示" not in out and "⟦?" not in out


def test_layout_degrades_without_geometry():
    """结构异常(拿不到框)→ 退化为纯文本,绝不崩。"""
    out = _layout_text([["not-a-box", "星巴克", 0.9], [None, "96.00", 0.9]])
    assert "星巴克" in out and "96.00" in out


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
