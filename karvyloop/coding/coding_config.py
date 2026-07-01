"""coding_config —「编码」技能的可编辑配置(#3,Hardy 2026-06-25)。

只存**外接编码工具命令**(高级用户想用自己的 coder CLI)。
诚实边界:v1.0 **不接入执行**(Hardy:"不做 CLI 接入")—— 这里只是把用户的偏好
**存下来 + 露出来可编辑**,`external_active` 恒 False;真正用外接命令跑(且绕过我们沙箱)
是后续显式 opt-in 的活儿。命令落 `~/.karvyloop/coding.json`(仓外),不进 repo。
"""
from __future__ import annotations

import json
import os
import pathlib


def _store_path() -> pathlib.Path:
    return pathlib.Path.home() / ".karvyloop" / "coding.json"


def get_external_executor() -> str | None:
    """外接编码命令,优先级:① 环境变量 CODING_EXTERNAL_EXECUTOR ② coding.json。无 → None。"""
    env = (os.environ.get("CODING_EXTERNAL_EXECUTOR") or "").strip()
    if env:
        return env
    try:
        p = _store_path()
        if p.exists():
            cmd = (json.loads(p.read_text(encoding="utf-8")) or {}).get("external_executor", "")
            cmd = (cmd or "").strip()
            return cmd or None
    except Exception:
        pass
    return None


def set_external_executor(cmd: str) -> dict:
    """保存/清除外接编码命令(空 = 清除)。写 ~/.karvyloop/coding.json(仓外)。返回公开态。"""
    cmd = (cmd or "").strip()
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not cmd:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    else:
        p.write_text(json.dumps({"external_executor": cmd}, ensure_ascii=False), encoding="utf-8")
    return get_coding_config_public()


def get_coding_config_public() -> dict:
    """公开态:外接命令 + 是否已接入执行(v1.0 恒 False,诚实标"已存未接入")。"""
    ext = get_external_executor()
    return {
        "external_executor": ext,      # 用户偏好的 coder 命令(可编辑);None=用内建 Forge
        "external_active": False,      # v1.0 不接入执行:存了也还是 Forge 跑(诚实,不当 yes-man)
    }
