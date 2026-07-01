"""test_update — 版本检测(detect→notify→你按下,绝不自动升 + 零遥测 + 永不崩)。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop import update as U  # noqa: E402


# ---- semver 解析 / 比较 ----
def test_parse_semver_tolerant():
    assert U._parse_semver("v1.2.3") == (1, 2, 3)
    assert U._parse_semver("1.2") == (1, 2, 0)
    assert U._parse_semver("v0.2.0-rc1") == (0, 2, 0)   # 丢 pre-release 后缀
    assert U._parse_semver("garbage") == (0, 0, 0)       # 非法 → 0,不崩


def test_is_newer():
    assert U.is_newer("0.3.0", "0.2.0") is True
    assert U.is_newer("v1.0.0", "0.9.9") is True
    assert U.is_newer("0.2.0", "0.2.0") is False
    assert U.is_newer("0.1.0", "0.2.0") is False


def test_upgrade_command_by_mode():
    assert U.upgrade_command("git") == "git pull && pip install -e ."
    assert U.upgrade_command("pip") == "pip install -U karvyloop"


def test_detect_install_mode_returns_known():
    assert U.detect_install_mode() in ("git", "pip")


# ---- 主权:env 关闭 ----
def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("KARVYLOOP_NO_UPDATE_CHECK", "1")
    assert U.check_disabled() is True
    r = U.check_update()
    assert r["source"] == "disabled" and r["newer"] is False
    assert r["current"] == U.current_version()


# ---- check_update 三条路径(monkeypatch 掉网络/缓存,不碰真 fs/网) ----
def test_check_update_live_newer(monkeypatch):
    monkeypatch.delenv("KARVYLOOP_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(U, "_read_cache", lambda: None)            # 强制 miss
    monkeypatch.setattr(U, "_write_cache", lambda d: None)         # 别写真盘
    # CalVer:用一个远未来日期当"明显更新"的哨兵(v99.0.0 在日期版本下反而更旧)。
    monkeypatch.setattr(U, "_fetch_latest",
                        lambda timeout=4.0: {"latest": "2099.1.1", "url": "http://x", "name": "2099.1.1"})
    r = U.check_update(now=1000.0)
    assert r["newer"] is True and r["latest"] == "2099.1.1" and r["source"] == "live"
    assert r["url"] == "http://x"


def test_check_update_unreachable(monkeypatch):
    monkeypatch.delenv("KARVYLOOP_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(U, "_read_cache", lambda: None)
    monkeypatch.setattr(U, "_fetch_latest", lambda timeout=4.0: None)   # 网断/限流/没 release
    r = U.check_update(now=1000.0)
    assert r["source"] == "unreachable" and r["newer"] is False
    assert r["current"] == U.current_version()


def test_check_update_uses_fresh_cache(monkeypatch):
    monkeypatch.delenv("KARVYLOOP_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(U, "_read_cache",
                        lambda: {"ts": 1000.0, "latest": "v0.0.1", "url": "http://c"})
    # 若误走网络会拿到 v99 → 断言没走,说明用了缓存
    monkeypatch.setattr(U, "_fetch_latest",
                        lambda timeout=4.0: {"latest": "v99.0.0", "url": "http://x"})
    r = U.check_update(now=1000.0 + 100)   # 100s < TTL(一天)→ 命中缓存
    assert r["source"] == "cache" and r["latest"] == "v0.0.1"


def test_check_update_stale_cache_refetches(monkeypatch):
    monkeypatch.delenv("KARVYLOOP_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(U, "_read_cache",
                        lambda: {"ts": 0.0, "latest": "v0.0.1", "url": "http://c"})
    monkeypatch.setattr(U, "_write_cache", lambda d: None)
    monkeypatch.setattr(U, "_fetch_latest",
                        lambda timeout=4.0: {"latest": "v0.3.0", "url": "http://x"})
    r = U.check_update(now=10_000_000.0)   # 远超 TTL → 重查
    assert r["source"] == "live" and r["latest"] == "v0.3.0"


# ---- 端点 ----
def test_endpoint_update_status(monkeypatch):
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    monkeypatch.setattr(U, "check_update",
                        lambda: {"current": "0.2.0", "latest": "0.3.0", "newer": True,
                                 "command": "pip install -U karvyloop", "url": "http://x",
                                 "checked": True, "source": "live"})
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    r = TestClient(app).get("/api/update_status").json()
    assert r["newer"] is True and r["latest"] == "0.3.0"
