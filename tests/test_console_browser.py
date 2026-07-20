"""test_console_browser — 前端二测:真浏览器加载 console,抓 JS 报错 + 验真渲染.

Hardy 的"纯前端渲染 bug 要浏览器二层"那一刀:单元/集成测后端,**碰不到浏览器运行时**——
app.js 抛未捕获异常、SPA 白屏、关键控件没渲染,这些后端测全看不见(料→去聊天那类就是)。
这里用 Playwright(已是依赖)真起 console 服务 + 真 chromium 加载,抓 console.error / pageerror,
并断言关键控件真在 DOM 里(不是崩成白屏)。

诚实降级:没装 chromium(`playwright install chromium`)→ **整模块干净跳过**,绝不假装验过。
"""
from __future__ import annotations

import importlib.util
import socket
import threading
import time

import pytest


def _pw_ready() -> bool:
    if importlib.util.find_spec("playwright") is None:
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pw_ready(), reason="playwright/chromium 未装(`pip install playwright && playwright install chromium`)→ 跳过浏览器二测")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def console_url(tmp_path):
    """真起一份 console(uvicorn 后台线程)+ 接对话编排器,让 SPA 有东西渲染。yield base_url。"""
    import uvicorn

    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()
    app.state.conversation_manager = mgr

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):                 # 等服务起来(最多 ~10s)
        if getattr(server, "started", False):
            break
        time.sleep(0.1)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_console_loads_in_real_browser(console_url):
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    selectors_present = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto(console_url, wait_until="commit", timeout=10000)
        page.wait_for_timeout(2500)      # 让 vendor + app.js + i18n 跑起来、异常浮出来
        for sel in ("#app", "#chat-log", "#chat-input", "#chat-send"):
            selectors_present[sel] = page.query_selector(sel) is not None
        ready_state = page.evaluate("document.readyState")
        browser.close()

    # ① 没有未捕获 JS 异常 / console.error(SPA 真起来了,不是哑的)
    assert not errors, f"console 在真浏览器里加载有 JS 报错:\n" + "\n".join(errors)
    # ② 关键控件真渲染(不是白屏 / 崩)
    missing = [s for s, ok in selectors_present.items() if not ok]
    assert not missing, f"这些关键控件没渲染出来(SPA 可能崩了):{missing}"
    assert ready_state != "loading", "settle 后页面仍 loading —— 主线程可能被同步初始化卡死"


@pytest.fixture
def console_no_llm(tmp_path):
    """真 console(no_llm 只读:不弹"强制配模型"锁死引导,好点按钮)。yield base_url。"""
    import uvicorn

    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()
    app.state.conversation_manager = mgr
    app.state.no_llm = True

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if getattr(server, "started", False):
            break
        time.sleep(0.1)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_view_ia_two_homes_and_rail_zoom(console_no_llm):
    """三视图收敛(docs/59 方案A)真浏览器验收:一主(对话)一副(桌面)两钮 + rail ⛶ 放大 +
    Esc 回家 + 存量"看板"开机偏好平滑迁移 + tour 在新 IA 下不断链(曾爆 6 BLOCKER 的雷区)。"""
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        # 老用户存量偏好 karvyloop_view=board:开机必须平滑迁移回对话(不落进已退位的视图);
        # tour_done 先标上,首启 tour 的全屏遮罩别拦点击(⑥ 再显式重看验 tour)。
        page.add_init_script(
            "try { localStorage.setItem('karvyloop_view', 'board');"
            " localStorage.setItem('karvyloop_tour_done', '1'); } catch (e) {}")
        page.goto(console_no_llm, wait_until="commit", timeout=10000)
        page.wait_for_selector("#view-switch", timeout=10000)

        # ① 顶栏只剩两个家:💬 对话 + 🖥 桌面;看板钮与遗留 #view-toggle 退场
        opts = page.eval_on_selector_all("#view-switch .view-switch-opt", "els => els.map(e => e.id)")
        assert opts == ["view-opt-chat", "view-opt-desk"], f"顶栏 switch 应恰为两钮,实际 {opts}"
        assert page.query_selector("#view-opt-board") is None, "看板钮应退场"
        assert page.query_selector("#view-toggle") is None, "遗留隐藏钮应删除"

        # ② 存量 board 偏好迁移:落在对话视图,localStorage 改写为 chat,对话钮 active
        page.wait_for_function("localStorage.getItem('karvyloop_view') === 'chat'", timeout=5000)
        assert page.evaluate("!document.body.classList.contains('board-view')"), "开机不许落进已退位的看板"
        assert page.evaluate("document.getElementById('view-opt-chat').classList.contains('active')")

        # ③ rail ⛶ 放大:2×2 全屏(body.board-view),钮变 ✕;临时态**不写**开机偏好;聊天收进弹层
        page.click("#rail-zoom-btn")
        page.wait_for_function("document.body.classList.contains('board-view')", timeout=3000)
        assert page.inner_text("#rail-zoom-btn").strip() == "✕", "放大态 ⛶ 应变 ✕(回对话)"
        assert page.evaluate("localStorage.getItem('karvyloop_view')") == "chat", "放大是临时态,不许写开机偏好"
        assert page.evaluate("document.getElementById('chat-modal').classList.contains('hidden')"), \
            "放大态聊天应收进弹层(FAB 再弹,原看板行为原样复用)"

        # ④ Esc 回家:回对话视图,聊天回中央常驻,钮复位 ⛶
        page.keyboard.press("Escape")
        page.wait_for_function("!document.body.classList.contains('board-view')", timeout=3000)
        assert page.evaluate("!document.getElementById('chat-modal').classList.contains('hidden')"), \
            "回家后聊天应回中央常驻"
        assert page.inner_text("#rail-zoom-btn").strip() == "⛶"

        # ⑤ 两个家互切:🖥 进桌面(放大态不许跨视图残留),💬 永远回得来
        page.click("#rail-zoom-btn")
        page.wait_for_function("document.body.classList.contains('board-view')", timeout=3000)
        page.click("#view-opt-desk")
        page.wait_for_function("document.body.classList.contains('desk-view')", timeout=5000)
        assert page.evaluate("!document.body.classList.contains('board-view')"), "切桌面必须先收掉放大态"
        page.click("#view-opt-chat")
        page.wait_for_function("!document.body.classList.contains('desk-view')", timeout=5000)
        assert page.evaluate("localStorage.getItem('karvyloop_view')") == "chat"

        # ⑥ tour 在两钮 IA 下不断链:💡 重看 → driver.js popover 真弹出(不许 0×0 钉左上角)
        page.click("#tour-replay")
        page.wait_for_selector(".driver-popover", timeout=8000)

        browser.close()

    assert not errors, "两钮 IA 下真浏览器必须 0 JS 报错:\n" + "\n".join(errors)


