"""test_lang_persistence — 语言偏好持久到 config.yaml(用户建议:设一次记住,拍 9.4).

用户原话:"语言选择记录本地偏好设置并记录,避免每次启动都要重新设置"。
canonical store = config.yaml `lang`;CLI/GUI 共用;清浏览器缓存也不丢。

AC:
- AC1: write_lang/read_lang 往返(新建 + 保留其余字段)
- AC2: set_startup_locale 优先级 显式 > env > config > en
- AC3: /api/lang GET 返当前;POST 持久到 config + set_locale
- AC4: / 路由注入 data-default-lang(全新浏览器按保存语言启动)
"""
from __future__ import annotations

import pytest

from karvyloop import i18n
from karvyloop.config_lang import read_lang, write_lang


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("KARVYLOOP_LANG", raising=False)
    i18n.set_locale(None)
    yield
    i18n.set_locale(None)


# ---- AC1 ----
def test_write_read_roundtrip(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("models:\n  providers: {}\nagents:\n  defaults:\n    model: x\n", encoding="utf-8")
    assert read_lang(cfg) is None
    assert write_lang("zh", cfg) is True
    assert read_lang(cfg) == "zh"
    # 保留其余字段
    import yaml
    out = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert out["lang"] == "zh"
    assert "models" in out and "agents" in out


def test_write_creates_file(tmp_path):
    cfg = tmp_path / "new.yaml"
    assert write_lang("zh", cfg) is True
    assert read_lang(cfg) == "zh"


# ---- AC2: 优先级 ----
def test_startup_locale_precedence(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    write_lang("zh", cfg)
    # config zh,无 env/显式 → zh
    i18n.set_startup_locale(explicit=None, config_lang=read_lang(cfg))
    assert i18n.get_locale() == "zh"
    # env en 压过 config zh
    monkeypatch.setenv("KARVYLOOP_LANG", "en")
    i18n.set_startup_locale(explicit=None, config_lang=read_lang(cfg))
    assert i18n.get_locale() == "en"
    # 显式 zh 压过 env en
    i18n.set_startup_locale(explicit="zh", config_lang=read_lang(cfg))
    assert i18n.get_locale() == "zh"


def test_startup_locale_no_pref_defaults_en(monkeypatch):
    monkeypatch.delenv("KARVYLOOP_LANG", raising=False)
    i18n.set_startup_locale(explicit=None, config_lang=None)
    assert i18n.get_locale() == "en"


# ---- AC3: /api/lang ----
def test_api_lang_get_and_persist(tmp_path):
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    cfg = tmp_path / "config.yaml"
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.config_path = str(cfg)
    c = TestClient(app)
    # POST 持久
    r = c.post("/api/lang", json={"lang": "zh"})
    assert r.status_code == 200 and r.json()["lang"] == "zh" and r.json()["persisted"]
    assert read_lang(cfg) == "zh"  # 写进 config 了
    # GET 返当前
    assert c.get("/api/lang").json()["lang"] == "zh"
    # 非法 lang → 422
    assert c.post("/api/lang", json={"lang": "fr"}).status_code == 422


# ---- AC4: / 注入 data-default-lang ----
def test_index_injects_default_lang(tmp_path):
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    i18n.set_locale("zh")
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    c = TestClient(app)
    html = c.get("/").text
    assert 'data-default-lang="zh"' in html  # 全新浏览器据此启动为 zh
