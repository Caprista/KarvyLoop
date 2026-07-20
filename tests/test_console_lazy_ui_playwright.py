"""四件 UI 收口的真浏览器回归(Playwright,2026-07-16)。

契约测试(test_console_static / test_console_render_static)锁字符串;这里验**浏览器运行时**:
"标签移了、注册表写了"≠"面板真的还开得开"。覆盖:

  1. T4 懒加载真验:开机只有 boot-ensure 的 models/tokens 两个面板脚本(meter/setup gate 行为
     不变),其余面板全局 undefined、无 <script id=panel-js-*>;
  2. 逐个打开全部 13 个左导航面板:脚本按需注入、面板真渲染(mgmt-body 有内容且可见 rect>0)、
     全程 0 console error / 0 pageerror;
  3. ② 侧栏三组分组(docs/90 刀2 挪位):三个组标题可见(rect>0)、13 项入口都在
     (docs/90 刀1:🎯我的追求双入口收一从左导航撤下);agents/external 已挪进引擎室;
  3b. docs/90 刀2 三组可折叠:默认 你的团队+它学到的你 展开、引擎室 收起(项 rect=0 但仍在 DOM);
     点组标题折/展 + 键盘可达(Enter);选择记 localStorage(karvyloop_navfold_<group>)重载保持;
  3c. docs/90 刀2 决策偏好就近入口:⚖ 列头 🧭 小钮开「你的决策偏好」面板(同一 data-panel 委托绑定);
  4. ④ T5 highlight 懒加载:开机无 hljs;真发一条带代码块的消息 → highlight.min.js+CSS 被注入、
     代码块真高亮(hljs span)且可见;
  5. ① CFG-01②:models 面板新增区 = provider 预设选择器(带「高级/自定义」回退);
  6. 桌面视图正常:dock 13 入口(**引擎室收起时也全**——克隆按 querySelectorAll 不看 display)、
     从 dock 开面板走同一条懒加载路径;
  7. 👀 demo 入口(demo_panel 原自绑 → 懒加载后第一击由 app.js 接)仍一击即开、不双开。

截图 3 张(tests/_artifacts/lazy_ui/):分组后侧栏 / 懒加载面板打开态 / models 新增区预设选择器。

守卫:Playwright 没装 → skip;console 起不来 → fail(真回归,不吞)。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

import pytest

playwright = pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright 未装(pip install playwright && playwright install chromium)")
from playwright.sync_api import sync_playwright  # noqa: E402

_PORT = 8905          # 任务钦点端口
_SHOTS = os.path.join(os.path.dirname(__file__), "_artifacts", "lazy_ui")

# 13 个左导航面板(= index.html data-panel 顺序 = app.js _PANEL_SCRIPTS nav 批)
# docs/90 刀1:🎯 pursuits「双入口收一」从左导航撤下(只留决策舱列头就近入口)→ 侧栏 13 项
# docs/90 刀2:agents/external 从「你的团队」挪进引擎室(§C #4/#5 稀有配置动作;组内相对顺序保持)
_NAV_PANELS = ["domains", "roles", "atoms", "devices",
               "memory", "decision_prefs", "skills",
               "agents", "external", "models", "diagnose", "files", "schedules"]


@pytest.fixture(scope="module")
def console_url():
    proc = subprocess.Popen(
        [sys.executable, "-m", "karvyloop", "console", "--no-llm", "--no-browser",
         "--host", "127.0.0.1", "--port", str(_PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{_PORT}"
    try:
        deadline = time.time() + 40
        up = False
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"console 进程提前退出(--no-llm --port {_PORT} 启动失败)")
            try:
                with urllib.request.urlopen(base + "/api/snapshot", timeout=1) as r:
                    if r.status == 200:
                        up = True
                        break
            except Exception:
                time.sleep(0.5)
        if not up:
            pytest.fail("console 40s 内没起来")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


# --no-llm 下 /api/model/config 回 no_llm → models 面板走只读空态,渲不出新增区。
# 与 desk 测试同法:route-mock 真实契约形状(不碰模型/key),让面板走满配路径。
_MODEL_CONFIG_FIXTURE = {
    "models": [{"id": "demo/model", "provider": "demo", "api": "openai-completions",
                "context_window": 200000, "max_tokens": 8192, "has_key": True,
                "api_key_masked": "sk-***", "is_default_chat": True}],
    "valid_apis": ["openai-completions", "anthropic-messages"],
    "valid_reasoning": ["fast", "balanced", "deep"], "default_reasoning": "",
}
_PRESETS_FIXTURE = {
    "presets": [
        {"id": "anthropic", "name": "Anthropic", "api": "anthropic-messages",
         "model_id": "claude", "get_key_url": "https://x", "key_env": "ANTHROPIC_API_KEY"},
        {"id": "openai", "name": "OpenAI", "api": "openai-completions",
         "model_id": "gpt", "get_key_url": "https://y", "key_env": "OPENAI_API_KEY"},
    ]
}


def _json_route(page, pattern, payload):
    page.route(pattern, lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(payload)))


def _wire(page, errors):
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script("try { localStorage.setItem('karvyloop_tour_done', '1'); } catch (e) {}")
    _json_route(page, "**/api/model/config", _MODEL_CONFIG_FIXTURE)
    _json_route(page, "**/api/providers/presets", _PRESETS_FIXTURE)
    _json_route(page, "**/api/providers/detect_local", {"found": False})
    _json_route(page, "**/api/search/config", {"mode": "keyless", "providers": ["brave", "tavily"]})
    # 本机真实例可能有存量待拍板卡,会叠在右下 FAB 上拦点击 → 钉空列表(确定性)
    _json_route(page, "**/api/proposals/pending", {"proposals": []})


def _visible_rect(page, sel: str) -> dict:
    return page.evaluate(
        "(s) => { const e = document.querySelector(s); if (!e) return {w: 0, h: 0};"
        " const r = e.getBoundingClientRect(); return {w: r.width, h: r.height}; }", sel)


def test_lazy_panels_full_regression(console_url):
    os.makedirs(_SHOTS, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        _wire(page, errors)
        page.goto(console_url, wait_until="domcontentloaded")
        page.wait_for_selector(".sidebar .nav-item[data-panel]", timeout=10000)

        # ---- 1) T4 懒加载真验:开机面板脚本不在(boot-ensure 的 models/tokens 除外)----
        page.wait_for_function(
            "document.getElementById('panel-js-models') !== null"
            " && document.getElementById('panel-js-tokens') !== null", timeout=10000)
        boot_ids = page.evaluate(
            "[...document.querySelectorAll('script[id^=\"panel-js-\"]')].map((s) => s.id).sort()")
        assert boot_ids == ["panel-js-models", "panel-js-tokens"], \
            f"开机只该 ensure models(setup gate)+ tokens(💰 meter),实际 {boot_ids}"
        lazies = page.evaluate("""() => {
            const gs = ['KarvyDevicesPanel','KarvyMemoryPanel','KarvySkillsPanel','KarvyExternalPanel',
                        'KarvyDomainsPanel','KarvyRolesPanel','KarvyAtomsPanel','KarvyAgentsPanel',
                        'KarvyFilesPanel','KarvySchedulesPanel','KarvyDiagnosePanel','KarvyDecisionPrefs',
                        'KarvyDemoPanel'];
            return gs.filter((g) => window[g] !== undefined);
        }""")
        assert lazies == [], f"这些面板全局不该开机就在(没真懒): {lazies}"
        # ④ T5:开机无 highlight(代码块出现才注入)
        assert page.evaluate("window.hljs === undefined"), "开机不该有 hljs(T5 懒加载)"
        assert page.evaluate("document.getElementById('hljs-js') === null")

        # ---- 3) ② 侧栏三组分组:组标题可见、13 项都在 ----
        # docs/90 刀2:标题文案在子 span(data-i18n),箭头另占一个 span —— 读文案取 span 不取整行
        titles = page.evaluate(
            "[...document.querySelectorAll('.sidebar .nav-group-title span[data-i18n]')]"
            ".map((n) => n.textContent.trim())")
        # docs/90 刀1(层级/图标):分组标题去 emoji、走纯文字区段眉标(图标归面板项)。
        # 锁"三组纯文字小标题"不变量(en/zh 两语都认),顺带钉死"不再以 emoji 开头"。
        _expect_titles = {
            ("Your Team", "What it's learned about you", "Engine Room"),   # en
            ("你的团队", "它学到的你", "引擎室"),                              # zh
        }
        assert len(titles) == 3 and tuple(titles) in _expect_titles, \
            f"侧栏应是三组纯文字小标题(docs/90 刀1:分组去 emoji、图标归面板项),实际 {titles}"
        for i in range(1, 4):
            r = _visible_rect(page, f".sidebar .nav-group:nth-of-type({i}) .nav-group-title")
            assert r["w"] > 0 and r["h"] > 0, f"第 {i} 组标题应真可见(rect>0),实际 {r}"
        nav_count = page.evaluate("document.querySelectorAll('.sidebar .nav-item[data-panel]').length")
        assert nav_count == 13, f"左导航应 13 项,实际 {nav_count}"   # docs/90 刀1:pursuits 双入口收一从左导航撤下
        page.screenshot(path=os.path.join(_SHOTS, "01-sidebar-groups.png"))

        # ---- 3b) docs/90 刀2:三组可折叠(默认态 / 折展 / localStorage 记忆 / 键盘可达)----
        def _fold_state(g):
            return page.evaluate(
                "(g) => { const grp = document.querySelector(`.sidebar .nav-group[data-navgroup='${g}']`);"
                " const t = grp.querySelector('.nav-group-title');"
                " const item = grp.querySelector('.nav-item');"
                " const r = item.getBoundingClientRect();"
                " return { folded: grp.classList.contains('is-folded'),"
                "          expanded: t.getAttribute('aria-expanded'),"
                "          arrow: (t.querySelector('.nav-fold-arrow') || {}).textContent,"
                "          in_dom: !!item, item_w: r.width, item_h: r.height }; }", g)

        # ① 默认态:你的团队+它学到的你 展开、引擎室 收起(docs/90 §C:引擎室整组降 S3 折叠)
        for g in ("team", "learned"):
            st = _fold_state(g)
            assert not st["folded"] and st["expanded"] == "true" and st["arrow"] == "▾", \
                f"{g} 组默认应展开(▾),实际 {st}"
            assert st["item_w"] > 0 and st["item_h"] > 0, f"{g} 组的面板项默认应可见,实际 {st}"
        st = _fold_state("engine")
        assert st["folded"] and st["expanded"] == "false" and st["arrow"] == "▸", \
            f"引擎室默认应收起(▸),实际 {st}"
        assert st["in_dom"] and st["item_w"] == 0 and st["item_h"] == 0, \
            f"收起组的项应 display:none 但仍在 DOM(desk dock 靠它克隆),实际 {st}"

        # ② 点组标题展开引擎室 → 项可见 + localStorage 记住
        page.click(".sidebar .nav-group[data-navgroup='engine'] .nav-group-title")
        st = _fold_state("engine")
        assert not st["folded"] and st["arrow"] == "▾" and st["item_w"] > 0, f"点标题应展开引擎室,实际 {st}"
        assert page.evaluate("localStorage.getItem('karvyloop_navfold_engine')") == "0"
        # ③ 键盘可达:Enter 折叠「你的团队」
        page.focus(".sidebar .nav-group[data-navgroup='team'] .nav-group-title")
        page.keyboard.press("Enter")
        st = _fold_state("team")
        assert st["folded"] and st["expanded"] == "false" and st["item_w"] == 0, \
            f"Enter 应折叠你的团队(键盘可达),实际 {st}"
        assert page.evaluate("localStorage.getItem('karvyloop_navfold_team')") == "1"
        # ④ 重载:折/展选择保持(引擎室仍展开、你的团队仍收起 —— 都与默认态相反,真读了存档)
        page.reload(wait_until="domcontentloaded")
        # 你的团队此刻收着(第一项 display:none)→ 等 attached 而非默认的 visible;
        # setupNavFold 读档跑完的信号 = 引擎室从 HTML 预置 is-folded 被翻回展开
        page.wait_for_selector(".sidebar .nav-item[data-panel]", state="attached", timeout=10000)
        page.wait_for_function(
            "!document.querySelector(\".sidebar .nav-group[data-navgroup='engine']\")"
            ".classList.contains('is-folded')", timeout=10000)
        st = _fold_state("engine")
        assert not st["folded"] and st["item_w"] > 0, f"重载后引擎室应保持展开(localStorage),实际 {st}"
        st = _fold_state("team")
        assert st["folded"] and st["item_w"] == 0, f"重载后你的团队应保持收起(localStorage),实际 {st}"
        # ⑤ 展回你的团队,三组全展开 —— 后面逐面板点击要用
        page.click(".sidebar .nav-group[data-navgroup='team'] .nav-group-title")
        st = _fold_state("team")
        assert not st["folded"] and st["item_w"] > 0, f"再点标题应展回你的团队,实际 {st}"

        # ---- 2) 逐个打开全部 13 个面板:脚本注入 + 真渲染 + 0 error ----
        for name in _NAV_PANELS:
            # 上一面板的 modal 蒙层会拦住侧栏点击 → 每轮先关掉;并清掉旧内容,
            # 让"渲染出来了"的等待不吃旧 DOM 的假阳性
            page.evaluate("""() => {
                const o = document.getElementById('mgmt-modal');
                if (o && !o.classList.contains('hidden')) {
                    document.getElementById('mgmt-close').click();
                }
                document.getElementById('mgmt-body').innerHTML = '';
            }""")
            page.wait_for_function(
                "document.getElementById('mgmt-modal').classList.contains('hidden')", timeout=5000)
            page.click(f'.sidebar .nav-item[data-panel="{name}"]')
            page.wait_for_function(
                f"document.getElementById('panel-js-{name}') !== null", timeout=10000)
            page.wait_for_function(
                "() => { const o = document.getElementById('mgmt-modal');"
                " const b = document.getElementById('mgmt-body');"
                " return o && !o.classList.contains('hidden') && b && b.children.length > 0; }",
                timeout=15000)
            r = _visible_rect(page, "#mgmt-body")
            assert r["w"] > 0 and r["h"] > 0, f"{name} 面板应真可见(rect>0),实际 {r}"
            assert not errors, f"打开 {name} 面板出了 console/page error: {errors}"
            if name == "devices":   # 截图②:懒加载面板打开态(最大的 63K 包)
                page.wait_for_timeout(400)   # 等 panel-swap 入场动画收尾,别拍半透明帧
                page.screenshot(path=os.path.join(_SHOTS, "02-lazy-panel-open.png"))
            if name == "models":
                # ---- 5) ① CFG-01②:新增区 = provider 预设选择器 + 高级回退 ----
                page.wait_for_selector(".models-add-guided .onb-picker", timeout=10000)
                n_prov = page.evaluate(
                    "document.querySelectorAll('.models-add-guided .onb-picker .onb-prov').length")
                assert n_prov >= 1, "新增区应列出 provider 预设按钮"
                adv = page.evaluate("""() => {
                    const w = document.querySelector('.models-add-guided');
                    return [...w.querySelectorAll('button')].some((b) =>
                        (b.className || '').indexOf('mgmt-inline-link') >= 0);
                }""")
                assert adv, "新增区应保留「高级/自定义」回退入口"
                el = page.query_selector(".models-add-guided")
                el.scroll_into_view_if_needed()
                page.wait_for_timeout(400)   # 等 panel-swap 入场动画收尾 + 滚动稳定
                page.screenshot(path=os.path.join(_SHOTS, "03-models-add-guided.png"))
            # domains 面板的「新建角色」直调 KarvyRolesPanel → deps 应把 roles 一起载了
            if name == "domains":
                assert page.evaluate("window.KarvyRolesPanel !== undefined"), \
                    "开 domains 应连带载 roles(「新建角色」直调它)"
        page.click("#mgmt-close")
        page.wait_for_function(
            "document.getElementById('mgmt-modal').classList.contains('hidden')", timeout=5000)

        # ---- 3c) docs/90 刀2:⚖ 列头 🧭 决策偏好就近入口(同一 data-panel 委托绑定)----
        r = _visible_rect(page, "#decision-prefs-open")
        assert r["w"] > 0 and r["h"] > 0, f"⚖ 列头 🧭 就近入口应可见,实际 {r}"
        page.evaluate("document.getElementById('mgmt-body').innerHTML = ''")
        page.click("#decision-prefs-open")
        page.wait_for_function(
            "() => { const o = document.getElementById('mgmt-modal');"
            " const b = document.getElementById('mgmt-body');"
            " return o && !o.classList.contains('hidden') && b && b.children.length > 0; }",
            timeout=10000)
        mgmt_title = page.evaluate("document.getElementById('mgmt-title').textContent")
        assert ("Decision Preferences" in mgmt_title) or ("决策偏好" in mgmt_title), \
            f"就近入口应打开决策偏好面板(dpref.title),实际标题 {mgmt_title!r}"
        assert not errors, f"🧭 就近入口开面板出了 error: {errors}"
        page.click("#mgmt-close")
        page.wait_for_function(
            "document.getElementById('mgmt-modal').classList.contains('hidden')", timeout=5000)

        # ---- 4) ④ T5 highlight 懒加载:发一条带代码块的消息 → 注入 + 真高亮 ----
        # 聊天入口:JS 派发 click(本机真实例任务板有长内容时会叠住 FAB 命中区 ——
        # 预存布局怪癖,与本次改动无关;这里的目的只是保证 chat-log 可见)
        page.evaluate("document.getElementById('chat-open').click()")
        page.wait_for_timeout(300)
        page.evaluate("""() => {
            const log = document.getElementById('chat-log');
            window.KarvyRender.appendMarkdown(log,
                '```python\\ndef hello():\\n    return "world"\\n```');
        }""")
        page.wait_for_function("document.getElementById('hljs-js') !== null", timeout=10000)
        page.wait_for_function("window.hljs !== undefined", timeout=10000)
        assert page.evaluate("document.getElementById('hljs-css') !== null"), \
            "highlight CSS 应与 JS 同点注入"
        page.wait_for_function(
            "document.querySelectorAll('#chat-log pre code [class*=\"hljs\"]').length > 0",
            timeout=10000)
        code_r = _visible_rect(page, "#chat-log pre code")
        assert code_r["w"] > 0 and code_r["h"] > 0, f"代码块应真可见(rect>0),实际 {code_r}"
        assert not errors, f"highlight 懒加载路径出了 error: {errors}"

        # ---- 7) 👀 demo 入口:懒加载后第一击即开(app.js 接第一击,之后 demo 自绑接手)----
        page.click("#demo-open")
        page.wait_for_function(
            "() => { const o = document.getElementById('mgmt-modal');"
            " return o && !o.classList.contains('hidden'); }", timeout=10000)
        page.wait_for_function("document.getElementById('panel-js-demo') !== null", timeout=5000)
        assert page.evaluate("window.KarvyDemoPanel !== undefined")
        page.click("#mgmt-close")
        page.wait_for_function(
            "document.getElementById('mgmt-modal').classList.contains('hidden')", timeout=5000)

        # ---- 6) 桌面视图正常:dock 13 入口 + 从 dock 开面板(同一条懒加载路径)----
        # docs/90 刀2:先把引擎室收回去再首次进桌面(dock 此刻才渲染)—— 真验「收起的组
        # 的 nav-item 仍在 DOM(display:none 不移除),dock 克隆不受折叠影响、始终全入口」
        page.click(".sidebar .nav-group[data-navgroup='engine'] .nav-group-title")
        assert page.evaluate(
            "document.querySelector(\".sidebar .nav-group[data-navgroup='engine']\")"
            ".classList.contains('is-folded')"), "引擎室应已收起(为 dock 折叠免疫验证摆场)"
        page.click("#view-opt-desk")
        page.wait_for_function("document.body.classList.contains('desk-view')", timeout=5000)
        dock_panels = page.evaluate(
            "document.querySelectorAll('#desk-dock .dock-item[data-panel]').length")
        assert dock_panels == 13, f"desk dock 应同构复用 13 入口(引擎室收起也全),实际 {dock_panels}"   # docs/90 刀1:pursuits 双入口收一从左导航撤下
        for eng in ("agents", "external", "models", "diagnose", "files", "schedules"):
            assert page.evaluate(
                f"!!document.querySelector('#desk-dock .dock-item[data-panel=\"{eng}\"]')"), \
                f"引擎室收起时 dock 仍应有 {eng} 入口(克隆按 querySelectorAll,不看 display)"
        page.evaluate("document.getElementById('mgmt-body').innerHTML = ''")
        page.click('#desk-dock .dock-item[data-panel="files"]')
        page.wait_for_function(
            "() => { const o = document.getElementById('mgmt-modal');"
            " const b = document.getElementById('mgmt-body');"
            " return o && !o.classList.contains('hidden') && b && b.children.length > 0; }",
            timeout=10000)
        assert not errors, f"desk dock 开面板出了 error: {errors}"
        page.click("#view-opt-chat")
        page.wait_for_function("!document.body.classList.contains('desk-view')", timeout=5000)

        browser.close()
    assert errors == [], f"真浏览器全程必须 0 error,实际: {errors}"
