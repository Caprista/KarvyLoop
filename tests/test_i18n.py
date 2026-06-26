"""test_i18n — 双语表现层基础(M3+ 拍 9.4-A2)。

设计:karvyloop/i18n/。用户原话"默认英文,可切中文,不影响代码逻辑"。

AC:
- AC1: 默认 locale = en(无 env、无显式设置)
- AC2: set_locale("zh") 生效;t() 返中文
- AC3: KARVYLOOP_LANG=zh 环境变量 → 默认 zh(显式 set 优先于 env)
- AC4: locale 归一化(zh-CN/zh_TW/ZH→zh;en-US→en;乱码→en)
- AC5: t() 占位插值({url}/{n})
- AC6: 缺 key → 回退当前 locale 查不到 → en → key 本身(永不抛、永不空)
- AC7: set_locale(None) 复位走 env/默认
- AC8: en/zh 两张表 key 集合一致(防漏翻 / 防孤儿)
- AC9: available_locales 列出受支持项
"""
from __future__ import annotations

import pytest

from karvyloop import i18n
from karvyloop.i18n._strings import TABLES


@pytest.fixture(autouse=True)
def _reset_locale(monkeypatch):
    """每个用例前后复位:清 env + 复位显式 locale,避免串味。"""
    monkeypatch.delenv("KARVYLOOP_LANG", raising=False)
    i18n.set_locale(None)
    yield
    i18n.set_locale(None)


# ---- AC1: 默认 en ----
def test_default_locale_is_en(monkeypatch):
    monkeypatch.delenv("KARVYLOOP_LANG", raising=False)
    i18n.set_locale(None)
    assert i18n.get_locale() == "en"


# ---- AC2: set zh 生效 ----
def test_set_locale_zh(monkeypatch):
    monkeypatch.delenv("KARVYLOOP_LANG", raising=False)
    i18n.set_locale("zh")
    assert i18n.get_locale() == "zh"
    assert i18n.t("console.bind_failed", error="x") == "[karvyloop console] 绑定失败: x"


# ---- AC3: env 默认 + 显式优先 ----
def test_env_default_and_explicit_wins(monkeypatch):
    monkeypatch.setenv("KARVYLOOP_LANG", "zh")
    i18n.set_locale(None)  # 无显式 → 走 env
    assert i18n.get_locale() == "zh"
    i18n.set_locale("en")  # 显式 en 压过 env zh
    assert i18n.get_locale() == "en"


# ---- AC4: 归一化 ----
@pytest.mark.parametrize("raw,expected", [
    ("zh-CN", "zh"), ("zh_TW", "zh"), ("ZH", "zh"), ("zh", "zh"),
    ("en-US", "en"), ("EN", "en"), ("en", "en"),
    ("fr", "en"), ("garbage", "en"), ("", "en"),
])
def test_normalize(raw, expected):
    i18n.set_locale(raw)
    assert i18n.get_locale() == expected


# ---- AC5: 占位插值 ----
def test_format_interpolation():
    i18n.set_locale("en")
    assert i18n.t("console.opening", url="http://127.0.0.1:8766") == \
        "[karvyloop console] opening http://127.0.0.1:8766"
    assert i18n.t("console.conv_ready", n=7) == \
        "[karvyloop console] conversation ready (resumed 7 turns)"


# ---- AC6: 缺 key 回退,永不抛 ----
def test_missing_key_falls_back_to_key():
    i18n.set_locale("zh")
    assert i18n.t("no.such.key") == "no.such.key"  # 当前/en 都无 → key 本身
    # 多余占位不抛
    assert i18n.t("no.such.key", foo="bar") == "no.such.key"


def test_missing_in_zh_falls_back_to_en():
    # 构造:某 key 仅 en 有(模拟漏翻)→ zh 取 en
    TABLES["en"]["_test.only_en"] = "english only"
    try:
        i18n.set_locale("zh")
        assert i18n.t("_test.only_en") == "english only"
    finally:
        del TABLES["en"]["_test.only_en"]


# ---- AC7: None 复位 ----
def test_set_none_resets(monkeypatch):
    monkeypatch.setenv("KARVYLOOP_LANG", "zh")
    i18n.set_locale("en")
    assert i18n.get_locale() == "en"
    i18n.set_locale(None)  # 复位 → 走 env zh
    assert i18n.get_locale() == "zh"


# ---- AC8: 两张表 key 一致(防漏翻)----
def test_en_zh_key_parity():
    en_keys = set(TABLES["en"])
    zh_keys = set(TABLES["zh"])
    missing_zh = en_keys - zh_keys
    missing_en = zh_keys - en_keys
    assert not missing_zh, f"zh 缺翻译: {missing_zh}"
    assert not missing_en, f"en 缺 key(zh 多孤儿): {missing_en}"


# ---- AC9: available_locales ----
def test_available_locales():
    locs = i18n.available_locales()
    assert "en" in locs and "zh" in locs
