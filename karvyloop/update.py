"""update — 版本检测(detect → notify → 你按下,**绝不自动升级**)。

本地主权工具的升级铁律(与产品论题自洽):只**检测 + 提示**,绝不静默自动升级。
检测是一次干净的版本查询 —— **零遥测、不带任何用户数据**、可用 env 关掉、查不到就**静默**
(永不抛、永不阻塞启动)。升级命令交给你自己执行(升级本身就是产品对自己生命周期的一次 H2A)。

数据承诺:升级不动 `~/.karvyloop/`(你的 beliefs/skills/decision_log/config 都在那,跨版本存活)。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Optional

from karvyloop import __version__

GITHUB_REPO = "Caprista/KarvyLoop"
_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_CACHE_PATH = Path.home() / ".karvyloop" / ".update_check.json"
_CACHE_TTL = 86400          # 一天最多查一次(别 hammer GitHub,尊重本地优先)
_ENV_OFF = "KARVYLOOP_NO_UPDATE_CHECK"   # 设任意非空值 = 彻底关闭检测(主权)


def current_version() -> str:
    return __version__


def _parse_semver(v: str) -> tuple:
    """'v1.2.3' / '1.2' / '1.2.3-rc1' → (1,2,3)。丢 pre-release 后缀做主比较;非法段当 0。"""
    core = (v or "").strip().lstrip("vV").split("-")[0].split("+")[0]
    parts: list[int] = []
    for chunk in core.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(latest: str, current: str) -> bool:
    return _parse_semver(latest) > _parse_semver(current)


def detect_install_mode() -> str:
    """git(可 `git pull`)还是 pip。包目录上溯找到 .git → git。"""
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        try:
            if (p / ".git").exists():
                return "git"
        except Exception:
            break
    return "pip"


def upgrade_command(mode: Optional[str] = None) -> str:
    mode = mode or detect_install_mode()
    return "git pull && pip install -e ." if mode == "git" else "pip install -U karvyloop"


def check_disabled() -> bool:
    return bool(os.environ.get(_ENV_OFF))


def _read_cache() -> Optional[dict]:
    try:
        d = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _write_cache(d: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass   # 缓存写失败不致命(下次再查)


def _fetch_latest(timeout: float = 4.0) -> Optional[dict]:
    """查 GitHub Releases 最新 tag。任何失败 → None(网断/限流/还没发过 release 都静默)。"""
    try:
        req = urllib.request.Request(_RELEASES_API, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "karvyloop-update-check",   # 仅版本查询;不带任何用户数据
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        tag = data.get("tag_name") or ""
        if not tag:
            return None
        return {"latest": tag, "url": data.get("html_url") or "", "name": data.get("name") or tag}
    except Exception:
        return None


def check_update(*, force: bool = False, now: Optional[float] = None,
                 timeout: float = 4.0) -> dict:
    """返回 {current, latest, newer, command, url, checked, source}。

    缓存一天(force 跳缓存);disabled → 只返 current;查不到 → newer=False。永不抛、永不阻塞。
    """
    cur = current_version()
    base = {"current": cur, "latest": None, "newer": False,
            "command": upgrade_command(), "url": "", "checked": False, "source": "disabled"}
    if check_disabled():
        return base
    now = now if now is not None else time.time()
    if not force:
        c = _read_cache()
        if c and c.get("latest") and (now - float(c.get("ts", 0))) < _CACHE_TTL:
            latest = c["latest"]
            return {"current": cur, "latest": latest, "newer": is_newer(latest, cur),
                    "command": upgrade_command(), "url": c.get("url", ""),
                    "checked": True, "source": "cache"}
    got = _fetch_latest(timeout=timeout)
    if got is None:
        return {**base, "checked": True, "source": "unreachable"}
    _write_cache({"ts": now, "latest": got["latest"], "url": got["url"]})
    latest = got["latest"]
    return {"current": cur, "latest": latest, "newer": is_newer(latest, cur),
            "command": upgrade_command(), "url": got["url"], "checked": True, "source": "live"}


__all__ = [
    "current_version", "is_newer", "detect_install_mode", "upgrade_command",
    "check_disabled", "check_update", "GITHUB_REPO",
]
