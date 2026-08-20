"""config_h2a_mode — H2A 拍板默认模式的本地持久化(config.yaml 的 `h2a_default_mode` 字段)。

镜像 config_lang 的放法(用户偏好默认持久):默认模式设一次就记在案,重启自动生效。
值:`"blocking"`(默认,卡等人拍)/ `"non_blocking"`(新卡默认先跑后拍;
高危 kind/不可逆语义在执行咽喉仍强制阻塞 —— 默认模式只是"普通卡松手",安全闸不动)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

BLOCKING = "blocking"
NON_BLOCKING = "non_blocking"


def _default_path() -> Path:
    return Path.home() / ".karvyloop" / "config.yaml"


def read_h2a_default_mode(config_path=None) -> str:
    """读 config.yaml 的 `h2a_default_mode`;缺失/读不出/非法值 → "blocking"(保守默认)。"""
    p = Path(config_path) if config_path else _default_path()
    if not p.exists():
        return BLOCKING
    try:
        import yaml
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return BLOCKING
    v = str(cfg.get("h2a_default_mode") or "").strip()
    return v if v in (BLOCKING, NON_BLOCKING) else BLOCKING


def write_h2a_default_mode(mode: str, config_path=None) -> bool:
    """把默认模式写进 config.yaml(保留其余字段);非法值拒写返 False。"""
    m = str(mode or "").strip()
    if m not in (BLOCKING, NON_BLOCKING):
        return False
    p = Path(config_path) if config_path else _default_path()
    try:
        import yaml
        cfg = (yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}) or {}
        cfg["h2a_default_mode"] = m
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return True
    except Exception:
        return False


__all__ = ["BLOCKING", "NON_BLOCKING", "read_h2a_default_mode", "write_h2a_default_mode"]
