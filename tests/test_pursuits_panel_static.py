"""test_pursuits_panel_static — 🎯「我的追求」Web 面板(Pursuit,docs/88 §7 + 第三刀)静态验收。

锁四件事(纯文件+正则,CI 无浏览器也跑):
1. 接线三件套:index.html 有 data-panel="pursuits" 入口 —— **左导航第 14 项**(docs/88 第三刀,
   Hardy 拍本程功能优先;「你的团队」组)+ 决策舱列头就近入口(第二刀落此,并存)+ app.js
   _PANEL_SCRIPTS/_panelOpeners 双注册 + 面板脚本真消费 pursuit 端点(列/详情/创建/讲讲);
2. i18n:面板用到的 pursuit.* 字面键 en/zh 双表齐(AC8 同口径);入口文案走 data-i18n;
3. 安全:pursuits_panel.js 落 DOM 全走 el()/textContent —— 不许 innerHTML 拼接后端文本
   (唯一豁免:清空用的 `.innerHTML = ""`);
4. 名词预算:面板文案不直出 verify_gate / H2A / commit 这类内部词(说「完成判据/拍板/承诺」)。
"""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "karvyloop" / "console" / "static"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


# ---- 1. 接线三件套 ----

def test_index_has_pursuits_entry_in_sidebar_and_near():
    """docs/88 第三刀:pursuits **进左导航第 14 项**(Hardy 拍本程功能优先),同时保留决策舱
    列头就近入口(常驻导航 + 就近入口并存,和其它面板一致)。"""
    html = _read("index.html")
    assert 'data-panel="pursuits"' in html, "index.html 缺 pursuits 入口"
    # ① 左导航里有一项(第 14 项,吃 setupMgmtPanels + desk dock 同构复用的 .sidebar .nav-item[data-panel])
    sidebar = html[html.find('<nav class="sidebar">'):html.find("</nav>")]
    assert 'data-panel="pursuits"' in sidebar, "pursuits 应进左导航(docs/88 第三刀,第 14 项)"
    side_btn = re.search(r'<button class="nav-item"[^>]*data-panel="pursuits"[^>]*>[\s\S]*?</button>', sidebar)
    assert side_btn, "侧栏 pursuits 应是 nav-item <button>"
    assert 'data-i18n="nav.pursuits_side"' in side_btn.group(0), "侧栏入口文案应走 i18n(nav.pursuits_side)"
    # ② 决策舱列头仍有就近入口(在侧栏之外),带 nav-item 类命中同一套委托绑定
    outside = html[html.find("</nav>"):]
    m = re.search(r'<button[^>]*data-panel="pursuits"[^>]*>', outside)
    assert m, "决策舱列头就近入口应保留(<button data-panel=pursuits>)"
    assert "nav-item" in m.group(0), "就近入口须带 nav-item 类,否则委托绑定不命中(死按钮)"
    assert 'data-i18n="nav.pursuits"' in m.group(0), "就近入口文案应走 i18n"


def test_app_js_registers_pursuits_both_tables():
    app_js = _read("app.js")
    assert re.search(r'pursuits:\s*\{\s*src:\s*"/static/pursuits_panel\.js",\s*global:\s*"KarvyPursuitsPanel"',
                     app_js), "_PANEL_SCRIPTS 缺 pursuits 注册"
    assert "pursuits: () => window.KarvyPursuitsPanel.open()" in app_js, "_panelOpeners 缺 pursuits 派发"


def test_panel_consumes_all_three_endpoints():
    js = _read("pursuits_panel.js")
    assert '"/api/pursuits"' in js, "列表端点没接"
    assert '"/api/pursuit/"' in js, "详情端点没接"
    assert re.search(r'_postJSON\(\s*"/api/pursuit"', js), "创建端点没接(POST /api/pursuit)"
    # docs/88 第三刀 #2:「让小卡讲讲」真调 narrate 端点(前端真调 → 不进 API_ONLY 白名单)
    assert '/narrate' in js, "讲讲端点没接(POST /api/pursuit/{id}/narrate)"
    assert 'pursuit.narrate_btn' in js, "讲讲按钮文案没走 i18n"
    # 详情路径参数要编码(id 来自后端,但纪律统一)
    assert 'encodeURIComponent' in js


