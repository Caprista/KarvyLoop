"""config_relay — relay 地址的本地持久化(~/.karvyloop/config.yaml 的 `relay` 字段)。

**BYO-server 硬要求(docs/74)**:开源项目不能让用户改源码换服务器。relay 地址是配置不是代码:
- config.yaml 设一次(`relay: wss://your-relay.example`),`karvyloop console` 自动挂;
- CLI `--relay` 临时覆盖(优先);
- 配对邀请/二维码一律从**运行时的这个地址**生成 —— 自建 relay 的用户,邀请天然指自己的服务器,
  源码里永远没有任何默认/硬编码 relay 域名。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def _default_path() -> Path:
    return Path.home() / ".karvyloop" / "config.yaml"


def read_relay(config_path=None) -> Optional[str]:
    """读 config.yaml 的 `relay`(wss://…);缺失/读不出 → None。"""
    p = Path(config_path) if config_path else _default_path()
    if not p.exists():
        return None
    try:
        import yaml
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    v = str(cfg.get("relay") or "").strip()
    return v or None


def write_relay(relay_url: str, config_path=None) -> bool:
    """把 `relay` 写进 config.yaml(保留其余字段);空串=清掉。成功返 True。"""
    p = Path(config_path) if config_path else _default_path()
    try:
        import yaml
        cfg = (yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}) or {}
        v = (relay_url or "").strip()
        if v:
            cfg["relay"] = v
        else:
            cfg.pop("relay", None)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return True
    except Exception:
        return False


__all__ = ["read_relay", "write_relay"]
