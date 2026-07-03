"""Web 工具（coding/tools/web.py）—— 智能体的**基础能力**:知识库没命中时上网查。

为什么是基础能力(Hardy):问到自身知识库没覆盖的事,角色应当能**联网搜索/读网页**核证,
而不是编或者干说"不知道"。业界不编排也具备 —— 我们也内建给每个 agent。

设计:
- **host 侧**(httpx 直发,不走沙箱):搜索/抓网页是只读取信息,不碰用户文件系统;
  沙箱是用来隔离"改文件/跑命令"的,网络读取放 host 更简单可靠。
- **keyless 即可用**(对齐"开箱即用"):web_search 走 DuckDuckGo 无 key HTML 端点;
  解析失败/被限流 → 老实返回"没搜到"(绝不编造结果)。web_fetch 抓任意 URL → 正文。
- **宁空勿毒**:抓取/解析失败一律返回 ok=False + 人话原因,绝不把垃圾当事实喂回模型。
- 安全:只 GET、跟随重定向上限、超时封顶、响应体封顶;不发任何凭证/header(除 UA)。

注:CodingTool 协议(name/description/parameters/__call__);构造签名与四件套一致
(sandbox/file_state/workspace_root/token),但 web 工具不用沙箱,只为统一工厂注入。
"""

from __future__ import annotations

import html as _html
import re
from typing import Any

from karvyloop.schemas import CapabilityToken

from ._result import CodingResult

_UA = "Mozilla/5.0 (compatible; KarvyLoop/1.0; +https://github.com/Caprista/KarvyLoop)"
_TIMEOUT = 15.0
_MAX_CHARS = 8000          # 抓回正文封顶(喂模型够用,不爆 context)
_MAX_RESULTS = 6


def _strip_html(raw: str) -> str:
    """极简 HTML→正文:去 script/style、去标签、解实体、压空白。够给模型读,不求完美排版。"""
    raw = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</(p|div|li|h[1-6]|tr)>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = _html.unescape(raw)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


# ---- 可选:配置了搜索 API key 就用它(更稳/更高质量),否则 keyless DDG 兜底 ----
# 接法:① 环境变量 KARVYLOOP_SEARCH_API_KEY(+ 可选 KARVYLOOP_SEARCH_PROVIDER);
#       ② ~/.karvyloop/config.yaml 加 search 段:
#            search:
#              provider: brave        # brave | tavily
#              api_key: "BSA..."      # 真 key 只在仓外的 config.yaml
# 没配 → 自动用无 key 的 DuckDuckGo,体验不变。
_SEARCH_CACHE: dict[str, Any] = {}
_VALID_PROVIDERS = ("brave", "tavily")


def _search_store_path():
    import pathlib
    return pathlib.Path.home() / ".karvyloop" / "search.json"


def _search_config() -> dict | None:
    """搜索 provider 配置,来源优先级:① 环境变量 ② 产品内设置 search.json ③ config.yaml search 段。
    都没有 → None(走 keyless DuckDuckGo)。缓存到 _SEARCH_CACHE,设置变更时 invalidate_search_config 清。"""
    if "cfg" in _SEARCH_CACHE:
        return _SEARCH_CACHE["cfg"]
    cfg = None
    try:
        import os
        env_key = (os.environ.get("KARVYLOOP_SEARCH_API_KEY") or "").strip()
        if env_key:
            cfg = {"provider": (os.environ.get("KARVYLOOP_SEARCH_PROVIDER") or "brave").strip().lower(),
                   "api_key": env_key}
        else:
            import json
            sp = _search_store_path()
            if sp.exists():   # ② 产品内设置(单独文件,不动有注释的 config.yaml)
                s = json.loads(sp.read_text(encoding="utf-8")) or {}
                key = str(s.get("api_key", "") or "").strip()
                if key:
                    cfg = {"provider": str(s.get("provider", "brave") or "brave").strip().lower(),
                           "api_key": key}
            if cfg is None:   # ③ config.yaml 手填
                import pathlib
                import yaml
                p = pathlib.Path.home() / ".karvyloop" / "config.yaml"
                if p.exists():
                    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                    s = data.get("search") or {}
                    key = str(s.get("api_key", "") or "").strip()
                    if key:
                        cfg = {"provider": str(s.get("provider", "brave") or "brave").strip().lower(),
                               "api_key": key}
    except Exception:
        cfg = None
    if cfg and cfg.get("provider") not in _VALID_PROVIDERS:
        cfg["provider"] = "brave"
    _SEARCH_CACHE["cfg"] = cfg
    return cfg


