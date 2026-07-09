"""ocr_recognize — 图片/扫描件 → 文字(报销识别的前置 OCR 工具,可选件 `[ocr]`)。

选型(2026-07-09 Hardy 纠偏后重定):**rapidocr-onnxruntime**(RapidOCR,Apache-2.0)。
病根复盘:先前选 PaddleOCR/paddlepaddle,paddlepaddle **常年追不上 Python**(实测 Python 3.14 上
`No matching distribution` —— 一个逼你"别升/降 Python 版本"的依赖 = 尾巴摇狗,选型硬伤)。
RapidOCR 用 **ONNX Runtime**(微软维护、新 Python wheel 跟得快)跑**同款 PP-OCR 模型**:
- **跟得上 Python**:实测 Python 3.14 上 onnxruntime/opencv/pyclipper 都有 cp314 wheel,直装即通;
- **更轻、模型随包**:不拖 paddlepaddle 训练框架;OCR 模型打进 wheel,**不另外下模型**;
- **CJK 最强档同源**:RapidOCR 默认就是 PP-OCRv 中英混合模型,票据/中英兼顾,on-device 不出机器。
诚实边界:经典 OCR 在照片/歪斜/手写上会烂 —— 如实标注,或用户配了视觉模型走那条。

与 audio_transcribe(ASR)同一纪律:
- 通用基建必借:OCR 引擎全借 RapidOCR(可选依赖 `karvyloop[ocr]`),没装 → ok=False +
  error="missing_dependency" + 明确安装提示,绝不崩。
- 宁空勿毒:坏图/解码异常 → 空文本 + 错误码,绝不把噪声灌进上下文。
- OCR 出来的是**脏文本**(有错字/乱序)—— 不当真相:交给下游角色(如报销员)的 prose 方法用 LLM
  校准(O↔0/l↔1/乱序)+ 算术对账(行项之和≟总额)。识别归识别、抽取归模型,不在这里塞 bespoke 抽取。
"""
from __future__ import annotations

from karvyloop.file_extract import MAX_EXTRACT_CHARS, ExtractResult

#: 缺依赖时的统一安装提示(与 file_extract / audio_transcribe 同形制)。
OCR_INSTALL_HINT = 'pip install "karvyloop[ocr]"'

#: 图片 magic(挡伪造扩展名;PDF 不在这,走 file_extract 的 pypdf 文字层,无文字层再当图)。
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"BM", b"GIF87a", b"GIF89a",
                b"RIFF")  # jpg/png/bmp/gif/webp(RIFF)

#: 进程内引擎缓存(首次识别加载一次,绝不 per-call 重载)。
_OCR = None


def _magic_ok(data: bytes) -> bool:
    return any(data[:len(m)] == m for m in _IMAGE_MAGIC)


def _load_ocr():
    """懒加载 RapidOCR;缺依赖抛 ImportError(调用方转 missing_dependency)。模型随包,不下网。"""
    global _OCR
    if _OCR is not None:
        return _OCR
    from rapidocr_onnxruntime import RapidOCR  # 缺 → ImportError
    _OCR = RapidOCR()
    return _OCR


def recognize_image(data: bytes, *, max_chars: int = MAX_EXTRACT_CHARS) -> ExtractResult:
    """图片字节 → 文字(脏文本,交下游 LLM 校准)。缺依赖/坏图 → 诚实错误,绝不崩、绝不吐垃圾。"""
    if not data or not _magic_ok(data):
        return ExtractResult(ok=False, text="", error="bad_file",
                             hint="不是可识别的图片(magic 不符);伪造扩展名或损坏文件已拒。")
    try:
        ocr = _load_ocr()
    except ImportError:
        return ExtractResult(ok=False, text="", error="missing_dependency",
                             hint=f"图片 OCR 需要可选依赖(RapidOCR):{OCR_INSTALL_HINT}")
    except Exception as e:  # noqa: BLE001
        return ExtractResult(ok=False, text="", error="ocr_failed",
                             hint=f"OCR 引擎加载失败:{type(e).__name__}")
    try:
        # RapidOCR 直接吃图片字节(内部 cv2 解码);返回 (result, elapse),
        # result = [[box, text, score], ...] 或 None(没识别出东西)。按行拼(脏、留给 LLM 校准)。
        result, _elapse = ocr(data)
        lines: list[str] = []
        for entry in (result or []):
            try:
                txt = entry[1]
                if txt and str(txt).strip():
                    lines.append(str(txt).strip())
            except (IndexError, TypeError):
                continue
        text = "\n".join(lines).strip()
    except Exception as e:  # noqa: BLE001
        return ExtractResult(ok=False, text="", error="ocr_failed",
                             hint=f"OCR 识别失败:{type(e).__name__}")
    if not text:
        return ExtractResult(ok=False, text="", error="ocr_empty",
                             hint="没识别出文字(可能是空白图/太糊);换清晰图或贴文字。")
    truncated = len(text) > max_chars
    return ExtractResult(ok=True, text=text[:max_chars], error="", truncated=truncated)


__all__ = ["recognize_image", "OCR_INSTALL_HINT"]