@pytest.fixture
def console_with_feed(tmp_path):
    """真 console + 一条"料"(done 任务)挂到一条有 TRACE 标记轮次的对话 → 验"去聊天"定位。"""
    import uvicorn

    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.console import build_console_app
    from karvyloop.console.tasks import TaskRegistry
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()
    mgr.record_turn("第一句无关的", "第一应", brain="slow")              # 噪音轮(定位别选错)
    mgr.record_turn("分析世界杯", "分析结果在此", brain="slow", task_id="TRACE")  # 目标轮
    conv_id = mgr.current().id
    app.state.conversation_manager = mgr

    treg = TaskRegistry()
    tid = treg.start(who="小卡", domain_id="l0", role="", intent="分析世界杯")
    treg.set_conversation(tid, conv_id, trace_id="TRACE")              # 料→该对话 + 定位键=TRACE
    treg.finish(tid, result="分析结果在此")
    app.state.task_registry = treg
    app.state.no_llm = True   # 只读模式:别弹"强制配模型"的锁死引导,好驱动 feed

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if getattr(server, "started", False):
            break
        time.sleep(0.1)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_feed_to_chat_locates_source_turn(console_with_feed):
    """料→去聊天:点料卡→点'去聊天'→聊天窗里**那一轮被高亮**(你撞过的静默失效就发生在这)。"""
    from playwright.sync_api import sync_playwright

    result = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(console_with_feed, wait_until="commit", timeout=10000)
        page.wait_for_selector(".task-card", timeout=8000)        # 料里那条任务出现
        page.click(".task-card", force=True)                      # → 任务详情模态(force:绕模态遮罩/2s轮询重渲染)
        page.wait_for_selector("#mgmt-body .mgmt-submit", timeout=5000)
        page.click("#mgmt-body .mgmt-submit", force=True)         # → 去聊天
        try:
            # 命门:目标轮(data-task-id=TRACE)被打上 flash 高亮(定位真生效)
            page.wait_for_selector('[data-task-id="TRACE"].turn-locate-flash', timeout=4000)
            result["located"] = True
        except Exception:
            result["located"] = False
            result["turn_rendered"] = page.query_selector('[data-task-id="TRACE"]') is not None
        browser.close()
    assert result.get("located"), f"去聊天没定位到来源那一轮(料→去聊天静默失效):{result}"


