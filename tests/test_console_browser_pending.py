"""test_console_browser_pending — 前端二测:挂起轮"执行中…"占位在真浏览器里**看得见**。

bug(执行中对话归属竞态)的前端刀:切到/刷新恢复到一条有挂起轮的对话时,回复位必须显
"执行中…"占位(不空白、不消失)。按纪律([[verify-real-deployed-ui-state-not-curated-screenshots]]):
UI 断言先验 rect 宽高 > 0(锁"看得见的反馈",防叼卡走 display:none)。

诚实降级:没装 chromium → 整模块干净跳过(绝不假装验过)。
"""
from __future__ import annotations

import importlib.util
import json
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


# 预置挂起轮所在的业务域场(非默认 → 刷新经 localStorage 恢复回来)
BIZ_PEER = {"domain_id": "dom-测试", "role": "agent", "agent_id": "设计师", "is_group": False}


@pytest.fixture
def console_with_pending(tmp_path):
    """真 console + 一条业务域对话里挂着**挂起轮**(begin 未 finalize)。yield base_url。"""
    import uvicorn

    from karvyloop.cognition.conversation import (
        ConversationManager, ConversationStore, karvy_world_peer)
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.domain.registry import Address

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()
    # 业务域场里落一条挂起轮(drive 还在跑那一刻的样子)
    biz = Address(domain_id=BIZ_PEER["domain_id"], role=BIZ_PEER["role"],
                  agent_id=BIZ_PEER["agent_id"])
    mgr.set_peer(biz)
    mgr.begin_turn("正在执行的活儿", conv=mgr.current())
    mgr.set_peer(karvy_world_peer())   # 当前落回默认线,让占位靠"恢复非默认场"路径现身
    app.state.conversation_manager = mgr

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


def test_pending_placeholder_visible_after_refresh_restore(console_with_pending):
    """刷新恢复到挂起对话:占位真渲染 + rect 宽高 > 0(看得见),user 消息在,0 JS 报错。"""
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        # 模拟"刷新前在这条业务域场":localStorage 存下非默认 active peer → 开机 _restoreActivePeerOrHistory
        # 会 switchPeer 回来,_renderConversationTurns 据 pending 渲占位。
        page.add_init_script(
            "try { localStorage.setItem('karvyloop_active_peer', %s);"
            " localStorage.setItem('karvyloop_tour_done','1'); } catch(e){}"
            % json.dumps(json.dumps(BIZ_PEER)))
        page.goto(console_with_pending, wait_until="commit", timeout=10000)
        # 占位应出现在聊天日志里
        page.wait_for_selector(".chat-line.pending-turn", timeout=10000)
        # ① 看得见:rect 宽高 > 0(不是挂了 class 却 display:none 的隐形元素)
        box = page.eval_on_selector(
            ".chat-line.pending-turn",
            "el => { const r = el.getBoundingClientRect(); return {w: r.width, h: r.height}; }")
        assert box["w"] > 0 and box["h"] > 0, f"占位挂了 class 却不可见: {box}"
        # ② 占位文案 = "执行中/正在忙"(i18n chat.executing);不是空白
        text = page.inner_text(".chat-line.pending-turn")
        assert text.strip(), "占位不能是空白"
        # ③ user 消息(挂起轮的 user_intent)也在日志里(没丢)
        log_text = page.inner_text("#chat-log")
        assert "正在执行的活儿" in log_text, "挂起轮的 user 消息必须在正确对话里可见"
        browser.close()

    assert not errors, "挂起占位渲染必须 0 JS 报错:\n" + "\n".join(errors)
