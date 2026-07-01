"""cli 验收测试 —— 逐条对应 docs/modules/workbench-cli.md §5 验收标准。

6 条 AC:init 合法 yaml / run 垂直切片(端到端 mock) / 思维链工具权限可见 /
快脑标注 / 结晶确认 / 权限 ask 渲染。
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

from karvyloop.atoms import Terminal
from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round, tool_round
from karvyloop.cli import (
    CONFIG_PATH,
    DECISION_ALLOW,
    DECISION_ALLOW_ALWAYS,
    DECISION_DENY,
    Renderer,
    ask_permission,
    cmd_init,
    cmd_run,
    confirm_crystallize,
    main,
)
from karvyloop.gateway import GatewayClient, ModelRegistry
from karvyloop.sandbox.base import Sandbox
from karvyloop.sandbox.exec_result import ExecResult


# ---- 测试用 FakeSandbox（与 test_forge.py 同形,放本文件避免互相依赖）----

class FakeSandbox(Sandbox):
    def __init__(self, root: str):
        self.root = root
        self.files: dict[str, bytes] = {}

    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0,
                   max_output_bytes=30_000) -> ExecResult:
        cmd = " ".join(argv[1:]) if argv and argv[0] == "sh" and len(argv) > 2 else " ".join(argv)
        if cmd.startswith("echo "):
            out = cmd[5:].encode("utf-8") + b"\n"
        else:
            out = b""
        return ExecResult(stdout=out, stderr=b"", exit_code=0)

    async def write_file(self, path, content, token):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        self.files[path] = content

    async def read_file(self, path, token):
        if path in self.files:
            return self.files[path]
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()
        raise FileNotFoundError(path)


def _mock_config(tmp_path: Path) -> Path:
    """写一份最小可用的 config.yaml(只含 chat 模型)。"""
    cfg = {
        "models": {
            "providers": {
                "p": {
                    "base_url": "x",
                    "models": [
                        {
                            "id": "p/a",
                            "api": "anthropic-messages",
                            "context_window": 1000,
                            "max_tokens": 100,
                        },
                    ],
                },
            },
        },
        "agents": {"defaults": {"model": "p/a"}},
        "embedding": {"model": "p/a"},
    }
    import yaml
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


def _patched_sandbox(monkeypatch, root: str) -> FakeSandbox:
    """替换 default_sandbox 返回 FakeSandbox。"""
    from karvyloop.sandbox import selector
    sb = FakeSandbox(root)
    monkeypatch.setattr(selector, "default_sandbox", lambda: sb)
    monkeypatch.setattr("karvyloop.cli.run.default_sandbox", lambda: sb)
    return sb


# ============ AC1:init 生成合法 config.yaml ============
def test_ac1_init_generates_legal_yaml(tmp_path: Path, monkeypatch):
    target = tmp_path / "sub" / "config.yaml"
    rc = cmd_init(path=target, interactive=False, force=False, stdout=io.StringIO())
    assert rc == 0
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    # 合法 YAML
    import yaml
    cfg = yaml.safe_load(text)
    assert "models" in cfg and "providers" in cfg["models"]
    # 默认含 ollama + anthropic
    assert "ollama" in cfg["models"]["providers"]
    assert "anthropic" in cfg["models"]["providers"]
    # 默认模型与 embedding
    assert "model" in cfg["agents"]["defaults"]
    assert "model" in cfg["embedding"]
    # 本地优先:ollama base_url 是 127.0.0.1
    assert "127.0.0.1" in cfg["models"]["providers"]["ollama"]["base_url"]
    # 密钥占位 ${...} 不写明文
    assert "${ANTHROPIC_API_KEY}" in text


def test_ac1b_init_refuses_existing_without_force(tmp_path: Path):
    target = tmp_path / "cfg.yaml"
    target.write_text("already: here", encoding="utf-8")
    rc = cmd_init(path=target, interactive=False, force=False, stdout=io.StringIO())
    assert rc == 1
    # 原文不变
    assert target.read_text(encoding="utf-8") == "already: here"


def test_ac1c_init_force_overwrites(tmp_path: Path):
    target = tmp_path / "cfg.yaml"
    target.write_text("old", encoding="utf-8")
    rc = cmd_init(path=target, interactive=False, force=True, stdout=io.StringIO())
    assert rc == 0
    assert "models" in target.read_text(encoding="utf-8")


# ============ AC2:run 跑通垂直切片(慢脑→沙箱执行→返回)============
def test_ac2_run_vertical_slice(tmp_path: Path, monkeypatch):
    cfg_path = _mock_config(tmp_path)
    sb = _patched_sandbox(monkeypatch, str(tmp_path))
    sb.files[str(tmp_path / "hello.txt")] = b"hi"
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"file_path": str(tmp_path / "hello.txt")}),
        text_round("done"),
    ])
    # 注入 mock adapter:在 GatewayClient 引入的模块里替换 default_adapters
    monkeypatch.setattr(
        "karvyloop.gateway.client.default_adapters",
        lambda: {"anthropic-messages": adapter},
    )

    rc = cmd_run(
        "read hello",
        config_path=cfg_path,
        workspace_root=str(tmp_path),
        renderer=Renderer(out=io.StringIO(), color=False),
        no_recall=True,           # AC2 测的是"垂直切片"慢脑路径;不跳过 recall 会命中前序 test 结晶的快脑
    )
    assert rc == 0
    # 验证 mock provider 被调过 2 次 (round 1 调工具 / round 2 收工具结果出 "done")
    assert adapter.call_count == 2


def test_ac2b_run_uses_ndjson_when_json_flag(tmp_path: Path, monkeypatch, capsys):
    cfg_path = _mock_config(tmp_path)
    _patched_sandbox(monkeypatch, str(tmp_path))
    adapter = ScriptedMockAdapter(rounds=[text_round("ok")])
    monkeypatch.setattr(
        "karvyloop.gateway.client.default_adapters",
        lambda: {"anthropic-messages": adapter},
    )
    rc = cmd_run(
        "x", config_path=cfg_path, workspace_root=str(tmp_path),
        json_output=True,
    )
    assert rc == 0
    captured = capsys.readouterr()
    # 至少有一行 NDJSON
    lines = [l for l in captured.out.splitlines() if l]
    assert lines
    # 每行合法 JSON
    for line in lines:
        obj = json.loads(line)
        assert obj["schema"] == "karvyloop-forge-ndjson"
        assert obj["v"] == 1


def test_ac2c_run_no_config_returns_error(tmp_path: Path, monkeypatch, capsys):
    _patched_sandbox(monkeypatch, str(tmp_path))
    rc = cmd_run("x", config_path=tmp_path / "missing.yaml",
                 workspace_root=str(tmp_path))
    assert rc == 1
    captured = capsys.readouterr()
    # 9.4 双语:系统默认 en;断言 locale-neutral 子串(两语都含 "karvyloop init")
    assert "karvyloop init" in captured.err


# ============ AC3:思维链/工具调用/权限请求在终端可见可回溯 ============
def test_ac3_render_tool_use_visible():
    out = io.StringIO()
    r = Renderer(out=out, color=False)
    # 模拟 ToolCallEvent-like
    class FakeEv:
        kind = "tool_call"
        name = "read_file"
        input = {"file_path": "/x/y.txt"}
    r.render(FakeEv())
    # 模拟 ToolResultEvent-like
    class FakeResult:
        is_error = False
        error_reason = ""
    class FakeResEv:
        kind = "tool_result"
        result = FakeResult()
    r.render(FakeResEv())
    text = out.getvalue()
    assert "read_file" in text
    assert "⚙" in text
    assert "✓" in text


def test_ac3b_render_permission_ask_visible():
    out = io.StringIO()
    r = Renderer(out=out, color=False)
    class FakeAsk:
        kind = "permission_ask"
        tool = "run_command"
        subject = "rm -rf /tmp/foo"
    r.render(FakeAsk())
    text = out.getvalue()
    assert "权限请求" in text
    assert "run_command" in text
    assert "rm -rf" in text


def test_ac3c_render_text_streamed():
    out = io.StringIO()
    r = Renderer(out=out, color=False)
    class FakeText:
        kind = "text_delta"  # 仿真实事件流
        text = "hello world"
    r.render(FakeText())
    assert out.getvalue() == "hello world"


# ============ AC4:快脑命中显式标注"省了 X token" ============
def test_ac4_fast_brain_note():
    out = io.StringIO()
    r = Renderer(out=out, color=False)
    r.fast_brain_note("daily-report", saved_tokens=850)
    text = out.getvalue()
    assert "⚡" in text
    assert "daily-report" in text
    assert "850" in text
    assert "token" in text.lower()


# ============ AC5:结晶前弹用户确认(不偷偷固化)============
def test_ac5_confirm_crystallize_default_no():
    out = io.StringIO()
    inp = io.StringIO("")  # 空 → 默认 N
    # 强制 non-tty 行为靠默认;这里靠 input = "" + stdin
    # 实际 _isatty() 会返回 False(非真 tty)→ 直接 default
    # 想测交互路径,monkeypatch _isatty
    from karvyloop.cli import prompt_ui
    orig = prompt_ui._isatty
    prompt_ui._isatty = lambda: True
    try:
        # 用户直接回车 → 默认 N
        result = confirm_crystallize("3", stdin=io.StringIO("\n"), stdout=out)
        assert result is False
        # 用户输入 y → yes
        result2 = confirm_crystallize("3", stdin=io.StringIO("y\n"), stdout=out)
        assert result2 is True
    finally:
        prompt_ui._isatty = orig


def test_ac5b_confirm_crystallize_non_tty_uses_default_false():
    """非 TTY → 默认 False(不偷偷固化)。"""
    from karvyloop.cli import prompt_ui
    orig = prompt_ui._isatty
    prompt_ui._isatty = lambda: False
    try:
        # 不传 stdin/stdout 也不应抛
        result = confirm_crystallize("3", default=False)
        assert result is False
        result2 = confirm_crystallize("3", default=True)
        assert result2 is True
    finally:
        prompt_ui._isatty = orig


# ============ AC6:权限 ask 渲染 + 决策回传 ============
def test_ac6_ask_permission_decision_matrix():
    from karvyloop.cli import prompt_ui
    orig = prompt_ui._isatty
    prompt_ui._isatty = lambda: True
    try:
        out = io.StringIO()
        # y → allow
        assert ask_permission("rm", "x", stdin=io.StringIO("y\n"), stdout=out) == DECISION_ALLOW
        # a → allow_always
        assert ask_permission("rm", "x", stdin=io.StringIO("a\n"), stdout=out) == DECISION_ALLOW_ALWAYS
        # n / 空 / 乱码 → deny
        assert ask_permission("rm", "x", stdin=io.StringIO("n\n"), stdout=out) == DECISION_DENY
        assert ask_permission("rm", "x", stdin=io.StringIO("\n"), stdout=out) == DECISION_DENY
        assert ask_permission("rm", "x", stdin=io.StringIO("garbage\n"), stdout=out) == DECISION_DENY
    finally:
        prompt_ui._isatty = orig


def test_ac6b_ask_permission_non_tty_defaults_deny():
    from karvyloop.cli import prompt_ui
    orig = prompt_ui._isatty
    prompt_ui._isatty = lambda: False
    try:
        # HR-1 fail-closed:non-tty 默认 deny
        result = ask_permission("rm", "x", default=DECISION_DENY)
        assert result == DECISION_DENY
    finally:
        prompt_ui._isatty = orig


# ============ 额外:main 入口 + 子命令路由 ============
def test_extra_main_version(capsys):
    from karvyloop import __version__   # 单一版本源 → 不硬编码,版本号 bump 不破测试
    with pytest.raises(SystemExit) as ex:
        main(["--version"])
    assert ex.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out


def test_extra_main_no_args_shows_help(capsys):
    rc = main([])
    assert rc == 0
    captured = capsys.readouterr()
    # 帮助信息含子命令
    assert "init" in captured.err or "run" in captured.err


def test_extra_main_init_routes(tmp_path: Path, monkeypatch, capsys):
    target = tmp_path / "cfg.yaml"
    rc = main(["init", "--config", str(target), "--force"])
    assert rc == 0
    assert target.exists()


def test_extra_main_run_missing_config(tmp_path: Path, monkeypatch, capsys):
    _patched_sandbox(monkeypatch, str(tmp_path))
    rc = main(["run", "x", "--config", str(tmp_path / "missing.yaml"),
               "--workspace", str(tmp_path)])
    assert rc == 1
