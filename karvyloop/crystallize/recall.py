"""recall — 召回(快脑/慢脑 路由)（crystallize/recall.py）。

规格:docs/modules/crystallize.md §3 recall.py + §4 HR-7
- 命中已结晶技能 → 快脑(直接调用,不走 ReAct)
- 未命中 → 慢脑(完整 ReAct,通过 forge)
- HR-7:召回时 verify_proof + trace_refs 必须存在(否则 fail-closed 拒)
- M1.5:auto-restore —— 命中归档技能时自动从 store.restore(可逆 evict 的入口)

M1.5 升级:
  - 召回策略 v1.1:intent token 经同义词+月份+停用词归一再算 overlap
  - auto-restore:命中项的 sig 处于 store.archived → 自动调 store.restore,
    把"刚恢复"的事实返回给 caller(RecallHit.restored 字段)
  - 接受 SkillIndex:有就优先用它(快、不读盘);没有再走磁盘兜底
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from karvyloop.registry.skills import parse_frontmatter

from .signature import _intent_cluster
from .skill_index import SkillIndex
from .store import UsageStore


_STOP_DIGITS = re.compile(r"\d+")
_STOP_PUNCT = re.compile(r"[^\w\s]+")
_STOP_WS = re.compile(r"\s+")


def _tokenize(text: str) -> set[str]:
    s = (text or "").lower()
    s = _STOP_DIGITS.sub(" ", s)
    s = _STOP_PUNCT.sub(" ", s)
    s = _STOP_WS.sub(" ", s).strip()
    return set(s.split())


def _load_skill_index(skills_dir: Path) -> list[dict]:
    """从 skills_dir 读所有 SKILL.md,构造轻量索引(无 SkillIndex 时的兜底)。

    返回:[{name, when_tokens, desc_tokens, scope, manifest, body, path, sig}, ...]
    """
    out: list[dict] = []
    if not skills_dir.is_dir():
        return out
    for p in sorted(skills_dir.glob("*/SKILL.md")):
        try:
            fm, body = parse_frontmatter(p)
        except OSError:
            continue
        if not fm.name:
            continue
        when_tokens = _tokenize(fm.when_to_use)
        desc_tokens = _tokenize(fm.description)
        all_tokens = when_tokens | desc_tokens
        out.append({
            "name": fm.name,
            "when_tokens": when_tokens,
            "desc_tokens": desc_tokens,
            "all_tokens": all_tokens,
            "scope": fm.scope or "user",
            "raw": fm.raw or {},
            "body": body,
            "path": str(p),
            "sig": fm.signature,
            "result_reuse": fm.result_reuse or "dynamic",
        })
    return out


@dataclass
class RecallHit:
    """召回命中(供快脑使用)。"""
    name: str
    body: str
    path: str
    score: float  # 匹配度(M1 简版:交集 token 数 / 意图 token 数)
    manifest: dict
    # M1.5:auto-restore 标识 —— 命中项原本在归档、被自动恢复 → True
    # caller 可据此打一条"已复活技能 X"的提示给用户
    restored: bool = False
    sig: str = ""  # 命中 sig(SkillIndex 命中时填;兜底路径无 sig 也允许空)
    # #2 §13:'dynamic'(默认)=命中重跑不回放;'stable'=可回放缓存结果
    result_reuse: str = "dynamic"


def load_bound_skills(
    names: list[str],
    *,
    skills_dir: Path,
    skill_index: Optional[SkillIndex] = None,
) -> list[RecallHit]:
    """加载角色**显式绑定**的技能(COMPOSITION.yaml `skills:`)—— 绑定即生效,不靠模糊召回。

    与 recall 的区别:recall 凭意图 token 碰匹配(碰运气);这里按名字**直接取**,
    保证一个角色随身声明的技能一定在场(docs/00 §2.2 + 角色"用不拥有"技能)。
    名字查不到的技能静默跳过(可能被归档/删除;不阻断 drive)。score=1.0(绑定=满信)。
    """
    if not names:
        return []
    want = list(dict.fromkeys(n for n in names if n))  # 去重保序
    by_name: dict[str, dict] = {}
    if skill_index is not None and len(skill_index) > 0:
        for entry in skill_index.all():
            if entry.name in want and entry.name not in by_name:
                by_name[entry.name] = {"path": entry.path, "sig": entry.sig}
    # 索引没覆盖到的,扫盘兜底(按目录名 == 技能名约定)
    for nm in want:
        if nm in by_name:
            continue
        p = Path(skills_dir) / nm / "SKILL.md"
        if p.is_file():
            by_name[nm] = {"path": str(p), "sig": ""}
    out: list[RecallHit] = []
    for nm in want:
        info = by_name.get(nm)
        if info is None:
            continue
        try:
            fm, body = parse_frontmatter(Path(info["path"]))
        except OSError:
            continue
        out.append(RecallHit(name=nm, body=body, path=info["path"], score=1.0,
                             manifest=fm.raw or {}, sig=info.get("sig", ""),
                             result_reuse=fm.result_reuse or "dynamic"))
    return out


def recall(
    intent: str,
    *,
    skills_dir: Path,
    scope: str = "user",
    store: Optional[UsageStore] = None,
    skill_index: Optional[SkillIndex] = None,
    prefer: Optional[list[str]] = None,
    satisfaction: Optional[object] = None,
) -> Optional[RecallHit]:
    """按意图召回一个最匹配的已结晶技能;没命中 → None(走慢脑)。

    匹配规则 v1.1:intent 经 normalize 后取 token 集,跟每个 skill 的
    when_to_use+description token 集求交集;交集非空 + scope 一致即命中;
    取交集覆盖度最大者。

    skill_index:有就优先用它(快、不读盘);没有走 _load_skill_index 兜底。
    store:有就启用 auto-restore —— 命中项若已归档,自动调 store.restore。
    prefer:当前角色**绑定**的技能名 —— 这些技能即便意图 token 弱匹配也给 +0.5 加权,
      让角色随身声明的技能在打平/接近时胜出(绑定优先于碰运气,但不绕过 scope)。
    """
    intent_tokens = _tokenize(_intent_cluster(intent))
    if not intent_tokens:
        return None
    prefer_set = set(prefer or [])

    candidates: list[dict] = []
    if skill_index is not None and len(skill_index) > 0:
        for entry in skill_index.all():
            if entry.scope != scope:
                continue
            try:
                _fm, body = parse_frontmatter(Path(entry.path))
            except OSError:
                continue
            all_tokens = _tokenize(entry.when_to_use) | _tokenize(entry.description)
            candidates.append({
                "name": entry.name,
                "body": body,
                "path": entry.path,
                "all_tokens": all_tokens,
                "raw": _fm.raw or {},
                "sig": entry.sig,
                # §13:SkillIndex 不存 result_reuse,从盘上 frontmatter 读(否则恒 dynamic、stable 永不回放)
                "result_reuse": _fm.result_reuse or "dynamic",
            })
    else:
        for c in _load_skill_index(skills_dir):
            if c["scope"] != scope:
                continue
            candidates.append({
                "name": c["name"],
                "body": c["body"],
                "path": c["path"],
                "all_tokens": c["all_tokens"],
                "raw": c["raw"],
                "sig": c.get("sig", ""),
            })

    best: Optional[RecallHit] = None
    # 排序键 = (意图匹配主分, 满意度裁决分)。**字典序**:意图匹配**绝对优先**,满意度
    # **只在意图打平时**裁决 —— 绝不能盖过真实的匹配差(对抗验收 MEDIUM:+0.3 加权曾能
    # 翻盘 20-30% 的匹配差 = 召回错技能;改成严格平手裁决兑现"只在打平时")。
    best_key: tuple[float, float] = (-1.0, -1.0)
    for c in candidates:
        overlap = intent_tokens & c["all_tokens"]
        if not overlap:
            continue
        primary = len(overlap) / max(1, len(intent_tokens))
        if c["name"] in prefer_set:
            primary += 0.5  # 绑定优先仍属"意图/归属"主分(不绕 scope,只加权)
        # docs/40:满意度回到行为 —— 意图打平时,role 评出来"更管用"的技能胜出(新近度加权,抗滞后)。
        # 无满意度 store / 无样本 → 0(优雅降级);异常吞掉不拖垮召回。
        secondary = 0.0
        if satisfaction is not None and c.get("sig"):
            try:
                # 置信分(贝叶斯收缩):用得少的技能往先验缩,不靠几次走运的高均值抢召回。
                sat = satisfaction.confidence_overall(c["sig"])
                if sat is not None:
                    secondary = float(sat)
            except Exception:
                pass
        key = (primary, secondary)
        if key > best_key:
            best = RecallHit(
                name=c["name"],
                body=c["body"],
                path=c["path"],
                score=primary,
                manifest=c["raw"],
                sig=c.get("sig", ""),
                result_reuse=c.get("result_reuse", "dynamic"),
            )
            best_key = key

    # auto-restore:命中项若在 store 归档集合里 → 翻出来
    if best is not None and store is not None and best.sig:
        if store.is_archived(best.sig):
            store.restore(best.sig)
            best.restored = True
        # 拍 9:每次召回命中都 +1(recall_count 是真"用进"信号,
        # 比 usage_count 更准;evict 应优先看这个)。
        store.recall_count_inc(best.sig)
    return best


__all__ = ["RecallHit", "recall", "load_bound_skills"]
