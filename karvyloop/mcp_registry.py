"""mcp_registry — 查官方 MCP Registry(registry.modelcontextprotocol.io)拿 server 目录。

docs/98 刀2:我们只**消费**官方 Registry(事实标准,大量安装源于它),**不自建 catalog**。
从这里接进来的 server = **未策展**(docs/98 刀1)→ 自动吃 fail-safe 外发门(名字判不出的
副作用工具走 H2A 草稿卡)。我们的增值只做**围栏 / 信任分级 / 草稿卡把关**,不复制目录。

薄:只做一次 HTTP GET + 防御式解析,**不缓存、不落盘**(每次实时查);只留**有 streamable-http
remote 的 server**(我们接 remote HTTP,`mcp_client` 已支持),stdio/package-only 的一键远程
接入这一刀先不收。解析**宁空勿毒**:形状不对返 [],不把垃圾塞进 UI。

API 形态(2025-12 schema,已核):
  GET /v0/servers?search=<q>&limit=<n>
  → {"servers": [{"server": {"name","title","description","version",
                             "remotes": [{"type":"streamable-http","url":…}]}}],
     "metadata": {"nextCursor":…, "count":…}}
"""
from __future__ import annotations

from typing import Any

_REGISTRY_BASE = "https://registry.modelcontextprotocol.io"
_SERVERS_PATH = "/v0/servers"
_MAX_LIMIT = 50
_DESC_CAP = 500

# 我们接的 remote transport(mcp_client 支持 streamable HTTP;归一连字符/下划线)
_HTTP_TYPES = {"streamable-http", "streamable_http", "http"}


class RegistryError(Exception):
    """查 Registry 失败(网络 / HTTP 4xx-5xx / JSON 坏)。文本不含敏感信息。"""


def _remote_http_url(srv: dict[str, Any]) -> str:
    """从一个 server 的 remotes 里取第一个 streamable-http 的 url(没有 → "")。"""
    for rem in srv.get("remotes") or []:
        if not isinstance(rem, dict):
            continue
        t = str(rem.get("type") or "").strip().lower().replace("_", "-")
        url = str(rem.get("url") or "").strip()
        if t in {"streamable-http", "http"} and url.lower().startswith(("http://", "https://")):
            return url
    return ""


def parse_servers(data: Any) -> list[dict[str, str]]:
    """官方 Registry 响应 → 简化 server 列表(只留有 streamable-http remote 的)。

    **宁空勿毒**:非预期形状一律跳过,绝不把半解析的垃圾塞进 UI。
    返回 [{name, title, description, version, url}]。
    """
    out: list[dict[str, str]] = []
    if not isinstance(data, dict):
        return out
    for item in data.get("servers") or []:
        if not isinstance(item, dict):
            continue
        srv = item.get("server")
        if not isinstance(srv, dict):
            continue
        url = _remote_http_url(srv)
        if not url:
            continue   # 只接 remote HTTP;stdio/package-only 这一刀先跳过
        name = str(srv.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "title": str(srv.get("title") or name).strip(),
            "description": str(srv.get("description") or "").strip()[:_DESC_CAP],
            "version": str(srv.get("version") or "").strip(),
            "url": url,
        })
    return out


def search_registry(query: str = "", *, limit: int = 20, timeout: float = 10.0,
                    base: str = _REGISTRY_BASE) -> list[dict[str, str]]:
    """查官方 MCP Registry,返回简化 server 列表(见 parse_servers)。

    query 空 = 列首页;host 固定(非用户可控,无 SSRF 面)。失败 → RegistryError。
    """
    import httpx
    params: dict[str, Any] = {"limit": int(max(1, min(limit, _MAX_LIMIT)))}
    q = str(query or "").strip()
    if q:
        params["search"] = q
    try:
        resp = httpx.get(base.rstrip("/") + _SERVERS_PATH, params=params,
                         timeout=timeout, headers={"Accept": "application/json"},
                         follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        # 不外泄底层细节(URL 固定、无凭证,但统一克制)
        raise RegistryError(f"查 MCP Registry 失败:{type(e).__name__}") from e
    return parse_servers(data)


__all__ = ["search_registry", "parse_servers", "RegistryError"]
