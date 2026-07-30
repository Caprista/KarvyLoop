"""test_console_takeover — 自愈接管:没起好的僵尸控制台占着端口 → 替用户清掉+接管。

Hardy 2026-07-30 事故:浏览器"MainLoop 未注入 请先 init",init 修不掉——真因是一个没起好的旧
KarvyLoop(MainLoop 未注入)蹲在 8766,浏览器连的是它。用户不该懂"端口/进程/kill"——console 启动
时该**自己接管**:替他清掉没起好的旧实例、接手。用户只看到人话结果。

安全铁律:**只终止 console.runtime.json 里登记的、且端口对得上的那个 pid**,绝不乱杀。这里用**真
子进程真占端口真被杀**验证(Windows 也能跑:os.kill=TerminateProcess),外加"pid/端口对不上就不动"。
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest

from karvyloop.console.entry import _port_free, _take_over_console


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _spawn_holder():
    """起一个真子进程,绑一个空闲端口一直 listen(模拟占端口的僵尸)。返回 (proc, port)。
    抗竞态:_free_port→子进程 bind 之间有 TOCTOU(满负载下别的测试可能抢走该端口),重试几个端口;
    都抢不到 → skip(诚实,不假红也不假绿)。"""
    for _ in range(5):
        port = _free_port()
        code = (
            "import socket,time,sys\n"
            "s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)\n"
            "try:\n"
            f"    s.bind(('127.0.0.1',{port}))\n"
            "    s.listen()\n"
            "except OSError:\n"
            "    sys.exit(3)\n"       # 端口被抢 → 立刻退,让父进程换端口重试
            "time.sleep(60)\n"
        )
        p = subprocess.Popen([sys.executable, "-c", code])
        for _ in range(60):          # 等它真占上端口(或它自己 exit=没抢到)
            if p.poll() is not None:
                break                # 子进程退了(没抢到端口)→ 换端口重试
            if not _port_free("127.0.0.1", port):
                return p, port       # 真占上了
            time.sleep(0.05)
        if p.poll() is None:
            p.kill(); p.wait(timeout=5)
    pytest.skip("端口竞态:多次都没抢到空闲端口占住(满负载),跳过(非代码问题)")


def _spawn_sleeper() -> subprocess.Popen:
    """起一个真子进程只 sleep(不绑端口)——给"端口对不上就绝不杀"这类只需一个活进程的测试用。"""
    return subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])


def test_takeover_kills_registered_squatter_and_frees_port(monkeypatch):
    squatter, port = _spawn_holder()
    try:
        assert not _port_free("127.0.0.1", port), "子进程应已占住端口"
        # runtime 登记的正是这个僵尸(pid + 同端口)→ 接管应终止它、腾空端口
        monkeypatch.setattr(
            "karvyloop.console.access.read_runtime",
            lambda: {"pid": squatter.pid, "port": port, "host": "127.0.0.1"})
        assert _take_over_console("127.0.0.1", port) is True
        assert _port_free("127.0.0.1", port), "接管后端口应腾空"
        assert squatter.poll() is not None, "僵尸进程应已被终止"
    finally:
        if squatter.poll() is None:
            squatter.kill()
            squatter.wait(timeout=5)


def test_takeover_refuses_when_port_mismatch_does_not_kill(monkeypatch):
    """安全:runtime 记的端口与当前不符 → 绝不动那个 pid(不乱杀)。不需真占端口(mismatch 在碰进程
    前就返回)——用纯 sleeper 避免端口竞态假红。"""
    proc = _spawn_sleeper()
    port = _free_port()
    try:
        # runtime 记的是**别的**端口 → 接管应拒绝、不碰这个 pid
        monkeypatch.setattr(
            "karvyloop.console.access.read_runtime",
            lambda: {"pid": proc.pid, "port": port + 1, "host": "127.0.0.1"})
        assert _take_over_console("127.0.0.1", port) is False
        assert proc.poll() is None, "端口对不上时绝不能杀它"
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_takeover_no_runtime_is_noop(monkeypatch):
    monkeypatch.setattr("karvyloop.console.access.read_runtime", lambda: None)
    assert _take_over_console("127.0.0.1", _free_port()) is False


def test_takeover_dead_pid_frees_port_true(monkeypatch):
    """登记的 pid 已经死了、端口其实是空的 → 视作已腾空(True),不炸。"""
    port = _free_port()   # 没人占
    monkeypatch.setattr(
        "karvyloop.console.access.read_runtime",
        lambda: {"pid": 2 ** 31 - 1, "port": port, "host": "127.0.0.1"})  # 极大 pid≈不存在
    assert _take_over_console("127.0.0.1", port) is True
