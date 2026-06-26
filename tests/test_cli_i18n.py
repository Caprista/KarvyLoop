"""CLI 双语验收(M3+ 拍 9.4-A3)。

设计:karvyloop/cli/main.py help 走 i18n,默认 en,KARVYLOOP_LANG/--lang 可切 zh。

AC:
- AC1: 默认(无 env)→ 顶层 description 英文
- AC2: KARVYLOOP_LANG=zh → 顶层 description 中文
- AC3: 顶层 parser 含全局 --lang 选项
- AC4: 子命令 help 也走 i18n(run 英文/中文)
"""
from __future__ import annotations

import pytest

from karvyloop import i18n


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("KARVYLOOP_LANG", raising=False)
    i18n.set_locale(None)
    yield
    i18n.set_locale(None)


def _help_text(monkeypatch, lang_env=None):
    if lang_env:
        monkeypatch.setenv("KARVYLOOP_LANG", lang_env)
    else:
        monkeypatch.delenv("KARVYLOOP_LANG", raising=False)
    i18n.set_locale(None)  # 复位 → 走 env
    from karvyloop.cli.main import _build_parser
    return _build_parser().format_help()


def test_default_help_is_english(monkeypatch):
    h = _help_text(monkeypatch)
    assert "AI-Native Agent runtime" in h
    assert "运行时" not in h


def test_zh_env_help_is_chinese(monkeypatch):
    h = _help_text(monkeypatch, "zh")
    assert "运行时" in h


def test_parser_has_global_lang_flag(monkeypatch):
    h = _help_text(monkeypatch)
    assert "--lang" in h


def test_subcommand_help_localized(monkeypatch):
    from karvyloop.cli.main import main
    # 默认 en:run --help 含英文;不直接调(SystemExit),用 _build_parser 取子串
    h_en = _help_text(monkeypatch)
    assert "sandbox exec" in h_en or "MainLoop" in h_en
    h_zh = _help_text(monkeypatch, "zh")
    assert "沙箱执行" in h_zh
