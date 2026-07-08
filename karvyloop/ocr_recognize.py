"""ocr_recognize — 图片/扫描件 → 文字(报销识别的前置 OCR 工具,可选件 `[ocr]`)。

选型(2026-07 世界雷达,Hardy 定):**PaddleOCR / PP-OCRv5**(Baidu,Apache-2.0)——80+ 语言、
**CJK 最强**、有 mobile 轻量档能端上跑、on-device 票据不出机器,是"经典 OCR 管线"里本地优先的最优。
文档 VLM(dots.ocr / GOT-OCR2 / Qwen3-VL / PaddleOCR-VL)更准但吃 GPU、绑视觉模型,记档为有算力时的
备选,不做笔记本默认。诚实边界:经典 OCR 在照片/歪斜/手写上会烂 —— 这些如实标注 truncated/flag,
或用户配了视觉模型走那条,不吹"OCR 万能"。

与 audio_transcribe(ASR)同一纪律:
- 通用基建必借:OCR 引擎全借 PaddleOCR(可选依赖 `karvyloop[ocr]`),没装 → ok=False +
  error="missing_dependency" + 明确安装提示,绝不崩。
- 宁空勿毒:坏图/解码异常 → 空文本 + 错误码,绝不把噪声灌进上下文。
- 模型下载诚实:模型首次使用时才下(不塞 wheel);下载/加载失败 → error="ocr_failed" + hint。
- OCR 出来的是**脏文本**(有错字/乱序)—— 交给 receipt_extract 的 LLM 校准 + 算术对账,不当真相。
"""
from __future__ import annotations

import os
from typing import Optional

from karvyloop.file_extract import MAX_EXTRACT_CHARS, ExtractResult

#: 缺依赖时的统一安装提示(与 file_extract / audio_transcribe 同形制)。
OCR_INSTALL_HINT = 'pip install "karvyloop[ocr]"'

#: 语言环境变量;默认 ch(PaddleOCR 的中英混合档,中英票据兼顾)。
LANG_ENV = "KARVYLOOP_OCR_LANG"
DEFAULT_LANG = "ch"

#: 图片 magic(挡伪造扩展名;PDF 不在这,走 file_extract 的 pypdf 文字层,无文字层再当图)。
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"BM", b"GIF87a", b"GIF89a",
                b"RIFF")  # jpg/png/bmp/gif/webp(RIFF)

#: 进程内模型缓存(首次识别加载一次,绝不 per-call 重载)。
_OCR = None


def _magic_ok(data: bytes) -> bool:
    return any(data[:len(m)] == m for m in _IMAGE_MAGIC)


def _load_ocr():
    """懒加载 PaddleOCR;缺依赖抛 ImportError(调用方转 missing_dependency)。"""
    global _OCR
    if _OCR is not None:
        return _OCR
    from paddleocr import PaddleOCR  # 缺 → ImportError
    lang = os.environ.get(LANG_ENV, DEFAULT_LANG)
    _OCR = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
    return _OCR


def recognize_image(data: bytes, *, max_chars: int = MAX_EXTRACT_CHARS) -> ExtractResult:
    """图片字节 → 文字(脏文本,交下游 LLM 校准)。缺依赖/坏图 → 诚实错误,绝不崩、绝不吐垃圾。"""
    if not data or not _magic_ok(data):
        return ExtractResult(ok=False, text="", error="bad_file",
                             hint="不是可识别的图片(magic 不符);伪造扩展名或损坏文件已拒。")
    try:
        import numpy as np  # paddleocr 依赖链里有;单列出来是为了 bytes→array
        from PIL import Image
    except Exception:
        return ExtractResult(ok=False, text="", error="missing_dependency",
                             hint=f"图片 OCR 需要可选依赖:{OCR_INSTALL_HINT}")
    try:
        ocr = _load_ocr()
    except ImportError:
        return ExtractResult(ok=False, text="", error="missing_dependency",
                             hint=f"图片 OCR 需要 PaddleOCR:{OCR_INSTALL_HINT}")
    except Exception as e:  # noqa: BLE001
        return ExtractResult(ok=False, text="", error="ocr_failed",
                             hint=f"OCR 模型加载失败(首次会下模型):{type(e).__name__}")
    try:
        import io
        img = np.array(Image.open(io.BytesIO(data)).convert("RGB"))
        result = ocr.ocr(img, cls=True)
        # PaddleOCR 返回 [[ [box, (text, conf)], ... ]];按行拼文本(脏、留给 LLM 校准)
        lines: list[str] = []
        for page in (result or []):
            for entry in (page or []):
                try:
                    txt = entry[1][0]
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
