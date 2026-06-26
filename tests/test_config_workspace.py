"""用户工作区解析验收(9.5 P1)—— agent 跟 KarvyLoop 源码隔离。"""
from __future__ import annotations

from pathlib import Path

from karvyloop.config_workspace import resolve_workspace


def test_default_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("KARVYLOOP_WORKSPACE", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    ws = resolve_workspace(config_path=tmp_path / "nope.yaml")
    assert ws == str((tmp_path / "karvyloop-work").resolve())
    assert Path(ws).is_dir()  # ensure=True → 已建


def test_env_override(tmp_path, monkeypatch):
    target = tmp_path / "myws"
    monkeypatch.setenv("KARVYLOOP_WORKSPACE", str(target))
    ws = resolve_workspace(config_path=tmp_path / "nope.yaml")
    assert ws == str(target.resolve())
    assert target.is_dir()


def test_config_override(tmp_path, monkeypatch):
    monkeypatch.delenv("KARVYLOOP_WORKSPACE", raising=False)
    cfg = tmp_path / "config.yaml"
    target = tmp_path / "cfgws"
    cfg.write_text(f"workspace: {target}\nlang: zh\n", encoding="utf-8")
    ws = resolve_workspace(config_path=cfg)
    assert ws == str(target.resolve())
    assert target.is_dir()


def test_env_beats_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"workspace: {tmp_path / 'cfgws'}\n", encoding="utf-8")
    envws = tmp_path / "envws"
    monkeypatch.setenv("KARVYLOOP_WORKSPACE", str(envws))
    assert resolve_workspace(config_path=cfg) == str(envws.resolve())


def test_not_the_source_tree(tmp_path, monkeypatch):
    """默认工作区绝不是 cwd/源码树(病根:别再读到 KarvyLoop 自己的 CLAUDE.md)。"""
    monkeypatch.delenv("KARVYLOOP_WORKSPACE", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    ws = Path(resolve_workspace(config_path=tmp_path / "nope.yaml"))
    assert ws.name == "karvyloop-work"
    assert ws != Path.cwd()
