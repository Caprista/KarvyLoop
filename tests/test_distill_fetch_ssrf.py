"""安全审计逮到的缝:知识馆员喂料的 URL 抓取(routes_memory._fetch_url,POST /api/memory/feed)
此前 follow_redirects=True 裸抓、**不过 urlguard.check_url** —— 用户把 http://169.254.169.254/
(云元数据)或内网 URL 当"材料"粘贴 → 被当跳板打内网/元数据(SSRF)。

本测试锁**接线**(check_url 自身的 IP/DNS 判定已在 test_ssrf.py 测,这里隔离掉,只验 _fetch_url
确实把 URL 过了闸、且抓前过、重定向逐跳过):被闸拦的 URL 一律返 "" 且**绝不触网**;放行的才抓。
"""
import asyncio

import pytest

from karvyloop.coding.tools import urlguard
from karvyloop.console import routes_memory as RM


class _RecordingClient:
    """httpx 桩:记 .get 到过没(reached)。可配 302 目标链。默认返一个正常页。"""
    reached: list = []
    redirect_to: str = ""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, *a, **k):
        _RecordingClient.reached.append(url)

        class _R:
            is_redirect = bool(_RecordingClient.redirect_to)
            headers = {"location": _RecordingClient.redirect_to} if _RecordingClient.redirect_to else {}
            next_request = None
            text = "<html><body>hello world</body></html>"
            def raise_for_status(self):
                pass
        return _R()


@pytest.fixture(autouse=True)
def _reset():
    _RecordingClient.reached = []
    _RecordingClient.redirect_to = ""
    yield


@pytest.mark.parametrize("bad", [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:8766/api/memory",
    "http://10.0.0.5/x",
])
def test_blocked_url_never_reaches_network(monkeypatch, bad):
    # check_url 判该 URL 危险(桩:对 bad 抛 SsrfBlocked)→ _fetch_url 必须在触网**之前**拦下
    def _guard(u):
        if u == bad:
            raise urlguard.SsrfBlocked("blocked")
    monkeypatch.setattr(urlguard, "check_url", _guard)
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _RecordingClient)
    out = asyncio.run(RM._fetch_url(bad))
    assert out == ""                          # 拦下 → 返空
    assert _RecordingClient.reached == []     # **绝不触网**(证明 check_url 在 httpx 前)


def test_allowed_url_is_fetched(monkeypatch):
    # check_url 放行 → 才走 httpx 抓正文(证明没误伤合法 URL)
    monkeypatch.setattr(urlguard, "check_url", lambda u: None)
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _RecordingClient)
    out = asyncio.run(RM._fetch_url("https://example.com/page"))
    assert "hello world" in out
    assert _RecordingClient.reached == ["https://example.com/page"]


def test_redirect_to_internal_blocked_per_hop(monkeypatch):
    # 首跳放行、但 302 到内网 → 逐跳 check_url 必须拦重定向目标(挡 redirect→内网)
    bad = "http://169.254.169.254/latest/meta-data/"

    def _guard(u):
        if u == bad:
            raise urlguard.SsrfBlocked("blocked redirect target")
    monkeypatch.setattr(urlguard, "check_url", _guard)
    import httpx
    _RecordingClient.redirect_to = bad
    monkeypatch.setattr(httpx, "AsyncClient", _RecordingClient)
    out = asyncio.run(RM._fetch_url("https://example.com/redir"))
    assert out == ""                                    # 重定向目标被拦
    assert _RecordingClient.reached == ["https://example.com/redir"]  # 只走了首跳,没抓到内网
