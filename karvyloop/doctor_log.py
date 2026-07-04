"""doctor_log — 运维事件固定落盘(~/.karvyloop/logs/),轮转防爆。

雷达 B②打脸点:"日志没有家 —— 用户看不到的日志再可读也是零"。
这一层给运维事件(doctor 结果 / --fix 动作 / 活性探测)一个**固定路径**,
让"可读性"有着落。零模型、永不抛、绝不写 key(只落 body 级事实)。

设计:
- 固定家:`~/.karvyloop/logs/doctor.log`(路径可查、可指、可 tail)。
- 轮转:RotatingFileHandler,单文件上限 + 保留几份 → 不会无限涨。
- 隔离:自建 logger(propagate=False),不污染 root logging;不改任何全局 handler。
- 脱敏纪律:调用方只传 body 级事实(finding code / 修了什么 / 探测结果),
  **绝不**把 api_key / Authorization / headers 传进来(见 CLAUDE.md 安全红线)。
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_LOGGER_NAME = "karvyloop.doctor"
_MAX_BYTES = 512 * 1024      # 单文件 512KB 上限
_BACKUPS = 3                 # 保留 3 份轮转(doctor.log + .1 .2 .3)
_configured = False


def logs_dir() -> Path:
    return Path.home() / ".karvyloop" / "logs"


def log_path() -> Path:
    return logs_dir() / "doctor.log"


def _ensure_logger() -> Optional[logging.Logger]:
    """惰性配置一个隔离的 rotating logger。落盘失败(只读盘等)→ 返回 None,永不抛。"""
    global _configured
    lg = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return lg
    try:
        d = logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(log_path(), maxBytes=_MAX_BYTES,
                                backupCount=_BACKUPS, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        lg.handlers = [h]           # 只挂自己的 handler(幂等:重配不叠加)
        lg.setLevel(logging.INFO)
        lg.propagate = False        # 不冒泡到 root(不污染别的日志)
        _configured = True
        return lg
    except Exception:
        return None


def log_event(message: str, *, level: str = "info") -> Optional[Path]:
    """落一条运维事件(body 级事实)。返回日志路径(便于 CLI 指给用户);失败→None。永不抛。

    ⚠️ 只传 body 级事实:finding code、修了什么、探测通不通。绝不传 key / header / auth。
    """
    lg = _ensure_logger()
    if lg is None:
        return None
    try:
        getattr(lg, level, lg.info)(message)
        return log_path()
    except Exception:
        return None


def log_findings(findings, *, phase: str = "doctor") -> Optional[Path]:
    """把一批 Finding 落盘(每条一行:level/code/params)。永不抛、不写 key。"""
    lg = _ensure_logger()
    if lg is None:
        return None
    try:
        lg.info("--- %s: %d finding(s) ---", phase, len(findings))
        for f in findings:
            lvl = {"fail": logging.ERROR, "warn": logging.WARNING}.get(
                getattr(f, "level", "ok"), logging.INFO)
            # params 里从来没有 key(doctor 只放 path/pkg/reason/host 等);仍显式只取 code+params
            lg.log(lvl, "[%s] %s %s", getattr(f, "level", "?"),
                   getattr(f, "code", "?"), dict(getattr(f, "params", {}) or {}))
        return log_path()
    except Exception:
        return None


__all__ = ["logs_dir", "log_path", "log_event", "log_findings"]
