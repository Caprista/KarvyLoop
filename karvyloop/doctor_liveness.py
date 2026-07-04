"""doctor_liveness — 活性检查(装了没配 vs 配了连不上)。

雷达 B②最大的洞:doctor 5 项全查"装没装好",**没一项查"现在还活着吗"**——
故障第一名(key 失效 / endpoint 连不上 / 网络 / 欠费)现有静态检查反而查不出。
这一层补上,并且**明确区分**两种状态(否则用户搞不清是自己没填还是网断了):
  - 没配(no_key / no_config)→ 走 check_config 那一层,不在这里重复报。
  - **配了但连不上**(endpoint_unreachable)→ 这一层专抓,故障第一名。

安全纪律(CLAUDE.md 红线,活性探测尤其注意):
  - **只探连通性,不碰 key 内容**。默认探测 = 一次 TCP connect 到 provider host:port,
    **完全不发送 api_key / Authorization / 任何 header**——连不上 host 时 key 有没有效都无从谈起,
    先把"网通不通"这层最常见的故障确定性地查出来。
  - 磁盘可写 = 在 ~/.karvyloop 建一个临时文件再删。
  - 沙箱可用 = 调 default_sandbox() 看选到哪一档(StubSandbox=fail-closed 拒跑=不可用)。
  - 网络层(TCP probe)可注入 `connect_probe=` 给测试 stub(CI 无网)。

永不抛、永不写 key。产结构化 Finding(渲染走 i18n,和 doctor 同规格)。
"""
from __future__ import annotations

import socket
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from karvyloop.doctor import FAIL, OK, WARN, Finding

# 本地 provider 不需要联网探测(local-first;起没起 ollama 由磁盘/进程另说)
_LOCAL_PROVIDERS = {"ollama", "llamacpp", "lmstudio", "vllm-local"}


def _data_dir() -> Path:
    return Path.home() / ".karvyloop"


def _host_port(base_url: str) -> tuple[str, int]:
    """从 base_url 抽 (host, port)。默认端口按 scheme。"""
    u = urlparse(base_url if "//" in base_url else "//" + base_url)
    host = u.hostname or base_url
    port = u.port or (443 if (u.scheme or "https") == "https" else 80)
    return host, port


def _tcp_connect(host: str, port: int, timeout: float = 2.0) -> bool:
    """一次 TCP connect,通=True。超时快速失败。**不发任何字节**(不碰 key)。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def check_endpoint(
    config_path: Optional[Path] = None,
    *,
    connect_probe: Optional[Callable[[str, int], bool]] = None,
) -> list[Finding]:
    """探默认 chat 模型的 provider endpoint 是否**连得上**(TCP 层,不发 key)。

    区分:
      - 没 config / 没默认模型 / 没 key → 交给 check_config,这里返回 skipped(不重复报)。
      - 本地 provider → 探本地端口(连不上=ollama 没起,提示级 warn)。
      - 云端配好了但 host 连不上 → endpoint_unreachable(FAIL,故障第一名)。
      - 连得上 → endpoint_reachable(OK)。
    """
    probe = connect_probe or _tcp_connect
    from karvyloop.cli.init import default_config_path
    cfg = config_path or default_config_path()
    if not Path(cfg).exists():
        return [Finding(OK, "liveness_skipped", {"reason": "no_config"})]
    try:
        from karvyloop.gateway.registry import ModelRegistry
        reg = ModelRegistry.load(cfg)
    except Exception:
        return [Finding(OK, "liveness_skipped", {"reason": "config_unreadable"})]

    from karvyloop.gateway.readiness import is_ready
    ready, reason = is_ready(reg)
    if not ready:
        # 没配好 → check_config 已报;活性层不重复喊,只标 skipped(区分"没配"vs"配了连不上")
        return [Finding(OK, "liveness_skipped", {"reason": reason})]

    dc = getattr(reg, "default_chat", "") or ""
    provider_name = dc.split("/", 1)[0]
    try:
        prov = reg.provider_of(dc)
        base_url = getattr(prov, "base_url", "") or ""
    except Exception:
        return [Finding(OK, "liveness_skipped", {"reason": "no_provider"})]
    if not base_url:
        return [Finding(OK, "liveness_skipped", {"reason": "no_base_url"})]

    host, port = _host_port(base_url)
    reachable = bool(probe(host, port))
    local = provider_name in _LOCAL_PROVIDERS
    if reachable:
        return [Finding(OK, "endpoint_reachable", {"host": host, "provider": provider_name})]
    if local:
        # 本地默认但端口不通 = ollama 没起(不是网断);提示级
        return [Finding(WARN, "local_endpoint_down", {"host": host, "port": port,
                                                      "provider": provider_name})]
    # 云端配好了却连不上 host —— 故障第一名(网络 / DNS / endpoint 挂)
    return [Finding(FAIL, "endpoint_unreachable", {"host": host, "provider": provider_name})]


def check_disk_writable() -> list[Finding]:
    """~/.karvyloop 可写吗(建临时文件再删)。只读盘 / 满盘 / 权限 → warn。"""
    d = _data_dir()
    probe = d / ".doctor_write_probe"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return [Finding(OK, "disk_writable", {"dir": str(d)})]
    except Exception as e:
        try:
            if probe.exists():
                probe.unlink()
        except Exception:
            pass
        return [Finding(WARN, "disk_not_writable", {"dir": str(d), "err": type(e).__name__})]


def check_sandbox() -> list[Finding]:
    """沙箱真能起吗。选到 StubSandbox = fail-closed 拒跑第三方 = 不可用(提示级)。"""
    try:
        from karvyloop.sandbox.selector import default_sandbox
        sb = default_sandbox()
        name = type(sb).__name__
    except Exception as e:
        return [Finding(WARN, "sandbox_error", {"err": type(e).__name__})]
    if name == "StubSandbox":
        return [Finding(WARN, "sandbox_stub", {"impl": name})]
    if name == "DegradedWindowsSandbox":
        # 第一方直通、第三方拒跑——诚实标"降级但可用"
        return [Finding(OK, "sandbox_degraded", {"impl": name})]
    return [Finding(OK, "sandbox_ok", {"impl": name})]


def run_liveness(
    *,
    config_path: Optional[Path] = None,
    connect_probe: Optional[Callable[[str, int], bool]] = None,
) -> list[Finding]:
    """跑全套活性检查(endpoint 连通 / 磁盘可写 / 沙箱可起)。永不抛。"""
    findings: list[Finding] = []
    for fn, kw in ((check_endpoint, {"config_path": config_path, "connect_probe": connect_probe}),
                   (check_disk_writable, {}), (check_sandbox, {})):
        try:
            findings += fn(**kw)
        except Exception as e:
            findings.append(Finding(WARN, "check_error", {"err": type(e).__name__}))
    return findings


__all__ = ["check_endpoint", "check_disk_writable", "check_sandbox", "run_liveness"]
