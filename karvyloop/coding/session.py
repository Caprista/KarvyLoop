"""coding 会话持久化（coding/session.py）。

规格：docs/modules/forge.md §2.6。
  - JSONL 增量 append(不重写整文件)
  - 落盘脱敏:`sk-ant-...` / `Bearer ...` / KarvyLoop key marker → `[redacted]`
  - **内存态保真**(脱敏只发生在落盘前)
  - 字段截断:单字段 ≤16K chars
  - 文件轮转:>256KB rename,保留最近 3
  - fork:新 id + {parent, branch} + 克隆历史
  - 原子写:.tmp → rename
  - 单调时间戳(单调递增;不依赖 wall clock)
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


SESSION_SCHEMA = "karvyloop-forge-session"
FORMAT_VERSION = 1
MAX_FIELD_CHARS = 16 * 1024
MAX_FILE_BYTES = 256 * 1024
ROTATE_KEEP = 3

# 脱敏模式
_REDACT_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),       # Anthropic
    re.compile(r"sk-[A-Za-z0-9]{20,}"),              # OpenAI 风格
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{10,}"),    # Bearer token
    re.compile(r"KARVYLOOP_KEY_[A-Za-z0-9]{10,}"),     # KarvyLoop 内部 key 标记
]


def _redact(text: str) -> str:
    """命中模式 → [redacted]。"""
    out = text
    for p in _REDACT_PATTERNS:
        out = p.sub("[redacted]", out)
    return out


def _truncate_field(v: Any) -> Any:
    if isinstance(v, str) and len(v) > MAX_FIELD_CHARS:
        return v[:MAX_FIELD_CHARS] + "…"
    return v


def _scrub_for_disk(record: dict) -> dict:
    """序列化前:每字段脱敏 + 截断。"""
    def walk(x):
        if isinstance(x, str):
            return _truncate_field(_redact(x))
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return walk(record)


def _atomic_write(path: Path, content: str) -> None:
    """原子写:.tmp + os.replace。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_append(path: Path, line: str) -> None:
    """追加写:open(append) 在 POSIX 上不严格原子但足够(单进程单 session)。"""
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


@dataclass
class SessionMeta:
    session_id: str
    parent: Optional[str]
    branch: Optional[str]
    started_at: float


class ForgeSession:
    """单会话:JSONL 增量 + 脱敏 + 轮转 + fork。"""

    def __init__(self, path: Path, meta: SessionMeta, *, _monotonic_seed: int = 0):
        self.path = Path(path)
        self.meta = meta
        self._ts_counter = _monotonic_seed  # 单调时间戳种子

    # ---- 工厂 ----
    @classmethod
    def create(cls, dir_: Path) -> "ForgeSession":
        dir_ = Path(dir_)
        dir_.mkdir(parents=True, exist_ok=True)
        sid = uuid.uuid4().hex[:16]
        meta = SessionMeta(session_id=sid, parent=None, branch=None,
                            started_at=time.time())
        path = dir_ / f"{sid}.jsonl"
        s = cls(path, meta, _monotonic_seed=1)
        s.append_record({"_meta": True, "schema": SESSION_SCHEMA, "v": FORMAT_VERSION,
                          **meta.__dict__})
        return s

    @classmethod
    def load(cls, path: Path) -> "ForgeSession":
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            first = f.readline().rstrip("\n")
        rec = json.loads(first)
        m = rec
        meta = SessionMeta(
            session_id=m["session_id"],
            parent=m.get("parent"),
            branch=m.get("branch"),
            started_at=m.get("started_at", 0.0),
        )
        s = cls(path, meta, _monotonic_seed=2)
        return s

    def fork(self, dir_: Path, branch: str) -> "ForgeSession":
        dir_ = Path(dir_)
        dir_.mkdir(parents=True, exist_ok=True)
        new_id = uuid.uuid4().hex[:16]
        meta = SessionMeta(session_id=new_id, parent=self.meta.session_id,
                            branch=branch, started_at=time.time())
        new_path = dir_ / f"{new_id}.jsonl"
        s = ForgeSession(new_path, meta, _monotonic_seed=1)
        # 写新 meta
        s.append_record({"_meta": True, "schema": SESSION_SCHEMA, "v": FORMAT_VERSION,
                          **meta.__dict__})
        # 克隆父历史(从所有轮转文件:旧 → 新 = .KEEP → .1 → 主文件)
        # 顺序:数字越大越旧,但路径模板按 .KEEP.jsonl / ...jsonl
        for rotated in sorted(self.path.parent.glob(self.path.stem + ".*.jsonl"),
                              key=lambda p: int(p.suffixes[-2].lstrip(".")),
                              reverse=True):
            with open(rotated, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    if '"_meta": true' in line or '"_meta":true' in line:
                        continue
                    _atomic_append(new_path, line)
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    if '"_meta": true' in line or '"_meta":true' in line:
                        continue
                    _atomic_append(new_path, line)
        return s

    # ---- 追加 ----
    def append_record(self, record: dict) -> None:
        """追加一条 record:脱敏 + 截断 + 写盘 + 检查轮转。"""
        # 单调时间戳
        self._ts_counter += 1
        record = {**record, "ts_mono": self._ts_counter}
        scrubbed = _scrub_for_disk(record)
        line = json.dumps(scrubbed, ensure_ascii=False, default=str)
        # 写盘
        _atomic_append(self.path, line + "\n")
        # 轮转检查
        self._maybe_rotate()

    def _maybe_rotate(self) -> None:
        if not self.path.exists():
            return
        if self.path.stat().st_size <= MAX_FILE_BYTES:
            return
        # 轮转:.1 → .2 → .3,自身 → .1(超过 KEEP 的删)
        for i in range(ROTATE_KEEP, 0, -1):
            older = self.path.with_suffix(f".{i}.jsonl")
            if i == ROTATE_KEEP and older.exists():
                older.unlink()
            if i > 1:
                src = self.path.with_suffix(f".{i-1}.jsonl")
                if src.exists():
                    src.replace(older)
            else:
                # 自身 → .1
                if self.path.exists():
                    self.path.replace(older)
                # 自身新建空文件(下条 append 会创建)

    # ---- 读回 ----
    def iter_records(self, include_meta: bool = False) -> Iterable[dict]:
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if not include_meta and rec.get("_meta"):
                    continue
                yield rec

    def count_records(self, include_meta: bool = False) -> int:
        return sum(1 for _ in self.iter_records(include_meta=include_meta))


__all__ = [
    "ForgeSession", "SessionMeta", "SESSION_SCHEMA", "FORMAT_VERSION",
    "MAX_FIELD_CHARS", "MAX_FILE_BYTES", "ROTATE_KEEP",
    "_redact", "_scrub_for_disk",  # 测试可能直接用
]
