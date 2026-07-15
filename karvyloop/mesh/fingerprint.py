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


def _capabilities(osname: str, sandbox: str) -> list:
    """本设备能**执行**哪类任务(调度 feasibility 的硬过滤词典;不含普适参与——发起/决策/旁观人人有)。

    PC 三平台 → coding/shell/big-task(能跑代码、shell、大任务);有沙箱 → sandbox。
    移动端的独占能力(camera/location/voice/mic)由移动客户端 declare(现无移动端,PC 不臆造)。
    """
    caps = set()
    if osname in ("linux", "darwin", "windows"):
        caps |= {"coding", "shell", "big-task"}
    if sandbox and sandbox != "none":
        caps.add("sandbox")
    return sorted(caps)


def device_fingerprint(state_dir=None, *, label: Optional[str] = None,
                       extra_capabilities: Optional[list] = None) -> dict:
    """本设备能力指纹(能力广告)。label = 人给设备起的名;extra_capabilities = 设备自报的额外
    执行能力(如移动端 declare camera/location,或某机装了特殊工具链)。"""
    osname = (_platform.system() or sys.platform).lower()      # linux/darwin/windows
    sandbox = _sandbox_backend()
    caps = set(_capabilities(osname, sandbox)) | set(extra_capabilities or ())
    return {
        "device_id": _device_id(state_dir),
        "label": label or "",
        "os": osname,
        "arch": (_platform.machine() or "").lower(),           # x86_64/arm64/amd64
        "python": _platform.python_version(),
        "karvyloop": _karvyloop_version(),
        "sandbox": sandbox,                                    # bwrap/seatbelt/win-restricted/none
        "capabilities": sorted(caps),                          # 执行能力集(feasibility 硬过滤输入)
    }


def device_advert(state_dir=None, relay_url: str = "", *,
                  label: Optional[str] = None) -> dict:
    """能力广告(docs/74 探活地基)= 能力指纹 + **怎么连到我**(relay_url + mesh 房号)。

    mesh 同步时双方互换 advert → 对端 register_peer 把我登进它的花名册,从此它的
    ticker 能拨回我(一次同步,双方花名册都齐)。**诚实缺省**:无 relay 身份
    (device_id 空)→ room 留空、不臆造(对端 register_peer 会因 device_id 空
    直接丢弃,不投毒花名册);mesh_rid 只在真有身份时才取(避免为广告而生成状态)。
    """
    adv = device_fingerprint(state_dir, label=label)
    adv["relay_url"] = str(relay_url or "")
    room = ""
    if adv.get("device_id"):
        try:
            from karvyloop.relay.pairing import PairingStore
            room = PairingStore(state_dir).mesh_rid()
        except Exception:
            room = ""            # 状态文件不可写等 → 诚实缺省,不编房号
    adv["room"] = room
    return adv


__all__ = ["device_fingerprint", "device_advert"]
