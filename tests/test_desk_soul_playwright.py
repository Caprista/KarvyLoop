"""desk 灵魂层 P1.5 的真浏览器验收(Playwright,docs/53)。

jsdom smoke 验逻辑,这里验**浏览器运行时**(官方原图真加载没 / CSS transition 真跑没 /
0 console error)——"看着写完了"≠"真能跑"(web_verify 同一教训)。

覆盖(全部走真实契约形状,没有假戏):
  1. desk 视图进场 → 右下小卡 sprite 渲染出来(官方原图 img 真加载,naturalWidth>0);
  2. h2a 卡到达(app.js 收 h2a_proposal 调的同一个钩子 KarvyDesktop.notifyH2A)
     → 叼卡小演员出现(carry 态 + 小白卡 overlay 真显示)→ 到位撤掉 → ⚖ 便签闪;
  3. task_status done(契约形状喂灵魂接缝)→ 署名便签浮出(who + 结果摘要);
  4. 全程 0 console error / 0 pageerror。

桌面视图三件套(2026-07-04,Hardy 实拍回归锁):
  5. 开屏回放存量 pending 卡(≥1 张,复现纪律)→ 前 3 秒吉祥物稳在窝(不叼卡不离席不闪),
     卡照常摆回列表;**真**新卡(live 事件)→ 叼卡剧场仍触发(功能没修没);
  6. 首开默认布局 = "有人住的桌面":4 张便签铺右侧两列(⚖ 预留整槽不盖 📥 头)、
     聊天浮窗待命(非最小化)、dock 在、吉祥物在窝;
  7. 日/夜壁纸:mock 时间(page.clock)验 auto 档昼夜类名,固定/关闭档 + localStorage 持久。

守卫:Playwright(python 包)没装 → skip(老实降级,不假装验过);
      console 起不来(端口/环境)→ fail(这是真回归,不吞)。
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="Playwright 未装(pip install playwright && playwright install chromium)")
from playwright.sync_api import sync_playwright  # noqa: E402


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def console_url():
    """起一个真 console(--no-llm:不碰模型/key,纯前端验收够用)。"""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "karvyloop", "console", "--no-llm", "--no-browser",
         "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 40
        up = False
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail("console 进程提前退出(--no-llm 启动失败)")
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


def test_desk_soul_in_real_browser(console_url):
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 首启 tour(driver.js 全屏遮罩)会拦点击 —— 标记已看过(测试不验 tour)
        page.add_init_script("try { localStorage.setItem('karvyloop_tour_done', '1'); } catch (e) {}")
        page.goto(console_url, wait_until="domcontentloaded")
        page.wait_for_selector("#view-opt-desk", timeout=10000)
        page.click("#view-opt-desk")
        page.wait_for_function("document.body.classList.contains('desk-view')", timeout=5000)

        # 1) 小卡 sprite 真渲染:根可见、官方原图 <img> 真加载(不是 404/占位)
        page.wait_for_selector("#desk-karvy-pixel", state="visible", timeout=5000)
        loaded = page.evaluate("""() => {
            const img = document.querySelector('#desk-karvy-pixel img.karvy-sprite-img');
            if (!img || !/karvy-capybara\\.png/.test(img.src)) return 0;
            return img.complete && img.naturalWidth > 0 ? img.naturalWidth : 0;
        }""")
        assert loaded > 0, f"小卡 sprite 应真加载官方原图(naturalWidth>0),实际 {loaded}"
        assert page.evaluate(
            "document.getElementById('desk-karvy-pixel').getAttribute('data-state') !== null"
        ), "sprite 根应带 data-state(CSS 状态动画钩子)"

        # 2) h2a 到达(app.js 收 h2a_proposal 调的就是这个钩子)→ 叼卡动画元素出现
        page.evaluate("window.KarvyDesktop.notifyH2A()")
        page.wait_for_selector("#desk-carry-actor", state="attached", timeout=3000)
        carry = page.evaluate("""() => {
            const sp = document.querySelector('#desk-carry-actor .karvy-sprite');
            if (!sp) return {state: '', card: 'none'};
            const card = sp.querySelector('.karvy-sprite-card');
            return {state: sp.getAttribute('data-state') || '',
                    card: card ? getComputedStyle(card).display : 'none'};
        }""")
        assert carry["state"] == "carry", f"叼卡小演员应进 carry 态,实际 {carry}"
        assert carry["card"] != "none", f"carry 态应真显示小白卡 overlay,实际 {carry}"
        # 到位:小演员撤掉、⚖ 便签闪(真 transition,transitionend/2s 兜底双保险)
        page.wait_for_selector("#desk-carry-actor", state="detached", timeout=4000)
        assert page.evaluate(
            "document.querySelector('.col-decide').classList.contains('note-alert')"
        ), "小卡到位后 ⚖ 便签应闪(note-alert)"

        # 3) task_status done(契约形状)→ 署名便签浮出
        page.evaluate("""window.KarvyDesktop._soul.handle({type: 'task_status', payload: {
            id: 'e2e1', who: '研究员', role: 'researcher', status: 'done',
            intent: '整理季度周报', result: '周报已生成,重点三条', finished: Date.now() / 1000
        }})""")
        page.wait_for_selector(".desk-signed-note", state="visible", timeout=3000)
        note_text = page.inner_text(".desk-signed-note")
        assert "研究员" in note_text and "周报已生成" in note_text, f"署名便签应带 who+结果摘要,实际: {note_text}"

        # 4) presence API 是否上线都不许崩:栏要么优雅隐藏要么真渲染,没有第三态
        presence_state = page.evaluate("""() => {
            const bar = document.getElementById('desk-presence');
            if (!bar) return 'missing';
            return bar.classList.contains('hidden') ? 'hidden'
                : (document.querySelectorAll('.desk-station').length > 0 || document.querySelectorAll('.desk-workcard').length > 0)
                    ? 'populated' : 'empty-shown';
        }""")
        assert presence_state in ("hidden", "populated"), f"工位栏不许空壳: {presence_state}"

        # 老视图零回归:切回对话视图,sprite 替身移除(fab 静态 PNG 回归)、灵魂 DOM 不漏
        page.click("#view-opt-chat")
        page.wait_for_function("!document.body.classList.contains('desk-view')", timeout=5000)
        assert page.evaluate("!document.getElementById('desk-karvy-pixel')"), "离开 desk 应移除 sprite 替身"
        assert page.evaluate("document.querySelectorAll('.desk-signed-note').length === 0"), "离开 desk 应清署名便签"

        browser.close()

    assert errors == [], f"真浏览器 console 必须 0 error,实际: {errors}"


# ============================================================================
# 桌面视图三件套(2026-07-04):开屏回放不演剧场 / 首开默认布局 / 日夜壁纸
# ============================================================================

import json  # noqa: E402

# 真实契约形状(Proposal.to_dict,karvyloop/karvy/atoms.py)——route-mock /api/proposals/pending
_PENDING_FIXTURE = {
    "proposals": [
        {
            "summary": "把周报整理成模板并沉淀为技能",
            "options": ["ACCEPT", "DEFER", "REJECT"],
            "strength": 0.9, "evidence_refs": [1, 2], "habit_id": 7,
            "model_ref": "test", "ts": 1751600000.0,
            "kind": "crystallize_skill", "payload": {},
            "proposal_id": "crystallize_skill-7-abc12345",
            "basis": "最近 3 次都手工整理了周报", "context_ref": {},
        },
        {
            "summary": "老板付 10 万的单子接不接",
            "options": ["ACCEPT", "DEFER", "REJECT"],
            "strength": 0.8, "evidence_refs": [], "habit_id": 8,
            "model_ref": "test", "ts": 1751600001.0,
            "kind": "confirm_decision_pref", "payload": {},
            "proposal_id": "confirm_decision_pref-8-def6789a",
            "basis": "邮件里出现了新合同", "context_ref": {},
        },
    ]
}

_CARRY_PROBE = """
    window.__carrySeen = false;
    (function probe() {
        if (!document.documentElement) { setTimeout(probe, 5); return; }
        new MutationObserver(() => {
            if (document.getElementById('desk-carry-actor')) window.__carrySeen = true;
            const cv = document.getElementById('desk-karvy-pixel');
            if (cv && cv.classList.contains('is-away')) window.__carrySeen = true;
        }).observe(document.documentElement,
                   {childList: true, subtree: true, attributes: true, attributeFilter: ['class']});
    })();
