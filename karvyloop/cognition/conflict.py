"""cognition.conflict — 记忆冲突消解（cognition/conflict.py）。

规格：docs/modules/cognition-memory.md §3 conflict.py + §4 "最新 + 最高 provenance 胜"
- 记忆可靠性三指标:provenance / freshness / conflict(矛盾标记)
- 消解:max(freshness_ts, provenance_rank)
- 矛盾标记:同 content 不同 provenance 留下的冲突由后台 review 处理

**写入路径 supersede(生产接线,不再是死代码)**:`run_supersede_pass` 在
ingest/auto_distill 写入新 Belief 后被调——用已有召回栈(overlap_score+概念标签,无向量)
找 top-k 相似旧条 → 一次便宜 LLM 判"矛盾/更新/无关"(严格 JSON,宁空勿毒,解析失败=
当无关不动旧条)→ 矛盾/更新:给**输的那条**打 `invalid_at`(失效不删,保历史可审计)。
谁输谁赢由 `provenance_rank` 把关:人明说的(user_explicit/ingest)盖过对话蒸馏猜的
(distill_extracted/conversation)——低权威的新条**不能**掀翻高权威的旧条,反被打失效。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from karvyloop.schemas import Belief

logger = logging.getLogger(__name__)


# ---- provenance 排序权重(越高越权威)----
PROVENANCE_RANK = {
    "user_explicit": 100,    # 用户明确告知(打字 / 文件)
    "trace_verified": 80,   # Trace + 通过验证门
    "trace_observed": 60,   # Trace 投影出来(默认)
    "distill_extracted": 40,  # 后台蒸馏小模型抽取
    "imported": 20,         # 导入
    "unknown": 0,
}

# 生产里真实写进 provenance.source 的取值 → 权威档位别名。
# (雷达实锤:原表只有抽象档位名,而 ingest/auto_distill 写的是 "ingest"/"conversation"…
# → provenance_rank 对所有真实数据一律返 0,权威表形同虚设。)
_SOURCE_ALIAS = {
    "ingest": "user_explicit",        # 用户显式喂料(/memory/feed 摄入编译)
    "knowledge": "user_explicit",     # 喂料蒸馏流人审后 persist 的通用知识
    "user": "user_explicit",
    "consolidated": "trace_verified",  # 知识合并条(人 ACCEPT 过 = 过了人审门)
    "roundtable": "trace_observed",   # 圆桌沉淀(系统观察产物)
    "conversation": "distill_extracted",  # 对话自动蒸馏(猜的,低权威)
    "import": "imported",
}


def provenance_rank(provenance: dict) -> int:
    """按 provenance.source 查权重;缺/未知 → 0。

    - 真实 source 值(ingest/conversation/…)经 `_SOURCE_ALIAS` 归到抽象档位。
    - `provenance["provisional"]=True`(auto 蒸的、没过人审)→ 权威封顶 distill_extracted:
      无人审直接写库的条目不与人审沉淀的知识同权。
    """
    prov = provenance or {}
    src = prov.get("source", "unknown")
    src = _SOURCE_ALIAS.get(src, src)
    rank = PROVENANCE_RANK.get(src, 0)
    if prov.get("provisional"):
        rank = min(rank, PROVENANCE_RANK["distill_extracted"])
    return rank


@dataclass
class ConflictReport:
    """冲突消解结果:胜出的 Belief + 被压制的 Belief 列表。"""
    winner: Belief
    losers: list[Belief]
    # 同 content 是否还有别的版本(用于后台 review 触发"矛盾标记")
    has_conflict: bool = False


def resolve(beliefs: Iterable[Belief]) -> Optional[Belief]:
    """单组矛盾 Belief 消解:winner = max(freshness_ts, provenance_rank)。

    beliefs 必须表达同一论断(上层按 content/语义聚类后再调)。
    """
    items = list(beliefs)
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    return max(items, key=lambda b: (b.freshness_ts, provenance_rank(b.provenance)))


def detect_conflict(beliefs: Iterable[Belief]) -> ConflictReport:
    """消解 + 矛盾标记。

    has_conflict = True 当有 ≥2 个 Belief 不完全相同(freshness_ts 或 content 不同)。
    """
    items = list(beliefs)
    if not items:
        raise ValueError("detect_conflict 需要至少一个 Belief")
    winner = resolve(items)
    assert winner is not None
    losers = [b for b in items if b is not winner]
    # 矛盾标记:仅当 losers 非空(意味着有不同时间/不同 provenance 的同主题记忆)
    has_conflict = len(losers) > 0
    return ConflictReport(winner=winner, losers=losers, has_conflict=has_conflict)


# ---- 写入路径 supersede(核心接线)----

_SUPERSEDE_TOP_K = 5          # 每条新知识最多比对的相似旧条数
_SUPERSEDE_MAX_OLD = 24       # 一次 LLM 调用里旧条总数上限(挡灌爆)

SUPERSEDE_SYSTEM = (
    "你是 KarvyLoop 的记忆一致性审查器。给你两组关于同一个用户/同一知识库的条目:"
    "「新条目」(刚写入)和「旧条目」(库里已有)。\n"
    "逐对判断新条目与旧条目的关系,只有三种:\n"
    "- contradict:两条**不能同时为真**(如「用户吃素」vs「用户吃肉」)。\n"
    "- update:讲同一件事,新条目是**更新/取代**旧条目的版本(状态随时间变了)。\n"
    "- unrelated:不冲突也不取代(相关但不矛盾的,算 unrelated)。\n"
    "**严格只输出一个 JSON 对象**:{\"pairs\":[{\"new\":<新条目编号>,\"old\":<旧条目编号>,"
    "\"relation\":\"contradict|update\"}]}——只列 contradict/update 的对,unrelated 一律不列;"
    "没有任何冲突就输出 {\"pairs\":[]}。编号必须来自给你的编号,不许编造。"
    "别的话都不要输出。"
)


def parse_supersede_pairs(text: str, n_new: int, n_old: int) -> list[dict]:
    """解析审查器输出 → [{"new":i,"old":j,"relation":...}]。**宁空勿毒**:
    严格 JSON(只剥外层 fence);解析失败/形状不对/编号越界/关系不认识 → 丢弃该项或返 []
    (= 当无关,不动旧条)。"""
    t = (text or "").strip()
    if not t:
        return []
    lines = t.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    if not cleaned.startswith("{"):
        return []
    try:
        obj = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(obj, dict) or not isinstance(obj.get("pairs"), list):
        return []
    out: list[dict] = []
    seen: set = set()
    for p in obj["pairs"]:
        if not isinstance(p, dict):
            continue
        rel = str(p.get("relation", "")).strip().lower()
        if rel not in ("contradict", "update"):
            continue   # unrelated / 编造关系 → 不动
        try:
            ni, oi = int(p.get("new")), int(p.get("old"))
        except (TypeError, ValueError):
            continue
        if not (0 <= ni < n_new and 0 <= oi < n_old) or (ni, oi) in seen:
            continue   # 编号越界/重复 → 丢
        seen.add((ni, oi))
        out.append({"new": ni, "old": oi, "relation": rel})
    return out


def find_supersede_candidates(new_content: str, olds: list, *, top_k: int = _SUPERSEDE_TOP_K,
                              concepts: Optional[list] = None) -> list[int]:
    """用**已有召回栈**(overlap_score 词面+CJK bigram;有缓存概念标签再加语义重叠)
    找与新条最相似的旧条下标,按分降序取 top_k。零命中 → [](零 LLM)。**无向量**(铁律)。

    标签命中规则共用 `graph.count_tag_hits`(与召回种子③同一条,别漂移)。旧版
    `tags & _tokens(new_content)` 要求标签**恰好等于**一个 bigram/整词 —— 多字 CJK 标签
    (如"夜间模式")永远不等于 2 字 bigram,语义层形同虚设(独立对抗验收揪出)。"""
    from karvyloop.context.relevance import overlap_score
    from karvyloop.cognition.graph import _tokens, count_tag_hits
    new_keys = _tokens(new_content or "")
    new_lower = (new_content or "").lower()
    memo: dict = {}
    scored: list[tuple[float, int]] = []
    for j, b in enumerate(olds):
        c = getattr(b, "content", "") or ""
        # 双向词面重叠(overlap_score 不对称:query 词命中 content;两个方向取大再相加保守放大)
        s = float(max(overlap_score(new_content, c), overlap_score(c, new_content)))
        # 概念标签重叠(LLM 创建时打一次的缓存标签;缺就纯词面,不引向量)
        cs = concepts[j] if (concepts and j < len(concepts) and concepts[j]) else None
        if cs:
            s += 2.0 * count_tag_hits(cs, new_lower, new_keys, memo)   # 权重高于单个词面命中
        if s > 0:
            scored.append((s, j))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [j for _, j in scored[:max(0, top_k)]]


async def run_supersede_pass(new_beliefs: list, *, mem: Any, gateway: Any,
                             model_ref: str = "", now: Optional[float] = None,
                             top_k: int = _SUPERSEDE_TOP_K) -> dict:
    """写入后 supersede 一轮:新条 vs 库里相似旧条,矛盾/更新 → 打失效标记(**失效不删**)。

    - 候选=已有召回栈(overlap+概念标签)top-k;**没有候选就不调 LLM**(零成本快路径)。
    - 一次便宜 LLM 调用判整批;解析**宁空勿毒**(失败=当无关,原库不动)。
    - `provenance_rank` 把关:rank(新) >= rank(旧) → 旧条失效;rank(新) < rank(旧) →
      **新条反被打失效**(蒸馏猜的掀不翻人明说的),两条都留库可审计。
    - 任何异常吞掉只打日志(写入主流程不因审查挂掉),返回摘要 dict。
    """
    if now is None:
        now = time.time()
    empty = {"checked": 0, "invalidated_old": 0, "invalidated_new": 0, "pairs": []}
    news = [b for b in (new_beliefs or []) if getattr(b, "content", "").strip()]
    if not news or mem is None or gateway is None:
        return empty
    try:
        # 旧条池:同 scope、仍有效、非本批新写(按对象身份和 content 双保险排除)。
        # 决策偏好条(source=decision_pref 类)不参与知识冲突(两层问责不同,别互相失效)。
        new_ids = {id(b) for b in news}
        new_contents = {b.content for b in news}
        scopes = {getattr(b, "scope", "personal") for b in news}
        olds: list = []
        seen: set = set()
        for sc in scopes:
            for b in mem.index.all(sc):
                if id(b) in seen or id(b) in new_ids:
                    continue
                seen.add(id(b))
                if b.content in new_contents:
                    continue
                if getattr(b, "invalid_at", None) is not None:
                    continue   # 已失效的不再参赛(但留库可审计)
                if _is_decision_pref(b):
                    continue
                olds.append(b)
        if not olds:
            return empty
        # #61 研判①d:旧条的缓存概念标签传进候选筛选(打分公式里的 2.0×标签命中一直在,
        # 此前全仓无人传参 = 语义层空转)。只读缓存零 LLM;没接/没标签 → None 纯词面。
        old_concepts: Optional[list] = None
        cc = getattr(mem, "concept_cache", None)
        if cc is not None:
            try:
                old_concepts = [cc.tags_for(getattr(b, "content", "") or "") for b in olds]
            except Exception:
                old_concepts = None
        # 每条新知识取 top-k 相似旧条;并集封顶 _SUPERSEDE_MAX_OLD(一次 LLM 判整批)
        cand_idx: list[int] = []
        for nb in news:
            for j in find_supersede_candidates(nb.content, olds, top_k=top_k,
                                               concepts=old_concepts):
                if j not in cand_idx:
                    cand_idx.append(j)
        cand_idx = cand_idx[:_SUPERSEDE_MAX_OLD]
        if not cand_idx:
            return empty   # 一个字面都不搭 → 无关,零 LLM
        cands = [olds[j] for j in cand_idx]
        out = await _judge(news, cands, gateway=gateway, model_ref=model_ref)
        pairs = parse_supersede_pairs(out, len(news), len(cands))
        inv_old = inv_new = 0
        applied: list[dict] = []
        for p in pairs:
            nb, ob = news[p["new"]], cands[p["old"]]
            if getattr(ob, "invalid_at", None) is not None:
                continue   # 同批里已被失效过
            rel = p["relation"]
            if provenance_rank(nb.provenance) >= provenance_rank(ob.provenance):
                # 新条权威不低于旧条 → 旧条失效(Graphiti 式失效不删)
                reason = (f"superseded({rel}) by newer belief "
                          f"[{(nb.provenance or {}).get('source', '?')}]: {nb.content[:80]}")
                ok = mem.invalidate(ob, reason=reason, now=now)
                inv_old += 1
                applied.append({"loser": ob.content[:60], "winner": nb.content[:60],
                                "relation": rel, "persisted": bool(ok)})
            else:
                # 新条权威更低(如 auto 蒸的 vs 人明说的)→ 新条反被失效,人明说的站住
                reason = (f"rejected({rel}): lower provenance than existing belief "
                          f"[{(ob.provenance or {}).get('source', '?')}]: {ob.content[:80]}")
                ok = mem.invalidate(nb, reason=reason, now=now)
                inv_new += 1
                applied.append({"loser": nb.content[:60], "winner": ob.content[:60],
                                "relation": rel, "persisted": bool(ok)})
        return {"checked": len(cands), "invalidated_old": inv_old,
                "invalidated_new": inv_new, "pairs": applied}
    except Exception as e:
        # 审查是增益不是命脉:失败绝不拖垮写入主流程,也绝不半判乱改库
        logger.warning(f"[conflict] supersede 审查失败(原库不动): {e}")
        return empty


def _is_decision_pref(b: Belief) -> bool:
    """决策偏好条不参与知识 supersede(问责链不同层)。import 失败当 False(不误伤)。"""
    try:
        from karvyloop.crystallize.decision_pref import is_decision_pref
        return bool(is_decision_pref(b))
    except Exception:
        return False


async def _judge(news: list, olds: list, *, gateway: Any, model_ref: str = "") -> str:
    """一次 LLM 调用判整批(同 ingest/consolidate 的 gateway.complete 模式)。"""
    from karvyloop.context.budget import LLM_MATERIAL_TOKENS, clip_to_tokens
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    try:
        ref = gateway.resolve_model(ResolveScope(atom_model=model_ref or None))
    except Exception:
        ref = model_ref
    lines = ["新条目:"]
    lines += [f"[{i}] {str(getattr(b, 'content', ''))[:200]}" for i, b in enumerate(news)]
    lines.append("旧条目:")
    lines += [f"[{j}] {str(getattr(b, 'content', ''))[:200]}" for j, b in enumerate(olds)]
    material, _ = clip_to_tokens("\n".join(lines), LLM_MATERIAL_TOKENS)
    out = ""
    async for ev in gateway.complete(
        [{"role": "user", "content": material}], [], ref,
        system=SystemPrompt(static=[SUPERSEDE_SYSTEM]),
    ):
        if type(ev).__name__ == "TextDelta":
            out += getattr(ev, "text", "")
    return out


__all__ = [
    "PROVENANCE_RANK", "provenance_rank",
    "ConflictReport", "resolve", "detect_conflict",
    "SUPERSEDE_SYSTEM", "parse_supersede_pairs", "find_supersede_candidates",
    "run_supersede_pass",
]
