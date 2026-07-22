"""test_audio_transcribe — 本地 ASR:音频(mp3/wav/m4a)→ 文字稿,进附件同一条产线。

选型 docs/64(faster-whisper,[asr] extra)。CI **不断言转写质量**(模型下载 + 环境差异),
只锁四层(与 test_file_extract 同形制):
1. 分类层:mp3/wav/m4a → "audio";ogg/flac 等**没承诺**的格式不进解析表(诚实降级 binary)。
2. 宁空勿毒:伪造扩展名(文本冒充 .mp3)→ magic 防线直接拒;解码异常收敛 bad_file,
   空文本 —— 这些测试不依赖 faster-whisper。
3. 优雅降级:没装 faster-whisper → error="missing_dependency" + `karvyloop[asr]` 提示;
   模型加载失败(断网/坏型号)→ error="asr_failed",不冒充"文件坏了"。
4. 产线缝合(mock 转写器,不下模型):/api/files/view 预览出转写文本;ReadTool 走行号
   产线;"会议录音→纪要"意图召回 meeting-notes;unlocks 面板有 asr 行。

真转写不进 CI:本机装了 [asr] 的话,跑 `pytest -m ''` 时最后一个 importorskip 测试
会用合成 wav 走一遍真模型(没装/没模型自动跳过)。
"""
from __future__ import annotations

import io
import math
import pathlib
import struct
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop import audio_transcribe  # noqa: E402
from karvyloop.file_extract import extract_kind, extract_text  # noqa: E402


def _wav_bytes(seconds: float = 0.5, freq: float = 440.0) -> bytes:
    """合成一段合法 PCM16 wav(正弦音,不是人声 —— 真模型测试只验"跑得通不装死")。"""
    rate, n = 16000, int(16000 * seconds)
    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n))
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVE"
           + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
           + b"data" + struct.pack("<I", len(frames)))
    return hdr + frames


class _FakeSeg:
    def __init__(self, start, text):
        self.start, self.text = start, text


class _FakeModel:
    """mock 转写器:形状与 faster-whisper 一致((segments, info) 迭代器),不碰网络。"""
    def __init__(self, segs=None):
        self._segs = segs if segs is not None else [
            _FakeSeg(0.0, "大家好,现在开周会。"),
            _FakeSeg(65.0, "决定:下周五发版,由 Chen 拍板。"),
        ]

    def transcribe(self, _f, **_kw):
        return iter(self._segs), {"language": "zh"}


# ---------------------------------------------------------------- 1. 分类层

def test_audio_kinds_mapped():
    for name in ("rec.mp3", "REC.MP3", "meeting.wav", "memo.m4a"):
        assert extract_kind(name) == "audio", name
    # 没承诺的格式不进表(诚实:预览按 binary 提示下载,不假装能转)
    for name in ("a.ogg", "a.flac", "a.aac", "a.mp4", "a.csv", "noext"):
        assert extract_kind(name) != "audio", name
    assert extract_kind("report.pdf") == "pdf"   # 文档产线原样


# ------------------------------------------------- 2. 宁空勿毒(无依赖也必须过)

def test_fake_extension_rejected_by_magic():
    """文本/垃圾冒充 .mp3 → magic 防线直接拒,不碰 ASR 引擎(装没装都一样)。"""
    for junk in (b"month,revenue\n2026-01,100\n", b"\x00\x01\x02\x03" * 8, b"short"):
        r = extract_text(junk, "audio")
        assert not r.ok and r.error == "bad_file" and r.text == ""


def test_wav_magic_accepted_mp3_magic_accepted():
    """合法容器头过 magic(后续是否成功取决于依赖/内容,这里只验防线不误杀)。"""
    assert audio_transcribe._magic_ok(_wav_bytes(0.05))
    assert audio_transcribe._magic_ok(b"ID3\x04\x00" + b"\x00" * 16)      # mp3 with ID3
    assert audio_transcribe._magic_ok(b"\xff\xfb\x90\x00" + b"\x00" * 16)  # mp3 frame sync
    assert audio_transcribe._magic_ok(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 8)
    assert not audio_transcribe._magic_ok(b"RIFF\x00\x00\x00\x00JUNK" + b"\x00" * 8)


