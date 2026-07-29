"""mcp_oauth — MCP remote server 的 OAuth 2.1 客户端(docs/96 刀2)。

Q5 铁律「通用基建必借」的教科书案例:OAuth 2.1 授权码 + PKCE + 动态客户端注册 + token 刷新
的**协议本身一行不手搓** —— 全用官方 `mcp` SDK 的 `OAuthClientProvider`(它就是个
`httpx.Auth`,直接塞进我们现有的 remote 连接的 httpx client 即可)。我们只写**属于自己
安全边界**的三样薄东西:

  1. `KarvyTokenStorage`(实现 SDK 的 `TokenStorage` 协议):access/refresh token +
     动态注册拿到的 client_info,落**每 server 一个 0600 独立文件**
     (~/.karvyloop/mcp_oauth/<server>.json)—— 照 console/access.py、channel_secret 先例:
     **绝不进 config.yaml 明文、绝不进 log / 异常文本 / repr、export 排除**(export_cmd 已加)。
  2. `redirect_handler`:开系统浏览器到授权 URL;开不了 fail-loud 把 URL 印出来让人手点。
  3. `callback_handler`:localhost 一次性回调 server 收 `?code=&state=` → 原样还给 SDK 换
     token(**CSRF state 由 SDK 自己生成并校验**,我们只忠实捕获+回传,不自造校验)。

凭证纪律:本模块**从不 log / print token 值**;唯一的 print 是 fail-loud 的授权 URL(那是
要给人点的公开链接,不含 token)。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# 回调等待上限:开着浏览器让人登录授权,给足时间但不无限挂(fail-loud)。
_CALLBACK_TIMEOUT_S = 300.0


def _oauth_dir(base_dir: Optional[Path] = None) -> Path:
    """~/.karvyloop/mcp_oauth/(0700,只有你自己进得去)。base_dir 供测试注入。"""
    root = (base_dir or (Path.home() / ".karvyloop")) / "mcp_oauth"
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)   # POSIX;Windows 忽略(同 access.py 先例)
    except Exception:
        pass
    return root


def _safe_name(server_name: str) -> str:
    """server 名 → 文件名安全片段(只留字母数字和 _-.);空则回退 'server'。"""
    s = re.sub(r"[^0-9A-Za-z._-]", "_", str(server_name or "").strip())
    return s or "server"


class KarvyTokenStorage:
    """SDK `TokenStorage` 协议的实现:token + client_info 落 0600 独立文件。

    一个 server 一个 `<server>.json`,内含 {"tokens": {...}, "client_info": {...}}。
    读写都在这个文件里,**绝不碰 config.yaml**。故意不实现 `__repr__` 携带内容(默认 repr
    只有类名地址,不泄 token)。
    """

    def __init__(self, server_name: str, *, base_dir: Optional[Path] = None) -> None:
        self._path = _oauth_dir(base_dir) / f"{_safe_name(server_name)}.json"

    # ---- SDK TokenStorage 协议(4 个 async 方法)----
    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        return self._read("tokens", OAuthToken)

    async def set_tokens(self, tokens) -> None:
        self._write("tokens", tokens)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        return self._read("client_info", OAuthClientInformationFull)

    async def set_client_info(self, client_info) -> None:
        self._write("client_info", client_info)

    # ---- 落盘(0600)----
    def _load(self) -> dict:
        try:
            d = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}   # 不存在 / 坏 JSON → 当空(下次授权重建)
        # 对抗验收 #8:有效 JSON 但**不是对象**(null / [..] / 裸值)也当空 —— 下游按 dict 用,
        # 否则 .get()/data[key] 会崩。兑现"绝不半信坏文件、绝不崩"的承诺。
        return d if isinstance(d, dict) else {}

    def _save(self, data: dict) -> None:
        self._path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(self._path, 0o600)   # 只有你自己能读 token(POSIX;Windows 忽略)
        except Exception:
            pass

    def _read(self, key: str, model):
        raw = self._load().get(key)
        if not raw:
            return None
        try:
            return model.model_validate(raw)
        except Exception:
            return None   # 形状对不上 → 当没有(宁重授权不喂坏 token)

    def _write(self, key: str, model) -> None:
        data = self._load()
        data[key] = model.model_dump(mode="json", exclude_none=True)
        self._save(data)


class OAuthCallbackError(RuntimeError):
    """回调阶段出错(授权被拒 / 超时 / server 回 error=)。"""


_DONE_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'><title>KarvyLoop</title></head>"
    "<body style='font-family:sans-serif;text-align:center;padding-top:80px'>"
    "<h2>✅ 授权完成</h2><p>可以关掉这个标签页,回到 KarvyLoop 了。</p>"
    "<p style='color:#888'>You can close this tab and return to KarvyLoop.</p></body></html>"
)


class _LocalCallbackReceiver:
    """localhost 一次性回调 server:授权服务商跳回 `http://127.0.0.1:<port>/callback?code=&state=`
    时捕获 code+state,还给 SDK。绑定发生在构造时(端口即刻可知 → redirect_uri 定得下来)。
    """

    def __init__(self) -> None:
        self._result: dict = {}
        self._event = threading.Event()
        # 绑 127.0.0.1:0 → 内核给一个空闲端口;只监听本机回环(外部够不着)。
        self._httpd = HTTPServer(("127.0.0.1", 0), self._make_handler())
        self.port: int = self._httpd.server_address[1]
        self.redirect_uri: str = f"http://127.0.0.1:{self.port}/callback"
        self._thread: Optional[threading.Thread] = None

    def _make_handler(receiver):  # noqa: N805 —— 闭包捕获 receiver(非标准 self 命名有意)
        class _H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                qs = parse_qs(urlparse(self.path).query)
                code = (qs.get("code") or [""])[0]
                error = (qs.get("error") or [""])[0]
                # 对抗验收 note#3:忽略与授权无关的杂请求(favicon 预取 / 本机随手一发)——
                # 别让它消费掉这次一次性回调把人逼去重授权。只认带 code 或 error 的真回调。
                if not code and not error:
                    self.send_response(204)
                    self.end_headers()
                    return
                receiver._result = {
                    "code": code,
                    "state": (qs.get("state") or [None])[0],
                    "error": error,
                }
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_DONE_PAGE.encode("utf-8"))
                receiver._event.set()

            def log_message(self, *a):  # 别把回调请求刷到 stderr(URL 里带 code)
                pass
        return _H

    def _start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._thread.start()

    def _shutdown(self) -> None:
        # 对抗验收 note#2:HTTPServer.shutdown() 在 serve_forever 没起时会**永久阻塞** ——
        # 只在真起了服务线程时才 shutdown;监听 socket 无论如何都 server_close(不挂)。
        try:
            if self._thread is not None and self._thread.is_alive():
                self._httpd.shutdown()
        except Exception:
            pass
        try:
            self._httpd.server_close()
        except Exception:
            pass

    async def redirect_handler(self, authorization_url: str) -> None:
        """SDK 拿到授权 URL → 开浏览器让人登录。开不了就 fail-loud 印 URL(不含 token,可安全打印)。"""
        self._start()   # 先确保在听(浏览器随时会跳回来)
        opened = False
        try:
            import webbrowser
            opened = webbrowser.open(authorization_url)
        except Exception:
            opened = False
        if not opened:
            print("[karvyloop] 打不开浏览器 —— 请手动打开下面的链接完成授权登录:\n  "
                  + authorization_url)

    async def callback_handler(self) -> tuple[str, Optional[str]]:
        """等浏览器带着 code 跳回来。超时/授权被拒 → fail-loud 抛错指路重授权。"""
        loop = asyncio.get_event_loop()
        got = await loop.run_in_executor(None, self._event.wait, _CALLBACK_TIMEOUT_S)
        self._shutdown()
        if not got:
            raise OAuthCallbackError(
                f"等授权回调超时({int(_CALLBACK_TIMEOUT_S)}s)—— 没收到登录跳转。重试授权。")
        r = self._result
        if r.get("error"):
            raise OAuthCallbackError(f"授权被拒或出错:{r['error']}。重试授权。")
        if not r.get("code"):
            raise OAuthCallbackError("回调里没有授权码(code)。重试授权。")
        return r["code"], r.get("state")


def build_oauth_provider(
    server_url: str,
    server_name: str,
    *,
    scopes: Optional[list[str]] = None,
    base_dir: Optional[Path] = None,
):
    """给一个 remote MCP server 造 SDK 的 `OAuthClientProvider`(= httpx.Auth,直接塞进
    httpx client)。协议全走 SDK;我们只提供 0600 存储 + 浏览器/回调两个 handler。

    server_url: MCP server 端点(SDK 从这里发现授权服务器 + 动态注册)。
    scopes: 申请的权限范围(如 Gmail 的 gmail.readonly);None = 让 server 决定。
    """
    try:
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata
    except ImportError as e:  # pragma: no cover —— 无 mcp SDK 环境
        raise RuntimeError(
            "接需要 OAuth 的 remote MCP server 要 mcp SDK(pip install 'mcp>=1.9,<2'): " + str(e)
        ) from e

    receiver = _LocalCallbackReceiver()
    storage = KarvyTokenStorage(server_name, base_dir=base_dir)
    metadata = OAuthClientMetadata(
        client_name="KarvyLoop",
        redirect_uris=[receiver.redirect_uri],       # 必须与回调 server 端口一致
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=(" ".join(scopes) if scopes else None),
        token_endpoint_auth_method="none",           # 公共客户端 + PKCE(无 client_secret)
    )
    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=receiver.redirect_handler,
        callback_handler=receiver.callback_handler,
    )


__all__ = [
    "KarvyTokenStorage",
    "OAuthCallbackError",
    "build_oauth_provider",
]
