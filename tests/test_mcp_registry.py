"""test_mcp_registry — 官方 MCP Registry 消费(docs/98 刀2)。

守:① 只留有 streamable-http remote 的 server;② 宁空勿毒(坏形状返 []);③ search/limit 透传;
④ 网络/坏 JSON → RegistryError;⑤ 端点优雅降级 + 标 uncurated(接进来吃 fail-safe 外发门)。
"""
from __future__ import annotations

import httpx
import pytest
import respx

from karvyloop.mcp_registry import RegistryError, parse_servers, search_registry

BASE = "https://registry.modelcontextprotocol.io"

_SAMPLE = {
    "servers": [
        {"server": {"name": "ac.inference.sh/mcp", "title": "inference.sh",
                    "description": "run any ai model", "version": "2.0.0",
                    "remotes": [{"type": "streamable-http", "url": "https://sh.inference.ac"}]}},
        {"server": {"name": "local.only/x", "title": "Local", "description": "stdio only",
                    "version": "1.0", "remotes": []}},                       # 无 remote → 跳
        {"server": {"name": "pkg.only/y", "packages": [{"identifier": "z"}]}},  # 无 remotes → 跳
        {"not": "a server"},                                                  # 垃圾 → 跳
    ],
    "metadata": {"nextCursor": "abc", "count": 4},
}


def test_parse_keeps_only_streamable_http():
    out = parse_servers(_SAMPLE)
    assert out == [{
        "name": "ac.inference.sh/mcp", "title": "inference.sh",
        "description": "run any ai model", "version": "2.0.0",
        "url": "https://sh.inference.ac"}]


def test_parse_is_garbage_refusing():
    assert parse_servers(None) == []
    assert parse_servers({"servers": "not a list"}) == []
    assert parse_servers({}) == []
    # 空 name 跳过
    assert parse_servers({"servers": [{"server": {
        "name": "", "remotes": [{"type": "streamable-http", "url": "https://x"}]}}]}) == []
    # stdio(非 http)跳过
    assert parse_servers({"servers": [{"server": {
        "name": "n", "remotes": [{"type": "stdio", "url": "x"}]}}]}) == []
    # 非 http(s) url 跳过
    assert parse_servers({"servers": [{"server": {
        "name": "n", "remotes": [{"type": "streamable-http", "url": "ftp://x"}]}}]}) == []


@respx.mock
def test_search_passes_query_and_limit_and_parses():
    route = respx.get(BASE + "/v0/servers").mock(return_value=httpx.Response(200, json=_SAMPLE))
    out = search_registry("github", limit=5)
    assert len(out) == 1 and out[0]["url"] == "https://sh.inference.ac"
    url = str(route.calls.last.request.url)
    assert "search=github" in url and "limit=5" in url


@respx.mock
def test_search_empty_query_omits_search_param():
    route = respx.get(BASE + "/v0/servers").mock(return_value=httpx.Response(200, json=_SAMPLE))
    search_registry("")
    assert "search=" not in str(route.calls.last.request.url)


@respx.mock
def test_search_limit_clamped():
    route = respx.get(BASE + "/v0/servers").mock(return_value=httpx.Response(200, json=_SAMPLE))
    search_registry("x", limit=9999)
    assert "limit=50" in str(route.calls.last.request.url)   # _MAX_LIMIT


@respx.mock
def test_search_http_error_raises():
    respx.get(BASE + "/v0/servers").mock(return_value=httpx.Response(503))
    with pytest.raises(RegistryError):
        search_registry("x")


@respx.mock
def test_search_bad_json_raises():
    respx.get(BASE + "/v0/servers").mock(return_value=httpx.Response(200, text="<html>not json"))
    with pytest.raises(RegistryError):
        search_registry("x")


# ---- 端点 ----

def _console_app():
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    return build_console_app(workbench=WorkbenchObserver(), main_loop=None)


def test_endpoint_ok_marks_uncurated(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(
        "karvyloop.mcp_registry.search_registry",
        lambda q="", **k: [{"name": "n", "title": "t", "description": "d",
                            "version": "1", "url": "https://x/mcp"}])
    r = TestClient(_console_app()).get("/api/mcp/registry/search?q=git").json()
    assert r["ok"] is True and r["uncurated"] is True
    assert r["servers"][0]["url"] == "https://x/mcp"


def test_endpoint_registry_error_is_graceful(monkeypatch):
    from fastapi.testclient import TestClient

    def _boom(q="", **k):
        raise RegistryError("查 MCP Registry 失败:ConnectError")
    monkeypatch.setattr("karvyloop.mcp_registry.search_registry", _boom)
    r = TestClient(_console_app()).get("/api/mcp/registry/search").json()
    assert r["ok"] is False and r["servers"] == []
