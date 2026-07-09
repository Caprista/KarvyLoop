"""karvy/slash.py — 小卡随聊**斜杠命令**(/help /status /doctor /url /version /reboot)。

以 "/" 开头的消息在 drive **之前**被确定性拦截,直接跑对应 ops 并返回 —— **零 LLM(0 token、
0 请求)**。这不只是便捷:订阅计划多按"每窗口请求次数"限流,ops 走确定性快捷 = 不吃请求配额。
只在私聊小卡(全局)时启用;业务域里不拦(那里的 "/" 可能是正文)。

**拦截很保守**(宁可漏拦、不可错吞正文):必须 ① "/" 在最前、② 恰是已注册命令、③ 命令后无多余文本、
④ 无图片/附件(附件门在 routes.api_intent 那侧)。任一不满足 → 返回 None,**原样交给正常 drive**——
这样以 "/" 开头的正文(路径 `/etc/hosts`、分数 `/`、写错的命令)不会被吞掉、附带的图也不会被丢。

返回 `{"text", "cmd", "ok"}`(键名不是 "reason",不进后端 reason 双语门);输出以数字/URL 为主、语言中性。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Any, Optional

_LEVEL_ICON = {"ok": "✓", "warn": "⚠", "fail": "✗"}


def is_slash(text: str) -> bool:
    return (text or "").lstrip().startswith("/")


def _parse(text: str) -> tuple[str, list[str]]:
    parts = (text or "").strip().lstrip("/").split()
    return (parts[0].lower() if parts else ""), parts[1:]


# ---- 处理器(每个返回一段文本;只读 app.state,defensive)----

def _cmd_help(args: list[str], app: Any) -> str:
    lines = ["🦫 小卡斜杠命令(确定性,不烧 token/请求):"]
    for name, meta in _REGISTRY.items():
        lines.append(f"  /{name} — {meta[1]}")
    return "\n".join(lines)


def _cmd_version(args: list[str], app: Any) -> str:
    import karvyloop
    return f"KarvyLoop v{karvyloop.__version__}"


def _cmd_status(args: list[str], app: Any) -> str:
    import karvyloop
    st = getattr(app, "state", None)
    out = [f"KarvyLoop v{karvyloop.__version__} · 状态"]
    stats = getattr(getattr(st, "main_loop", None), "stats", None)
    if stats is not None:
        out.append(f"  跑过 {getattr(stats, 'drive_calls', 0)} · 快脑命中 {getattr(stats, 'fast_brain_hits', 0)}"
                   f" · 慢脑 {getattr(stats, 'slow_brain_runs', 0)} · 结晶 {getattr(stats, 'crystallizations', 0)}")
    tr = getattr(st, "task_registry", None)
    if tr is not None:
        try:
            out.append(f"  任务 {len(tr.list())} 个")
        except Exception:
            pass
    pr = getattr(st, "proposal_registry", None)
    if pr is not None:
        try:
            out.append(f"  待你拍板 {len(list(pr.pending()))} 张")
        except Exception:
            pass
    led = getattr(st, "token_ledger", None)
    if led is not None:
        try:
            tot = led.totals()
            it = tot.get("input_tokens", tot.get("input", 0))
            ot = tot.get("output_tokens", tot.get("output", 0))
            out.append(f"  token 累计 in {it} / out {ot}")
        except Exception:
            pass
    return "\n".join(out)


def _cmd_doctor(args: list[str], app: Any) -> str:
    try:
        from karvyloop.doctor import run_doctor
        findings = run_doctor()
    except Exception as e:  # noqa: BLE001
        return f"🩺 doctor 跑不起来:{type(e).__name__}"
    out = ["🩺 doctor 自检(零模型):"]
    _keep = ("path", "pkg", "port", "model", "reason", "err", "dir", "version")
    for f in findings:
        icon = _LEVEL_ICON.get(getattr(f, "level", ""), "·")
        params = getattr(f, "params", {}) or {}
        detail = " ".join(f"{k}={v}" for k, v in params.items() if k in _keep)
        out.append(f"  {icon} {getattr(f, 'code', '?')}{(' — ' + detail) if detail else ''}")
    fails = sum(1 for f in findings if getattr(f, "level", "") == "fail")
    warns = sum(1 for f in findings if getattr(f, "level", "") == "warn")
    out.append("  —— " + ("全部正常 ✓" if not fails and not warns
                          else f"{fails} 错 / {warns} 警(要修:karvyloop doctor --fix)"))
    return "\n".join(out)


def _cmd_url(args: list[str], app: Any) -> str:
    try:
        from karvyloop.console.access import access_urls, read_runtime
        rt = read_runtime() or {}
        urls = access_urls(rt.get("host", "127.0.0.1"), int(rt.get("port", 8766)), rt.get("token", ""))
        lines = [f"本机(免密):{urls['local']}"]
        if urls.get("remote"):
            lines.append(f"跨设备(带 token):{urls['remote']}")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"取链接失败:{type(e).__name__}"


def _cmd_reboot(args: list[str], app: Any) -> str:
    import json
    st = getattr(app, "state", None)
    relaunch = getattr(st, "console_relaunch", None)
    if not relaunch or not relaunch.get("argv"):
        return "无法重启:没记住启动参数(console_relaunch 未设)。请手动 `karvyloop console`。"
    argv = list(relaunch["argv"])
    port = int(relaunch.get("port", 8766))
    # detached 重启器:等旧 console 退出、端口释放 → 起新的(避免端口未放就撞 bind)。
    restarter = (
        "import socket,time,subprocess,json\n"
        f"argv=json.loads({json.dumps(json.dumps(argv))}); port={port}\n"
        "for _ in range(60):\n"
        " s=socket.socket()\n"
        " try:\n  s.bind(('0.0.0.0',port)); s.close(); break\n"
        " except OSError:\n  s.close(); time.sleep(0.5)\n"
        "subprocess.Popen(argv)\n"
    )

    def _do() -> None:
        import time as _t
        _t.sleep(1.2)  # 让本次响应先发出去
        try:
            subprocess.Popen([sys.executable, "-c", restarter],
                             start_new_session=(os.name != "nt"),
                             creationflags=(subprocess.DETACHED_PROCESS if os.name == "nt" else 0))
        except Exception:
            pass
        _t.sleep(0.3)
        os._exit(0)

    threading.Thread(target=_do, daemon=True).start()
    return "🔄 正在重启 console(等端口释放再起)…重启后 token 会变,回头用 /url 或重开链接连回来。"


#: 命令注册表:name → (handler, 一句话说明)。顺序 = /help 展示序。
_REGISTRY: dict[str, tuple[Any, str]] = {
    "help": (_cmd_help, "列出所有命令"),
    "status": (_cmd_status, "系统状态(跑过/快慢脑/结晶/任务/待拍板/token)"),
    "doctor": (_cmd_doctor, "确定性自检(零模型;要修跑 karvyloop doctor --fix)"),
    "url": (_cmd_url, "本机 + 跨设备访问链接"),
    "version": (_cmd_version, "当前版本"),
    "reboot": (_cmd_reboot, "重启 console(token 会变)"),
}


def dispatch_slash(text: str, app: Any) -> Optional[dict]:
    """跑一条斜杠命令。**只在"恰是已注册命令、且命令后无多余文本"时拦截**,返回 {"text","cmd","ok"};
    否则(非斜杠 / 未知命令 / 命令后带正文)→ None,原样交给正常 drive(别吞正文)。绝不抛。
    (附件门在 routes.api_intent 那侧:有图/附件时压根不进这里。)"""
    if not is_slash(text):
        return None
    cmd, args = _parse(text)
    meta = _REGISTRY.get(cmd)
    if meta is None or args:   # 未知命令,或命令后还有多余文本 → 不拦,交给正常 drive
        return None
    try:
        return {"text": meta[0](args, app), "cmd": cmd, "ok": True}
    except Exception as e:  # noqa: BLE001
        return {"text": f"/{cmd} 出错:{type(e).__name__}", "cmd": cmd, "ok": False}


__all__ = ["is_slash", "dispatch_slash"]