@pytest.fixture
def console_with_proposal(tmp_path):
    """真 console + 一条待决提案 + 你结晶过的相关标准 → 验决策卡把"你的标准 + 回执"渲染出来。"""
    import uvicorn

    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.console import build_console_app
    from karvyloop.crystallize.decision_pref import make_decision_pref_belief
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry, proposal_for_route

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()
    app.state.conversation_manager = mgr
    app.state.no_llm = True

    mem = MemoryManager(store=BeliefStore(tmp_path / "beliefs.json"))
    mem.write(make_decision_pref_belief(
        "动生产数据库前必须先有完整备份,未备份一律不批", "constraint",
        strength=0.8, status="confirmed", explicit=True,
        evidence=[{"ts": 1.0, "decision": "REJECT", "gist": "没备份不许动生产"}]))
    app.state.memory = mem

    pr = PendingProposalRegistry()
    pr.register(proposal_for_route(domain_id="d", role="运维", agent_id="运维",
                                   domain_name="运维组", requirement="在生产库上直接跑数据迁移", ts=1.0))
    app.state.proposal_registry = pr

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if getattr(server, "started", False):
            break
        time.sleep(0.1)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_decision_card_shows_your_standard_with_receipt(console_with_proposal):
    """楔子的脸(docs/90 刀1b 卡片折叠后):详情折起来了,但摘要行留一句**可见 chip**
    「🧭 已按你 N 条标准对齐」—— 楔子的脸不藏进折叠。点开 chip → 你的标准 + 📍来自你的拍板
    回执全露出(依据=想深究才点开的下一步)。chip 崩了或点开没回执 = 楔子对用户隐形。"""
    from playwright.sync_api import sync_playwright

    found = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(console_with_proposal, wait_until="commit", timeout=10000)
        try:
            # 决策卡"双面出"(右栏 #h2a-list + 聊天流内联),各一份折叠 —— 全程限定右栏这张,别串台。
            # ① 楔子的脸:可见 chip(不展开就能看见"系统在按你的标准对齐")
            page.wait_for_selector("#h2a-list .dcard-aligned-chip", timeout=8000)
            found["chip_text"] = page.query_selector("#h2a-list .dcard-aligned-chip").inner_text()
            # ② 点 chip 展开右栏这张的折叠 → 你的标准 + 回执露出(折叠里的支撑信息,想深究才点)
            page.click("#h2a-list .dcard-aligned-chip")
            page.wait_for_selector("#h2a-list .dcard-pref-receipt", timeout=8000)   # 回执渲染 + 展开后可见
            found["receipt_text"] = page.query_selector("#h2a-list .dcard-pref-receipt").inner_text()
            found["h2a_text"] = page.query_selector("#h2a-list").inner_text()
        except Exception:
            found.setdefault("chip_text", "")
            found.setdefault("receipt_text", "")
            found["h2a_text"] = page.query_selector("#h2a-list").inner_text() if page.query_selector("#h2a-list") else "(no #h2a-list)"
        browser.close()
    assert "🧭" in found.get("chip_text", ""), f"楔子的脸 chip 没渲染(折叠后它必须可见):{found}"
    assert "没备份" in found.get("receipt_text", ""), f"回执没渲染(展开后应露出):{found}"
    assert "备份" in found.get("h2a_text", ""), f"标准没摆上卡:{found}"


def test_tour_spotlight_mask_both_views(console_no_llm):
    """引导可见性(Hardy 2026-07-04:「引导气泡不认真看找不到」)回归锁:开 tour 必须有
    黑半透蒙版(≥0.6)罩住其余界面 + 目标镂空高亮(driver-active-element + 3px 光圈)+
    高对比 popover(karvy-tour-pop);Esc 退出蒙版即撤。对话/桌面两视图各验一遍,0 JS 报错。"""
    from playwright.sync_api import sync_playwright

    probe = """() => {
      const ov = document.querySelector('.driver-overlay');
      const path = ov ? ov.querySelector('path') : null;
      const act = document.querySelector('.driver-active-element');
      const pop = document.querySelector('.driver-popover');
      return {
        overlay: !!ov,
        opacity: path ? parseFloat(getComputedStyle(path).opacity) : 0,
        z: ov ? parseInt(getComputedStyle(ov).zIndex || '0', 10) : 0,
        ring: act ? getComputedStyle(act).outlineWidth + ' ' + getComputedStyle(act).outlineStyle : '',
        popClass: pop ? pop.className : '',
      };
    }"""
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.add_init_script(
            "try { localStorage.setItem('karvyloop_tour_done', '1');"
            " localStorage.setItem('karvyloop_view', 'chat'); } catch (e) {}")
        page.goto(console_no_llm, wait_until="commit", timeout=10000)
        page.wait_for_selector("#tour-replay", timeout=10000)

        for view_btn, view_name in ((None, "chat"), ("#view-opt-desk", "desk")):
            if view_btn:
                page.click(view_btn)
                page.wait_for_function("document.body.classList.contains('desk-view')", timeout=5000)
                page.wait_for_timeout(500)
            page.click("#tour-replay")
            page.wait_for_selector(".driver-popover", timeout=8000)
            page.wait_for_timeout(400)     # 蒙版入场动画提交
            m = page.evaluate(probe)
            assert m["overlay"], f"[{view_name}] 开 tour 没有蒙版 —— 引导又回到「不认真看找不到」"
            assert m["opacity"] >= 0.6, f"[{view_name}] 蒙版太浅(opacity={m['opacity']}),挡不住其它部分"
            assert m["z"] > 9600, f"[{view_name}] 蒙版层级 {m['z']} 会被桌面便签/壁纸部件(≤9600)盖掉"
            assert m["ring"] == "3px solid", f"[{view_name}] 目标没有镂空光圈(ring={m['ring']!r})"
            assert "karvy-tour-pop" in m["popClass"], f"[{view_name}] popover 没走高对比样式:{m['popClass']!r}"
            page.keyboard.press("Escape")
            page.wait_for_function("!document.querySelector('.driver-overlay')", timeout=3000)

        browser.close()
    assert not errors, "tour 蒙版高亮下必须 0 JS 报错:\n" + "\n".join(errors)
