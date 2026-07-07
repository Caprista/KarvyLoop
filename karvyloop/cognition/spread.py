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

**种子三层**(#61 研判①,对齐技能召回侧 P3-c 的三层匹配,[[matching-is-grep-overlap-tags-no-vectors]]):
① 词面 grep/overlap(拉丁整词 + CJK bigram)——措辞有交集时的主力;
② IDF 降权(语料内手写统计)——高重复措辞库里"用户/编号"这类模板词不再与实体词同权;
③ **LLM 语义标签重叠**——写入/蒸馏时预计算的概念标签(ConceptCache,读缓存零 LLM)与
   query 词面重叠计入种子。同义改写(query 与全库**零词面交集**,如"夜间模式"↔库里"深色主题")
   靠它点火:实测 recall@8 从 0.00 → 1.00,词面命中不伤(0.95 恒定)。标签在此前只当"边"用
   ——边只能扩散已有激活,救不了零种子,这是接线缺口不是参数问题。
   权重 2.0/枚 + 命中规则均与 conflict.find_supersede_candidates 一致(共用
   graph.count_tag_hits,别造第三套)。

**性能**:用**倒排索引(token/概念 → 节点)+ 种子出发的有界 BFS**,只探种子的邻域,
**不重建全图**(旧版 concept_graph 是 O(N²)、每次 recall 在整库上重算 → 大库上 drive 热路径
会卡几秒;对抗验收实测 N=2000 ~2.8s)。现在工作量正比于**相关邻域**大小,与库总量解耦。
倒排 postings 有界(_postings_cap):出现在超过上界条数里的 hub token 不当边——高频词
无区分度,还会让 BFS 候选集爆成 O(几百)/前沿点(实测 10k 高重复库 4.4s → 加界后 ms 级)。

零 LLM(图用缓存概念或词面边,均同步);非向量库(已否决方向)。严格退化:无命中→返空;
无边→纯 overlap 序(不投毒、不回归);无标签(老库)→ 纯词面(渐进增强,daily 慢侧补抽)。
"""

from __future__ import annotations

from typing import Optional

from .graph import _tokens, count_tag_hits

# 有界化常量(防病态:海量种子 / 超稠密簇把热路径拖垮)。命中上限不静默——超了在 hop 里截断。
_SEED_CAP = 256          # 同时活跃的种子/前沿上限(按激活取前 N)
_POSTINGS_CAP = 64       # hub token 上界:一个 token/概念出现在超过 max(64, N/100) 条里 → 不当边
_DEFAULT_HOPS = 3
_DEFAULT_DECAY = 0.5
_TAG_HIT_WEIGHT = 2.0    # 语义标签命中权重(高于单个词面命中;与 find_supersede_candidates 一致)


def _postings_cap(n: int) -> int:
    return max(_POSTINGS_CAP, n // 100)


def _tag_seed_scores(query: str, concepts: Optional[list], n: int) -> Optional[list]:
    """种子第三层:预计算概念标签与 query 的词面重叠(读缓存零 LLM,打字热路径不调模型)。

    命中规则与 supersede 候选共用 `graph.count_tag_hits`(一条规则别漂移):CJK 多字标签
    走整串子串或 token 交集;纯拉丁标签只按整词(防 "AI" 命中 "email" 类子串投毒)。
    返回与 beliefs 对齐的加分表;无标签可用 → None(纯词面,不回归)。
    """
    if not concepts:
        return None
    qtok = _tokens(query)
    ql = (query or "").lower()
    if not qtok and not ql.strip():
        return None
    out = [0.0] * n
    memo: dict = {}   # tag → (tokens, has_cjk):同一标签万条库上只跑一次正则
    for i in range(min(n, len(concepts))):
        cs = concepts[i]
        if not cs:
            continue
        hits = count_tag_hits(cs, ql, qtok, memo)
        if hits:
            out[i] = _TAG_HIT_WEIGHT * hits
    return out


def _matched_surface_terms(query: str, content: str, cap: int = 5) -> list:
    """explain 用:query 的哪些词面项(拉丁整词 / CJK bigram)真出现在这条 content 里。

    命中规则与种子打分 `idf_weighted_scores` **同一条**(同切分 + 同子串包含判定),
    只对入选的 top_k 条算(≤8 条),不碰全库热路径。"""
    from karvyloop.context.relevance import _cjk_bigrams, _latin_tokens
    q = (query or "").lower()
    c = (content or "").lower()
    terms = _latin_tokens(q) | _cjk_bigrams(q)
    return sorted(t for t in terms if t in c)[:max(0, cap)]


def _matched_tags(tags, query_lower: str, query_tokens: set, memo: dict) -> list:
    """explain 用:这条的哪些概念标签真命中了 query(命中规则与种子③共用 count_tag_hits)。"""
    out = []
    for t in tags or []:
        tl = str(t).strip()
        if tl and count_tag_hits([tl], query_lower, query_tokens, memo):
            out.append(tl)
    return out


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
                                decay: float = _DEFAULT_DECAY,
                                explain_sink: Optional[list] = None):
    """激活扩散召回:返回按激活分排序的 top_k 个 belief(种子=query 相关度,沿图最强路径扩散)。

    - `concepts`:与 beliefs 对齐的 list[list[str]](LLM 写入时抽的概念标签,读缓存零 LLM);
      既当**边**(共享概念=语义边)也进**种子**(标签×query 词面重叠,同义改写的救场层);
      缺/空 → 词面边 + 纯词面种子(老库优雅退化)。
    - 无任何命中(词面 + 标签种子全 0)→ 返回空(不投毒,见下)。
    - `explain_sink`(Q1 召回解释,可选):给了就按返回顺序 append 每条入选的解释
      {index, surface_terms, concept_tags, via_spread, hops, score} —— 只把已算出的
      中间量带出来(种子分/激活分/标签命中),唯一新增记录是扩散跳数(int 赋值,
      且只在要解释时跟踪);默认 None = 行为与热路径一字不变。
    """
    if not beliefs:
        return []
    from karvyloop.context.relevance import idf_weighted_scores
    n = len(beliefs)
    # 种子①+②:词面重叠,IDF 降权高频词(同一切分来源 relevance,不与决策召回漂移)
    overlap = idf_weighted_scores(query, [getattr(b, "content", "") or "" for b in beliefs])
    lex_seed = overlap   # 词面层单独留引用(explain 区分「词面命中」vs「标签命中」)
    # 种子③:LLM 语义标签重叠(预计算缓存;同义改写零词面交集时唯一能点火的层)
    tag_seed = _tag_seed_scores(query, concepts, n)
    if tag_seed is not None:
        overlap = [overlap[i] + tag_seed[i] for i in range(n)]
    if sum(overlap) <= 0:
        # 词面和标签一个都没命中 → **返回空**,绝不靠"最新"凭空塞无关知识(真实压测 J10 揪出:
        # 问 python、库里只有咖啡 → 旧的 freshness 兜底会把咖啡当"知识"注进上下文,串台/投毒)。
        # "没相关知识"是正确答案,好过塞一篇无关的最新文章。
        return []

    keys, has_c = _keys_for(beliefs, concepts)
    # 倒排索引:key → 含该 key 的节点(找邻居 O(postings) 而非 O(N²))
    postings: dict = {}
    for i, k in enumerate(keys):
        for t in k:
            postings.setdefault(t, []).append(i)
    # hub token 加界(#61 研判②):出现在超过 max(64, N/100) 条里的 token 无区分度,不当边。
    # 不加界 = BFS 每个前沿点的候选邻居爆成 O(几百)(高重复措辞库是蒸馏产物常态,
    # 实测 10k 条把打字热路径拖到 4.4s);token 仍留在 keys 里参与共享度阈值判定。
    cap = _postings_cap(n)
    for t in [t for t, p in postings.items() if len(p) > cap]:
        del postings[t]

    mx = max(overlap) or 1.0
    act = [overlap[i] / mx if overlap[i] > 0 else 0.0 for i in range(n)]   # 种子=归一化命中度(floor)
    frontier = [i for i in range(n) if act[i] > 0.0]
    # 跳数记录(explain 独享):最强路径最后一次刷新 act[j] 时它在第几跳被够到。默认不跟踪。
    hop_of = [0] * n if explain_sink is not None else None

    for _hop in range(max(0, hops)):
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
                    if hop_of is not None:
                        hop_of[j] = _hop + 1
        frontier = list(nxt)
        if not frontier:
            break

    order = sorted(range(n), key=lambda i: (act[i], getattr(beliefs[i], "freshness_ts", 0)),
                   reverse=True)
    picked = [i for i in order if act[i] > 0.0][:max(0, top_k)]
    if explain_sink is not None:
        qtok, ql, memo = _tokens(query), (query or "").lower(), {}
        for i in picked:
            via_spread = overlap[i] <= 0.0   # 种子分 0 = 全靠图谱扩散抬上来的
            explain_sink.append({
                "index": i,
                "surface_terms": (_matched_surface_terms(query, getattr(beliefs[i], "content", "") or "")
                                  if lex_seed[i] > 0 else []),
                "concept_tags": (_matched_tags(concepts[i], ql, qtok, memo)
                                 if (tag_seed is not None and tag_seed[i] > 0
                                     and concepts and i < len(concepts)) else []),
                "via_spread": via_spread,
                "hops": (hop_of[i] if via_spread else 0),   # 种子恒 0(即使后被更强路径刷过)
                "score": round(act[i], 4),
            })
    return [beliefs[i] for i in picked]


__all__ = ["spreading_activation_recall"]