def test_decode_failure_collapses_to_bad_file(monkeypatch):
    """引擎在但音频流坏 → 异常收敛 bad_file + 空文本(不吐半转写垃圾)。"""
    class _Boom:
        def transcribe(self, _f, **_kw):
            raise RuntimeError("invalid audio stream")
    monkeypatch.setattr(audio_transcribe, "_load_model", lambda: _Boom())
    r = extract_text(_wav_bytes(0.05), "audio")
    assert not r.ok and r.error == "bad_file" and r.text == ""


# --------------------------------------------- 3. 缺依赖/坏模型 优雅降级

def test_missing_dependency_clear_error(monkeypatch):
    monkeypatch.setattr(audio_transcribe, "_MODEL", None)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # 强制 ImportError
    r = extract_text(_wav_bytes(0.05), "audio")
    assert not r.ok and r.error == "missing_dependency" and r.text == ""
    assert "karvyloop[asr]" in r.hint


def test_model_load_failure_is_not_blamed_on_file(monkeypatch):
    """依赖在、模型下载/加载失败 → asr_failed(说人话),不冒充 bad_file。"""
    def _boom():
        raise RuntimeError("HF download failed")
    monkeypatch.setattr(audio_transcribe, "_load_model", _boom)
    r = extract_text(_wav_bytes(0.05), "audio")
    assert not r.ok and r.error == "asr_failed" and r.text == ""
    assert "模型" in r.hint


# ------------------------------------------------- 4a. 转写形状(mock,不下模型)

def test_transcript_has_timestamps_and_text(monkeypatch):
    monkeypatch.setattr(audio_transcribe, "_load_model", lambda: _FakeModel())
    r = extract_text(_wav_bytes(0.05), "audio")
    assert r.ok and r.kind == "audio" and not r.truncated
    assert "[00:00] 大家好,现在开周会。" in r.text
    assert "[01:05] 决定:下周五发版,由 Chen 拍板。" in r.text   # mm:ss 时间戳可被纪要引用


def test_transcript_truncation_capped_and_flagged(monkeypatch):
    segs = [_FakeSeg(i * 2.0, f"第 {i} 段发言,内容很长" * 3) for i in range(50)]
    monkeypatch.setattr(audio_transcribe, "_load_model", lambda: _FakeModel(segs))
    r = extract_text(_wav_bytes(0.05), "audio", max_chars=200)
    assert r.ok and r.truncated and len(r.text) <= 200


# ------------------------------------------ 4b. 产线缝合:/api/files/view + ReadTool

def _client(workspace):
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None,
                            runtime_kwargs={"workspace_root": str(workspace)})
    return TestClient(app)


def test_view_audio_transcribes(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_transcribe, "_load_model", lambda: _FakeModel())
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "meeting.wav").write_bytes(_wav_bytes(0.05))
    v = _client(ws).get("/api/files/view", params={"path": "meeting.wav"}).json()
    assert v["ok"] is True and v.get("extract") == "audio"
    assert "开周会" in v["text"] and "binary" not in v


def test_view_audio_missing_dep_honest_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_transcribe, "_MODEL", None)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "memo.m4a").write_bytes(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 64)
    v = _client(ws).get("/api/files/view", params={"path": "memo.m4a"}).json()
    assert v.get("extract_error") == "missing_dependency"
    assert v.get("extract") == "audio"          # 前端据此给 [asr] 而非 [files] 的安装提示
    assert "karvyloop[asr]" in v.get("hint", "")


def _read_tool(ws):
    from karvyloop.capability.token import mint
    from karvyloop.coding.filestate import FileState
    from karvyloop.coding.tools.read import ReadTool
    from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox
    from karvyloop.schemas import Capability
    tok = mint("t-audio", [Capability(resource=f"fs:{ws}", ops=["read", "write"])])
    return ReadTool(BubblewrapSandbox(), FileState(), str(ws), token=tok)


