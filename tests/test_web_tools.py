"""test_web_tools — 智能体基础联网能力:web_fetch / web_search。

锁:工具形状 + 抓网页(respx 拦)+ HTML→正文 + DDG 结果解析 + **宁空勿毒/优雅降级**
(网络失败返 ok=False 人话原因,绝不编;搜不到返"没搜到"不伪造)+ 工厂默认带这两个工具。
"""
from __future__ import annotations

import pathlib
import sys

import httpx
import pytest
import respx

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.coding.tools import make_coding_tools  # noqa: E402
from karvyloop.coding.tools.web import (  # noqa: E402
    WebFetchTool, WebSearchTool, _parse_ddg, _strip_html,
)


@pytest.fixture(autouse=True)
def _no_search_key(request, monkeypatch):
    """默认强制 keyless(别在跑测试的机器上误读真实 config.yaml 的 search key);
    名字含 search_config 的用例要测真实配置读写,跳过此 patch。"""
    if "search_config" in request.node.name:
        return
    import karvyloop.coding.tools.web as W
    monkeypatch.setattr(W, "_search_config", lambda: None)


@pytest.fixture(autouse=True)
def _pin_ssrf_dns_to_public(monkeypatch):
    """这些是 **响应处理** 测试(respx 拦 HTTP),不是 SSRF 测试。SSRF 地板(urlguard)会对
    host 做**真实** DNS 解析,respx 拦不到这步 —— 某些跑测环境把公网域名解析到 198.18/15
    基准测试段(ipaddress 判为 private)→ 误拦。这里把 urlguard 的解析 pin 到一个固定**公网**
    IP,让地板逻辑照常跑(不削弱防护),只是不依赖真实 DNS。SSRF 拦截本身由
    tests/security/test_ssrf.py 用字面内网 IP 独立验收。"""
    import karvyloop.coding.tools.urlguard as UG
    monkeypatch.setattr(UG, "_resolve_all_ips", lambda host: ["93.184.216.34"])


def test_strip_html_basic():
    html = "<html><head><style>x{}</style></head><body><h1>Hi</h1><script>bad()</script><p>a&amp;b</p></body></html>"
    text = _strip_html(html)
    assert "Hi" in text and "a&b" in text
    assert "bad()" not in text and "x{}" not in text   # script/style 去掉


def test_parse_ddg_extracts_results():
    html = (
        '<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">First &amp; Title</a>'
        '<a class="result__snippet">snippet one</a>'
        '<a class="result__a" href="https://example.org/b">Second</a>'
        '<a class="result__snippet">snippet two</a>'
    )
    res = _parse_ddg(html, 6)
    assert len(res) == 2
    assert res[0]["url"] == "https://example.com/a" and "First & Title" in res[0]["title"]
    assert res[0]["snippet"] == "snippet one"
    assert res[1]["url"] == "https://example.org/b"


def test_factory_includes_web_tools():
    tools = make_coding_tools(sandbox=None, file_state=None, workspace_root="/", token=None)
    assert "web_search" in tools and "web_fetch" in tools
    # 只读验收者(checker)也该有(联网只读,安全)
    ro = make_coding_tools(sandbox=None, file_state=None, workspace_root="/", token=None, read_only=True)
    assert "web_search" in ro and "web_fetch" in ro
    assert "write_file" not in ro   # 只读仍不给写


# ---- web_fetch ----
async def test_fetch_bad_url_rejected():
    r = await WebFetchTool()({"url": "ftp://nope"})
    assert r.ok is False and "http" in r.error_message


@respx.mock
async def test_fetch_html_returns_text():
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(200, text="<html><body><h1>Title</h1><p>Body text</p></body></html>"))
    r = await WebFetchTool()({"url": "https://example.com/page"})
    assert r.ok is True and "Title" in r.payload and "Body text" in r.payload
    assert "<h1>" not in r.payload   # 标签已剥


@respx.mock
async def test_fetch_network_error_degrades():
    respx.get("https://down.example").mock(side_effect=httpx.ConnectError("boom"))
    r = await WebFetchTool()({"url": "https://down.example"})
    assert r.ok is False and "抓取失败" in r.error_message   # 老实报错,不编


# ---- web_search ----
async def test_search_empty_query_rejected():
    r = await WebSearchTool()({"query": "  "})
    assert r.ok is False


@respx.mock
async def test_search_parses_and_lists():
    html = ('<a class="result__a" href="https://ex.com/x">Result X</a>'
            '<a class="result__snippet">about x</a>')
    respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(
        return_value=httpx.Response(200, text=html))
    r = await WebSearchTool()({"query": "what is x"})
    assert r.ok is True and "Result X" in r.payload and "https://ex.com/x" in r.payload


