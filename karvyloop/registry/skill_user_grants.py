"""skill_user_grants — 用户对技能的显式授权(P1:第三方按需授网)。

第三方技能默认拒网(skill_grants 信任收口)。但很多 API 类技能联网才有用 —— 用户可**逐个授权**。
授权是**人的决定**,且**与技能本体分开存**(放 ~/.karvyloop/skill_grants.json,不写进 SKILL.md)——
这样重新导入/技能更新都不会悄悄改动你给过的授权,也不让技能自己往 frontmatter 里塞个 flag 提权。

存储:{ "<skill-name>": {"net": true}, ... }。宁缺勿崩:文件坏/缺 → 当作"无任何授权"。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class SkillUserGrants:
    def __init__(self, path) -> None:
        self._path = Path(path)
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self._path.is_file():
                obj = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    self._data = {k: v for k, v in obj.items() if isinstance(v, dict)}
        except Exception:
            self._data = {}   # 坏文件 → 当无授权(默认拒,fail-safe)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    def net_granted(self, name: str) -> bool:
        return bool(self._data.get(name, {}).get("net", False))

    def set_net(self, name: str, allowed: bool) -> None:
        entry = self._data.setdefault(name, {})
        entry["net"] = bool(allowed)
        if not allowed and entry == {"net": False}:
            # 收回到无任何授权 → 删条目保持干净
            self._data.pop(name, None)
        self._save()


def load_user_grants(skills_dir) -> Optional[SkillUserGrants]:
    """授权文件与 skills 同住 ~/.karvyloop/;给定 skills_dir 推出同级 skill_grants.json。"""
    try:
        return SkillUserGrants(Path(skills_dir).parent / "skill_grants.json")
    except Exception:
        return None


__all__ = ["SkillUserGrants", "load_user_grants"]
