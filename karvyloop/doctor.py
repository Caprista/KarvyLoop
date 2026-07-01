"""doctor — 确定性自检(零模型,永远能跑)。无门槛"修"的 Layer 0。

升级铁律的对偶:无门槛不只在"用",也在"修"。但**最常见的故障(没 key / 模型连不上 /
config 坏 / 依赖缺)恰恰是 agent 跑不起来的时候**——所以这一层**不依赖模型**,纯确定性检查,
用人话说清**哪坏了 + 怎么修**。运维 agent(Layer 1)是后手,垫在这层之上(见 ROADMAP)。

本模块只产**结构化 findings**(level + code + params);人话文案在 CLI/console 层走 i18n 渲染
(逻辑/文案分离,双语)。永不抛、永不联网阻塞(版本检查走缓存)。
"""
from __future__ import annotations

import importlib.util
import json
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

OK, WARN, FAIL = "ok", "warn", "fail"

# 控制台/运行所需的核心依赖 + 可选依赖(可选缺失只降级,不算 fail)
_REQUIRED = [("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("pydantic", "pydantic"),
             ("yaml", "pyyaml"), ("httpx", "httpx")]
_OPTIONAL = [("textual", "textual (TUI workbench)"), ("mcp", "mcp (MCP client)")]
# ~/.karvyloop 下应可解析的 JSON 数据(坏了不致命:系统会从空开始,但要提示)
_DATA_JSON = ["beliefs.json", "decision_log.json", "decision_stats.json", "tasks.json", "domains.json"]
_CONSOLE_PORT = 8766


@dataclass
class Finding:
    level: str            # ok | warn | fail
    code: str             # 渲染用:i18n doctor.msg.<code> / doctor.fix.<code>
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "params": dict(self.params)}


def _data_dir() -> Path:
    return Path.home() / ".karvyloop"


def check_config(config_path: Optional[Path] = None) -> list[Finding]:
    """config 在不在 / 能不能读 / 模型就绪没(复用 readiness,不联网)。"""
    from karvyloop.cli.init import default_config_path
    cfg = config_path or default_config_path()
    if not Path(cfg).exists():
        return [Finding(FAIL, "config_missing", {"path": str(cfg)})]
    try:
        from karvyloop.gateway.registry import ModelRegistry
        reg = ModelRegistry.load(cfg)
    except Exception as e:
        return [Finding(FAIL, "config_unreadable", {"path": str(cfg), "err": type(e).__name__})]
    from karvyloop.gateway.readiness import is_ready
    ok, reason = is_ready(reg)
    if ok:
        return [Finding(OK, "model_ready", {"model": getattr(reg, "default_chat", "") or "?"})]
    # reason ∈ no_config / no_default_model / no_key(readiness 的语汇)→ 各给对应修法
    code = {"no_default_model": "no_default_model", "no_key": "no_key"}.get(reason, "model_not_ready")
    return [Finding(FAIL, code, {"reason": reason})]


def check_deps() -> list[Finding]:
    out: list[Finding] = []
    for mod, pkg in _REQUIRED:
        if importlib.util.find_spec(mod) is None:
            out.append(Finding(FAIL, "dep_missing", {"pkg": pkg}))
    for mod, label in _OPTIONAL:
        if importlib.util.find_spec(mod) is None:
            out.append(Finding(WARN, "dep_optional_missing", {"pkg": label}))
    if not any(f.code == "dep_missing" for f in out):
        out.insert(0, Finding(OK, "deps_ok", {}))
    return out


def check_data_dir() -> list[Finding]:
    d = _data_dir()
    if not d.exists():
        # 没目录不是错:首跑还没建;系统会自动建
        return [Finding(OK, "data_fresh", {})]
    corrupt: list[str] = []
    for name in _DATA_JSON:
        p = d / name
        if p.exists():
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                corrupt.append(name)
    if corrupt:
        return [Finding(WARN, "data_corrupt", {"files": ", ".join(corrupt)})]
    return [Finding(OK, "data_ok", {"dir": str(d)})]


