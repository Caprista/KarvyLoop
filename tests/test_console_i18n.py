"""console 前端 i18n 静态验收(M3+ 拍 9.4-A3)。

设计:karvyloop/console/static/i18n.js + index.html data-i18n 标注 + app.js 走 t()。
默认 en,可切 zh,纯表现层。Q5:不引 JS 引擎,纯文件 + 正则(node 无关,CI 友好)。

AC:
- AC1: i18n.js 存在且非空
- AC2: index.html 在 app.js **之前**加载 i18n.js(否则 app.js 引用 KarvyI18n 为 undefined)
- AC3: index.html 用 data-i18n 标注静态文案 + 含语言切换器容器
- AC4: app.js 走 window.KarvyI18n + t(),且 boot 调 applyStatic
- AC5: i18n.js en/zh 两张表 key 一致(防漏翻 / 防孤儿)
- AC6: 迁移锁 —— 旧硬编码中文 UI 串不再留在 app.js(已搬进 i18n.js)
- AC7: styles.css 含语言切换器样式
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "karvyloop" / "console" / "static"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


# ---- AC1 ----
def test_i18n_js_exists():
    p = STATIC / "i18n.js"
    assert p.is_file() and p.stat().st_size > 0


# ---- AC2: 加载顺序 ----
def test_index_loads_i18n_before_app():
    html = _read("index.html")
    i_pos = html.find("i18n.js")
    a_pos = html.find("app.js")
    assert i_pos != -1 and a_pos != -1
    assert i_pos < a_pos, "i18n.js 必须在 app.js 之前加载"


# ---- AC3: data-i18n 标注 + 切换器 ----
def test_index_has_data_i18n_and_switcher():
    html = _read("index.html")
    assert html.count("data-i18n") >= 10, "静态文案应大面积走 data-i18n"
    assert 'id="lang-switcher"' in html
    # placeholder / title 也走 i18n
    assert "data-i18n-ph" in html
    assert "data-i18n-title" in html


# ---- AC4: app.js 走 t() ----
def test_app_js_uses_i18n():
    js = _read("app.js")
    assert "window.KarvyI18n" in js
    assert "function t(" in js or "T.t(" in js
    assert "applyStatic" in js
    assert "mountSwitcher" in js


# ---- AC5: en/zh key 一致 ----
def _extract_block(js: str, label: str) -> set[str]:
    """从 i18n.js 里抽 `en: { ... }` / `zh: { ... }` 块内的 key 集合。"""
    # 定位 `<label>: {` 起点,括号配平找终点
    m = re.search(rf"\b{label}\s*:\s*\{{", js)
    assert m, f"i18n.js 缺 {label} 表"
    start = m.end() - 1  # 指向 '{'
    depth = 0
    for i in range(start, len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                body = js[start + 1:i]
                return set(re.findall(r'"([^"]+)"\s*:', body))
    raise AssertionError(f"{label} 块括号不配平")


def test_i18n_en_zh_key_parity():
    js = _read("i18n.js")
    en = _extract_block(js, "en")
    zh = _extract_block(js, "zh")
    assert en, "en 表为空"
    missing_zh = en - zh
    missing_en = zh - en
    assert not missing_zh, f"zh 缺翻译: {missing_zh}"
    assert not missing_en, f"en 缺 key(zh 孤儿): {missing_en}"


# ---- AC6: 迁移锁(旧硬编码不再留 app.js)----
@pytest.mark.parametrize("old", [
    "暂无 domain", "暂无结晶技能", "已结晶", "快脑命中", "慢脑输出",
    "已开新对话", "已续上对话", "私聊小卡", "建域失败",
])
def test_app_js_no_legacy_hardcoded_zh(old):
    js = _read("app.js")
    assert old not in js, f"app.js 仍硬编码中文 UI 串: {old}(应搬进 i18n.js)"


# ---- AC7: 切换器样式 ----
def test_styles_has_lang_switcher():
    css = _read("styles.css")
    assert ".lang-switcher" in css
    assert ".lang-btn" in css
