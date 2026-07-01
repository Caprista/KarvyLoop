"""skill_sources — 可配置的技能检索源(增删改 + 开关)。

Hardy:目录检索源不能写死,要能增/删/改,每个源带开关(源太多会拖慢检索);**至少留一个开**,
否则不让保存。默认 seed 两个:官方 anthropics/skills + 市场 SkillsMP(都可开关、可删)。

存 ~/.karvyloop/skill_sources.json。源 schema:
  { "id": str, "label": str, "type": "github"|"skillsmp",
    "enabled": bool, "repo": "owner/repo"(github), "root": "skills"(github), "ref": "main"(github) }
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

DEFAULT_SOURCES: list[dict] = [
    {"id": "official", "label": "Anthropic 官方库 · anthropics/skills", "type": "github",
     "repo": "anthropics/skills", "root": "skills", "ref": "main", "enabled": True},
    {"id": "skillsmp", "label": "市场 · SkillsMP", "type": "skillsmp", "enabled": True},
]

_ID_RE = re.compile(r"^[\w\-]{1,40}$")
_VALID_TYPES = {"github", "skillsmp"}


def _clean_source(s: dict) -> Optional[dict]:
    """消毒/校验一条源;非法 → None。"""
    if not isinstance(s, dict):
        return None
    sid = str(s.get("id", "")).strip()
    stype = str(s.get("type", "")).strip()
    if not _ID_RE.match(sid) or stype not in _VALID_TYPES:
        return None
    out = {"id": sid, "type": stype, "enabled": bool(s.get("enabled", True)),
           "label": str(s.get("label", "") or sid)[:80]}
    if stype == "github":
        repo = str(s.get("repo", "")).strip()
        # 只接受 owner/repo 形态(防注入/乱填)
        if not re.match(r"^[\w.\-]+/[\w.\-]+$", repo):
            return None
        out["repo"] = repo
        out["root"] = str(s.get("root", "skills")).strip().strip("/") or "skills"
        out["ref"] = str(s.get("ref", "main")).strip() or "main"
    return out


class SkillSources:
    def __init__(self, path) -> None:
        self._path = Path(path)
        self._sources: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            if self._path.is_file():
                obj = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(obj, list):
                    self._sources = [c for c in (_clean_source(s) for s in obj) if c]
        except Exception:
            self._sources = []
        if not self._sources:
            # 空/坏/首次 → seed 默认(深拷贝避免外部改到常量)
            self._sources = [dict(s) for s in DEFAULT_SOURCES]

    def list(self) -> list[dict]:
        return [dict(s) for s in self._sources]

    def enabled(self) -> list[dict]:
        return [dict(s) for s in self._sources if s.get("enabled", True)]

    def save(self, sources: list[dict]) -> tuple[bool, str]:
        """整表替换保存。校验:每条合法 + id 不重 + **至少一个 enabled**。失败不落盘。"""
        if not isinstance(sources, list) or not sources:
            return False, "检索源不能为空(至少保留一个)"
        cleaned: list[dict] = []
        seen: set[str] = set()
        for s in sources:
            c = _clean_source(s)
            if c is None:
                return False, f"非法的检索源:{s.get('id', s)}(检查 type / repo 格式)"
            if c["id"] in seen:
                return False, f"检索源 id 重复:{c['id']}"
            seen.add(c["id"])
            cleaned.append(c)
        if not any(c.get("enabled") for c in cleaned):
            return False, "至少要开启一个检索源(否则没法检索)"
        self._sources = cleaned
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
        return True, ""


def load_skill_sources(skills_dir) -> Optional[SkillSources]:
    try:
        return SkillSources(Path(skills_dir).parent / "skill_sources.json")
    except Exception:
        return None


__all__ = ["SkillSources", "load_skill_sources", "DEFAULT_SOURCES"]