async def test_read_tool_audio_real_lineno_pipeline(tmp_path, monkeypatch):
    """agent 的 read_file 读音频 → 转写文本走 CSV 同款行号产线(meeting-notes 的输入口)。"""
    monkeypatch.setattr(audio_transcribe, "_load_model", lambda: _FakeModel())
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "meeting.mp3").write_bytes(b"ID3\x04\x00" + b"\x00" * 64)
    res = await _read_tool(ws)({"file_path": str(ws / "meeting.mp3").replace("\\", "/")})
    assert res.ok, res.error_message
    payload = str(res.payload)
    assert "开周会" in payload and "[01:05]" in payload
    assert payload.lstrip().startswith("1")     # 行号产线与 CSV/PDF 一致


async def test_read_tool_audio_missing_dep_tells_asr_extra(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_transcribe, "_MODEL", None)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "meeting.wav").write_bytes(_wav_bytes(0.05))
    res = await _read_tool(ws)({"file_path": str(ws / "meeting.wav").replace("\\", "/")})
    assert not res.ok and "karvyloop[asr]" in (res.error_message or "")


# ------------------------------------- 4c. 召回 + 解锁面板(接线自证,不 self-hype)

def test_meeting_recording_intent_recalls_meeting_notes(tmp_path):
    from karvyloop.crystallize.recall import recall
    from karvyloop.crystallize.skill_index import SkillIndex
    idx = SkillIndex(); idx.rebuild_from_disk(tmp_path)
    for intent in ("把这段会议录音转成文字并整理成会议纪要",
                   "turn this meeting recording into minutes"):
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "meeting-notes", \
            f"录音→纪要意图没召回 meeting-notes: {intent!r} -> {hit and hit.name}"


def test_unlocks_has_asr_row():
    from karvyloop.console.unlocks import list_unlocks
    got = {u["id"]: u for u in list_unlocks("", has_dep=lambda m: False)}
    assert got["asr"]["status"] == "missing_dep"
    assert got["asr"]["install"] == 'pip install "karvyloop[asr]"'
    got_on = {u["id"]: u for u in list_unlocks("", has_dep=lambda m: True)}
    assert got_on["asr"]["status"] == "on"


# ----------------------------- 5. 真模型冒烟(显式开关才跑;CI/日常 pytest 自动跳过)

def test_real_faster_whisper_smoke(monkeypatch):
    """真引擎吃合成 wav:只验「跑得通、返回 ExtractResult、不装死」,不断言内容
    (正弦音没有人声)。**双门**:装了 [asr] 且 KARVYLOOP_ASR_REAL_TEST=1 才跑 ——
    首次跑要联网下模型,绝不让普通 `pytest` 意外触发几十 MB 下载。"""
    # exc_type=ImportError:新版 pytest 对非 ModuleNotFoundError 的导入失败默认重抛;
    # 但 [asr] 是可选 extra,依赖的 av DLL 可能被机器策略拦(实捕:应用程序控制策略
    # 阻止 av\audio\frame DLL)—— 装了却载不进 = 环境缺席,与没装同义,诚实跳过。
    # 双门(env 开关)仍在,不存在掩盖真回归的面。
    # 2026-07-22 第二形态:同一策略的拦截也会在 ctypes 层抛 OSError(WinError 4551),
    # 穿过只认 ImportError 的门 → 仅对"应用控制策略"这一种 OSError 同判环境缺席跳过;
    # 其它 OSError 照旧重抛(收窄豁免面,不掩真回归)。
    try:
        pytest.importorskip("faster_whisper", exc_type=ImportError)
    except OSError as e:
        if getattr(e, "winerror", None) == 4551 or "应用程序控制策略" in str(e):
            pytest.skip(f"[asr] 依赖 DLL 被本机应用控制策略拦截(环境缺席): {e}")
        raise
    import os
    if os.environ.get("KARVYLOOP_ASR_REAL_TEST") != "1":
        pytest.skip("真模型冒烟要显式 KARVYLOOP_ASR_REAL_TEST=1(涉及模型下载)")
    monkeypatch.setenv(audio_transcribe.MODEL_ENV, "tiny")   # 最小模型(≈75MB),够验接线
    monkeypatch.setattr(audio_transcribe, "_MODEL", None)    # 别吃到别的测试的缓存
    r = audio_transcribe.transcribe(_wav_bytes(1.0))
    assert r.kind == "audio"
    assert r.ok or r.error in ("asr_failed", "bad_file")   # 断网/无模型 → 诚实降级
