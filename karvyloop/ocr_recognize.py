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

#: 低于此把握的识别段 → 标注 ⟦?score⟧ 交给 LLM 存疑(不再盲喂纯文本让它全信)。
#: RapidOCR 清晰印刷体常 0.9+,糊/歪/反光段掉到 0.5-0.8 —— 正是要 LLM 别当准的那些。
_CONF_THRESHOLD = 0.80


def _magic_ok(data: bytes) -> bool:
    return any(data[:len(m)] == m for m in _IMAGE_MAGIC)


def _layout_text(result, *, conf_threshold: float = _CONF_THRESHOLD) -> str:
    """RapidOCR 结果 → 文本,但**不丢 box/score**(解决"LLM 盲信 OCR"的根因):

    - **行重建**:按识别框的纵向位置分行、行内按横向排 —— 票据里"品名……价格"本在同一视觉行,
      RapidOCR 却常拆成两段;重建后同一行拼一起,下游 LLM 能按行关联"项↔价",不靠猜。
    - **逐段置信度标注**:把握 < 阈值的段包 ⟦?0.NN⟧,并在顶部加一句 legend 说明 —— LLM 据此
      "该疑的疑"(优先怀疑、用上下文/算术校正、拿不准就 flag),信任度从"盲信"变"按把握信"。
    纯函数(不碰引擎),可单测:喂合成 [box, text, score] 断言分行+低把握标注+legend。
    拿不到几何(结构异常)→ 优雅退化为按原顺序纯文本,绝不崩。
    """
    segs: list[dict] = []
    for entry in (result or []):
        try:
            box, text, score = entry[0], entry[1], float(entry[2])
        except (IndexError, TypeError, ValueError):
            try:                       # 退化:至少把文本捞出来(几何不可用)
                t = str(entry[1]).strip()
                if t:
                    segs.append({"text": t, "score": 1.0, "yc": None, "xl": 0.0, "h": 0.0})
            except Exception:
                pass
            continue
        text = str(text).strip()
        if not text:
            continue
        try:
            ys = [float(p[1]) for p in box]
            xs = [float(p[0]) for p in box]
            segs.append({"text": text, "score": score,
                         "yc": sum(ys) / len(ys), "xl": min(xs), "h": max(ys) - min(ys)})
        except (TypeError, ValueError, IndexError):
            segs.append({"text": text, "score": score, "yc": None, "xl": 0.0, "h": 0.0})
    if not segs:
        return ""

    any_low = False

    def _fmt(s: dict) -> str:
        nonlocal any_low
        if s["score"] < conf_threshold:
            any_low = True
            return f"{s['text']}⟦?{s['score']:.2f}⟧"
        return s["text"]

    have_geom = all(s["yc"] is not None for s in segs)
    if have_geom:
        heights = sorted(s["h"] for s in segs if s["h"] > 0)
        H = heights[len(heights) // 2] if heights else 0.0
        tol = max(H * 0.5, 6.0)                       # 同一视觉行的纵向容差
        segs.sort(key=lambda s: s["yc"])
        rows: list[list[dict]] = [[segs[0]]]
        for s in segs[1:]:
            if abs(s["yc"] - rows[-1][-1]["yc"]) <= tol:
                rows[-1].append(s)
            else:
                rows.append([s])
        lines = ["  ".join(_fmt(s) for s in sorted(row, key=lambda s: s["xl"])) for row in rows]
    else:
        lines = [_fmt(s) for s in segs]

    text = "\n".join(lines).strip()
    if any_low:
        text = ("[OCR 置信度提示:带 ⟦?0.NN⟧ 的段 OCR 把握低(0–1,越低越可能读错)——请优先怀疑这些,"
                "用上下文/算术校正,拿不准就 flag,别当准;没标的是 OCR 有把握的。]\n" + text)
    return text


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
        # result = [[box, text, score], ...] 或 None。**行重建 + 逐段置信度标注**(不再丢 box/score),
        # 让下游 LLM 能按行关联"项↔价"、并对低把握段存疑(解"LLM 盲信 OCR"的根)。
        result, _elapse = ocr(data)
        text = _layout_text(result)
    except Exception as e:  # noqa: BLE001
        return ExtractResult(ok=False, text="", error="ocr_failed",
                             hint=f"OCR 识别失败:{type(e).__name__}")
    if not text:
        return ExtractResult(ok=False, text="", error="ocr_empty",
                             hint="没识别出文字(可能是空白图/太糊);换清晰图或贴文字。")
    truncated = len(text) > max_chars
    return ExtractResult(ok=True, text=text[:max_chars], error="", truncated=truncated)


__all__ = ["recognize_image", "OCR_INSTALL_HINT"]
