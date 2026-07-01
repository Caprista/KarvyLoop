"""test_web_verify — 网页运行时验收门:可选依赖优雅降级 + 入口检查 + CLI + 纪律。

真·无头浏览器加载是 integration(需 Playwright,VM 上 demo);这里测**不需要浏览器**的部分:
没装时老实降级、入口缺失、CLI 退出码、persona 纪律含"真加载"规则。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.coding import web_verify as W  # noqa: E402


def _browser_ready() -> bool:
    """有浏览器才跑 integration —— 没装则 skip(团队/CI 没浏览器时不红)。"""
    if not W.playwright_available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


_needs_browser = pytest.mark.skipif(not _browser_ready(),
                                    reason="无 Playwright/Chromium —— 跳过真·浏览器 integration")


def test_unavailable_degrades_honestly(monkeypatch):
    monkeypatch.setattr(W, "playwright_available", lambda: False)
    r = W.verify_web_app("/whatever")
    assert r.available is False and r.ok is None
    assert "Playwright" in r.reason and "无法验证" in r.reason   # 老实说验不了,不假装


def test_entry_missing_is_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(W, "playwright_available", lambda: True)   # 装了,但目录空
    r = W.verify_web_app(str(tmp_path))
    assert r.available is True and r.ok is False
    assert "入口文件不存在" in r.reason


def test_result_to_dict():
    r = W.WebVerifyResult(available=True, ok=False, errors=["pageerror: boom"], reason="x", url="u")
    d = r.to_dict()
    assert d["available"] and d["ok"] is False and d["errors"] == ["pageerror: boom"]


# ---- CLI ----
def test_cli_unavailable_exit0(monkeypatch, capsys):
    import karvyloop.coding.web_verify as wv
    from karvyloop.cli.web_verify_cmd import cmd_verify_web
    monkeypatch.setattr(wv, "verify_web_app",
                        lambda path, entry="index.html": wv.WebVerifyResult(available=False, ok=None, reason="x"))
    assert cmd_verify_web("/x") == 0          # 没装不算失败(降级)
    assert "Playwright" in capsys.readouterr().out


def test_cli_runtime_errors_exit1(monkeypatch, capsys):
    import karvyloop.coding.web_verify as wv
    from karvyloop.cli.web_verify_cmd import cmd_verify_web
    monkeypatch.setattr(wv, "verify_web_app",
                        lambda path, entry="index.html": wv.WebVerifyResult(
                            available=True, ok=False, errors=["pageerror: ReferenceError x"], reason="有报错"))
    assert cmd_verify_web("/x") == 1          # 有运行时报错 → 非零
    assert "ReferenceError" in capsys.readouterr().out


def test_cli_inconclusive_exit0_not_misreported(monkeypatch, capsys):
    """装了 Playwright 但浏览器没跑成(ok=None)→ 老实说『没验到运行时』,
    **绝不**误报成「✗ 0 条报错」失败。退 0(降级,不是失败)。"""
    import karvyloop.coding.web_verify as wv
    from karvyloop.cli.web_verify_cmd import cmd_verify_web
    monkeypatch.setattr(wv, "verify_web_app",
                        lambda path, entry="index.html": wv.WebVerifyResult(
                            available=True, ok=None, errors=[],
                            reason="运行验证器自身出错:Executable doesn't exist"))
    assert cmd_verify_web("/x") == 0          # 没验成 ≠ 失败
    out = capsys.readouterr().out
    assert "✗" not in out and "0 条" not in out   # 绝不误报成「✗ 0 条报错」
    assert "Executable" in out                     # 老实带出原因


def test_cli_ok_exit0(monkeypatch):
    import karvyloop.coding.web_verify as wv
    from karvyloop.cli.web_verify_cmd import cmd_verify_web
    monkeypatch.setattr(wv, "verify_web_app",
                        lambda path, entry="index.html": wv.WebVerifyResult(available=True, ok=True, url="u"))
    assert cmd_verify_web("/x") == 0


# ---- integration:真无头浏览器(有浏览器才跑,否则 skip)----
@_needs_browser
def test_browser_clean_page_passes(tmp_path):
    """干净页面 → ok=True(锁:commit+settle 策略不把正常页判成超时)。"""
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><body><h1>ok</h1>"
        "<script>document.title='ready'</script></body></html>",
        encoding="utf-8")
    r = W.verify_web_app(str(tmp_path))
    assert r.available is True and r.ok is True, r.reason


@_needs_browser
def test_browser_init_throw_is_caught(tmp_path):
    """初始化抛异常(典型'哑按钮'成因:监听器永远没挂上)→ ok=False + pageerror。
    锁住核心价值:语法过(node --check 不报)但运行时炸,这里要抓到。"""
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><body><button id='go'>开始</button>"
        "<script type='module'>"
        "const cfg=undefined; console.log(cfg.value);"   # TypeError 在挂监听器之前
        "document.getElementById('go').addEventListener('click',()=>{});"
        "</script></body></html>",
        encoding="utf-8")
    r = W.verify_web_app(str(tmp_path))
    assert r.available is True and r.ok is False
    assert any("pageerror" in e for e in r.errors), r.errors


# ---- persona 纪律含"网页要真加载"规则 ----
def test_persona_has_web_runtime_verify_rule():
    from karvyloop.coding.persona import build_karvy_persona_prompt
    text = build_karvy_persona_prompt().to_text()
    assert "verify-web" in text and "真加载" in text
    assert "绝不" in text and "能玩" in text   # 别因语法过就说"做好了能玩"
