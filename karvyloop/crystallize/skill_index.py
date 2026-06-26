"""SkillIndex — 已结晶技能的 sig ↔ name 双向索引（crystallize/skill_index.py）。

规格:docs/modules/crystallize.md §3 M1.5 + §4 保守可逆
- 在内存里维护 sig↔name 的双向映射
- 启动时从 skills_dir 扫描所有 SKILL.md,把 frontmatter.signature 写进映射
  (兜底无 signature 的旧技能 —— 不进索引,让 recall 落空走慢脑)
- 结晶(crystallize)成功后 register
- recall 命中归档技能时,辅助 restore(把 name 翻成 sig 给 store.restore)

设计意图:把所有"已结晶技能"的可发现信息集中在一个类,recall / auto_suggest /
管理面(list / delete)都从这里取。避免每次 recall 都 glob 磁盘。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from karvyloop.registry.skills import parse_frontmatter


@dataclass
class IndexEntry:
    """索引里一条记录(从 SKILL.md frontmatter 投影)。"""
    name: str
    sig: str
    scope: str
    when_to_use: str
    description: str
    path: str  # SKILL.md 绝对路径


@dataclass
class SkillIndex:
    """sig↔name 双向索引。

    内存结构:
      _by_sig:  sig -> name
      _by_name: name -> IndexEntry
    线程安全:本类不内置锁;recall / crystallize 都在主循环同一线程,
    后台 review 单独线程 —— 共享读多写少,加锁成本不必要(M1 v1 接受)。
    真要并发安全时再升级。
    """
    _by_sig: dict[str, str] = field(default_factory=dict)
    _by_name: dict[str, IndexEntry] = field(default_factory=dict)

    # ---- 启动重建 ----

    def rebuild_from_disk(self, skills_dir: Path) -> int:
        """扫描 skills_dir 下所有 SKILL.md,把有 signature 字段的收进索引。

        - 无 signature 的旧技能:不收(让 recall 落空;升级时一次写入即可)
        - 同 sig 多 name:取后写者胜(理论上不会发生 —— 同一 sig 必产同 name)
        - 同 name 多 sig:取先到者,后到的记入 stderr 风格的"覆盖告警"
          (实际不会发生 —— crystallize 对同 name 会拒)

        返回收进索引的条数。
        """
        self._by_sig.clear()
        self._by_name.clear()
        if not skills_dir.is_dir():
            return 0
        count = 0
        for p in sorted(skills_dir.glob("*/SKILL.md")):
            try:
                fm, _body = parse_frontmatter(p)
            except OSError:
                continue
            if not fm.name or not fm.signature:
                continue
            entry = IndexEntry(
                name=fm.name,
                sig=fm.signature,
                scope=fm.scope or "user",
                when_to_use=fm.when_to_use or "",
                description=fm.description or "",
                path=str(p),
            )
            self._by_name[fm.name] = entry
            self._by_sig[fm.signature] = fm.name
            count += 1
        return count

    # ---- 注册(结晶成功后)----

    def register(self, *, name: str, sig: str, scope: str,
                 when_to_use: str, description: str, path: str) -> None:
        """结晶/重建时把一条写进索引;同 name 覆盖、同 sig 覆盖。"""
        entry = IndexEntry(
            name=name, sig=sig, scope=scope,
            when_to_use=when_to_use, description=description, path=path,
        )
        self._by_name[name] = entry
        self._by_sig[sig] = name

    def unregister(self, name: str) -> None:
        """从索引移除(spec:evict 真删时用,默认 evict 不删盘所以不常用)。"""
        entry = self._by_name.pop(name, None)
        if entry is not None:
            self._by_sig.pop(entry.sig, None)

    # ---- 反查 ----

    def lookup_by_name(self, name: str) -> Optional[IndexEntry]:
        return self._by_name.get(name)

    def lookup_by_sig(self, sig: str) -> Optional[IndexEntry]:
        n = self._by_sig.get(sig)
        return self._by_name.get(n) if n else None

    def sig_for_name(self, name: str) -> Optional[str]:
        e = self._by_name.get(name)
        return e.sig if e else None

    def name_for_sig(self, sig: str) -> Optional[str]:
        return self._by_sig.get(sig)

    def all(self) -> list[IndexEntry]:
        return list(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name


__all__ = ["SkillIndex", "IndexEntry"]
