"""mesh/fingerprint — 本设备能力指纹(设备 mesh 的"能力广告",docs/74 item3)。

一台设备上线时播报它能干什么:OS/架构/Python/karvyloop 版本/沙箱后端。调度协同据此做
feasibility 过滤("这活要 Linux bwrap 沙箱 / 要这台有那个文件")。纯 stdlib 探测,三平台一致。

`device_id` = **relay 身份指纹**(和 `relay-pair` 打印的是同一个,所以设备在 mesh 里可寻址)——
复用 relay 密钥当设备身份,不另造。无 crypto/无密钥时退回空串(设备尚未有 relay 身份)。
"""
from __future__ import annotations

import platform as _platform
import shutil
import sys
from typing import Optional


def _karvyloop_version() -> str:
    try:
        import karvyloop
        return getattr(karvyloop, "__version__", "") or ""
    except Exception:
        return ""


def _sandbox_backend() -> str:
    """本机可用的沙箱后端(best-effort 探测;能力广告用,不做强断言)。"""
    osname = sys.platform
    if osname.startswith("linux"):
        return "bwrap" if shutil.which("bwrap") else "none"
    if osname == "darwin":
        return "seatbelt" if shutil.which("sandbox-exec") else "none"
    if osname.startswith("win"):
        return "win-restricted"
    return "none"


def _device_id(state_dir=None) -> str:
    """设备身份 = relay 密钥指纹(可寻址;和 relay-pair 一致)。无密钥/无 crypto → 空串。

    只**读**已有密钥,不为取指纹而生成密钥(生成是 relay-pair/console --relay 的事,有副作用)。
    """
    try:
        from pathlib import Path

        from karvyloop.relay import e2e
        from karvyloop.relay.pairing import KEY_FILE
        d = Path(state_dir) if state_dir else (Path.home() / ".karvyloop")
        kp = d / KEY_FILE
        if not kp.exists():
            return ""
        priv = kp.read_bytes()
        if len(priv) != 32:
            return ""
        return e2e.fingerprint(e2e.pub_from_priv(priv))
    except Exception:
        return ""


def device_fingerprint(state_dir=None, *, label: Optional[str] = None) -> dict:
    """本设备能力指纹(能力广告)。label 可选(人给设备起的名,如"家里的 Linux")。"""
    return {
        "device_id": _device_id(state_dir),
        "label": label or "",
        "os": (_platform.system() or sys.platform).lower(),   # linux/darwin/windows
        "arch": (_platform.machine() or "").lower(),           # x86_64/arm64/amd64
        "python": _platform.python_version(),
        "karvyloop": _karvyloop_version(),
        "sandbox": _sandbox_backend(),                         # bwrap/seatbelt/win-restricted/none
    }


__all__ = ["device_fingerprint"]
