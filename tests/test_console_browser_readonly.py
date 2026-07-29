"""test_console_browser_readonly — 前端二测:引擎缺席时聊天框在真浏览器里**真被置灰**。

bug(页面能开、发消息才报"MainLoop 未注入"):没接模型引擎(--no-llm/没 init/构造失败/缺 Key)
时,聊天框不该假装能用 —— 要当场置灰 + 显真原因横幅。按纪律
([[verify-real-deployed-ui-state-not-curated-screenshots]]):UI 断言先验 rect 宽高 > 0
(锁"看得见的反馈",防挂了 class 却 display:none)。

诚实降级:没装 chromium → 整模块干净跳过(绝不假装验过)。
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
    not _pw_ready(), reason="playwright/chromium 未装 → 跳过浏览器二测")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def console_no_llm(tmp_path):
    """真 console + 显式 --no-llm(main_loop=None)。yield base_url。"""
    import uvicorn

    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.no_llm = True   # 显式只读模式:引擎缺席,聊天框应被置灰

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


def test_readonly_composer_greyed_and_banner_visible(console_no_llm):
    """引擎缺席:横幅看得见(rect>0)+说真话(--no-llm,不叫人去 init)+输入框不可编辑+发送禁用。"""
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.add_init_script("try { localStorage.setItem('karvyloop_tour_done','1'); } catch(e){}")
        page.goto(console_no_llm, wait_until="commit", timeout=10000)

        # 横幅从 hidden 翻出来(_refreshEngineState 拉 setup_status 后)
        page.wait_for_selector("#composer-readonly:not(.hidden)", timeout=10000)
        # ① 看得见:rect 宽高 > 0(不是挂了 class 却 display:none 的隐形元素)
        box = page.eval_on_selector(
            "#composer-readonly",
            "el => { const r = el.getBoundingClientRect(); return {w: r.width, h: r.height}; }")
        assert box["w"] > 0 and box["h"] > 0, f"只读横幅挂了 class 却不可见: {box}"
        # ② 说真话:点名 --no-llm,且**不**叫人去 init(用户没错、是主动选的只读)
        text = page.inner_text("#composer-readonly")
        assert "--no-llm" in text, f"横幅要说清真原因(--no-llm): {text!r}"
        assert "karvyloop init" not in text, f"只读模式不该叫人去 init: {text!r}"
        # ③ 输入框不可编辑
        editable = page.get_attribute("#chat-input", "contenteditable")
        assert editable == "false", f"引擎缺席时聊天框应不可编辑,实为 {editable!r}"
        # ④ 发送按钮禁用
        assert page.is_disabled("#chat-send"), "引擎缺席时发送按钮应禁用"
        # ⑤ wrap 挂上 is-readonly(CSS 置灰真生效的开关)
        assert page.eval_on_selector(
            ".chat-input-wrap", "el => el.classList.contains('is-readonly')"), "wrap 应有 is-readonly"
        browser.close()

    assert not errors, "只读态渲染必须 0 JS 报错:\n" + "\n".join(errors)
