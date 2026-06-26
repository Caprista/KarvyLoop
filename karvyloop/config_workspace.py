"""config_workspace — 用户工作区根的解析(9.5 P1)。

病根(用户 2026-06-19):console 的 workspace_root = 启动目录 = KarvyLoop 源码树。
后果:① agent 想写文件没权限(token 只授 fs:源码树,它退 /tmp 又没授)→ 写不了;
      ② coding 提示从 cwd 往上灌 CLAUDE.md/CONTEXT → 角色读到 KarvyLoop 内部、串成"开发助手"。

修:给一个**独立的用户工作区**,跟 KarvyLoop 源码彻底隔离。agent 在这读写(token 授权),
读不到 KarvyLoop 自己的指令文件。

解析顺序:env `KARVYLOOP_WORKSPACE` > config.yaml `workspace` > 默认 `~/karvyloop-work`。
解析后**确保目录存在**(mkdir -p)。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _default_workspace() -> Path:
    return Path.home() / "karvyloop-work"


def _config_path(config_path=None) -> Path:
    return Path(config_path) if config_path else (Path.home() / ".karvyloop" / "config.yaml")


def _read_config_workspace(config_path=None) -> Optional[str]:
    p = _config_path(config_path)
    if not p.exists():
        return None
    try:
        import yaml
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    v = cfg.get("workspace")
    return str(v) if v else None


def resolve_workspace(config_path=None, *, ensure: bool = True) -> str:
    """解析用户工作区根(env > config > 默认 ~/karvyloop-work);ensure=True 则 mkdir -p。

    返回绝对路径字符串。任何异常 → 退回默认并尽力 mkdir(不阻塞启动)。
    """
    raw = os.environ.get("KARVYLOOP_WORKSPACE") or _read_config_workspace(config_path)
    ws = Path(raw).expanduser() if raw else _default_workspace()
    try:
        ws = ws.resolve()
    except Exception:
        pass
    if ensure:
        try:
            ws.mkdir(parents=True, exist_ok=True)
        except Exception:
            # 兜底:默认目录
            ws = _default_workspace()
            ws.mkdir(parents=True, exist_ok=True)
    return str(ws)


__all__ = ["resolve_workspace"]