def invalidate_search_config() -> None:
    _SEARCH_CACHE.pop("cfg", None)


def set_search_config(provider: str, api_key: str) -> dict:
    """产品内保存搜索配置(写 ~/.karvyloop/search.json;真 key 只落本地,不进 repo)。
    provider 空 / key 空 → 视为"清除"(回到 keyless)。返回脱敏后的公开态。"""
    import json
    sp = _search_store_path()
    provider = (provider or "").strip().lower()
    api_key = (api_key or "").strip()
    sp.parent.mkdir(parents=True, exist_ok=True)
    if not api_key or provider not in _VALID_PROVIDERS:
        try:
            sp.unlink(missing_ok=True)   # 清除 → 回 keyless
        except Exception:
            pass
    else:
        sp.write_text(json.dumps({"provider": provider, "api_key": api_key}, ensure_ascii=False), encoding="utf-8")
    invalidate_search_config()
    return get_search_config_public()


def get_search_config_public() -> dict:
    """公开态(给设置 UI):只说**用哪个 provider、配没配 key、从哪来**,绝不回传 key 明文。"""
    cfg = _search_config()
    if not cfg:
        return {"provider": "", "has_key": False, "mode": "keyless"}
    return {"provider": cfg.get("provider", ""), "has_key": True, "mode": "keyed"}


async def _search_brave(query: str, key: str, limit: int) -> list[dict]:
    """Brave Search API → [{title,url,snippet}]。https://api.search.brave.com (X-Subscription-Token)。"""
    import httpx
    from urllib.parse import quote_plus
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers={
        "Accept": "application/json", "X-Subscription-Token": key, "User-Agent": _UA,
    }) as client:
        r = await client.get(f"https://api.search.brave.com/res/v1/web/search?q={quote_plus(query)}&count={limit}")
        r.raise_for_status()
        web = (r.json().get("web") or {}).get("results") or []
        return [{"title": x.get("title", ""), "url": x.get("url", ""),
                 "snippet": x.get("description", "")} for x in web[:limit]]


async def _search_tavily(query: str, key: str, limit: int) -> list[dict]:
    """Tavily(面向 LLM 的搜索)→ [{title,url,snippet}]。POST api.tavily.com/search。"""
    import httpx
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
        r = await client.post("https://api.tavily.com/search", json={
            "api_key": key, "query": query, "max_results": limit, "search_depth": "basic"})
        r.raise_for_status()
        return [{"title": x.get("title", ""), "url": x.get("url", ""),
                 "snippet": x.get("content", "")} for x in (r.json().get("results") or [])[:limit]]


async def _http_get(url: str) -> tuple[bool, str]:
    """GET url → (ok, text_or_error)。无 httpx / 网络失败 → (False, 人话原因)。"""
    try:
        import httpx
    except Exception:
        return False, "httpx 未安装,无法联网"
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, max_redirects=5,
            headers={"User-Agent": _UA},
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            return True, r.text
    except Exception as e:  # 网络/超时/4xx/5xx 一律降级
        return False, f"{type(e).__name__}: {e}"


class WebFetchTool:
    name = "web_fetch"
    description = ("Fetch a web page (or text/JSON URL) over HTTPS and return its readable "
                  "text content. Use to read a specific page found via web_search or given by the user — "
                  "especially to verify time-sensitive facts (news, prices, versions, docs) at the source "
                  "instead of answering from possibly-stale memory.")
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "http(s) URL to fetch"},
            "max_chars": {"type": "integer", "default": _MAX_CHARS},
        },
        "required": ["url"],
    }

    def __init__(self, sandbox=None, file_state=None, workspace_root: str = "/",
                 *, token: CapabilityToken | None = None):
        self.token = token   # web 工具不用沙箱/工作区,仅为统一工厂签名

    def is_concurrency_safe(self, inp: dict) -> bool:
        return True   # 只读网络,安全

    async def __call__(self, inp: dict) -> CodingResult:
        url = str(inp.get("url", "") or "").strip()
        if not re.match(r"^https?://", url, re.I):
            return CodingResult(ok=False, payload=None, error_code=1,
                                error_message="url 必须是 http(s):// 开头")
        ok, body = await _http_get(url)
        if not ok:
            return CodingResult(ok=False, payload=None, error_code=4,
                                error_message=f"抓取失败:{body}")
        max_chars = int(inp.get("max_chars", _MAX_CHARS) or _MAX_CHARS)
        text = _strip_html(body) if "<" in body[:2000] and ">" in body[:2000] else body.strip()
        truncated = len(text) > max_chars
        return CodingResult(ok=True, payload=text[:max_chars], truncated=truncated)


