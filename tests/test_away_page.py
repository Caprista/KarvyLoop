"""test_away_page — 🌅 /away 托管接入页(karvy.chat 静态外壳 + 首次配对入口)静态+路由验收。

设计裁决(docs/43 P2 岔路口,「独立可信静态源」):karvy.chat 托管一份开源 app bundle
(与 console 同一份前端源的构建产物),出门在外浏览器打开 → 首次配对 → 经 relay E2E 隧道回家
→ 在浏览器里拍板。与家里 console **永远不同源** → 数据面**隧道-only**(不能像 /m 那样直连兜底)。

锁的三件事:
- 脚本序 e2e → tunnel → i18n → away(错序 = 隧道/i18n 开机静默失效);
- 隧道-only:away.js 里**没有裸 fetch("/api…")**,所有 /api 字面量都必须经 tunnelFetch;
- 首配入口在:解析 {relay,room,fingerprint,code}(JSON 原样或 karvy-pair:<base64url> 深链)。
"""
from __future__ import annotations

import pathlib
import re
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402

STATIC = ROOT / "karvyloop" / "console" / "static"


def _client():
    return TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None))


def test_away_route_serves_page_with_lang_injection():
    """/away = dev/test 便利路由(production 由 karvy.chat 静态托管);语言注入同 index/m。"""
    r = _client().get("/away")
    assert r.status_code == 200
    assert 'data-default-lang="' in r.text
    assert "/static/away.js" in r.text and "/static/i18n.js" in r.text
    assert "viewport" in r.text


def test_away_static_assets_exist():
    html = (STATIC / "away.html").read_text(encoding="utf-8")
    assert (STATIC / "away.js").is_file(), "away.js 构建产物缺失(cd frontend && npm run build)"
    assert html, "away.html 为空"


def test_away_html_script_load_order():
    """脚本序锁:tunnel.js 依赖 KarvyE2E、away.js 依赖 KarvyTunnel+KarvyI18n →
    次序必须 e2e → tunnel → i18n → away(任务裁定的载入序)。"""
    html = (STATIC / "away.html").read_text(encoding="utf-8")
    order = [html.find(f"/static/{f}") for f in ("e2e.js", "tunnel.js", "i18n.js", "away.js")]
    assert all(p >= 0 for p in order), "四个 bundle 必须都在 away.html 里"
    assert order == sorted(order), f"脚本序错(须 e2e<tunnel<i18n<away): {order}"


def test_away_is_tunnel_only():
    """隧道-only 契约:away.js 里不许有 /m 那种裸 fetch("/api…") 直连兜底 —— 与 console 不同源,
    直连永远打不到家。所有 /api 字面量都必须是 tunnelFetch 的实参。"""
    js = (STATIC / "away.js").read_text(encoding="utf-8")
    # 裸(小写)fetch("/api…") = m.ts 那种直连形态,away 里一处都不许有
    bare = re.findall(r'(?<![A-Za-z])fetch\(\s*["\'](/api/[^"\']+)', js)
    assert not bare, f"away 出现裸直连 fetch 到 /api(应隧道-only): {bare}"
    all_api = set(re.findall(r'["\'](/api/[^"\']+)["\']', js))
    via = set(re.findall(r'tunnelFetch\(\s*["\'](/api/[^"\']+)["\']', js))
    assert all_api == via, f"有 /api 字面量不经 tunnelFetch(违隧道-only): {all_api - via}"
    assert {"/api/proposals/pending", "/api/h2a_decide"} <= via, \
        f"拍板屏契约缺失(须经隧道拉卡+拍板): {via}"


def test_away_pairing_entry_present():
    """首配屏入口:解析 {relay,room,fingerprint,code}(JSON 原样或 karvy-pair 深链)→ pairAndSave。"""
    js = (STATIC / "away.js").read_text(encoding="utf-8")
    assert "pairAndSave" in js, "首配必经 tunnel.pairAndSave(生成密钥+一次性码握手)"
    assert "karvy-pair" in js, "深链形态 karvy-pair:<base64url> 解析缺失"
    for f in ("relay", "room", "fingerprint", "code"):
        assert f in js, f"配对四字段解析缺 {f}"
    assert "KarvyAway" in js, "全局契约 KarvyAway 缺失"


def test_away_reuses_decision_card_classes():
    """复用 /m 拍板卡视觉 + 对抗验收过的 diff 纪律(按 proposal_id、不整列重建)。"""
    js = (STATIC / "away.js").read_text(encoding="utf-8")
    assert "m-card" in js and "m-btn" in js, "应复用 m-* 卡片 class"
    assert "data-pid" in js, "按 proposal_id diff(不整列重建)纪律缺失"


def test_away_i18n_keys_parity_en_zh():
    """away.* 键 en/zh 两表齐(防漏翻;bundle 裸显键名)。从 i18n.js 抽 en/zh 块比对。"""
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")

    def _block(label: str) -> set[str]:
        m = re.search(rf"\b{label}\s*:\s*\{{", js)
        assert m, f"i18n.js 缺 {label} 表"
        start = m.end() - 1
        depth = 0
        for i in range(start, len(js)):
            if js[i] == "{":
                depth += 1
            elif js[i] == "}":
                depth -= 1
                if depth == 0:
                    return set(re.findall(r'"([^"]+)"\s*:', js[start + 1:i]))
        raise AssertionError(f"{label} 块括号不配平")

    en, zh = _block("en"), _block("zh")
    en_away = {k for k in en if k.startswith("away.")}
    zh_away = {k for k in zh if k.startswith("away.")}
    assert en_away, "en 表没有 away.* 键"
    assert en_away == zh_away, f"away.* en/zh 不齐: en-only={en_away - zh_away} zh-only={zh_away - en_away}"


def test_away_low_floor_no_coined_nouns():
    """低地板锁([[avoid-ivory-tower]]):away 用户可见面不出现生造名词。"""
    html = (STATIC / "away.html").read_text(encoding="utf-8")
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    away_lines = "\n".join(ln for ln in js.splitlines() if '"away.' in ln)
    for banned in ("H2A", "atom", "Atom", "L0", "L4", "crystalli", "结晶", "原子"):
        assert banned not in html, f"away.html 出现生造名词: {banned}"
        assert banned not in away_lines, f"away 页 i18n 串出现生造名词: {banned}"
