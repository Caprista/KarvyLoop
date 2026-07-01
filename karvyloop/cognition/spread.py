"""cognition/spread.py — 认知网状检索:激活扩散召回(best-path spreading activation)。

**为什么**(Hardy + 搜证):认知不是扁平表,是**网**。检索该像人——先找到和问题最相关的
知识点,再**沿关联一跳跳往周围扩散**,把"弱字面命中但强关联"的点抬上来,直到够到真正要的。
这是认知科学经典的 **spreading activation**(激活扩散)/ HippoRAG 式图上关联检索的工程化。

**算法 = 最强路径传播(max-propagation),不是 PageRank 求和。** 关键:一个节点的激活 =
**从任一种子出发的最强路径**,不是把所有邻居的贡献累加。这条选择是刻意的——
求和式 PPR 有 hub-bias:一坨字面相近的点会互相累加把分数顶上去,**把真正命中、但孤立的
答案挤掉**(对抗验收实测:flat 排第 0 的正确答案被求和式 PPR 挤到第 13、跌出 top-k)。
最强路径传播下,一个稠密簇里的点彼此只能给对方 `decay×自身`(< 自身),无法互相抬升越过
自己的种子分 → **强直接命中永不被簇淹没**;而零命中、但强关联到某个高分点的点,会被抬到
`邻居分×decay`。这既要了多跳关联,又守住了"别把对的答案埋掉"。

**性能**:用**倒排索引(token/概念 → 节点)+ 种子出发的有界 BFS**,只探种子的邻域,
**不重建全图**(旧版 concept_graph 是 O(N²)、每次 recall 在整库上重算 → 大库上 drive 热路径
会卡几秒;对抗验收实测 N=2000 ~2.8s)。现在工作量正比于**相关邻域**大小,与库总量解耦。

零 LLM(图用缓存概念或词面边,均同步);非向量库(已否决方向)。严格退化:无命中→freshness;
无边→纯 overlap 序(不投毒、不回归)。
"""

from __future__ import annotations

from typing import Optional

from .graph import _tokens

# 有界化常量(防病态:海量种子 / 超稠密簇把热路径拖垮)。命中上限不静默——超了在 hop 里截断。
_SEED_CAP = 256          # 同时活跃的种子/前沿上限(按激活取前 N)
_DEFAULT_HOPS = 3
_DEFAULT_DECAY = 0.5


def _keys_for(beliefs: list, concepts: Optional[list]):
    """每条 belief 的关联键:有 LLM 概念用概念(语义边),否则回退词面 token。返回 (keys, has_concept)。"""
    keys, has_c = [], []
    for i, b in enumerate(beliefs):
        cs = concepts[i] if (concepts and i < len(concepts) and concepts[i]) else None
        if cs:
            keys.append({str(c).strip() for c in cs if str(c).strip()})
            has_c.append(True)
        else:
            keys.append(_tokens(getattr(b, "content", "") or ""))
            has_c.append(False)
    return keys, has_c


def spreading_activation_recall(beliefs: list, query: str, *,
                                concepts: Optional[list] = None,
                                top_k: int = 8, hops: int = _DEFAULT_HOPS,
                                decay: float = _DEFAULT_DECAY):
    """激活扩散召回:返回按激活分排序的 top_k 个 belief(种子=query 相关度,沿图最强路径扩散)。

    - `concepts`:与 beliefs 对齐的 list[list[str]](LLM 抽的核心概念,缓存即零 LLM);缺/空 → 词面边。
    - 无任何字面命中(种子全 0)→ 退回按 freshness 取 top_k(与旧 recall 行为一致,不回归)。
    """
    if not beliefs:
        return []
    from karvyloop.context.relevance import overlap_score
    n = len(beliefs)
    overlap = [float(overlap_score(query, getattr(b, "content", "") or "")) for b in beliefs]
    if sum(overlap) <= 0:
        # 一个字面都没命中 → **返回空**,绝不靠"最新"凭空塞无关知识(真实压测 J10 揪出:
        # 问 python、库里只有咖啡 → 旧的 freshness 兜底会把咖啡当"知识"注进上下文,串台/投毒)。
        # "没相关知识"是正确答案,好过塞一篇无关的最新文章。
        return []

    keys, has_c = _keys_for(beliefs, concepts)
    # 倒排索引:key → 含该 key 的节点(找邻居 O(postings) 而非 O(N²))
    postings: dict = {}
    for i, k in enumerate(keys):
        for t in k:
            postings.setdefault(t, []).append(i)

    mx = max(overlap) or 1.0
    act = [overlap[i] / mx if overlap[i] > 0 else 0.0 for i in range(n)]   # 种子=归一化命中度(floor)
    frontier = [i for i in range(n) if act[i] > 0.0]

    for _ in range(max(0, hops)):
        if len(frontier) > _SEED_CAP:                      # 有界:前沿过大只留激活最高的(不静默)
            frontier = sorted(frontier, key=lambda i: act[i], reverse=True)[:_SEED_CAP]
        nxt: set = set()
        for i in frontier:
            src = decay * act[i]
            if src <= 0.0:
                continue
            cand: set = set()
            for t in keys[i]:                              # i 的候选邻居 = 共享任一 key 的点
                for j in postings.get(t, ()):  # noqa: E501
                    if j != i:
                        cand.add(j)
            for j in cand:
                # 边阈值与 concept_graph 一致:都有概念→共享≥1;回退词面→需≥2(词面噪声大)
                need = 1 if (has_c[i] and has_c[j]) else 2
                if len(keys[i] & keys[j]) < need:
                    continue
                if src > act[j]:                           # 最强路径:取 max,不累加(防 hub-bias)
                    act[j] = src
                    nxt.add(j)
        frontier = list(nxt)
        if not frontier:
            break

    order = sorted(range(n), key=lambda i: (act[i], getattr(beliefs[i], "freshness_ts", 0)),
                   reverse=True)
    return [beliefs[i] for i in order if act[i] > 0.0][:max(0, top_k)]


__all__ = ["spreading_activation_recall"]
