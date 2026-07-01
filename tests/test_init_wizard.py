"""拍 8 Onboarding wizard 测试。

设计:plans/snoopy-singing-sunbeam.md §批 8。

AC 列表:
  AC1: --no-wizard 走默认 config (非交互, 开发者 / CI 用)
  AC2: wizard 模式 (interactive=True + TTY + 不带 --no-wizard) 问 provider 选 1/2/3
  AC3: API key 格式错 (含 'FAKE' / 太短 / 缺前缀) → render_error_with_hint 报错 + 给建议
  AC4: 写完的 config.yaml 含 crystallize.skills_dir 默认 ~/.karvyloop/skills
  AC5: Renderer.render_error_with_hint 输出 "原因 + 建议" 2 行格式

载体: pytest
Why: 拍 8 是"真人能不能跑起来"的生死线前一步, 必须保证 wizard + 错误格式稳。
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from karvyloop.cli.init import cmd_init, DEFAULT_CONFIG_YAML
from karvyloop.cli.wizard import (
    PROVIDERS,
    WizardError,
    run_wizard,
    validate_api_key,
)
from karvyloop.cli.render import Renderer


# ============ AC1: --no-wizard 走默认 config ============

class TestAC1NoWizardFlag:
    """AC1: --no-wizard 跳过 wizard, 直接写默认 config。"""

    def test_no_wizard_skips_wizard(self, tmp_path, monkeypatch):
        """interactive=True 但 no_wizard=True → 不进 wizard, 写默认。"""
        target = tmp_path / "config.yaml"
        # monkeypatch input 防 stdin 阻塞
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        rc = cmd_init(
            path=target,
            interactive=True,
            force=False,
            no_wizard=True,
            stdout=io.StringIO(),
        )
        assert rc == 0
        assert target.exists()
        text = target.read_text(encoding="utf-8")
        # 默认含 ollama (本地优先)
        assert "ollama" in text
        assert "127.0.0.1" in text
        # 默认含 anthropic 占位
        assert "${ANTHROPIC_API_KEY}" in text

    def test_no_interactive_skips_wizard(self, tmp_path, monkeypatch):
        """非 TTY → 自动跳过 wizard, 写默认。"""
        target = tmp_path / "config.yaml"
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        rc = cmd_init(
            path=target,
            interactive=True,  # True 但 stdin 不是 TTY → 跳过
            force=False,
            stdout=io.StringIO(),
        )
        assert rc == 0
        assert target.exists()


# ============ AC2: wizard 模式问 provider ============

class TestAC2WizardAsksProvider:
    """AC2: wizard 模式跑通完整流程(provider → API key → 写 yaml)。"""

    def test_wizard_ollama_no_api_key(self, tmp_path, monkeypatch, capsys):
        """选 1 (ollama) → 不问 API key → 直接写 config。"""
        target = tmp_path / "config.yaml"
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        # 模拟输入:选 1=ollama
        monkeypatch.setattr("builtins.input", lambda prompt="": "1")
        # stub config 写入
        rc = cmd_init(
            path=target,
            interactive=True,
            force=False,
            no_wizard=False,  # 显式允许 wizard
            stdout=io.StringIO(),
        )
        assert rc == 0
        text = target.read_text(encoding="utf-8")
        # ollama 块在
        assert "ollama" in text
        assert "127.0.0.1" in text
        # anthropic 占位也在(默认模板含)
        assert "${ANTHROPIC_API_KEY}" in text

    def test_wizard_anthropic_with_key(self, tmp_path, monkeypatch):
        """选 2 (anthropic) + 假 key (但满足长度/前缀) → 写 config。"""
        target = tmp_path / "config.yaml"
        # 用一个有效格式的 key 避免格式校验失败(28 字符, 前缀对, 不含 FAKE/TODO/PLACEHOLDER)
        valid_key = "sk-ant-zZ1234567890ABCDEFGHIJKL"  # 28 字符
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        # 模拟:选 2=anthropic, 然后给 key
        inputs = iter(["2", valid_key])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        rc = cmd_init(
            path=target,
            interactive=True,
            force=False,
            no_wizard=False,
            stdout=io.StringIO(),
        )
        assert rc == 0
        text = target.read_text(encoding="utf-8")
        # 真 key 写入(替换占位)
        assert valid_key in text
        # 占位已消失
        assert "${ANTHROPIC_API_KEY}" not in text

    def test_wizard_invalid_provider_raises(self, tmp_path, monkeypatch, capsys):
        """无效 provider (输 999 远超 16 个 vendor) → 友好错误 + raise WizardError。"""
        target = tmp_path / "config.yaml"
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": "999")
        rc = cmd_init(
            path=target,
            interactive=True,
            force=False,
            no_wizard=False,
            stdout=io.StringIO(),
        )
        # WizardError 捕获 → 返 1
        assert rc == 1
        # 未写 config
        assert not target.exists()
        # 友好错误输出
        captured = capsys.readouterr()
        assert "未知 provider" in captured.err or "E_PROVIDER" in captured.err


# ============ AC3: API key 格式校验 ============

class TestAC3ApiKeyValidation:
    """AC3: API key 格式校验(空 / 短 / 占位符 / 错前缀)。"""

    def test_empty_key(self):
        ok, err = validate_api_key("anthropic", "")
        assert not ok
        assert "不能为空" in err

    def test_short_key(self):
        ok, err = validate_api_key("anthropic", "sk-ant-xx")
        assert not ok
        assert "太短" in err

    def test_fake_placeholder(self):
        ok, err = validate_api_key("anthropic", "FAKE-KEY-12345")
        assert not ok
        assert "占位符" in err

    def test_wrong_prefix_anthropic(self):
        ok, err = validate_api_key("anthropic", "sk-12345678901234567890")
        assert not ok
        assert "sk-ant-" in err

    def test_key_with_surrounding_whitespace(self):
        ok, err = validate_api_key("anthropic", "  sk-ant-zzzzZZZZ0123456789  ")
        assert not ok
        assert "首尾有空格" in err

    def test_valid_anthropic_format(self):
        # 28 字符 sk-ant- 前缀(测格式校验,不是真 key)
        ok, err = validate_api_key("anthropic", "sk-ant-zzzzZZZZ0123456789ABCDE")
        assert ok, f"应该通过: {err}"
        assert err == ""

    def test_valid_ollama_dummy(self):
        # ollama 不真校验
        ok, err = validate_api_key("ollama", "dummy")
        assert ok

    def test_openai_requires_sk_prefix(self):
        ok, err = validate_api_key("openai", "ant-zzzzZZZZ0123456789ABCDE")
        assert not ok
        assert "sk-" in err


# ============ AC4: 写完的 config 含默认值 ============

class TestAC4ConfigDefaults:
    """AC4: 写完的 config.yaml 含 crystallize.skills_dir 默认 ~/.karvyloop/skills。

    拍 8 加 crystallize 节点(拍 6 已经在 skill_index.py 默认, 这里要写进 config)。
    """

    def test_default_yaml_has_crystallize_node(self):
        """DEFAULT_CONFIG_YAML 模板含 crystallize.skills_dir 默认。"""
        assert "crystallize" in DEFAULT_CONFIG_YAML
        assert "skills_dir" in DEFAULT_CONFIG_YAML
        # 默认值路径
        assert "~/.karvyloop/skills" in DEFAULT_CONFIG_YAML

    def test_written_yaml_contains_crystallize(self, tmp_path, monkeypatch):
        """wizard 写出的 yaml 含 crystallize.skills_dir 节点。"""
        target = tmp_path / "config.yaml"
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": "1")  # ollama
        rc = cmd_init(
            path=target,
            interactive=True,
            force=False,
            no_wizard=False,
            stdout=io.StringIO(),
        )
        assert rc == 0
        text = target.read_text(encoding="utf-8")
        import yaml
        cfg = yaml.safe_load(text)
        assert "crystallize" in cfg
        assert "skills_dir" in cfg["crystallize"]
        assert "~/.karvyloop/skills" in cfg["crystallize"]["skills_dir"]


# ============ AC5: Renderer.render_error_with_hint 2 行格式 ============

class TestAC5ErrorHintFormat:
    """AC5: Renderer.render_error_with_hint 输出 '原因 + 建议' 2 行格式。"""

    def test_outputs_reason_line(self, capsys):
        r = Renderer(color=False)  # out/err 默认 = 真 stdout/stderr,capsys 能抓
        r.render_error_with_hint(
            code="E_TEST",
            message="出了问题 X",
            hint="试试 Y",
        )
        captured = capsys.readouterr()
        # 第一行:✗ 原因 (code)
        assert "出了问题 X" in captured.err
        assert "E_TEST" in captured.err
        # 第二行:→ 建议
        assert "试试 Y" in captured.err
        # 错误计数 +1
        assert r.stats.errors == 1

    def test_no_color_when_disabled(self, capsys):
        """color=False 时无 ANSI。"""
        r = Renderer(color=False)
        r.render_error_with_hint("E_X", "msg", "hint")
        captured = capsys.readouterr()
        # 无 ANSI 转义
        assert "\033[" not in captured.err

    def test_distinct_from_single_line_error(self, capsys):
        """2 行格式 ≠ 单行 _error (验证 2 个方法都存在且格式不同)。"""
        r1 = Renderer(color=False)
        r1.render_error_with_hint("E_H", "hint msg", "do this")
        out1 = capsys.readouterr().err

        r2 = Renderer(color=False)
        r2._error("E_S", "single msg")
        out2 = capsys.readouterr().err

        # render_error_with_hint 有 "→" 提示符
        assert "→" in out1
        # _error 没有
        assert "→" not in out2


# ============ 边界 ============

class TestWizardEdgeCases:
    """Wizard 边界:键盘中断 / EOF / 缺省值。"""

    def test_wizard_eof_returns_1(self, tmp_path, monkeypatch):
        """EOFError 返 1(用户 Ctrl+D)。"""
        target = tmp_path / "config.yaml"
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        def _raise_eof(prompt=""):
            raise EOFError
        monkeypatch.setattr("builtins.input", _raise_eof)
        rc = cmd_init(
            path=target,
            interactive=True,
            force=False,
            no_wizard=False,
            stdout=io.StringIO(),
        )
        assert rc == 1
        assert not target.exists()

    def test_empty_provider_defaults_to_ollama(self, tmp_path, monkeypatch):
        """provider 选空(直接回车) → 默认 ollama。"""
        target = tmp_path / "config.yaml"
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": "")  # 空
        rc = cmd_init(
            path=target,
            interactive=True,
            force=False,
            no_wizard=False,
            stdout=io.StringIO(),
        )
        assert rc == 0
        text = target.read_text(encoding="utf-8")
        # 默认 ollama 在
        assert "ollama" in text
        assert "127.0.0.1" in text

    def test_anthropic_skip_api_key_writes_placeholder(self, tmp_path, monkeypatch):
        """选 anthropic + API key 跳过(空) → 写 ${ANTHROPIC_API_KEY} 占位。"""
        target = tmp_path / "config.yaml"
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        inputs = iter(["2", ""])  # 选 anthropic + 跳过 key
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        rc = cmd_init(
            path=target,
            interactive=True,
            force=False,
            no_wizard=False,
            stdout=io.StringIO(),
        )
        assert rc == 0
        text = target.read_text(encoding="utf-8")
        # 占位在(没被真 key 替换)
        assert "${ANTHROPIC_API_KEY}" in text
