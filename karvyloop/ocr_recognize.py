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

#: 预处理阈值:短边 < 此像素 → 判为"太小",上采样把字放大到识别器够用的尺度。
#: 400×298 的 Taco Bell 实测漏字就栽在这;短边到 ~1000 才够印刷体稳读。大图不动(见下)。
_MIN_SIDE_TARGET = 1000
#: 上采样上限:再小的图也不放到超过此短边(挡 OOM/慢,同时避免过度插值反而糊)。
_MAX_UPSCALE = 3.0
#: 歪斜纠正只在估角落在此绝对值内才动手(> 此值多半是估歪了/整图旋转,盲转会更糟 → 跳过)。
_MAX_DESKEW_DEG = 15.0
#: 估出的角 < 此值视作"本来就正",不折腾(避免对好图做无谓旋转引入插值噪声)。
_MIN_DESKEW_DEG = 0.3


def _magic_ok(data: bytes) -> bool:
    return any(data[:len(m)] == m for m in _IMAGE_MAGIC)


def preprocess_for_ocr(data: bytes) -> bytes:
    """OCR 前的**确定性、无模型**图像预处理:救低分辨率/歪斜/低对比的票据照片(报销识别的"腿三")。

    只在**明显有帮助时**动手,好图基本原样透传 —— 不给已经清楚的图添插值噪声:
      1. **小图上采样**(最大杠杆):短边 < ~1000px → INTER_CUBIC 放大到目标短边(不超过 3×),
         把字放大到识别器够用的尺度。400×298 的糊票就栽在这一步。
      2. **灰度 + CLAHE 局部对比增强**:打平热敏纸褪色/反光,弱化不均匀光照,让淡字浮出来。
      3. **纠偏(保守)**:估计文本行的整体倾角;只有 |角| 落在 [0.3°, 15°] 才旋正,
         估不准/角太大(多半估歪或整图旋转)→ 不盲转,原样保留(旋歪比不旋更坏)。

    纯函数、可单测:进图片字节 → 出**重编码后**的图片字节(PNG,无损、下游 magic 仍认)。
    纪律=宁空勿毒:cv2 缺失(没装 [ocr])、解码失败、任何异常 → **原样返回入参 data**,绝不崩、绝不臆造。
    大图/已够大 → 不上采样(按分辨率门控),只做便宜的灰度+对比,几何尺寸不变。
    """
    if not data:
        return data
    try:
        import cv2  # 随 rapidocr 的 opencv-python 依赖来;没装 [ocr] → ImportError → 原样透传
        import numpy as np
    except Exception:  # noqa: BLE001  缺依赖:优雅跳过预处理,让原字节直接进(下游自会报 missing_dependency)
        return data
    try:
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None or img.size == 0:            # 解不出来 → 别动,交给下游诚实报错
            return data
        h, w = img.shape[:2]
        if h <= 0 or w <= 0:
            return data

        # ── 步骤 1:小图上采样(短边门控;大图不动)──────────────────────────
        short = min(h, w)
        if short < _MIN_SIDE_TARGET:
            scale = min(_MIN_SIDE_TARGET / float(short), _MAX_UPSCALE)
            if scale > 1.01:                        # 值得放才放(挡 ~1.0 的无谓重采样)
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

        # ── 步骤 2:灰度 + CLAHE 局部对比(打热敏纸褪色/反光/不均匀光)────────
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        try:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            gray = clahe.apply(gray)
        except Exception:  # noqa: BLE001  某些精简 opencv 无 CLAHE → 退回未增强灰度,不崩
            pass

        # ── 步骤 3:保守纠偏(估不准/角太大 → 不转)──────────────────────────
        angle = _estimate_skew_deg(gray, cv2, np)
        if angle is not None and _MIN_DESKEW_DEG <= abs(angle) <= _MAX_DESKEW_DEG:
            gh, gw = gray.shape[:2]
            center = (gw / 2.0, gh / 2.0)
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            gray = cv2.warpAffine(gray, M, (gw, gh),
                                  flags=cv2.INTER_CUBIC,
                                  borderMode=cv2.BORDER_REPLICATE)

        ok, buf = cv2.imencode(".png", gray)        # 无损重编码;PNG magic 下游仍认
        if not ok:
            return data
        out = buf.tobytes()
        return out if out else data
    except Exception:  # noqa: BLE001  任何一步炸了 → 退回原字节,绝不让预处理成为新的崩溃源
        return data


def _estimate_skew_deg(gray, cv2, np):
    """估文本整体倾角(度,正=需顺时针旋回)。拿不准 → 返回 None(调用方据此跳过纠偏)。

    做法:Otsu 二值 → 取前景像素点云 → minAreaRect 拟合最小外接矩形取其角。
    只作粗估:前景太少/异常 → None,交给调用方"估不准就不转"的纪律。
    """
    try:
        # 前景=深色文字:反相后 Otsu 阈值,让字为白(非零像素)。
        _thr, binimg = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        coords = cv2.findNonZero(binimg)
        if coords is None or len(coords) < 50:      # 前景太少 → 估不出可靠角
            return None
        angle = cv2.minAreaRect(coords)[-1]
        # OpenCV 的角落在 (-90, 0];归一到 [-45, 45] 的"相对正的偏差"。
        if angle < -45:
            angle = 90 + angle
        return float(angle)
    except Exception:  # noqa: BLE001
        return None


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
        # 送进识别器前先做**确定性、无模型**预处理:救低分辨率/歪斜/低对比的糊票(报销"腿三")——
        # 上采样(最大杠杆,救 400×298 那类)+ 灰度/CLAHE + 保守纠偏。任何异常内部已退回原字节,绝不崩。
        prepped = preprocess_for_ocr(data)
        # RapidOCR 直接吃图片字节(内部 cv2 解码);返回 (result, elapse),
        # result = [[box, text, score], ...] 或 None。**行重建 + 逐段置信度标注**(不再丢 box/score),
        # 让下游 LLM 能按行关联"项↔价"、并对低把握段存疑(解"LLM 盲信 OCR"的根)。
        result, _elapse = ocr(prepped)
        text = _layout_text(result)
    except Exception as e:  # noqa: BLE001
        return ExtractResult(ok=False, text="", error="ocr_failed",
                             hint=f"OCR 识别失败:{type(e).__name__}")
    if not text:
        return ExtractResult(ok=False, text="", error="ocr_empty",
                             hint="没识别出文字(可能是空白图/太糊);换清晰图或贴文字。")
    truncated = len(text) > max_chars
    return ExtractResult(ok=True, text=text[:max_chars], error="", truncated=truncated)


__all__ = ["recognize_image", "preprocess_for_ocr", "OCR_INSTALL_HINT"]