def _parse_ddg(html_text: str, limit: int) -> list[dict]:
    """从 DuckDuckGo HTML 结果页抽 (title,url,snippet)。解析不出 → [](不编)。"""
    out: list[dict] = []
    # 结果块:<a class="result__a" href="...">title</a> ... <a class="result__snippet">snippet</a>
    for m in re.finditer(r'(?is)<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html_text):
        href, title = m.group(1), _strip_html(m.group(2))
        # DDG 跳转链接 uddg= 真 URL
        u = re.search(r"[?&]uddg=([^&]+)", href)
        if u:
            try:
                from urllib.parse import unquote
                href = unquote(u.group(1))
            except Exception:
                pass
        if href and title:
            out.append({"title": title, "url": href})
        if len(out) >= limit:
            break
    # 配上 snippet(顺序对齐,缺就空)
    snips = [_strip_html(s) for s in re.findall(r'(?is)<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', html_text)]
    for i, item in enumerate(out):
        item["snippet"] = snips[i] if i < len(snips) else ""
    return out


class WebSearchTool:
    name = "web_search"
    description = ("Search the web and return top results as title · url · snippet. "
                  "You MUST use this for anything time-sensitive or likely to have changed since your "
                  "training data: current events / news, prices, exchange rates, stocks, weather, sports "
                  "scores, latest software versions or releases, and any question about 'today', 'now', "
                  "'latest' or the current year — your memory of these is stale. Also use it FIRST whenever "
                  "the question needs facts outside your own knowledge, then web_fetch the most relevant "
                  "result to read it. Do NOT use it for local files, the user's own workspace, or stable "
                  "knowledge (concepts, history, how-to). Never invent results; if search fails, say so "
                  "honestly instead of answering from memory.")
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": _MAX_RESULTS},
        },
        "required": ["query"],
    }

    def __init__(self, sandbox=None, file_state=None, workspace_root: str = "/",
                 *, token: CapabilityToken | None = None):
        self.token = token

    def is_concurrency_safe(self, inp: dict) -> bool:
        return True

    def _format(self, results: list[dict]) -> CodingResult:
        if not results:
            return CodingResult(ok=True, payload="(没搜到结果;换个关键词,或直接 web_fetch 已知 URL)",
                                truncated=False)
        lines = [f"{i+1}. {r.get('title','')}\n   {r.get('url','')}\n   {r.get('snippet','')}".rstrip()
                 for i, r in enumerate(results)]
        return CodingResult(ok=True, payload="\n".join(lines), truncated=False)

    async def __call__(self, inp: dict) -> CodingResult:
        query = str(inp.get("query", "") or "").strip()
        if not query:
            return CodingResult(ok=False, payload=None, error_code=1, error_message="query 为空")
        limit = max(1, min(int(inp.get("max_results", _MAX_RESULTS) or _MAX_RESULTS), 10))
        # ① 配了搜索 API key → 用它(更稳/更优);失败/没配 → ② keyless DuckDuckGo 兜底
        cfg = _search_config()
        if cfg:
            try:
                if cfg["provider"] == "tavily":
                    kr = await _search_tavily(query, cfg["api_key"], limit)
                else:
                    kr = await _search_brave(query, cfg["api_key"], limit)
                if kr:
                    return self._format(kr)
            except Exception:
                pass   # 配置的 provider 出错 → 落到 DDG,不让搜索整个挂掉
        from urllib.parse import quote_plus
        ok, body = await _http_get("https://html.duckduckgo.com/html/?q=" + quote_plus(query))
        if not ok:
            return CodingResult(ok=False, payload=None, error_code=4,
                                error_message=f"搜索失败:{body}(可重试或改用 web_fetch 已知 URL)")
        return self._format(_parse_ddg(body, limit))


__all__ = ["WebFetchTool", "WebSearchTool"]
