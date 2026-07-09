"""capability_install — 「能力解锁」的**一键启用**(app 替用户装可选件,不用敲命令)。

Hardy 2026-07-09:"你也没引导用户装啊,用户想用你咋办 —— 别纯靠你判断他默认不需要。"
能力解锁面板本来只给"复制这条 pip 命令自己跑"(还是有门槛)。这里让面板能**替用户装**:
点「启用」→ 后台 `pip install <底层包>` → 进度/结果回传 → 装完下次用即生效(可选件都是懒加载,
调用时才 import,**无需重启**)。

设计:
- **直装底层包,不装 `karvyloop[extra]`**:karvyloop 还没上 PyPI,`pip install karvyloop[ocr]` 会找不到包;
  但底层依赖(paddleocr / faster-whisper / …)都在 PyPI,直接按名装 —— 编辑安装/pip 安装都通。
  包清单是 pyproject extras 的**镜像**,`test_capability_install` 交叉核对防漂移。
- **externally-managed 兜底**:Debian 系 Python 直接 pip 会被 PEP 668 拦;探到该错自动重试
  `--break-system-packages`(用户机器上装自己要的可选件,是机主意愿,不是越权)。
- **线程执行 + 状态文件**:装可能几分钟(paddlepaddle 重),端点即返 started,前端轮询状态文件。
  与一键升级不同:这里**不重启 console**(可选件懒加载),故用线程即可,不需 detached runner。
- 安全同一键升级:端点侧加 CSRF 头 + 本机/私网来源门(见 routes 层);本模块只管"装 + 记状态"。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

#: 可一键启用的可选件 → 底层 pip 包(**pyproject [project.optional-dependencies] 的镜像**;
#: test 交叉核对防漂移)。纯配置型能力(webhook/email)不在此(没有可装的东西)。
INSTALLABLE: dict[str, list[str]] = {
    "ocr": ["paddleocr>=2.7", "paddlepaddle>=2.6", "pillow>=10.0"],
    "asr": ["faster-whisper>=1.1"],
    "files": ["pypdf>=4.0", "python-docx>=1.1", "openpyxl>=3.1"],
    "relay": ["cryptography>=42"],
    "mcp": ["mcp>=1.9"],
    "web": ["playwright>=1.40"],   # 注:装完还需 `playwright install chromium`(前端文案说明,不算失败)
}

#: 装完还需额外一步(纯一键装不全)的能力 —— 前端据此提示,不当失败。
NEEDS_EXTRA_STEP: dict[str, str] = {"web": "playwright install chromium"}

_KL = Path.home() / ".karvyloop"
_STALE_SECS = 1800   # 状态文件超 30min 视为陈旧(装崩了没写完 → 允许重试)


def _status_path(cap_id: str) -> Path:
    return _KL / f"_enable_{cap_id}.json"


def _write(cap_id: str, d: dict[str, Any]) -> None:
    try:
        _KL.mkdir(parents=True, exist_ok=True)
        d["ts"] = time.time()
        d["id"] = cap_id
        _status_path(cap_id).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def read_status(cap_id: str) -> dict[str, Any]:
    """读某能力的启用进度/结果;无 → {}。state ∈ running|done|failed。"""
    try:
        d = json.loads(_status_path(cap_id).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _is_running_fresh(cap_id: str) -> bool:
    d = read_status(cap_id)
    return d.get("state") == "running" and (time.time() - float(d.get("ts", 0))) < _STALE_SECS


def _run_pip(packages: list[str], python: str) -> tuple[int, str]:
    """跑 pip install;externally-managed(PEP 668)→ 自动重试 --break-system-packages。返回 (rc, 尾部输出)。"""
    base = [python, "-m", "pip", "install", *packages]
    try:
        p = subprocess.run(base, capture_output=True, text=True, timeout=1800)
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode != 0 and "externally-managed" in out.lower():
            p2 = subprocess.run([*base, "--break-system-packages"],
                                capture_output=True, text=True, timeout=1800)
            out = (p2.stdout or "") + (p2.stderr or "")
            return p2.returncode, out[-1500:]
        return p.returncode, out[-1500:]
    except subprocess.TimeoutExpired:
        return -1, "pip install 超时(>30min);网络太慢或包太大,请稍后重试或手动安装。"
    except Exception as e:  # noqa: BLE001
        return -1, f"启动 pip 失败:{type(e).__name__}: {e}"


def _worker(cap_id: str, packages: list[str], python: str) -> None:
    rc, tail = _run_pip(packages, python)
    if rc == 0:
        _write(cap_id, {"state": "done", "rc": 0,
                        "extra_step": NEEDS_EXTRA_STEP.get(cap_id, "")})
    else:
        _write(cap_id, {"state": "failed", "rc": rc, "tail": tail})


def start_install(cap_id: str, *, python: Optional[str] = None,
                  runner=None) -> dict[str, Any]:
    """启动一次一键启用(线程后台跑 pip)。返回 {ok, started|reason, state}。

    ``runner`` 可注入(测试用:跳过真 pip,直接给结果);默认起后台线程真装。
    """
    if cap_id not in INSTALLABLE:
        return {"ok": False, "reason": f"'{cap_id}' 不是可一键安装的能力"}
    if _is_running_fresh(cap_id):
        return {"ok": True, "started": False, "state": "running", "reason": "已在安装中"}
    packages = INSTALLABLE[cap_id]
    py = python or sys.executable
    _write(cap_id, {"state": "running", "packages": packages})
    if runner is not None:
        runner(cap_id, packages, py)          # 同步注入(测试)
    else:
        threading.Thread(target=_worker, args=(cap_id, packages, py), daemon=True).start()
    return {"ok": True, "started": True, "state": "running"}


__all__ = ["INSTALLABLE", "NEEDS_EXTRA_STEP", "start_install", "read_status"]
