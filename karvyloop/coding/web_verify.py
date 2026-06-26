"""web_verify — 网页类产物的**运行时**验收门(无头浏览器真加载,抓控制台报错)。

补的是研发 loop 的一个真实盲区:Forge 自检只到"语法/import 对得上",**碰不到浏览器运行时**——
"看着写完了"≠"真能跑"(karvy3d 实例:语法全过、点开始却是哑按钮)。本模块用无头浏览器
(Playwright)**真加载** index.html、抓 console error + 未捕获异常 → 把"我跑过了"从语法升到运行时。

诚实边界:
- Playwright 是重依赖(要下浏览器),**可选**。没装 → 不报错,老实返回 `available=False`
  + 安装指引(降级,不阻断)——和 doctor 一个路子:验不了就说验不了,绝不假装"验过了"。
- 用本地 http server 起服务再让浏览器访问(`type=module`/importmap 在 file:// 下会被 CORS 拦,
  http 才是对的跑法)——顺手消解了"双击 file:// 打不开"那类假象。
"""
from __future__ import annotations

import contextlib
import importlib.util
import socket
import threading
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional


@dataclass
class WebVerifyResult:
    available: bool            # Playwright 在不在(不在 → 没法验运行时)
    ok: Optional[bool]         # True=加载无报错 / False=有报错 / None=没法验
    errors: list = field(default_factory=list)   # console error + pageerror 文本
    reason: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return {"available": self.available, "ok": self.ok,
                "errors": list(self.errors), "reason": self.reason, "url": self.url}


def playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.contextmanager
def _serve(directory: Path):
    """在 directory 上起一个后台 http server(绕开 file:// 的模块 CORS 限制)。yield base_url。"""
    port = _free_port()
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _resolve(path: str, entry: str) -> tuple[Path, str]:
    """path 可以是目录或 html 文件 → (服务根目录, 入口文件名)。"""
    p = Path(path).expanduser().resolve()
    if p.is_file():
        return p.parent, p.name
    return p, entry


def verify_web_app(path: str, *, entry: str = "index.html",
                   timeout_ms: int = 8000, settle_ms: int = 3000) -> WebVerifyResult:
    """无头浏览器真加载网页,抓 console error + 未捕获异常 + 主线程假死。

    没装 Playwright → available=False(老实降级,绝不假装验过)。

    wait 策略(踩过坑):**不** gate 在 `load`/`domcontentloaded` 上。重型 app(3D/世界生成)
    会合法地推迟这两个事件,gate 在上面只会得到一坨没用的 Playwright 超时栈,反而盖住真问题。
    正确路子:`wait_until="commit"`(拿到响应即可)→ settle 一段时间让脚本跑 + 异常浮出来 →
    抓 console.error + pageerror;**外加**:settle 完若 readyState 仍是 loading,说明主线程被
    同步初始化卡死(karvy3d 实例:同步世界生成 → 页面假死、按钮点了没反应)——这本身就是真问题,
    老实报出来,而不是抛 Playwright 超时。
    """
    if not playwright_available():
        return WebVerifyResult(
            available=False, ok=None,
            reason="Playwright 未安装,无法验证浏览器运行时(只能验到语法)。",
        )
    root, entry_file = _resolve(path, entry)
    if not (root / entry_file).exists():
        return WebVerifyResult(available=True, ok=False, errors=[],
                               reason=f"入口文件不存在:{root / entry_file}")

    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    url = ""
    try:
        with _serve(root) as base:
            url = f"{base}/{entry_file}"
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                        if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
                try:
                    # 只等"导航提交"(拿到响应),不等 load —— 重型 app 合法地推迟 load。
                    page.goto(url, wait_until="commit", timeout=timeout_ms)
                    page.wait_for_timeout(settle_ms)   # 让脚本跑完 / 异常浮出来 / HUD 初始化
                    # settle 完仍 loading = 主线程被同步初始化卡死(页面假死、按钮哑)。
                    try:
                        if page.evaluate("document.readyState") == "loading":
                            errors.append(
                                f"运行时:settle {settle_ms}ms 后页面仍未完成加载"
                                f"(readyState=loading)—— 主线程很可能被同步初始化卡住"
                                f"(如一次性同步生成世界),表现为页面假死、按钮点了没反应。")
                    except Exception:
                        pass   # readyState 读不到不致命(导航已失败时会到这)
                except Exception as e:
                    errors.append(f"navigation: {type(e).__name__}: {e}")
                browser.close()
    except Exception as e:
        return WebVerifyResult(available=True, ok=None, url=url,
                               reason=f"运行验证器自身出错:{type(e).__name__}: {e}")
    real = [e for e in errors if e]
    return WebVerifyResult(available=True, ok=(len(real) == 0), errors=real, url=url,
                           reason=("加载无报错" if not real else f"加载时有 {len(real)} 条报错"))


__all__ = ["WebVerifyResult", "verify_web_app", "playwright_available"]
