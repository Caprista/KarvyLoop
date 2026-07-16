"""cluster — 结晶累积的意图聚类(token-overlap,不引向量)。

设计:9.4 门1 真机发现"同一任务换说法 → 碎成不同签名 → 永不结晶"(M1 生死线)。
用户拍板 token-overlap 累积(不引向量库,不违反"记忆做深/向量调参"否决项):

**结晶宽松**:累积时不要求签名精确相等,而是把新说法**归并到最相近的已有 cluster**
(intent-token 重叠度超阈值即同 cluster)。同任务不同说法 → 攒到一起 → 能结晶。
**召回严格**:recall 本就按 intent-token 重叠匹配技能(执行前),不受影响。

复用 recall 的思路(token 重叠),但补 **CJK bigram** —— 中文无空格,整词难重叠
("平方计算器" vs "求平方")→ 补 2 字滑窗让"平方"能命中。否则中文说法永远不聚。
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .signature import _intent_cluster

_SPLIT = re.compile(r"[\s,;.，；、/|:：。!！?？()（）\"'`\-_]+")


def _is_cjk(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


def intent_tokens(intent: str) -> set[str]:
    """聚类用的意图 token 集:先走 _intent_cluster 归一(同义词/停用词/月份),
    再切词;CJK 词条补 2 字 bigram(中文无空格,整词难重叠)。"""
    norm = _intent_cluster(intent or "")
    base = [t for t in _SPLIT.split(norm) if len(t) >= 2]
    out: set[str] = set()
    for t in base:
        out.add(t)
        if _is_cjk(t):
            for i in range(len(t) - 1):
                bg = t[i:i + 2]
                if _is_cjk(bg):
                    out.add(bg)
    return out


def overlap_score(new_tokens: set[str], cluster_tokens: set[str]) -> float:
    """新意图与某 cluster 的重叠分 = |交| / |新意图 token|(与 recall 同口径:
    相对新意图,有多少 token 被该 cluster 覆盖)。0..1。"""
    if not new_tokens:
        return 0.0
    return len(new_tokens & cluster_tokens) / len(new_tokens)


# 归并最少需共享几个 token(防短意图只因共享 1 个通用词"python"就误并不同任务)。
_MIN_SHARED = 2


def match_cluster(
    intent: str,
    existing: Iterable[tuple[str, str]],
    threshold: float,
    *,
    min_shared: int = _MIN_SHARED,
    explain_sink: Optional[dict] = None,
) -> Optional[str]:
    """把 intent 归并到最相近的已有 cluster,返回其 sig;无人达标 → None(开新 cluster)。

    existing:(sig, intent_repr) 序列 —— 每个已有 cluster 的代表意图。
    threshold:重叠分门槛(0..1);<=0 视为关闭聚类(总是 None=精确签名旧行为)。
    min_shared:还要求**绝对共享 token 数 >= 此值** —— 防短意图只共享 1 个通用词
      (如 "python")就被误并到不同任务(ratio 对短意图太敏感)。
    explain_sink(B-5 #5 标定,可选;模式同 spread.explain_sink):给个 dict 就回填判定
      中间量 —— best_overlap(**未过门槛也记**的最强原始重叠分,给分布看阈值 0.2 卡在哪)、
      best_shared / n_candidates / merged。默认 None = 热路径行为与产出一字不变。
    """
    if explain_sink is not None:
        explain_sink.update({"best_overlap": 0.0, "best_shared": 0,
                             "n_candidates": 0, "merged": False})
    if threshold <= 0:
        return None
    toks = intent_tokens(intent)
    if not toks:
        return None
    best_sig: Optional[str] = None
    best = 0.0
    for sig, repr_intent in existing:
        if not repr_intent:
            continue
        cluster_toks = intent_tokens(repr_intent)
        shared = len(toks & cluster_toks)
        if explain_sink is not None:
            # 标定要看"没并上的差多远":原始重叠分不吃 min_shared/threshold 门(只记不判)
            explain_sink["n_candidates"] += 1
            s_raw = shared / len(toks)
            if s_raw > explain_sink["best_overlap"]:
                explain_sink["best_overlap"] = s_raw
                explain_sink["best_shared"] = shared
        if shared < min_shared:
            continue  # 共享太少 → 不同任务,不并
        s = shared / len(toks)
        if s > best and s >= threshold:
            best = s
            best_sig = sig
    if explain_sink is not None:
        explain_sink["merged"] = best_sig is not None
    return best_sig


__all__ = ["intent_tokens", "overlap_score", "match_cluster"]