"""


def _wire(page, errors, *, desk_boot: bool):
    """公共接线:tour 跳过 + (可选)以 desk 为首开视图 + console error 收集。"""
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    boot = "try { localStorage.setItem('karvyloop_tour_done', '1');"
    if desk_boot:
        boot += " localStorage.setItem('karvyloop_view', 'desk');"
    boot += " } catch (e) {}"
    page.add_init_script(boot)


def test_desk_boot_replay_mascot_stays_home(console_url):
    """复现纪律:先造 ≥1 张 pending 决策卡再刷新 —— 开屏前 3 秒吉祥物必须稳在窝里
    (存量卡 = 状态回放,不是新卡事件);随后真新卡到来,叼卡剧场仍要演(功能别修没)。"""
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        _wire(page, errors, desk_boot=True)
        page.route("**/api/proposals/pending",
                   lambda route: route.fulfill(status=200, content_type="application/json",
                                               body=json.dumps(_PENDING_FIXTURE)))
        page.add_init_script(_CARRY_PROBE)
        page.goto(console_url, wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('desk-view')", timeout=10000)
        page.wait_for_timeout(3000)   # 录开屏前 3 秒(MutationObserver 探针盯全程,不靠轮询碰运气)

        assert not page.evaluate("window.__carrySeen"), \
            "开屏 3 秒内吉祥物离窝了(叼卡剧场被存量 pending 卡误触发 —— Hardy 实拍 bug 回归)"
        cards = page.evaluate("document.querySelectorAll('#h2a-list .h2a-card').length")
        assert cards == 2, f"状态回放本身要照做:2 张存量卡应摆回 ⚖ 列表,实际 {cards}"
        assert not page.evaluate(
            "document.querySelector('.col-decide').classList.contains('note-alert')"
        ), "回放不许闪 ⚖(剧场只回应真事件)"
        assert page.evaluate(
            "!document.getElementById('desk-karvy-pixel').classList.contains('is-away')"
        ), "吉祥物应稳在右下窝里"

        # 真新卡到来(app.js 收 WS h2a_proposal 调的同一钩子,不带 replay)→ 剧场照演
        page.evaluate("window.KarvyDesktop.notifyH2A()")
        page.wait_for_selector("#desk-carry-actor", state="attached", timeout=3000)
        page.wait_for_selector("#desk-carry-actor", state="detached", timeout=4000)
        assert page.evaluate(
            "document.querySelector('.col-decide').classList.contains('note-alert')"
        ), "真新卡到位后 ⚖ 应闪(叼卡剧场不许修没)"
        browser.close()
    assert errors == [], f"真浏览器 console 必须 0 error,实际: {errors}"


def test_desk_first_open_default_layout(console_url):
    """首开默认态 = 布置好的桌面(不是空场):4 张便签铺右侧两列、⚖ 整槽不盖 📥 头、
    聊天浮窗待命(非最小化)、dock 在位、吉祥物在窝。只验默认 —— 用户挪过的存档另有拖拽测试。"""
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        _wire(page, errors, desk_boot=False)   # 家仍是对话视图(方案 A),手动切进桌面
        page.goto(console_url, wait_until="domcontentloaded")
        page.wait_for_selector("#view-opt-desk", timeout=10000)
        page.click("#view-opt-desk")
        page.wait_for_function("document.body.classList.contains('desk-view')", timeout=5000)

        # 4 张便签全部拿到 translate3d 摆位(有人住的铺开,不是 0,0 堆角落)
        layout = page.evaluate("""() => {
            const out = {};
            document.querySelectorAll('.cockpit-grid .cockpit-col').forEach((c) => {
                const k = Array.from(c.classList).find((x) => x.indexOf('col-') === 0);
                out[k] = { x: parseFloat(c.dataset.deskX || 'NaN'), y: parseFloat(c.dataset.deskY || 'NaN'),
                           t: c.style.transform };
            });
            out._w = document.querySelector('.cockpit').getBoundingClientRect().width;
            return out;
        }""")
        for k in ("col-decide", "col-intel", "col-predict", "col-busy"):
            assert layout[k]["t"].startswith("translate3d"), f"{k} 未摆位(空场):{layout}"
        # ⚖ 在最右列且第一(永远第一);📥 同列在其整槽(330px)之下 —— 存量卡长高也盖不住头
        assert layout["col-decide"]["x"] > layout["_w"] * 0.6, f"⚖ 应铺在右侧: {layout}"
        assert layout["col-intel"]["x"] == layout["col-decide"]["x"], "📥 应与 ⚖ 同列"
        assert layout["col-intel"]["y"] - layout["col-decide"]["y"] >= 330, \
            f"⚖ 应预留整槽(≥330px),否则待拍卡一多就盖住 📥 的头: {layout}"
        assert layout["col-predict"]["x"] < layout["col-decide"]["x"], "🔮 应在第二列(错落有桌感)"
        # 聊天浮窗待命:可见、非最小化、在左半区(默认主位)
        chat = page.evaluate("""() => {
            const ov = document.getElementById('chat-modal');
            const p = document.querySelector('#chat-modal .chat-panel');
            const r = p.getBoundingClientRect();
            return { min: ov.classList.contains('desk-min'), w: r.width, x: r.left };
        }""")
        assert not chat["min"] and chat["w"] > 400, f"聊天浮窗应默认待命(非最小化): {chat}"
        assert chat["x"] < layout["_w"] * 0.4, f"聊天浮窗默认应在左半区: {chat}"
        # dock 在位(12 入口 + 窗口指示 + 🌗 + ↺),吉祥物在窝
        assert page.evaluate("document.querySelectorAll('#desk-dock .dock-item').length") >= 12
        assert page.is_visible("#desk-dock")
        assert page.is_visible("#desk-karvy-pixel")
        assert page.evaluate("document.getElementById('desk-wall-btn') !== null"), "dock 应有 🌗 壁纸换挡"
        browser.close()
    assert errors == [], f"真浏览器 console 必须 0 error,实际: {errors}"


def test_desk_wallpaper_day_night(console_url):
    """日/夜壁纸:mock 客户端时钟(page.clock)—— auto 档白天挂 desk-wall-day、
    夜晚换 desk-wall-night;固定档/off 档立即生效且 localStorage 持久;离场摘类。"""
    import datetime
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        _wire(page, errors, desk_boot=True)
        page.clock.set_fixed_time(datetime.datetime(2026, 7, 4, 10, 0, 0))   # 白天 10:00
        page.goto(console_url, wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('desk-view')", timeout=10000)
        assert page.evaluate("document.body.classList.contains('desk-wall-day')"), \
            "auto 档(默认)白天开屏应挂 desk-wall-day"
        # 时间走到夜里 → 重判(生产 = 分钟级 interval 调同一 apply;测试直接调,不等真分钟)
        page.clock.set_fixed_time(datetime.datetime(2026, 7, 4, 22, 0, 0))
        page.evaluate("window.KarvyDesktop._wall.apply()")
        assert page.evaluate("document.body.classList.contains('desk-wall-night')"), \
            "auto 档 19:00 后应换挂 desk-wall-night"
        # 固定档 + off 档 + localStorage 持久
        page.evaluate("window.KarvyDesktop._wall.set('day')")
        assert page.evaluate("document.body.classList.contains('desk-wall-day')")
        page.evaluate("window.KarvyDesktop._wall.set('off')")
        assert page.evaluate(
            "!document.body.classList.contains('desk-wall-day') && !document.body.classList.contains('desk-wall-night')"
        ), "off 档 = 纯色回现状"
        assert page.evaluate("localStorage.getItem('karvyloop_desk_wall.v1')") == "off"
        # 壁纸图真的能被服务出来(不是 404 装饰)
        for name in ("wallpaper-day.jpg", "wallpaper-night.jpg"):
            status = page.evaluate(
                "(u) => fetch(u).then((r) => r.status)", f"/static/assets/{name}")
            assert status == 200, f"{name} 应能静态服务(实际 {status})"
        # 离场摘类(老视图零痕迹)
        page.evaluate("window.KarvyDesktop._wall.set('night')")
        page.click("#view-opt-chat")
        page.wait_for_function("!document.body.classList.contains('desk-view')", timeout=5000)
        assert page.evaluate(
            "!document.body.classList.contains('desk-wall-day') && !document.body.classList.contains('desk-wall-night')"
        ), "离开桌面应摘光壁纸类"
        browser.close()
    assert errors == [], f"真浏览器 console 必须 0 error,实际: {errors}"