@respx.mock
async def test_search_no_results_is_honest_not_fabricated():
    respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(
        return_value=httpx.Response(200, text="<html><body>nothing here</body></html>"))
    r = await WebSearchTool()({"query": "obscure"})
    assert r.ok is True and "没搜到" in r.payload   # 不伪造结果


@respx.mock
async def test_search_network_error_degrades():
    respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(
        side_effect=httpx.ConnectError("boom"))
    r = await WebSearchTool()({"query": "x"})
    assert r.ok is False and "搜索失败" in r.error_message


# ---- 配了搜索 API key:优先用它,出错则回落 DDG ----
@respx.mock
async def test_keyed_brave_used_when_configured(monkeypatch):
    import karvyloop.coding.tools.web as W
    monkeypatch.setattr(W, "_search_config", lambda: {"provider": "brave", "api_key": "FAKE-DO-NOT-LEAK"})
    respx.get(url__startswith="https://api.search.brave.com").mock(return_value=httpx.Response(
        200, json={"web": {"results": [{"title": "Brave Hit", "url": "https://b.com", "description": "snip"}]}}))
    r = await WebSearchTool()({"query": "x"})
    assert r.ok is True and "Brave Hit" in r.payload and "https://b.com" in r.payload


# ---- 产品内配置(写仓外 search.json,不动 config.yaml;不回传 key 明文)----
def test_set_get_search_config_roundtrip(monkeypatch, tmp_path):
    import karvyloop.coding.tools.web as W
    monkeypatch.setattr(W, "_search_store_path", lambda: tmp_path / "search.json")
    monkeypatch.delenv("KARVYLOOP_SEARCH_API_KEY", raising=False)
    W.invalidate_search_config()
    # 默认 keyless
    assert W.get_search_config_public()["mode"] == "keyless"
    # 设 brave key → keyed,但公开态不含明文 key
    pub = W.set_search_config("brave", "BSA-FAKE-DO-NOT-LEAK")
    assert pub == {"provider": "brave", "has_key": True, "mode": "keyed"}
    cfg = W._search_config()
    assert cfg["provider"] == "brave" and cfg["api_key"] == "BSA-FAKE-DO-NOT-LEAK"
    assert (tmp_path / "search.json").exists()
    # 清除(空 key)→ 回 keyless + 文件删除
    assert W.set_search_config("", "")["mode"] == "keyless"
    assert not (tmp_path / "search.json").exists()


def test_set_search_config_rejects_unknown_provider(monkeypatch, tmp_path):
    import karvyloop.coding.tools.web as W
    monkeypatch.setattr(W, "_search_store_path", lambda: tmp_path / "search.json")
    monkeypatch.delenv("KARVYLOOP_SEARCH_API_KEY", raising=False)
    W.invalidate_search_config()
    assert W.set_search_config("bogus", "k")["mode"] == "keyless"   # 未知 provider = 不存,回 keyless


def test_search_config_endpoints(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import karvyloop.coding.tools.web as W
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    monkeypatch.setattr(W, "_search_store_path", lambda: tmp_path / "search.json")
    monkeypatch.delenv("KARVYLOOP_SEARCH_API_KEY", raising=False)
    W.invalidate_search_config()
    c = TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None))
    assert c.get("/api/search/config").json()["mode"] == "keyless"
    r = c.post("/api/search/config", json={"provider": "tavily", "api_key": "tvly-FAKE-DO-NOT-LEAK"}).json()
    assert r["ok"] and r["mode"] == "keyed" and r["provider"] == "tavily" and "api_key" not in r
    assert c.get("/api/search/config").json() == {"ok": True, "provider": "tavily", "has_key": True,
                                                  "mode": "keyed", "providers": ["brave", "tavily"]}


@respx.mock
async def test_keyed_provider_error_falls_back_to_ddg(monkeypatch):
    import karvyloop.coding.tools.web as W
    monkeypatch.setattr(W, "_search_config", lambda: {"provider": "brave", "api_key": "FAKE-DO-NOT-LEAK"})
    respx.get(url__startswith="https://api.search.brave.com").mock(side_effect=httpx.ConnectError("down"))
    respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(return_value=httpx.Response(
        200, text='<a class="result__a" href="https://d.com">DDG Hit</a><a class="result__snippet">s</a>'))
    r = await WebSearchTool()({"query": "x"})
    assert r.ok is True and "DDG Hit" in r.payload   # provider 挂了也不让搜索整体失败