def test_panel_has_resume_drop_for_suspended():
    """docs/88 真伤2:详情页对**挂起/改方向**记录有「继续 / 放下」出口 —— 真调 resume/drop 端点 +
    按钮文案走 i18n,让永久僵尸有路可走。"""
    js = _read("pursuits_panel.js")
    assert '/resume"' in js, "resume 端点没接(POST /api/pursuit/{id}/resume)"
    assert '/drop"' in js, "drop 端点没接(POST /api/pursuit/{id}/drop)"
    assert 'pursuit.resume_btn' in js and 'pursuit.drop_btn' in js, "继续/放下按钮文案没走 i18n"
    assert re.search(r'p\.suspended\s*\|\|\s*p\.status\s*===\s*"revised"', js), \
        "继续/放下应只对挂起/改方向记录显示"


# ---- 2. i18n 双表齐 + 入口键在 ----

def _extract_block(js: str, label: str) -> set:
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


def test_pursuit_i18n_keys_en_zh_parity():
    i18n = _read("i18n.js")
    en = _extract_block(i18n, "en")
    zh = _extract_block(i18n, "zh")
    js = _read("pursuits_panel.js")
    used = set(re.findall(r'\bt\(\s*"([^"]+)"\s*[,)]', js))
    # 动态状态键 t("pursuit.st." + …) 正则天然抓不到 → 显式点名锁全五态
    used |= {f"pursuit.st.{s}" for s in ("active", "committed", "revised", "done", "dropped")}
    # 时间线行状态经 t(stKey) 变量分派(docs/88 第三刀 #2)→ 正则抓不到,显式点名
    used |= {f"pursuit.round.{s}" for s in ("running", "error", "done")}
    used |= {"nav.pursuits", "nav.pursuits.title", "nav.pursuits_side"}   # index.html 入口键(列头 + 侧栏第 14 项)
    missing_en = used - en
    missing_zh = used - zh
    assert not missing_en, f"en 表缺 pursuit 键: {sorted(missing_en)}"
    assert not missing_zh, f"zh 表缺 pursuit 键: {sorted(missing_zh)}"


# ---- 3. 安全:不许 innerHTML 拼接(后端文本必须 textContent 落 DOM)----

def test_panel_no_innerhtml_injection():
    js = _read("pursuits_panel.js")
    for m in re.finditer(r'\.innerHTML\s*=\s*(.+);', js):
        assert m.group(1).strip() in ('""', "''"), \
            f"pursuits_panel.js 有非清空的 innerHTML 赋值(注入面): {m.group(0)}"
    assert "insertAdjacentHTML" not in js
    assert "document.write" not in js


# ---- 4. 名词预算:用户可见文案不直出内部词 ----

def test_no_internal_jargon_in_user_copy():
    i18n = _read("i18n.js")
    en = _extract_block(i18n, "en")
    # 从 en/zh 表抽出所有 pursuit.*/nav.pursuits* 的值,断言不含内部词
    banned = ("verify_gate", "H2A", "PursuitManager", "verify gate")
    for label in ("en", "zh"):
        m = re.search(rf"\b{label}\s*:\s*\{{", i18n)
        start = m.end() - 1
        depth = 0
        end = start
        for i in range(start, len(i18n)):
            if i18n[i] == "{":
                depth += 1
            elif i18n[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        body = i18n[start + 1:end]
        for k, v in re.findall(r'"((?:nav\.)?pursuit[^"]*)"\s*:\s*"([^"]*)"', body):
            for b in banned:
                assert b.lower() not in v.lower(), f"{label} 表 {k} 文案直出内部词 {b!r}: {v}"
    assert en   # sanity