def check_version() -> list[Finding]:
    """当前版本 + 是否有更新(走缓存,不强制联网 → 离线也不卡)。"""
    try:
        from karvyloop.update import check_update
        r = check_update()  # 缓存优先;不可达 → newer=False
        if r.get("newer") and r.get("latest"):
            return [Finding(WARN, "version_newer",
                            {"current": r["current"], "latest": r["latest"], "command": r.get("command", "")})]
        return [Finding(OK, "version_current", {"current": r.get("current", "?")})]
    except Exception:
        from karvyloop import __version__
        return [Finding(OK, "version_current", {"current": __version__})]


def check_console_port(port: int = _CONSOLE_PORT) -> list[Finding]:
    """8766 是否被占(信息级:被占 = 已有 console 在跑 / 端口冲突)。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        busy = s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        busy = False
    finally:
        s.close()
    return [Finding(WARN, "port_busy", {"port": port})] if busy else [Finding(OK, "port_free", {"port": port})]


def run_doctor(*, config_path: Optional[Path] = None, check_port: bool = True) -> list[Finding]:
    """跑全套确定性自检,返回 findings(顺序:版本/依赖/config-模型/数据/端口)。永不抛。"""
    findings: list[Finding] = []
    for fn in (check_version, check_deps):
        try:
            findings += fn()
        except Exception as e:
            findings.append(Finding(WARN, "check_error", {"err": type(e).__name__}))
    try:
        findings += check_config(config_path)
    except Exception as e:
        findings.append(Finding(WARN, "check_error", {"err": type(e).__name__}))
    try:
        findings += check_data_dir()
    except Exception:
        pass
    if check_port:
        try:
            findings += check_console_port()
        except Exception:
            pass
    return findings


def overall(findings: list[Finding]) -> str:
    """整体结论:有 fail → fail;否则有 warn → warn;否则 ok。"""
    levels = {f.level for f in findings}
    return FAIL if FAIL in levels else (WARN if WARN in levels else OK)


# ---- L1 自愈(确定性):只自动修**可逆/低风险**的;其余一律留给人拍(propose-only)----
# 升级铁律的对偶在修复上同样成立:"自动修"绝不等于"悄悄改你系统"。
AUTO_FIXABLE = {"data_corrupt"}


def repair_finding(finding: Finding) -> Optional[Finding]:
    """对可自动修的 finding 执行**可逆**修复。返回一条描述"修了什么"的 ok Finding;不可自动修 → None。

    data_corrupt:把解析不了的 JSON **备份**成 `<name>.corrupt.bak`(原子 rename),原文件移走
    → 系统下次从空重建那一个文件(其余数据不动)。可逆:用户能从 .bak 找回。
    """
    if finding.code == "data_corrupt":
        d = _data_dir()
        moved: list[str] = []
        for name in [s.strip() for s in finding.params.get("files", "").split(",") if s.strip()]:
            p = d / name
            if not p.exists():
                continue
            try:
                p.replace(d / (name + ".corrupt.bak"))   # 备份+移走(原子)
                moved.append(name)
            except Exception:
                pass
        if moved:
            return Finding(OK, "repaired_data_corrupt", {"files": ", ".join(moved)})
    return None


def apply_fixes(findings: list[Finding]) -> list[Finding]:
    """对所有可自动修的 finding 执行修复,返回"修了什么"的 Finding 列表。永不抛。"""
    out: list[Finding] = []
    for f in findings:
        if f.code in AUTO_FIXABLE:
            try:
                r = repair_finding(f)
            except Exception:
                r = None
            if r is not None:
                out.append(r)
    return out


__all__ = ["Finding", "run_doctor", "overall", "OK", "WARN", "FAIL",
           "check_config", "check_deps", "check_data_dir", "check_version", "check_console_port",
           "AUTO_FIXABLE", "repair_finding", "apply_fixes"]
