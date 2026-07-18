"""四件 UI 收口的真浏览器回归(Playwright,2026-07-16)。

契约测试(test_console_static / test_console_render_static)锁字符串;这里验**浏览器运行时**:
"标签移了、注册表写了"≠"面板真的还开得开"。覆盖:

  1. T4 懒加载真验:开机只有 boot-ensure 的 models/tokens 两个面板脚本(meter/setup gate 行为
     不变),其余面板全局 undefined、无 <script id=panel-js-*>;
  2. 逐个打开全部 14 个左导航面板:脚本按需注入、面板真渲染(mgmt-body 有内容且可见 rect>0)、
     全程 0 console error / 0 pageerror;
  3. ② 侧栏三组分组:三个组标题可见(rect>0)、14 项入口都在(docs/88 第三刀:🎯我的追求进主导航);
  4. ④ T5 highlight 懒加载:开机无 hljs;真发一条带代码块的消息 → highlight.min.js+CSS 被注入、
     代码块真高亮(hljs span)且可见;
  5. ① CFG-01②:models 面板新增区 = provider 预设选择器(带「高级/自定义」回退);
  6. 桌面视图正常:dock 14 入口、从 dock 开面板走同一条懒加载路径;
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

# 14 个左导航面板(= index.html data-panel 顺序 = app.js _PANEL_SCRIPTS nav 批)
# docs/88 第三刀:🎯 pursuits 进「你的团队」组(Hardy 拍本程功能优先)
_NAV_PANELS = ["domains", "roles", "atoms", "agents", "external", "devices", "pursuits",
               "memory", "decision_prefs", "skills",
               "models", "diagnose", "files", "schedules"]


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

        # ---- 3) ② 侧栏三组分组:组标题可见、14 项都在 ----
        titles = page.evaluate(
            "[...document.querySelectorAll('.sidebar .nav-group-title')].map((n) => n.textContent.trim())")
        assert len(titles) == 3 and titles[0].startswith("👥") and titles[1].startswith("🧠") \
            and titles[2].startswith("🔧"), f"侧栏应是 👥/🧠/🔧 三组小标题,实际 {titles}"
        for i in range(1, 4):
            r = _visible_rect(page, f".sidebar .nav-group:nth-of-type({i}) .nav-group-title")
            assert r["w"] > 0 and r["h"] > 0, f"第 {i} 组标题应真可见(rect>0),实际 {r}"
        nav_count = page.evaluate("document.querySelectorAll('.sidebar .nav-item[data-panel]').length")
        assert nav_count == 14, f"左导航应 14 项,实际 {nav_count}"   # docs/88 第三刀:pursuits 进主导航
        page.screenshot(path=os.path.join(_SHOTS, "01-sidebar-groups.png"))

        # ---- 2) 逐个打开全部 14 个面板:脚本注入 + 真渲染 + 0 error ----
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

        # ---- 6) 桌面视图正常:dock 14 入口 + 从 dock 开面板(同一条懒加载路径)----
        page.click("#view-opt-desk")
        page.wait_for_function("document.body.classList.contains('desk-view')", timeout=5000)
        dock_panels = page.evaluate(
            "document.querySelectorAll('#desk-dock .dock-item[data-panel]').length")
        assert dock_panels == 14, f"desk dock 应同构复用 14 入口,实际 {dock_panels}"   # docs/88 第三刀:pursuits 进主导航
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
