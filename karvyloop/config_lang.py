"""config_lang — 语言偏好的本地持久化(~/.karvyloop/config.yaml 的 `lang` 字段)。

用户偏好默认持久(项目原则):语言设一次就记在案,之后启动自动生效,不必每次 --lang。
canonical store = config.yaml `lang`,CLI 与 GUI 共用;清浏览器缓存也不丢(对比 localStorage)。
i18n 模块保持 path-agnostic,读写配置放这里。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def _default_path() -> Path:
    return Path.home() / ".karvyloop" / "config.yaml"


def read_lang(config_path=None) -> Optional[str]:
    """读 config.yaml 的 `lang`;缺失/读不出 → None。"""
    p = Path(config_path) if config_path else _default_path()
    if not p.exists():
        return None
    try:
        import yaml
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    v = cfg.get("lang")
    return str(v) if v else None


def write_lang(lang: str, config_path=None) -> bool:
    """把 `lang` 写进 config.yaml(保留其余字段);成功返 True。

    config 不存在则新建(只含 lang)。yaml 往返保留 providers/api_key 等其余内容。
    """
    p = Path(config_path) if config_path else _default_path()
    try:
        import yaml
        cfg = (yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}) or {}
        cfg["lang"] = lang
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return True
    except Exception:
        return False


__all__ = ["read_lang", "write_lang"]
