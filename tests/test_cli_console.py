"""`karvyloop console` CLI 验收测试(M3+ 批 8.5-C-frontend)。

3 条 AC(对应 plans/snoopy-singing-sunbeam.md §批 8.5-C-frontend):
- AC1: `karvyloop console --help` exit 0,含 4 选项(--host/--port/--no-browser/--config)。
- AC2: `_resolve_runtime` 在无 config + 无 workspace 路径下走默认
       (`~/.karvyloop/config.yaml` + cwd),不抛异常。
- AC3: 0.0.0.0 stderr 警告(安全地基)。

Q5 自检:不引 main_loop,不引真实 LLM,只走 argparse + 模块级常量。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from karvyloop.cli._runtime import ResolvedRuntime, resolve_runtime
from karvyloop.console.entry import build_console_parser


# ============ AC1: `karvyloop console --help` ============

def test_console_subcommand_help_via_main():
    """通过主入口调 --help(走子命令 router),exit 0,输出含 4 选项。"""
    result = subprocess.run(
        [sys.executable, "-m", "karvyloop.cli.main", "console", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, f"stderr={result.stderr}"
    out = result.stdout
    for opt in ("--host", "--port", "--no-browser", "--config"):
        assert opt in out, f"missing {opt} in:\n{out}"


# ============ AC2: resolve_runtime defaults ============

def test_resolve_runtime_with_no_config_no_workspace(tmp_path, monkeypatch):
    """无 config / 无 workspace 时,走默认:`~/.karvyloop/config.yaml` + cwd,不抛。"""
    # 隔离 home 避免污染真实 ~/.karvyloop
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    resolved: ResolvedRuntime = resolve_runtime()

    # 默认 path 解析
    assert resolved.config_path == tmp_path / ".karvyloop" / "config.yaml"
    # config 不存在 → silent-fail: main_loop=None, runtime_kwargs={}
    assert resolved.main_loop is None
    assert resolved.runtime_kwargs == {}
    # skills_dir 走默认
    assert resolved.skills_dir == tmp_path / ".karvyloop" / "skills"


def test_resolve_runtime_returns_dataclass():
    """返回值是 ResolvedRuntime 实例,字段齐全。"""
    resolved = resolve_runtime()
    assert isinstance(resolved, ResolvedRuntime)
    assert hasattr(resolved, "config_path")
    assert hasattr(resolved, "main_loop")
    assert hasattr(resolved, "runtime_kwargs")
    assert hasattr(resolved, "skills_dir")


# ============ AC3: 0.0.0.0 stderr warning ============

def test_console_warns_on_lan_bind(monkeypatch, capsys, tmp_path):
    """--host 0.0.0.0 触发 stderr 警告(CLAUDE.md 安全地基)。

    借 Q5:不实际启 uvicorn.run,只调用 cmd_console 验证 stderr 行为。
    """
    import pathlib
    from argparse import Namespace
    from karvyloop.console.entry import cmd_console
    from karvyloop.llm.token_ledger import get_ledger, register_ledger

    # **隔离真 HOME**:cmd_console 会往 `~/.karvyloop/` 写一堆东西(token 账本/对话/域/原子/角色),
    # 并把**真账本**注册成全局 → 不隔离的话:① 在用户真 ~/.karvyloop 留垃圾 ② 全局账本留着,
    # 后续 gateway/executor 桩测试(p/a、claude-opus 等假数据)会写进用户真 tokens.db,污染 token 面板
    # (Hardy 在真机 token 面板上抓到的 6M anthropic/claude-opus 假数据正是这么来的)。
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))

    args = Namespace(
        host="0.0.0.0",
        port=8766,
        config=None,
        no_browser=True,    # 阻止自动开浏览器
        no_llm=True,        # 跳过 MainLoop 注入(silent-fail 路径)
    )

    # 用 monkeypatch 阻止 uvicorn.run 真起(避免阻塞测试)
    import uvicorn
    uvicorn_called = []
    def fake_uvicorn_run(*_a, **_kw):
        uvicorn_called.append((_a, _kw))
    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)
    # 隔离端口状态:若真机上 8766 已被占(VM 上常有跑着的 console),cmd_console 会走"已在运行"
    # 早返回、不到 uvicorn.run → 本测验的是"绑 0.0.0.0 会警告 + 起服务",必须 mock 端口空闲才确定。
    import karvyloop.console.entry as _entry
    monkeypatch.setattr(_entry, "_port_free", lambda *a, **k: True)

    _prev_ledger = get_ledger()
    try:
        rc = cmd_console(args)
        assert rc == 0
        captured = capsys.readouterr()
        # stderr 应含 LAN 暴露警告
        assert "0.0.0.0" in captured.err
        assert "LAN" in captured.err or "安全" in captured.err or "受信" in captured.err
        # uvicorn.run 应被调用(host=0.0.0.0)
        assert len(uvicorn_called) == 1
        assert uvicorn_called[0][1].get("host") == "0.0.0.0"
    finally:
        register_ledger(_prev_ledger)   # 别把 console 注册的账本留在全局,污染后续测试记账


def test_console_no_warn_on_localhost(monkeypatch, capsys):
    """默认 127.0.0.1 不应触发 LAN 警告。"""
    from argparse import Namespace
    from karvyloop.console.entry import cmd_console

    args = Namespace(
        host="127.0.0.1",
        port=8766,
        config=None,
        no_browser=True,
        no_llm=True,
    )
    import karvyloop.console.entry as entry_mod
    import uvicorn as uvicorn_mod
    monkeypatch.setattr(uvicorn_mod, "run", lambda *a, **kw: None)

    cmd_console(args)
    captured = capsys.readouterr()
    # 不应含 LAN 警告
    assert "LAN" not in captured.err
    assert "受信" not in captured.err


# ============ build_console_parser 自测 ============

def test_build_console_parser_registers_correctly():
    """build_console_parser 注册到 sub 后,`karvyloop console` 可被 parse。"""
    import argparse
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    p_console = build_console_parser(sub)
    assert p_console is not None

    args = p.parse_args(["console", "--host", "0.0.0.0", "--port", "9000", "--no-browser"])
    assert args.cmd == "console"
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.no_browser is True
