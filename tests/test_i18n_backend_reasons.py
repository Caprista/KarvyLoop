"""test_i18n_backend_reasons — 后端中文 reason 透传 → 双语表契约(P2-c).

病根(全盘 review):后端 API 的 reason/detail 中文写死,英文界面原样漏中文(违双语纪律)。
修:前端 i18n.ts 以**中文原文为稳定 key**建 BACKEND_ZH_EN(zh→en)表,tBackend() 查表译。

本测试是**接线契约门**:grep console 后端所有静态中文 `"reason": "…"` 字符串,逐条断言
在 BACKEND_ZH_EN 表里 —— 新加后端 reason 不补翻译 = 挂测试(fail-loud,不静默漏译)。
f-string 动态拼接的 reason 无法整句入表,由 tBackend 的最长前缀匹配 + 原文回退兜底(诚实降级)。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "karvyloop" / "console"
I18N_TS = CONSOLE / "frontend" / "src" / "i18n.ts"
I18N_JS = CONSOLE / "static" / "i18n.js"

_CJK = re.compile(r"[一-鿿]")
# 静态 reason:值里无 f-string 花括号(动态拼接的走前缀回退,不强制入表)
_REASON_RE = re.compile(r'"reason":\s*"([^"{}]*[一-鿿][^"{}]*)"')


def _backend_static_reasons() -> set[str]:
    out: set[str] = set()
    for p in CONSOLE.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for m in _REASON_RE.finditer(text):
            out.add(m.group(1))
    return out


def _table_keys(src: str) -> set[str]:
    m = re.search(r"BACKEND_ZH_EN[^=]*=\s*\{(.*?)\n  \};", src, re.DOTALL)
    assert m, "i18n 源里找不到 BACKEND_ZH_EN 表"
    return set(re.findall(r'"([^"]*[一-鿿][^"]*)":', m.group(1)))


def test_every_static_backend_reason_is_translated():
    reasons = _backend_static_reasons()
    assert reasons, "grep 应至少找到一批后端中文 reason(找不到=正则或路径坏了)"
    keys = _table_keys(I18N_TS.read_text(encoding="utf-8"))
    missing = sorted(r for r in reasons if r not in keys)
    assert not missing, (
        "以下后端中文 reason 不在 i18n.ts BACKEND_ZH_EN 表(英文界面会漏中文),"
        "请补 zh→en 翻译:\n" + "\n".join(f"  - {r}" for r in missing)
    )


def test_built_artifact_carries_table_and_tbackend():
    """构建产物 static/i18n.js 真带上了表 + tBackend(改了 ts 没 npm run build = 假接线)。"""
    js = I18N_JS.read_text(encoding="utf-8")
    assert "tBackend" in js, "static/i18n.js 缺 tBackend(没重新 build?)"
    assert "跨源请求被拒(same-origin only)" in js, "static/i18n.js 缺 BACKEND_ZH_EN 表内容"


def test_app_js_uses_tbackend_on_reason_sites():
    """app.js 的 reason/detail 透传点真走 tB()(表建了没接=假接线)。"""
    app_js = (CONSOLE / "static" / "app.js").read_text(encoding="utf-8")
    assert "function tB(" in app_js
    assert "tB(res.reason)" in app_js
    assert "tB(d.detail)" in app_js
