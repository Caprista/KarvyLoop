"""skill_catalog — 技能目录浏览(P1-b):官方仓库 + 市场,统一成可一键导入的条目。

Hardy 拍"两个都接"。两源最终都收敛成一个 `source` 串(喂给 skill_import.import_skill):
  - **官方** anthropics/skills:GitHub contents API 枚举 skills/<name>,读各自 SKILL.md 的 description。
  - **市场** SkillsMP:`GET /api/v1/skills/search?q=`,返回 data.skills[] 里每条带 githubUrl
    (https://github.com/owner/repo/tree/<ref>/<path>)—— 正好是 importer 的 _parse_github 吃的格式。

只读、不执行;网络可注入(测试不触网)。失败返回空列表,不崩(浏览不该挡住别的)。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from typing import Callable, Optional

Fetch = Callable[[str], bytes]

OFFICIAL_REPO = "anthropics/skills"
OFFICIAL_ROOT = "skills"
_SKILLSMP_SEARCH = "https://skillsmp.com/api/v1/skills/search?q="
_DESC_RE = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)


@dataclass
class CatalogEntry:
    name: str
    description: str
    source: str       # 直接喂 import_skill 的 spec
    origin: str       # "official" | "market"
    author: str = ""
    stars: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _default_fetch(url: str) -> bytes:
    import httpx
    r = httpx.get(url, timeout=20.0, follow_redirects=True,
                  headers={"User-Agent": "karvyloop-skill-catalog", "Accept": "application/json"})
    r.raise_for_status()
    return r.content


def _desc_from_skill_md(text: str) -> str:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    block = m.group(1) if m else text
    d = _DESC_RE.search(block)
    return (d.group(1).strip().strip('"').strip("'") if d else "")[:300]


def browse_github_repo(repo: str, root: str = "skills", *, origin: str = "github",
                       author: str = "", query: str = "", fetch: Optional[Fetch] = None,
                       ref: str = "main", limit: int = 40) -> list[CatalogEntry]:
    """枚举任意 GitHub 仓库 `repo` 下 `root/` 里的技能子目录(读各自 SKILL.md description)。

    通用化:任何"一堆 SKILL.md 子目录"的仓库都能当检索源(可配置源用)。
    """
    fetch = fetch or _default_fetch
    out: list[CatalogEntry] = []
    try:
        listing = json.loads(fetch(
            f"https://api.github.com/repos/{repo}/contents/{root}?ref={ref}").decode("utf-8"))
    except Exception:
        return out
    if not isinstance(listing, list):
        return out
    q = (query or "").strip().lower()
    for item in listing:
        if item.get("type") != "dir":
            continue
        name = item.get("name", "")
        if not name:
            continue
        desc = ""
        try:
            raw = fetch(f"https://raw.githubusercontent.com/{repo}/{ref}/"
                        f"{root}/{name}/SKILL.md").decode("utf-8", "replace")
            desc = _desc_from_skill_md(raw)
        except Exception:
            pass   # 没 SKILL.md / 取失败 → 条目仍在,描述空
        if q and q not in name.lower() and q not in desc.lower():
            continue
        out.append(CatalogEntry(name=name, description=desc,
                                source=f"{repo}/{root}/{name}",
                                origin=origin, author=author or repo.split("/")[0]))
        if len(out) >= limit:
            break
    return out


def browse_official(*, query: str = "", fetch: Optional[Fetch] = None,
                    limit: int = 40) -> list[CatalogEntry]:
    """官方 anthropics/skills(browse_github_repo 的便捷封装)。"""
    return browse_github_repo(OFFICIAL_REPO, OFFICIAL_ROOT, origin="official",
                              author="anthropics", query=query, fetch=fetch, limit=limit)


def search_marketplace(query: str, *, fetch: Optional[Fetch] = None,
                       limit: int = 30) -> list[CatalogEntry]:
    """SkillsMP 关键词搜;每条的 githubUrl 即一键导入的 source。"""
    fetch = fetch or _default_fetch
    q = (query or "").strip()
    if not q:
        return []
    out: list[CatalogEntry] = []
    try:
        import urllib.parse as _up
        data = json.loads(fetch(_SKILLSMP_SEARCH + _up.quote(q)).decode("utf-8"))
    except Exception:
        return out
    skills = (((data or {}).get("data") or {}).get("skills")) or []
    if not isinstance(skills, list):
        return out
    for s in skills[:limit]:
        gh = s.get("githubUrl") or ""
        if not gh:
            continue   # 没源 = 导不了,跳过
        try:
            stars = int(s.get("stars") or 0)
        except (ValueError, TypeError):
            stars = 0
        out.append(CatalogEntry(
            name=str(s.get("name", "")), description=str(s.get("description", ""))[:300],
            source=gh, origin="market", author=str(s.get("author", "")), stars=stars))
    return out


def browse_source(src: dict, *, query: str = "", fetch: Optional[Fetch] = None) -> list[CatalogEntry]:
    """按一条**可配置检索源**(dict)取技能。type:github|skillsmp。未知/未启用 → 空。"""
    if not src or not src.get("enabled", True):
        return []
    stype = src.get("type")
    if stype == "github":
        repo = src.get("repo", "")
        if not repo:
            return []
        return browse_github_repo(repo, src.get("root", "skills"),
                                  origin=src.get("id") or "github",
                                  query=query, fetch=fetch, ref=src.get("ref", "main"))
    if stype == "skillsmp":
        return search_marketplace(query, fetch=fetch) if (query or "").strip() else []
    return []


def search_catalog(query: str = "", *, source: str = "all",
                   sources: Optional[list[dict]] = None,
                   fetch: Optional[Fetch] = None) -> list[CatalogEntry]:
    """合并浏览。

    - 传 `sources`(可配置源列表)→ 只走其中 enabled 的(按 `source` 再过滤:all / 某源 id / 'official' / 'market')。
    - 不传 → 回退默认(官方 anthropics + 市场 SkillsMP),保持老行为。market 需 query。
    """
    entries: list[CatalogEntry] = []
    if sources is not None:
        for src in sources:
            if not src.get("enabled", True):
                continue
            sid = src.get("id", "")
            stype = src.get("type", "")
            # source 过滤:'all' 全要;'official'/'market' 按语义;否则按源 id 精确
            if source not in ("all", sid):
                if source == "market" and stype != "skillsmp":
                    continue
                if source == "official" and sid != "official":
                    continue
                if source not in ("market", "official"):
                    continue
            entries.extend(browse_source(src, query=query, fetch=fetch))
        return entries
    # 回退:默认两源
    if source in ("all", "official"):
        entries.extend(browse_official(query=query, fetch=fetch))
    if source in ("all", "market") and (query or "").strip():
        entries.extend(search_marketplace(query, fetch=fetch))
    return entries


__all__ = ["CatalogEntry", "browse_official", "browse_github_repo",
           "search_marketplace", "browse_source", "search_catalog"]
