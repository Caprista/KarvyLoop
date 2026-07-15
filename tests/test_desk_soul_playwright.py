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
        # 到位:小演员撤掉、**看得见的锚点**收卡一跳(真 transition,transitionend/2s 兜底双保险)。
        # 折起态便签全藏 → 剧场终点=右上看板缩略卡(fold-ping);便签显形时才闪 note-alert
        # (Hardy 实拍:旧剧场朝隐形便签的 (0,0) rect 走,小卡飘过半空"要去哪里?")
        page.wait_for_selector("#desk-carry-actor", state="detached", timeout=4000)
        assert page.evaluate("""() => {
            const fold = document.getElementById('desk-board-fold');
            const note = document.querySelector('.col-decide');
            return (fold && fold.classList.contains('fold-ping'))
                || (note && note.classList.contains('note-alert'));
        }"""), "小卡到位后可见锚点应有到达反馈(看板缩略卡 fold-ping / 显形便签 note-alert)"

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

# 本周纪念物真实契约形状(GET /api/desk/memento,零 LLM 从 Trace/账本投影)
_MEMENTO_FIXTURE = {
    "week_label": "W27", "tasks_done": 12, "skills_new": 3,
    "decisions": 5, "tokens_total": 48200,
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

        # 真新卡到来(app.js 收 WS h2a_proposal 调的同一钩子,不带 replay)→ 剧场照演,
        # 终点=看得见的锚点(折起态便签全藏 → 看板缩略卡收卡一跳;不再朝隐形便签走空路)
        page.evaluate("window.KarvyDesktop.notifyH2A()")
        page.wait_for_selector("#desk-carry-actor", state="attached", timeout=3000)
        page.wait_for_selector("#desk-carry-actor", state="detached", timeout=4000)
        assert page.evaluate("""() => {
            const fold = document.getElementById('desk-board-fold');
            const note = document.querySelector('.col-decide');
            return (fold && fold.classList.contains('fold-ping'))
                || (note && note.classList.contains('note-alert'));
        }"""), "真新卡到位后可见锚点应有到达反馈(叼卡剧场不许修没)"
        browser.close()
    assert errors == [], f"真浏览器 console 必须 0 error,实际: {errors}"


import os  # noqa: E402

_SHOTS_DIR = os.path.join(os.path.dirname(__file__), "_artifacts", "desk_layout")


def test_desk_first_open_default_layout(console_url):
    """首开默认态 = **空旷、单焦点**(Hardy 2026-07-05 重构):主角 = 顶部大时间 + 居中精简聊天;
    标签卡默认**收起停靠**(不自动铺满)、看板**收进 dock 📋 图标**(角标提示新数据);
    待处理任务**轻量列出**(极简条目,非完整卡);dock/吉祥物在位。截图存档给人工看空旷度。"""
    os.makedirs(_SHOTS_DIR, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        _wire(page, errors, desk_boot=False)   # 家仍是对话视图(方案 A),手动切进桌面
        # 造 ≥1 张待拍板卡 → 验待办轻量条真列出 + 看板角标真亮
        page.route("**/api/proposals/pending",
                   lambda route: route.fulfill(status=200, content_type="application/json",
                                               body=json.dumps(_PENDING_FIXTURE)))
        # 造本周纪念物 → 验它退到角落、**不压大时间**(bug1 曾居中 top:22 盖住时钟顶)
        page.route("**/api/desk/memento",
                   lambda route: route.fulfill(status=200, content_type="application/json",
                                               body=json.dumps(_MEMENTO_FIXTURE)))
        page.goto(console_url, wait_until="domcontentloaded")
        page.wait_for_selector("#view-opt-desk", timeout=10000)
        page.click("#view-opt-desk")
        page.wait_for_function("document.body.classList.contains('desk-view')", timeout=5000)
        page.wait_for_timeout(400)   # 等 h2a 卡回放进列表(待办条从中读)

        w = page.evaluate("document.querySelector('.cockpit').getBoundingClientRect().width")

        # ① 空旷:4 张标签卡默认全部**收起**(col-collapsed),不自动铺满右半屏
        collapsed = page.evaluate("""() => {
            const out = {};
            document.querySelectorAll('.cockpit-grid .cockpit-col').forEach((c) => {
                const k = Array.from(c.classList).find((x) => x.indexOf('col-') === 0);
                out[k] = c.classList.contains('col-collapsed');
            });
            return out;
        }""")
        for k in ("col-decide", "col-intel", "col-predict", "col-busy"):
            assert collapsed[k] is True, f"{k} 默认应收起(空旷,不自动铺满): {collapsed}"
        # 看板默认没摊开(收进图标,召唤才出)
        assert not page.evaluate("document.body.classList.contains('desk-board-open')"), "看板默认应收起(不铺满)"

        # ② 居中精简聊天窗在:可见、非最小化、compact(无会话列表)、大致居中
        chat = page.evaluate("""() => {
            const ov = document.getElementById('chat-modal');
            const pnl = document.querySelector('#chat-modal .chat-panel');
            const side = document.querySelector('#chat-modal .chat-panel-side');
            const r = pnl.getBoundingClientRect();
            const sideVisible = side ? getComputedStyle(side).display !== 'none' : false;
            return { min: ov.classList.contains('desk-min'), w: r.width,
                     cx: r.left + r.width / 2, sideVisible };
        }""")
        assert not chat["min"] and chat["w"] > 300, f"精简聊天应默认待命(非最小化): {chat}"
        assert not chat["sideVisible"], f"compact 态应藏会话列表(单窗口聊天形态): {chat}"
        assert abs(chat["cx"] - w / 2) < w * 0.18, f"精简聊天应大致居中: cx={chat['cx']} w={w}"

        # ③ 顶部大时间在(桌面锚点),H:MM 结构
        assert page.is_visible("#desk-clock"), "顶部大时间应在(桌面锚点)"
        clock_txt = page.inner_text("#desk-clock")
        assert ":" in clock_txt, f"大时间应是 H:MM: {clock_txt!r}"

        # ④ 待处理任务项轻量列出(极简条目,非完整卡)。
        # 聊天窗开着 = 你在聚焦对话 → 待办速览**让位**(chat z220 曾盖住它下半,Hardy 实拍"覆盖");
        # 聊天最小化进 dock → 桌面转待命面板,待办速览才回来。两态都验。
        assert not page.is_visible("#desk-pending"), "聊天窗开着时,待办速览应让位(不被压半截)"
        page.click("#chat-modal-close")   # 桌面下 ✕ = 最小化进 dock
        page.wait_for_timeout(200)
        assert page.is_visible("#desk-pending"), "聊天最小化后,待处理任务轻量条应回来"
        rows = page.evaluate("document.querySelectorAll('#desk-pending .desk-pending-row').length")
        assert rows >= 1, "有待拍板卡时,待办轻量条应至少列一条(极简条目)"
        page.click("#chat-open")          # 恢复聊天窗,后续断言在默认态上继续
        page.wait_for_timeout(200)

        # 看板 dock 📋 图标 + 角标(有没有新料);dock/🌗/吉祥物在位
        assert page.evaluate("document.getElementById('desk-board-btn') !== null"), "dock 应有 📋 看板图标"
        assert page.evaluate("document.querySelector('#desk-board-btn .dock-badge') !== null"), \
            "有待拍板卡 → 看板图标应有角标(新数据提示)"
        assert page.evaluate("document.querySelectorAll('#desk-dock .dock-item').length") >= 13
        assert page.is_visible("#desk-dock")
        assert page.is_visible("#desk-karvy-pixel")
        assert page.evaluate("document.getElementById('desk-wall-btn') !== null"), "dock 应有 🌗 壁纸换挡"

        # ⑤ 视觉断言(Hardy 2026-07-05 修 5 bug,不只验存在还验**不遮挡/不重叠**):
        #   bug1 = 大时间锚点必须有清晰垂直空间(chat 在其下不盖时钟底,memento 在角不压时钟)。
        page.wait_for_selector("#desk-memento", state="visible", timeout=3000)   # 纪念物真渲染(才验得了不遮挡)
        rects = page.evaluate("""() => {
            const R = s => { const e=document.querySelector(s); return e?e.getBoundingClientRect().toJSON():null; };
            return {clock:R('#desk-clock'), memento:R('#desk-memento'), chat:R('#chat-modal .chat-panel'),
                    dock:R('#desk-dock')};
        }""")
        def _overlap(a, b):
            if not a or not b:
                return False
            return not (a["right"] <= b["left"] or b["right"] <= a["left"]
                        or a["bottom"] <= b["top"] or b["bottom"] <= a["top"])
        clock = rects["clock"]
        assert clock, "大时间应在(桌面锚点)"
        assert not _overlap(rects["memento"], clock), \
            f"bug1: 本周纪念物不许压在大时间上(时钟顶被夹): memento={rects['memento']} clock={clock}"
        chat = rects["chat"]
        assert chat["top"] >= clock["bottom"], \
            f"bug1: 精简聊天窗顶必须落在时钟底之下(不夹时钟底部): chat.top={chat['top']} clock.bottom={clock['bottom']}"
        #   看板 4→1(Hardy 2026-07-06,替代旧 bug5"折叠卡对齐"):折起 = 一张缩略卡、4 象限全藏
        #   (不存在"对齐 4 张绝对定位卡"就不存在重叠);点开 = 一整块 2×2 网格,格子间天然无重叠。
        assert page.is_visible("#desk-board-fold"), "折起态应有看板缩略卡(点开=一整块看板)"
        vis_cols = page.evaluate(
            "[...document.querySelectorAll('.cockpit-col')].filter(c => c.offsetParent !== null).length")
        assert vis_cols == 0, f"折起态 4 象限应全藏(看板是一个整体): 可见 {vis_cols}"

        # 截图①:默认空旷态(给人工看主次分明/不拥挤 + 时钟完整)
        page.screenshot(path=os.path.join(_SHOTS_DIR, "01-default-empty.png"))

        # 点缩略卡摊开看板 → 一整块 2×2:4 象限全可见、两两无重叠、底清 dock;点遮罩空白关回
        page.click("#desk-board-fold")
        page.wait_for_function("document.body.classList.contains('desk-board-open')", timeout=3000)
        board = page.evaluate("""() => {
            const cols = [...document.querySelectorAll('.cockpit-col')].filter(c => c.offsetParent !== null);
            const rects = cols.map(c => c.getBoundingClientRect().toJSON());
            let overlaps = 0;
            for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
                const a = rects[i], b = rects[j];
                if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) overlaps++;
            }
            const grid = document.querySelector('.cockpit-grid').getBoundingClientRect();
            const dock = document.getElementById('desk-dock').getBoundingClientRect();
            return {n: cols.length, overlaps, gridBottom: grid.bottom, dockTop: dock.top};
        }""")
        assert board["n"] == 4, f"摊开 = 一整块看板,4 象限全可见: {board}"
        assert board["overlaps"] == 0, f"整块看板格子间不许重叠: {board}"
        assert board["gridBottom"] <= board["dockTop"], f"看板底不许钻 dock: {board}"
        # 干净 2×2 + 无切底(Hardy 实拍:col-decide 的 grid-column:1/-1 + rail 的 minmax(600px..)
        # 泄漏进桌面看板 → 破成 1+2+1 三行、中间大空、你可能想做被切在浮层外滚不到)。
        shape = page.evaluate("""() => {
            const cols = [...document.querySelectorAll('.cockpit-col')].filter(c => c.offsetParent !== null);
            const rects = cols.map(c => c.getBoundingClientRect());
            const rows = new Set(rects.map(r => Math.round(r.top)));   // 不同 top = 不同行
            const grid = document.querySelector('.cockpit-grid').getBoundingClientRect();
            const withinAll = rects.every(r => r.bottom <= grid.bottom + 1);   // 每格底都在看板内(不被切)
            const decideCol = getComputedStyle(document.querySelector('.col-decide')).gridColumn;
            return {rows: rows.size, withinAll, decideCol};
        }""")
        assert shape["rows"] == 2, f"桌面看板必须是干净 2×2(两行),实际 {shape['rows']} 行: {shape}"
        assert shape["withinAll"], f"四象限须全落在看板内(你可能想做不许被切): {shape}"
        assert shape["decideCol"] not in ("1 / -1", "1 / 3"), \
            f"col-decide 在桌面看板不许跨列(会破 2×2): {shape['decideCol']}"
        page.screenshot(path=os.path.join(_SHOTS_DIR, "01b-board-open.png"))
        page.mouse.click(60, 450)   # 点遮罩空白 = 关(看板外)
        page.wait_for_function("!document.body.classList.contains('desk-board-open')", timeout=3000)

        browser.close()
    assert errors == [], f"真浏览器 console 必须 0 error,实际: {errors}"


def test_desk_chat_three_states_and_screenshots(console_url):
    """⤢ 放大三态:compact(精简)→ expanded(完整,带会话列表)→ full(网页内全屏,占满视口)。
    每态截一张图给人工看。三态循环,形态持久化。"""
    os.makedirs(_SHOTS_DIR, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        _wire(page, errors, desk_boot=True)
        page.goto(console_url, wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('desk-view')", timeout=10000)
        page.wait_for_selector("#desk-chat-expand", state="visible", timeout=5000)

        # compact(默认):无会话列表
        assert page.evaluate("window.KarvyDesktop._layout.chatMode()") == "compact"
        assert not page.evaluate("() => { const s = document.querySelector('#chat-modal .chat-panel-side'); return s && getComputedStyle(s).display !== 'none'; }"), \
            "compact 态应藏会话列表"

        # ⤢ 第一下 → expanded:完整,会话列表回来、窗更大
        page.click("#desk-chat-expand")
        page.wait_for_function("document.body.classList.contains('desk-chat-expanded')", timeout=3000)
        assert page.evaluate("() => { const s = document.querySelector('#chat-modal .chat-panel-side'); return s && getComputedStyle(s).display !== 'none'; }"), \
            "expanded 态会话列表应可见(完整聊天)"
        page.screenshot(path=os.path.join(_SHOTS_DIR, "02-chat-expanded.png"))

        # ⤢ 第二下 → full:网页内全屏(几乎占满视口宽)
        page.click("#desk-chat-expand")
        page.wait_for_function("document.body.classList.contains('desk-chat-full')", timeout=3000)
        full = page.evaluate("""() => {
            const r = document.querySelector('#chat-modal .chat-panel').getBoundingClientRect();
            return { w: r.width, vw: window.innerWidth };
        }""")
        assert full["w"] > full["vw"] * 0.9, f"full 态应占满视口宽(网页内全屏): {full}"
        page.screenshot(path=os.path.join(_SHOTS_DIR, "03-chat-full.png"))

        # ⤢ 第三下 → 回 compact(三态循环)+ 持久化
        page.click("#desk-chat-expand")
        page.wait_for_function("window.KarvyDesktop._layout.chatMode() === 'compact'", timeout=3000)
        assert not page.evaluate("document.body.classList.contains('desk-chat-full')")
        browser.close()
    assert errors == [], f"真浏览器 console 必须 0 error,实际: {errors}"


def test_desk_global_karvy_zindex_above_panels(console_url):
    """全局小卡(右下卡皮巴拉)永远置顶、绝不被面板遮挡(Hardy 2026-07-05 修 bug:现在能被面板盖住)。
    造一个开着的 mgmt 面板窗,断言吉祥物的堆叠 z-index 仍高于面板(用 elementsFromPoint 命中最上层)。"""
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        _wire(page, errors, desk_boot=True)
        page.goto(console_url, wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('desk-view')", timeout=10000)
        page.wait_for_selector("#desk-karvy-pixel", state="visible", timeout=5000)

        # 开一个 mgmt 面板窗(点 dock 的域入口),并把它拖/摆到覆盖右下吉祥物地盘
        panel_z = page.evaluate("""() => {
            const nav = document.querySelector('#desk-dock .dock-item[data-panel]');
            if (nav) nav.click();
            const ov = document.getElementById('mgmt-modal');
            const m = document.querySelector('#mgmt-modal .modal');
            if (!ov || !m) return null;
            ov.classList.remove('hidden');
            // 强行把面板摆到右下角(覆盖吉祥物地盘),并抬到很高的 z
            m.style.transform = 'translate3d(1100px, 500px, 0)';
            ov.style.zIndex = '9000';
            return parseInt(getComputedStyle(ov).zIndex || '0', 10);
        }""")
        assert panel_z is not None, "mgmt 面板应可开(dock 域入口)"

        # 吉祥物的实际堆叠 z(.karvy-dock)应高于面板 —— elementsFromPoint 命中吉祥物区最上层不是面板
        fab_z = page.evaluate(
            "parseInt(getComputedStyle(document.querySelector('.karvy-dock')).zIndex || '0', 10)")
        assert fab_z > panel_z, f"全局小卡 z({fab_z}) 应高于面板 z({panel_z})(绝不被遮挡)"

        # 命中测试:吉祥物中心点最上层元素应属于 .karvy-dock(不是被面板盖住)
        on_top = page.evaluate("""() => {
            const fab = document.getElementById('chat-open');
            const r = fab.getBoundingClientRect();
            const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return !!(el && el.closest('.karvy-dock'));
        }""")
        assert on_top, "右下吉祥物中心点最上层应是它自己(没被面板遮挡)"
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


# ============================================================================
# 5-bug 修复回归(Hardy 2026-07-05 实拍):弹窗不被 dock 遮挡 + 窗口标题栏可拖
# ============================================================================

def test_desk_popup_not_hidden_under_dock(console_url):
    """bug3:弹窗/窗口(小林 demo 面板等)开着时底部不许钻进 dock 底下、内容被盖。
    验:demo 面板矩形底 ≤ dock 顶(留出 dock 空间);同时精简聊天窗底也在 dock 之上。截图存档。"""
    os.makedirs(_SHOTS_DIR, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # 用略矮的视口更容易暴露"钻进 dock"(1600×720)
        page = browser.new_page(viewport={"width": 1600, "height": 720})
        _wire(page, errors, desk_boot=True)
        page.goto(console_url, wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('desk-view')", timeout=10000)
        page.wait_for_selector("#desk-dock", state="visible", timeout=5000)

        dock_top = page.evaluate("document.getElementById('desk-dock').getBoundingClientRect().top")
        # 精简聊天窗底 ≤ dock 顶
        chat_bottom = page.evaluate("document.querySelector('#chat-modal .chat-panel').getBoundingClientRect().bottom")
        assert chat_bottom <= dock_top + 1, \
            f"bug3: 精简聊天窗底({chat_bottom})不许钻进 dock(顶 {dock_top})"

        # 打开小林 demo 面板(👀 入口)→ 面板底 ≤ dock 顶
        page.click("#demo-open")
        page.wait_for_selector("#mgmt-modal .modal", state="visible", timeout=5000)
        page.wait_for_timeout(300)
        demo_bottom = page.evaluate("document.querySelector('#mgmt-modal .modal').getBoundingClientRect().bottom")
        assert demo_bottom <= dock_top + 1, \
            f"bug3: 小林 demo 面板底({demo_bottom})必须留在 dock(顶 {dock_top})之上,不被遮挡"
        page.screenshot(path=os.path.join(_SHOTS_DIR, "03-demo-clears-dock.png"))
        browser.close()
    assert errors == [], f"真浏览器 console 必须 0 error,实际: {errors}"


def test_desk_window_drag_by_title_bar(console_url):
    """bug4:窗口应能抓标题栏拖动改位置(聊天窗 / demo 面板)。
    验:标题栏 mousedown → move → up 后窗口 left/top 变了 + 落盘(karvyloop_desk.v1)。"""
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        _wire(page, errors, desk_boot=True)
        page.goto(console_url, wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('desk-view')", timeout=10000)
        page.wait_for_selector("#chat-modal .chat-panel-head", state="visible", timeout=5000)

        def _rect(sel):
            return page.evaluate(f"() => {{ const r=document.querySelector('{sel}').getBoundingClientRect(); return {{left:r.left, top:r.top}}; }}")

        def _drag(handle_sel, panel_sel, dx, dy):
            before = _rect(panel_sel)
            box = page.query_selector(handle_sel).bounding_box()
            # 抓标题栏**中段空白拖柄**(避开左侧头像/标题与右侧 ✕/─/⤢ 按钮 —— 压按钮不该拖=正确行为)。
            # 命中测试确保落点不是按钮/链接/头像图(否则 4px 死区外仍不启动,是产品意图)。
            gx = box["x"] + box["width"] * 0.42
            gy = box["y"] + box["height"] / 2
            hit = page.evaluate("([x,y]) => { const e=document.elementFromPoint(x,y); return e && e.closest && e.closest('button,a,input,select,textarea,img,[contenteditable]') ? 'blocked' : 'ok'; }", [gx, gy])
            assert hit == "ok", f"标题栏中段应是干净拖柄(不是按钮/图),实际命中被挡:{handle_sel}"
            page.mouse.move(gx, gy)
            page.mouse.down()
            page.mouse.move(gx + dx, gy + dy, steps=12)
            page.mouse.up()
            page.wait_for_timeout(150)
            return before, _rect(panel_sel)

        # 聊天窗:抓标题栏拖 → left/top 变
        cb, ca = _drag("#chat-modal .chat-panel-head", "#chat-modal .chat-panel", -180, 90)
        assert abs(ca["left"] - cb["left"]) > 30 or abs(ca["top"] - cb["top"]) > 30, \
            f"bug4: 抓聊天窗标题栏应能拖动: before={cb} after={ca}"
        # 落盘:karvyloop_desk.v1 里 chat 位置更新了
        saved = page.evaluate("() => { try { return JSON.parse(localStorage.getItem('karvyloop_desk.v1')); } catch(e) { return null; } }")
        assert saved and saved.get("windows", {}).get("chat"), "bug4: 拖过的聊天窗位置应落盘(karvyloop_desk.v1)"

        # demo/mgmt 面板:开一个,抓标题栏拖 → 也能动
        page.click("#demo-open")
        page.wait_for_selector("#mgmt-modal .modal-head", state="visible", timeout=5000)
        page.wait_for_timeout(300)
        mb, ma = _drag("#mgmt-modal .modal-head", "#mgmt-modal .modal", 140, 120)
        assert abs(ma["left"] - mb["left"]) > 30 or abs(ma["top"] - mb["top"]) > 30, \
            f"bug4: 抓 demo/mgmt 面板标题栏应能拖动: before={mb} after={ma}"
        browser.close()
    assert errors == [], f"真浏览器 console 必须 0 error,实际: {errors}"
