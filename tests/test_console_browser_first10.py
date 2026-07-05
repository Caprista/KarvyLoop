"""test_console_browser_first10 — 真浏览器验收:「10 分钟惊艳」两件套。

① 人格采集器:fresh 用户旅程条里出 4 问 → 答完种子**真落盘 beliefs.json**(文件断言)→
   回执 + 旅程继续(chip1);全跳过 = 零种子;老用户(旅程 done)不弹。
② 文件管家第一课:引荐卡 ACCEPT(真入住)→ 第一任务 chip → 扫真实(tmp)桌面/下载 →
   方案预览卡 → ACCEPT 真执行 → 文件真挪了 + 白名单外金丝雀一字不动 + 台账在。

全链走**真生产路径**(真 uvicorn + 真 WS + 真 handler),不 mock 决策链;
没装 chromium → 整模块干净跳过(诚实降级,绝不假装验过)。
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import threading
import time
import types
from pathlib import Path

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


def _serve(app):
    import uvicorn
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if getattr(server, "started", False):
            break
        time.sleep(0.1)
    return server, thread, f"http://127.0.0.1:{port}"


def _console_app(tmp_path, *, llm_ready: bool):
    """真 console + 对话编排器;llm_ready=True 用 stub main_loop/gateway 让旅程条亮起
    (采集器只在配好 key 后出现;演示任务不跑,不碰真模型)。"""
    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.conversation import ConversationManager, ConversationStore
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()
    app.state.conversation_manager = mgr
    app.state.no_llm = True   # 不弹"强制配模型"锁(它会盖住旅程条)
    app.state.memory = MemoryManager(store=BeliefStore(tmp_path / "beliefs.json"))
    if llm_ready:
        app.state.main_loop = types.SimpleNamespace(trace=None)
        app.state.runtime_kwargs = {"gateway": object()}
    return app


@pytest.fixture
def onboarding_env(tmp_path):
    """采集器/旅程状态隔离到 tmp(绝不碰真 ~/.karvyloop)。"""
    old = os.environ.get("KARVYLOOP_ONBOARDING_PATH")
    os.environ["KARVYLOOP_ONBOARDING_PATH"] = str(tmp_path / "onboarding.json")
    yield tmp_path / "onboarding.json"
    if old is None:
        os.environ.pop("KARVYLOOP_ONBOARDING_PATH", None)
    else:
        os.environ["KARVYLOOP_ONBOARDING_PATH"] = old


def _open(pw, url):
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    page.on("dialog", lambda d: d.accept())
    page.add_init_script(
        "try { localStorage.setItem('karvyloop_tour_done', '1');"
        " localStorage.setItem('karvyloop_view', 'chat'); } catch (e) {}")
    page.goto(url, wait_until="commit", timeout=10000)
    return browser, page


# ---- ① 人格采集器 ----

def test_intake_appears_answers_seed_beliefs_on_disk(tmp_path, onboarding_env):
    from playwright.sync_api import sync_playwright

    app = _console_app(tmp_path, llm_ready=True)
    server, thread, url = _serve(app)
    try:
        with sync_playwright() as pw:
            browser, page = _open(pw, url)
            page.wait_for_selector("#journey-bar .intake-q", timeout=10000)
            page.keyboard.press("Escape")   # 撤聚光蒙版,别挡点击
            # 4 题各点第一个选项(第一个选项分别是:结论先行/先问我/直给犀利/按类型)
            for _ in range(4):
                page.wait_for_selector("#journey-bar .intake-opt", timeout=5000)
                page.locator("#journey-bar .intake-opt").first.click()
                page.wait_for_timeout(150)
            # 回执出现(预对齐文案)+ 旅程继续:chip1 亮出(采集器收起)
            page.wait_for_function(
                "!document.querySelector('#journey-bar .intake-q')", timeout=8000)
            page.wait_for_selector("#journey-bar .journey-chip", timeout=8000)
            chat_text = page.inner_text("#chat-log")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    # 种子真落盘(文件断言,不信 UI 自述)
    beliefs = tmp_path / "beliefs.json"
    assert beliefs.exists(), "答完 4 题 beliefs.json 没落盘"
    raw = beliefs.read_text(encoding="utf-8")
    data = json.loads(raw)
    items = data if isinstance(data, list) else data.get("items") or data.get("beliefs") or []
    assert '"decision_pref"' in raw and '"intake_q"' in raw, "种子不是决策偏好机制的条目"
    for qid in ("output_style", "unsure", "tone", "filing"):
        assert f'"{qid}"' in raw, f"{qid} 的种子没落盘"
    assert len(items) >= 4 if isinstance(items, list) else True
    # 采集器状态落盘 done(答过的不再弹)
    st = json.loads(onboarding_env.read_text(encoding="utf-8"))
    assert st["intake"]["done"] is True
    # 文案纪律:回执绝不说"我懂你了"
    assert "懂你" not in chat_text and "understand you" not in chat_text.lower()


def test_intake_skip_all_plants_nothing(tmp_path, onboarding_env):
    from playwright.sync_api import sync_playwright

    app = _console_app(tmp_path, llm_ready=True)
    server, thread, url = _serve(app)
    try:
        with sync_playwright() as pw:
            browser, page = _open(pw, url)
            page.wait_for_selector("#journey-bar .intake-q", timeout=10000)
            page.keyboard.press("Escape")
            page.click("#journey-bar .intake-skip:has-text('Skip all')")
            page.wait_for_function(
                "!document.querySelector('#journey-bar .intake-q')", timeout=8000)
            page.wait_for_selector("#journey-bar .journey-chip", timeout=8000)   # 旅程不受惩罚
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    raw = (tmp_path / "beliefs.json").read_text(encoding="utf-8") \
        if (tmp_path / "beliefs.json").exists() else ""
    assert '"intake_q"' not in raw, "跳过必须零种子"
    st = json.loads(onboarding_env.read_text(encoding="utf-8"))
    assert st["intake"]["done"] is True


def test_old_user_never_sees_intake(tmp_path, onboarding_env):
    from playwright.sync_api import sync_playwright

    onboarding_env.write_text(json.dumps({"stage": "done", "ts": 1.0}), encoding="utf-8")
    app = _console_app(tmp_path, llm_ready=True)
    server, thread, url = _serve(app)
    try:
        with sync_playwright() as pw:
            browser, page = _open(pw, url)
            page.wait_for_selector("#chat-log", timeout=10000)
            page.wait_for_timeout(1500)   # 给 _initJourney 足够时间(它不该亮任何东西)
            assert page.evaluate(
                "document.getElementById('journey-bar').classList.contains('hidden')"), \
                "老用户旅程条必须保持隐藏"
            assert page.query_selector("#journey-bar .intake-q") is None, "老用户绝不弹采集器"
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_intake_rewatch_same_page_re_presents_questions(tmp_path, onboarding_env):
    """回归锁(对抗验收 BREAK #9):**同页**内完成采集器→点 🎬 重看,4 问必须重新出现,
    绝不静默跳问、拿旧答案重播种。整页刷新会重置模块变量掩盖此 bug,所以必须同页不 reload。"""
    from playwright.sync_api import sync_playwright

    app = _console_app(tmp_path, llm_ready=True)
    server, thread, url = _serve(app)
    try:
        with sync_playwright() as pw:
            browser, page = _open(pw, url)
            # 第一遍:答完 4 题(采集器收起)
            page.wait_for_selector("#journey-bar .intake-q", timeout=10000)
            page.keyboard.press("Escape")
            for _ in range(4):
                page.wait_for_selector("#journey-bar .intake-opt", timeout=5000)
                page.locator("#journey-bar .intake-opt").first.click()
                page.wait_for_timeout(150)
            page.wait_for_function(
                "!document.querySelector('#journey-bar .intake-q')", timeout=8000)
            # 同页点 🎬 重看(不 reload;JS 直派点击绕过 chip 聚光蒙版,测的是重看逻辑非像素)
            page.evaluate("document.getElementById('journey-replay').click()")
            # 修复前:_intakeIdx 停在 4 → _renderIntake 短路 → 直接重播种,4 问不再出现。
            # 修复后:idx 归 0 + 答案清空 → 第 1 题重新露面、进度回到 1/N。
            page.wait_for_selector("#journey-bar .intake-q", timeout=8000)
            prog = page.inner_text("#journey-bar .intake-progress")
            assert prog.strip().startswith("1 "), f"重看没回到第 1 题(进度={prog!r})"
            assert page.query_selector("#journey-bar .intake-opt") is not None, \
                "重看必须重新展示选项,不能静默跳过"
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


# ---- ② 文件管家第一课(引荐 ACCEPT → chip → 方案卡 → ACCEPT 真执行) ----

def _mk_home(base: Path) -> Path:
    home = base / "home"
    (home / "Desktop").mkdir(parents=True)
    (home / "Downloads").mkdir(parents=True)
    (home / "Desktop" / "photo.png").write_text("img", encoding="utf-8")
    (home / "Downloads" / "report.pdf").write_text("pdf", encoding="utf-8")
    (home / "Downloads" / "setup.exe").write_text("bin", encoding="utf-8")
    outside = home / "Documents"          # 白名单外金丝雀(第一课不碰 Documents)
    outside.mkdir()
    (outside / "canary.txt").write_text("勿动 marker-CANARY", encoding="utf-8")
    return home


def test_butler_first_lesson_full_chain_in_browser(tmp_path, onboarding_env):
    from playwright.sync_api import sync_playwright

    from karvyloop.capability.fs_grants import FsGrantsStore
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry
    from karvyloop.roles.registry import RoleRegistry

    # 老旅程(免采集器/旅程条抢戏,聚焦第一课链)
    onboarding_env.write_text(json.dumps({"stage": "done", "ts": 1.0}), encoding="utf-8")
    home = _mk_home(tmp_path)
    app = _console_app(tmp_path, llm_ready=False)
    app.state.role_registry = RoleRegistry(tmp_path / "roles")   # 空角色库 → 引荐触发
    app.state.proposal_registry = PendingProposalRegistry()
    app.state.fs_grants = FsGrantsStore(tmp_path / "fs_grants.json")
    app.state.residents_state_path = tmp_path / "referral_state.json"
    app.state.residents_home = home
    app.state.butler_journal_path = tmp_path / "butler_moves.json"
    app.state.silence_grants_path = tmp_path / "silence_grants.json"
    app.state.proposal_handlers = build_proposal_handlers(app)

    server, thread, url = _serve(app)
    try:
        with sync_playwright() as pw:
            browser, page = _open(pw, url)
            # 引荐卡(开机拉取即出)→ ACCEPT = 真入住 + fs 白名单落台账
            page.wait_for_selector('#h2a-list [data-proposal-id^="resident_referral"]',
                                   timeout=10000)
            page.click('#h2a-list [data-proposal-id^="resident_referral"] .h2a-accept')
            # 入住回执 → 顺势递上第一课 chip
            page.wait_for_selector("#butler-lesson-offer", timeout=10000)
            page.keyboard.press("Escape")   # 撤聚光蒙版
            page.click("#butler-lesson-offer .journey-chip")
            # 方案预览卡(WS 广播)到 → 专属渲染在 + 还什么都没动
            page.wait_for_selector('#h2a-list [data-proposal-id^="butler_plan"]',
                                   timeout=10000)
            assert page.query_selector("#h2a-list .butler-plan") is not None, \
                "方案卡没走专属渲染(moves 预览不可见)"
            assert (home / "Downloads" / "report.pdf").exists(), "拍板前就动文件 = H2A 破"
            page.keyboard.press("Escape")
            page.click('#h2a-list [data-proposal-id^="butler_plan"] .h2a-accept')
            # 执行回执(dispatch 回显)
            page.wait_for_function(
                "document.getElementById('chat-log').innerText.includes('butler_plan')",
                timeout=10000)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    # 磁盘真相(不信 UI 自述):文件真挪进桶里、金丝雀一字不动、台账在
    assert (home / "Downloads" / "Documents" / "report.pdf").exists(), "report.pdf 没归位"
    assert (home / "Downloads" / "Installers" / "setup.exe").exists(), "setup.exe 没归位"
    assert (home / "Desktop" / "Images" / "photo.png").exists(), "photo.png 没归位"
    assert (home / "Documents" / "canary.txt").read_text(encoding="utf-8") == "勿动 marker-CANARY", \
        "白名单外金丝雀被动了(边界破)"
    journal = json.loads((tmp_path / "butler_moves.json").read_text(encoding="utf-8"))
    assert journal[0]["origin"] == "butler_first_lesson" and len(journal[0]["moved"]) == 3
