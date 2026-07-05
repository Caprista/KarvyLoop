"""audio_transcribe — 本地 ASR:音频(mp3 / wav / m4a)→ 文字稿,全程不出机器。

file_extract 的音频分支(`extract_kind` → "audio" → 这里),与 PDF/docx/xlsx 走**同一条**
附件产线:console 预览 /api/files/view、read_file 工具、meeting-notes 通道都免新接线。

选型(docs/64-asr-selection,2026-07 世界雷达):**faster-whisper**(SYSTRAN,MIT,
CTranslate2 后端)—— 纯 pip 可装(自带 PyAV 解码,不要求系统 ffmpeg)、CPU int8 可跑、
中英同一模型、维护活跃。中文更强的 SenseVoice 路线(自定义 model license + 手动下模型)
记档为备选,不做默认。

设计原则(与 file_extract 同一纪律):
- **通用基建必借**:ASR 引擎全借 faster-whisper(可选依赖 ``karvyloop[asr]``),
  没装 → ``ok=False`` + ``error="missing_dependency"`` + 明确安装提示,绝不崩。
- **宁空勿毒**:坏音频 / 伪造扩展名(magic 不符)/ 解码异常 → 空文本 + 明确错误码,
  绝不把噪声转写垃圾灌进上下文。
- **模型下载诚实**:模型**首次使用时**才从 Hugging Face 下载(默认 small ≈ 480 MB),
  不塞进 wheel;下载/加载失败 → ``error="asr_failed"`` + 人可读 hint,不冒充"文件坏了"。
- **截断同一条线**:转写文本封顶 max_chars(与 file_extract 同默认),超限明示 truncated。

型号:环境变量 ``KARVYLOOP_ASR_MODEL``(默认 ``small``;tiny≈75MB / base≈145MB /
small≈480MB / medium≈1.5GB / large-v3≈3GB,或任意 CTranslate2 模型目录/HF repo id)。
中文吃重的用户建议 ``small`` 起步、嫌错多换 ``medium``——诚实说:模型越小中文越糙。
"""
from __future__ import annotations

import io
import os

from karvyloop.file_extract import ExtractResult, MAX_EXTRACT_CHARS

#: 缺依赖时的统一安装提示(与 file_extract.INSTALL_HINT 同形制)。
ASR_INSTALL_HINT = 'pip install "karvyloop[asr]"'

#: 模型型号环境变量;默认 small(CPU int8 可跑、中英兼顾的最低可用档)。
MODEL_ENV = "KARVYLOOP_ASR_MODEL"
DEFAULT_MODEL = "small"

#: 进程内模型缓存(首次转写加载一次;模型加载是秒级~分钟级,绝不 per-call 重载)。
_MODEL = None


def _magic_ok(data: bytes) -> bool:
    """音频容器 magic:ID3/MPEG 帧同步(mp3)、RIFF+WAVE(wav)、ftyp 盒(m4a/mp4)。
    与 file_extract 同一防线语义:magic 不符 → 解码器还没碰到字节就被拒。"""
    if len(data) < 12:
        return False
    if data[:3] == b"ID3":
        return True
    if data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:   # MPEG audio frame sync
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return True
    if data[4:8] == b"ftyp":                            # MP4 家族容器(m4a)
        return True
    return False


def _load_model():
    """加载(并缓存)faster-whisper 模型。ImportError 原样上抛 → 调用方译成 missing_dependency;
    其余异常(模型下载失败/坏模型名)也上抛 → 译成 asr_failed。测试可整体 monkeypatch。"""
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        name = (os.environ.get(MODEL_ENV, "") or "").strip() or DEFAULT_MODEL
        # CPU + int8:没 GPU 是常态;有 GPU 的用户体感差异小于配置踩坑成本,不自动探测。
        _MODEL = WhisperModel(name, device="cpu", compute_type="int8")
    return _MODEL


def _mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def transcribe(data: bytes, *, max_chars: int = MAX_EXTRACT_CHARS) -> ExtractResult:
    """音频字节 → 文字稿(带 ``[mm:ss]`` 段落时间戳,可被纪要引用)。唯一对外入口。

    返回 file_extract.ExtractResult(kind="audio"),错误码:
    ``missing_dependency``(没装 [asr])/ ``bad_file``(magic 不符或解码失败)/
    ``asr_failed``(依赖在、模型下载或加载失败 —— 不是文件的错,hint 说人话)。
    """
    if not _magic_ok(data):
        return ExtractResult(False, "audio", error="bad_file",
                             hint="不是可识别的音频容器(mp3/wav/m4a)或文件已损坏")
    try:
        model = _load_model()
    except ImportError:
        return ExtractResult(False, "audio", error="missing_dependency",
                             hint=f"faster-whisper 未安装 — {ASR_INSTALL_HINT}")
    except Exception as e:
        # 依赖在,但模型没到位(首次下载断网/型号名打错)——不是文件坏,别冒充 bad_file
        return ExtractResult(False, "audio", error="asr_failed",
                             hint=f"语音模型加载失败(首次使用需联网下载模型): {type(e).__name__}")
    try:
        segments, _info = model.transcribe(io.BytesIO(data), vad_filter=True)
        parts: list[str] = []
        total, truncated = 0, False
        for seg in segments:
            txt = (getattr(seg, "text", "") or "").strip()
            if not txt:
                continue
            parts.append(f"[{_mmss(getattr(seg, 'start', 0.0) or 0.0)}] {txt}")
            total += len(parts[-1]) + 1
            if total > max_chars:
                truncated = True
                break
        text = "\n".join(parts).replace("\x00", "").strip()
        if len(text) > max_chars:
            text, truncated = text[:max_chars], True
        return ExtractResult(True, "audio", text, truncated=truncated)
    except Exception as e:
        # 坏音频流/解码异常 → 空 + 明确错误,不吐半转写垃圾(宁空勿毒)
        return ExtractResult(False, "audio", error="bad_file", hint=type(e).__name__)


__all__ = ["transcribe", "ASR_INSTALL_HINT", "MODEL_ENV", "DEFAULT_MODEL"]
