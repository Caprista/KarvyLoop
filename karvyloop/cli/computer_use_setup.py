"""computer-use setup/doctor —— 把"能动手"从手动 SSH sysadmin 变成用户一条命令上手(docs/99).

背景(真机 E2E 门到门逼出来的):computer use 的**看**(截图)开箱即用,但**动**(键盘/鼠标)
在 Linux/Wayland 上要一套输入后端 —— computer-use-linux 的键盘走 **ydotool**,而 ydotool 要:
① 装 ydotool ② /dev/uinput 可访问 ③ ydotoold 常驻。这套我在 VM 上是手动 sudo 补的;对真实
用户不能靠手动,得有个**诊断 + 引导修**的 L0 门(零模型、确定性,同 `karvyloop doctor` 的
"无门槛修"精神 [[karvyloop-self-healing-ops]])。

**这门只诊断 + 出计划 + 跑安全的那批,privileged 的那批生成一段可复核的脚本让你 sudo 跑**
(你始终在驾驶座,不替你偷偷 sudo —— 同 doctor 的 CONFIRM_FIXABLE 精神)。跨平台:V1 聚焦
GNOME/Wayland Linux(computer-use-linux 验证过的环境);mac/Win 输入路径不同,如实标 out-of-scope。

用法(在你的 Linux 桌面上):
    python -m karvyloop.cli.computer_use_setup          # 诊断 + 打印计划 + 跑安全项
    python -m karvyloop.cli.computer_use_setup --run    # 额外:交互确认后连 privileged 脚本一起跑
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import subprocess
import sys
from typing import Optional

def _ydotool_socket_path(uid: int) -> str:
    """computer-use-linux 的 ydotool client **写死**用 $XDG_RUNTIME_DIR/.ydotool_socket
    (**忽略** YDOTOOL_SOCKET —— VM 门到门实测:设了也不认)→ 常驻服务必须把 socket 放这。"""
    return f"/run/user/{uid}/.ydotool_socket"


@dataclasses.dataclass
class Env:
    """setup 计划的输入(纯状态;_detect() 探出来,plan_setup() 只读它 → 可单测)。"""
    is_linux: bool
    session_type: str          # "wayland" / "x11" / "tty" / ""(SSH 无图形会话)
    desktop: str               # 如 "ubuntu:GNOME"
    server_installed: bool      # computer-use-linux 在不在
    ydotool_installed: bool
    ydotoold_running: bool      # ydotoold 守护进程在跑(输入后端活着;查进程比查 socket 文件可靠)
    a11y_on: bool               # toolkit-accessibility 开没开
    distro_pkg: str            # "apt" / "dnf" / "pacman" / ""(认不出)
    uid: int
    gid: int


@dataclasses.dataclass
class Step:
    """一条修复步骤。auto=能安全自动跑(非特权、幂等);privileged=要 sudo/改系统。"""
    code: str
    title: str
    why: str
    commands: list         # 要跑的命令(privileged 的进"复核脚本")
    privileged: bool = False
    auto: bool = False


def _pkg_install_cmd(distro_pkg: str, pkg: str) -> str:
    return {
        "apt": f"sudo apt-get install -y {pkg}",
        "dnf": f"sudo dnf install -y {pkg}",
        "pacman": f"sudo pacman -S --noconfirm {pkg}",
    }.get(distro_pkg, f"# 用你的包管理器安装 {pkg}(未能识别发行版)")


def _ydotoold_unit(uid: int, gid: int) -> str:
    """root systemd 服务:ydotoold 常驻,socket 放 client 默认位($XDG_RUNTIME_DIR/.ydotool_socket)
    且归你。root 开 /dev/uinput → 免把你加进 input 组、免重登。ExecStartPre 等登录会话就绪 + 清
    残留 socket(登录/注销会重建 /run/user/UID);Restart + StartLimitIntervalSec=0 兜登录切换。"""
    sock = _ydotool_socket_path(uid)
    return (
        "[Unit]\n"
        "Description=ydotoold (KarvyLoop computer-use input backend)\n"
        "After=systemd-user-sessions.service\n"
        "StartLimitIntervalSec=0\n\n"
        "[Service]\n"
        f"ExecStartPre=/bin/sh -c 'until [ -d /run/user/{uid} ]; do sleep 1; done; rm -f {sock}'\n"
        f"ExecStart=/usr/bin/ydotoold --socket-path={sock} "
        f"--socket-own={uid}:{gid} --socket-perm=0660\n"
        "Restart=always\n"
        "RestartSec=2\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def plan_setup(env: Env) -> list:
    """纯函数:环境状态 → 有序修复计划。**只读 env,不跑任何东西** → 可单测。"""
    steps: list = []

    if not env.is_linux:
        steps.append(Step(
            "not_linux",
            "computer use 输入后端目前只自动配 Linux",
            "本机不是 Linux。macOS 需在系统设置里授予 Accessibility + Screen Recording;"
            "Windows 用系统原生输入。V1 的自动 setup 只覆盖 GNOME/Wayland Linux。",
            [], privileged=False, auto=False))
        return steps

    if env.session_type not in ("wayland", "x11"):
        steps.append(Step(
            "no_gui_session",
            "得在图形桌面里跑,不是 SSH",
            "computer use 要控你看得见的桌面;当前不在图形会话里(session_type="
            f"{env.session_type or '空'})。请到桌面上打开一个终端再跑 setup 和 E2E。",
            [], privileged=False, auto=False))

    if not env.server_installed:
        steps.append(Step(
            "install_server",
            "装 computer-use MCP server",
            "驱动桌面(截图/a11y/输入)的上游 server;缺它 computer use 整个跑不起来。",
            ["npm install -g @agent-sh/computer-use-linux"],
            privileged=True, auto=False))

    if not env.ydotool_installed:
        steps.append(Step(
            "install_ydotool",
            "装 ydotool(键盘/鼠标注入后端)",
            "computer use 的**输入**(点击/键入)走 ydotool;缺它只能看不能动。",
            [_pkg_install_cmd(env.distro_pkg, "ydotool")],
            privileged=True, auto=False))

    if not env.a11y_on:
        steps.append(Step(
            "enable_a11y",
            "开无障碍(a11y)工具包",
            "让应用暴露无障碍树,planner 才能按语义元素定位(而不是纯猜坐标)。非特权、可逆。",
            ["gsettings set org.gnome.desktop.interface toolkit-accessibility true"],
            privileged=False, auto=True))

    if not env.ydotoold_running:
        steps.append(Step(
            "ydotoold_service",
            "装 ydotoold 常驻服务(开机自启、socket 放 client 默认位、免重登)",
            "输入后端要一个常驻守护进程 + 可访问 /dev/uinput。装成 root systemd 服务最省事:root 开"
            " uinput、socket 放 computer-use-linux 认的默认位($XDG_RUNTIME_DIR/.ydotool_socket)"
            "且归你、开机自启,不用每次手动起、也不用重新登录换用户组。",
            [f"sudo tee /etc/systemd/system/ydotoold.service >/dev/null <<'UNIT'\n"
             f"{_ydotoold_unit(env.uid, env.gid)}UNIT",
             "sudo systemctl daemon-reload",
             "sudo systemctl enable --now ydotoold"],
            privileged=True, auto=False))

    # 窗口定位扩展(可选增强,不装也能靠截图+键盘干活)
    steps.append(Step(
        "window_targeting",
        "(可选)装窗口定位 GNOME 扩展",
        "让 list_windows/activate_window 可用;不装也能干活(planner 靠截图+键盘绕过,"
        "真机 E2E 已验)。要它就跑,登录会话里生效可能要重开 GNOME。",
        ["computer-use-linux setup-window-targeting"],
        privileged=False, auto=False))

    return steps


# ---------------------------------------------------------------- 探测 + 执行

def _detect() -> Env:
    uid = os.getuid() if hasattr(os, "getuid") else 0
    gid = os.getgid() if hasattr(os, "getgid") else 0
    is_linux = sys.platform.startswith("linux")
    session_type = os.environ.get("XDG_SESSION_TYPE", "") or ""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "") or ""
    server = shutil.which("computer-use-linux") is not None
    ydotool = shutil.which("ydotool") is not None
    # 输入后端就绪 = ydotoold 进程在 **且** socket 在 client 默认位(两者都要:进程在但 socket
    # 放错位 = 动作仍失败;socket 文件在但进程死 = 残留假阳。两个坑门到门都踩过)。
    default_sock = f"{os.environ.get('XDG_RUNTIME_DIR', '') or f'/run/user/{uid}'}/.ydotool_socket"
    ydotoold_running = _proc_running("ydotoold") and _is_socket(default_sock)
    a11y_on = _gsettings_bool("org.gnome.desktop.interface", "toolkit-accessibility")
    distro_pkg = _detect_pkg_mgr()
    return Env(is_linux=is_linux, session_type=session_type, desktop=desktop,
               server_installed=server, ydotool_installed=ydotool,
               ydotoold_running=ydotoold_running, a11y_on=a11y_on,
               distro_pkg=distro_pkg, uid=uid, gid=gid)


def _proc_running(name: str) -> bool:
    try:
        return subprocess.run(["pgrep", "-x", name], capture_output=True,
                              timeout=8).returncode == 0
    except Exception:
        return False


def _is_socket(path: str) -> bool:
    try:
        import stat
        return stat.S_ISSOCK(os.stat(path).st_mode)   # 跟随 symlink → 指到活 socket 也算
    except Exception:
        return False


def _gsettings_bool(schema: str, key: str) -> bool:
    try:
        out = subprocess.run(["gsettings", "get", schema, key],
                             capture_output=True, text=True, timeout=8)
        return out.stdout.strip() == "true"
    except Exception:
        return False


def _detect_pkg_mgr() -> str:
    for mgr in ("apt-get", "dnf", "pacman"):
        if shutil.which(mgr):
            return "apt" if mgr == "apt-get" else mgr
    return ""


def _run_auto(step: Step) -> bool:
    """跑一条 auto(非特权)命令。返回是否全成功。绝不 sudo。"""
    ok = True
    for cmd in step.commands:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            ok = ok and (r.returncode == 0)
        except Exception:
            ok = False
    return ok


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m karvyloop.cli.computer_use_setup",
        description="computer use 输入后端诊断 + 引导修(GNOME/Wayland Linux)。")
    p.add_argument("--run", action="store_true",
                   help="额外:交互确认后,连需要 sudo 的 privileged 脚本一起跑(默认只打印让你复核)")
    a = p.parse_args(argv)

    env = _detect()
    plan = plan_setup(env)
    print("== computer use 就绪检查 ==")
    print(f"  平台: {'linux' if env.is_linux else sys.platform}  会话: {env.session_type or '(无)'}"
          f"  桌面: {env.desktop or '?'}")
    print(f"  server={_yn(env.server_installed)} ydotool={_yn(env.ydotool_installed)} "
          f"输入常驻={_yn(env.ydotoold_running)} a11y={_yn(env.a11y_on)}")

    auto_steps = [s for s in plan if s.auto]
    priv_steps = [s for s in plan if s.privileged]
    info_steps = [s for s in plan if not s.auto and not s.privileged]

    for s in info_steps:
        print(f"\nℹ️  {s.title}\n    {s.why}")

    # 安全项:直接跑(非特权、幂等)
    for s in auto_steps:
        print(f"\n▶ 自动修:{s.title}\n    {s.why}")
        print("    ✓ 已修" if _run_auto(s) else "    ⚠ 没跑成(可手动跑:" + " ; ".join(s.commands) + ")")

    # privileged 项:生成可复核脚本
    if priv_steps:
        print("\n── 下面这些要 sudo / 改系统,给你一段**可复核**的脚本 ──")
        script_lines: list = []
        for s in priv_steps:
            print(f"\n#  {s.title}\n#  {s.why}")
            for c in s.commands:
                print(c)
                script_lines.append(c)
        if a.run and sys.stdin.isatty():
            sys.stdout.write("\n以上都要 sudo,确认全部执行? [y/N] ")
            sys.stdout.flush()
            if sys.stdin.readline().strip().lower() in ("y", "yes"):
                for c in script_lines:
                    print(f"\n$ {c}")
                    subprocess.run(c, shell=True)
            else:
                print("跳过(你可以自己复核后逐条跑)。")
        else:
            print("\n(复核无误后自己跑上面这几条;或加 --run 让我交互确认后代跑。)")

    print("\n跑完 `python -m karvyloop.cli.computer_use_e2e` 验证:能看能动就成了。"
          "\n提示:输入 socket 放在 computer-use-linux 认的默认位 $XDG_RUNTIME_DIR/.ydotool_socket"
          "(它写死这条路径、不认 YDOTOOL_SOCKET)。")
    return 0


def _yn(b: bool) -> str:
    return "✓" if b else "✗"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
