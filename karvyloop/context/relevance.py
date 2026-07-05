"""context/relevance — 轻量相关性打分(context engineering 的"相关检索"层,无向量).

守否决清单(不上 embedding/向量调参,Karpathy 路线):纯词面重叠。
- 拉丁词:空白/逗号切词,整词命中计分。
- 中日韩:无分词 → 用**相邻字 bigram** 重叠("先备份"↔"动生产前先备份" 命中"备份/先备"…),
  比"整句子串"鲁棒得多。

知识召回(recall_block)与决策标准召回(recall_decision_prefs)**共用同一打分**——
别再各算各的、漂移成两套(Hardy:别只一两个功能用上 context engineering)。
本模块是词面切分的唯一来源:`overlap_score`(单对,平权)与 `idf_weighted_scores`
(批量,语料内 IDF 降权高频词;#61 研判②)共用 `_latin_tokens/_cjk_bigrams`,不许各切各的。
"""
from __future__ import annotations

import math


def _latin_tokens(s: str) -> set[str]:
    return {w for w in s.replace("，", " ").replace(",", " ").split() if w}


def _cjk_bigrams(s: str) -> set[str]:
    cjk = [ch for ch in s if "一" <= ch <= "鿿"]
    return {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}


def overlap_score(query: str, content: str) -> int:
    """query 与 content 的词面相关度(越大越相关)。query 空 → 0(调用方回退到强度/新鲜度)。"""
    q = (query or "").lower()
    c = (content or "").lower()
    if not q or not c:
        return 0
    score = 0
    for t in _latin_tokens(q):
        if t in c:
            score += 1
    for bg in _cjk_bigrams(q):
        if bg in c:
            score += 1
    return score


def idf_weighted_scores(query: str, contents: list) -> list:
    """`overlap_score` 的**批量/语料感知**版:同一词面切分,加手写 IDF 降权高频词(#61 研判②)。

    每个 query 词/bigram 的权重 = log(1 + N/df) / log(1 + N) ∈ (0, 1]:
    - df = 语料内含该词的条数;全库都有(hub token,高重复措辞库里"用户/编号/复盘"这类
      模板词)→ 权重趋近 0;只在少数条出现的实体词 → 趋近 1。
    - 修的是精度 + 延迟双雷:平权下低信息词与实体词同分(精度);hub token 让种子/边爆炸
      (实测 10k 高重复库把召回热路径拖到秒级)。
    - **手写不引库**:现成全文检索引擎的 BM25 默认切词对 CJK 失效(CJK tokenization
      pitfall,维持否决);IDF 这点增量收益 20 行自算即得。零 LLM、无向量(铁律不动)。

    返回与 contents 等长的 list[float];query 空/无词 → 全 0(调用方自回退)。
    """
    n = len(contents)
    q = (query or "").lower()
    if not q or n == 0:
        return [0.0] * n
    terms = _latin_tokens(q) | _cjk_bigrams(q)
    if not terms:
        return [0.0] * n
    lowered = [(c or "").lower() for c in contents]
    scores = [0.0] * n
    log_norm = math.log1p(n) or 1.0
    for t in terms:
        hits = [i for i, c in enumerate(lowered) if t in c]
        if not hits:
            continue
        w = math.log1p(n / len(hits)) / log_norm
        for i in hits:
            scores[i] += w
    return scores


__all__ = ["overlap_score", "idf_weighted_scores"]
