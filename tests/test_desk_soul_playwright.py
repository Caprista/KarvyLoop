"""desk 灵魂层 P1.5 的真浏览器验收(Playwright,docs/53)。

jsdom smoke 验逻辑,这里验**浏览器运行时**(canvas 真画了没 / CSS transition 真跑没 /
0 console error)——"看着写完了"≠"真能跑"(web_verify 同一教训)。

覆盖(全部走真实契约形状,没有假戏):
  1. desk 视图进场 → 右下像素小卡 canvas 渲染出来(getImageData 非空);
  2. h2a 卡到达(app.js 收 h2a_proposal 调的同一个钩子 KarvyDesktop.notifyH2A)
     → 叼卡小演员出现 → 到位撤掉 → ⚖ 便签闪;
  3. task_status done(契约形状喂灵魂接缝)→ 署名便签浮出(who + 结果摘要);
  4. 全程 0 console error / 0 pageerror。

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

        # 1) 像素小卡真渲染:canvas 存在、可见、且有非透明像素(不是空 canvas)
        page.wait_for_selector("#desk-karvy-pixel", state="visible", timeout=5000)
        painted = page.evaluate("""() => {
            const cv = document.getElementById('desk-karvy-pixel');
            const ctx = cv.getContext('2d');
            const d = ctx.getImageData(0, 0, cv.width, cv.height).data;
            let n = 0;
            for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
            return n;
        }""")
        assert painted > 80, f"像素小卡 canvas 应画了实像素,实际非透明像素 {painted}"

        # 2) h2a 到达(app.js 收 h2a_proposal 调的就是这个钩子)→ 叼卡动画元素出现
        page.evaluate("window.KarvyDesktop.notifyH2A()")
        page.wait_for_selector("#desk-carry-actor", state="attached", timeout=3000)
        carry_painted = page.evaluate("""() => {
            const cv = document.querySelector('#desk-carry-actor canvas');
            if (!cv) return -1;
            const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
            let n = 0;
            for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
            return n;
        }""")
        assert carry_painted > 80, f"叼卡小演员 canvas 应画了实像素,实际 {carry_painted}"
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

        # 老视图零回归:切回对话视图,像素替身移除(PNG 回归)、灵魂 DOM 不漏
        page.click("#view-opt-chat")
        page.wait_for_function("!document.body.classList.contains('desk-view')", timeout=5000)
        assert page.evaluate("!document.getElementById('desk-karvy-pixel')"), "离开 desk 应移除像素替身"
        assert page.evaluate("document.querySelectorAll('.desk-signed-note').length === 0"), "离开 desk 应清署名便签"

        browser.close()

    assert errors == [], f"真浏览器 console 必须 0 error,实际: {errors}"
