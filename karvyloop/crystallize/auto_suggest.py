"""auto_suggest — 未命中召回时的"相近技能"推荐（crystallize/auto_suggest.py）。

规格:docs/modules/crystallize.md §5.1 M1.5
- 当 recall 没找到足够匹配(>阈值)的技能时,主循环会走慢脑;
  但用户可能想看到"我有 X、Y、Z 跟你说的有点像"的提示 —— 这就是
  auto_suggest 的位置
- 与 recall 区别:
    recall       → 只返回 Top-1 命中(快脑/慢脑二选一)
    auto_suggest → 返回 Top-N(默认 3),给"我有什么"清单或"旁路建议"用
- 评分:复用 recall 的 token overlap,再叠加 store 的 usage_score 作为
  "熟悉度"信号(常用技能优先浮出)
- 归档技能不在主建议里(它们是"我曾经有"而不是"我现在有"),但 caller
  可以通过 include_archived=True 拿到(罕见场景,例如管理面)

设计意图:把 recall 跟 suggest 解耦 —— 主循环默认不调 suggest(只调 recall),
仅在用户问"我有什么技能"或"和这个差不多的还有别的吗"时才走 suggest。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .crystallize import usage_score
from .signature import _intent_cluster
from .skill_index import SkillIndex
from .store import UsageStore


# 复用 recall 的 tokenize(简单一致;不强求共享函数以避免循环 import)
_STOP_DIGITS = re.compile(r"\d+")
_STOP_PUNCT = re.compile(r"[^\w\s]+")
_STOP_WS = re.compile(r"\s+")


def _tokenize(text: str) -> set[str]:
    s = (text or "").lower()
    s = _STOP_DIGITS.sub(" ", s)
    s = _STOP_PUNCT.sub(" ", s)
    s = _STOP_WS.sub(" ", s).strip()
    return set(s.split())


@dataclass
class SuggestHit:
    """auto_suggest 返回的一项(给上层"旁路建议"或"我有什么"用)。"""
    name: str
    sig: str
    scope: str
    score: float  # 意图匹配度(0~1)
    usage_score: float  # 熟悉度(由 store.usage_score 算)
    is_archived: bool
    when_to_use: str
    description: str


def auto_suggest(
    intent: str,
    *,
    skills_dir: Path,
    scope: str = "user",
    store: Optional[UsageStore] = None,
    skill_index: Optional[SkillIndex] = None,
    top_n: int = 3,
    min_score: float = 0.0,
    include_archived: bool = False,
    now: Optional[float] = None,
) -> list[SuggestHit]:
    """返回 Top-N 相近技能;默认不含归档(主建议位);可入参放开。

    - skill_index 有就优先用(快)
    - store 有就把每个 sig 的 usage_score 算上,作为熟悉度加权
      (但不影响 sort 主键 —— sort 仍是 match score 降序;
       usage_score 仅作为展示字段,让用户能区分"老用"和"刚结晶")
    """
    import time
    if now is None:
        now = time.time()

    intent_tokens = _tokenize(_intent_cluster(intent))
    if not intent_tokens:
        return []

    entries: list[tuple] = []  # (name, sig, scope, all_tokens, when, desc)
    if skill_index is not None and len(skill_index) > 0:
        for e in skill_index.all():
            entries.append((
                e.name, e.sig, e.scope,
                _tokenize(e.when_to_use) | _tokenize(e.description),
                e.when_to_use, e.description,
            ))
    else:
        # 兜底:无 SkillIndex 时,走 _load_skill_index 等价
        from karvyloop.registry.skill_lock import reject_tampered_untrusted
        from karvyloop.registry.skills import parse_frontmatter
        if skills_dir.is_dir():
            for p in sorted(skills_dir.glob("*/SKILL.md")):
                try:
                    fm, _ = parse_frontmatter(p)
                except OSError:
                    continue
                if not fm.name:
                    continue
                # 完整性锁:扫盘兜底不收被篡改的 untrusted 技能(与 recall/_scan_dir 同门)
                if reject_tampered_untrusted(skills_dir, p.parent.name, fm.raw or {}):
                    continue
                entries.append((
                    fm.name, fm.signature or "", fm.scope or "user",
                    _tokenize(fm.when_to_use) | _tokenize(fm.description),
                    fm.when_to_use, fm.description,
                ))

    hits: list[SuggestHit] = []
    for name, sig, sc, all_tokens, when, desc in entries:
        if sc != scope:
            continue
        overlap = intent_tokens & all_tokens
        s = len(overlap) / max(1, len(intent_tokens))
        if s < min_score:
            continue
        is_arch = bool(store and sig and store.is_archived(sig))
        if is_arch and not include_archived:
            continue
        u_score = 0.0
        if store is not None and sig:
            stats = store.get(sig)
            if stats is not None:
                u_score = usage_score(stats, now=now)
        hits.append(SuggestHit(
            name=name, sig=sig, scope=sc,
            score=s, usage_score=u_score,
            is_archived=is_arch,
            when_to_use=when, description=desc,
        ))

    # sort:match score 降序 → 同分按 usage_score 降序(老用的更前)
    hits.sort(key=lambda h: (-h.score, -h.usage_score, h.name))
    return hits[:top_n]


__all__ = ["SuggestHit", "auto_suggest"]
