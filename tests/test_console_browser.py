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
