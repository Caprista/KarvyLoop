"""test_ocr_recognize — 图片 OCR 前置(RapidOCR;报销识别的图片腿)。

选型 2026-07-09 从 PaddleOCR 换 rapidocr-onnxruntime(paddlepaddle 追不上 Python:3.14 无 wheel)。
本机通常没装 [ocr] → 锁的是**没装时的诚实降级**(宁空勿毒 + 明确安装提示,绝不崩),不真跑引擎。
真识别在 VM/装了 [ocr] 的机器上真机验(已在 Python 3.14 上 rapidocr 读通 'Starbucks/96.00')。
"""
from __future__ import annotations

import pytest

from karvyloop.ocr_recognize import (
    OCR_INSTALL_HINT,
    _layout_text,
    preprocess_for_ocr,
    recognize_image,
)

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


# ---- ② OCR 前置预处理:救糊/歪/低对比票据(报销"腿三")----
#
# 纪律:cv2 缺失(没装 [ocr])或坏输入 → **原样返回入参**,绝不崩。这些"降级"断言在**任何机器上**都要过。
# 真正的几何变换(上采样/纠偏)只有装了 cv2 才验得了 → 用 importorskip 语义门控,没装就跳过那几条。

def _has_cv2():
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


# --- 降级路径:随处可跑,锁"宁空勿毒 + 绝不崩" ---

def test_preprocess_empty_returns_input():
    assert preprocess_for_ocr(b"") == b""


def test_preprocess_none_does_not_crash():
    """None 传进来(理论上类型不对)也不许炸 —— 出错就退回入参。"""
    assert preprocess_for_ocr(None) is None


def test_preprocess_garbage_returns_input_unchanged():
    """非图片垃圾字节:cv2 缺失走跳过、装了则 imdecode 失败 —— 两条路都必须原样退回,绝不崩。"""
    garbage = b"this is definitely not an image, not even close"
    assert preprocess_for_ocr(garbage) == garbage


def test_preprocess_truncated_png_returns_input():
    """有 PNG magic 但体是垃圾(解不出图)→ 原样退回,交给下游诚实报错。"""
    junk_png = _PNG_MAGIC + b"\x00" * 64
    assert preprocess_for_ocr(junk_png) == junk_png


def test_preprocess_never_raises_on_random_bytes():
    """一堆随机/边界输入都不许抛异常(预处理绝不能成为新的崩溃源)。"""
    for blob in (b"\x00", b"\xff" * 10, _PNG_MAGIC, b"\xff\xd8\xff" + b"\x11" * 30):
        out = preprocess_for_ocr(blob)
        assert isinstance(out, bytes)


# --- 真变换路径:只在装了 cv2 时验(几何/格式确定性) ---

def _encode_png(img):
    import cv2
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _decode_shape(data: bytes):
    import cv2
    import numpy as np
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    assert img is not None
    return img.shape[:2]  # (h, w)


def test_preprocess_upscales_small_image():
    """400×298 的糊票那一类:短边 < 1000 → 被上采样到短边 ≈ 1000(不超过 3×),字够大给识别器。"""
    if not _has_cv2():
        pytest.skip("cv2 未装([ocr] 未安装)——真上采样在装了 [ocr] 的机器/VM 上验")
    import numpy as np
    small = np.full((298, 400, 3), 255, dtype=np.uint8)      # 白底
    small[140:160, 40:360] = 0                                # 画一道黑"文字"条,给纠偏/前景一点料
    out = preprocess_for_ocr(_encode_png(small))
    h, w = _decode_shape(out)
    assert min(h, w) >= 900, f"小图应被上采样到短边≈1000,得到 {w}x{h}"
    # 不该超过 3× 上限(298*3=894 短边;宽随比例)——挡过度插值/OOM。
    assert min(h, w) <= 298 * 3 + 5, f"上采样不该超过 3×,得到 {w}x{h}"
    assert out[: len(_PNG_MAGIC)] == _PNG_MAGIC, "重编码应为 PNG(下游 magic 仍认)"


def test_preprocess_leaves_large_image_geometry_unchanged():
    """已够大的图(短边 ≥ 1000)不上采样:几何尺寸原样(只做便宜的灰度/对比),不添插值噪声。"""
    if not _has_cv2():
        pytest.skip("cv2 未装([ocr] 未安装)——大图门控在装了 [ocr] 的机器/VM 上验")
    import numpy as np
    big = np.full((1200, 1600, 3), 255, dtype=np.uint8)
    big[600:620, 200:1400] = 0
    out = preprocess_for_ocr(_encode_png(big))
    h, w = _decode_shape(out)
    assert (h, w) == (1200, 1600), f"大图几何不该变,得到 {w}x{h}"


def test_preprocess_output_is_decodable_image():
    """输出必须仍是可解码图片(格式确定性)——预处理不能把下游能吃的图搞成 magic 都不认的东西。"""
    if not _has_cv2():
        pytest.skip("cv2 未装([ocr] 未安装)")
    import numpy as np
    img = np.full((500, 700, 3), 200, dtype=np.uint8)
    img[240:260, 100:600] = 10
    out = preprocess_for_ocr(_encode_png(img))
    h, w = _decode_shape(out)                                 # 解得开即通过
    assert h > 0 and w > 0
