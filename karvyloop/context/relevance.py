"""context/relevance — 轻量相关性打分(context engineering 的"相关检索"层,无向量).

守否决清单(不上 embedding/向量调参,Karpathy 路线):纯词面重叠。
- 拉丁词:空白/逗号切词,整词命中计分。
- 中日韩:无分词 → 用**相邻字 bigram** 重叠("先备份"↔"动生产前先备份" 命中"备份/先备"…),
  比"整句子串"鲁棒得多。

知识召回(recall_block)与决策标准召回(recall_decision_prefs)**共用同一打分**——
别再各算各的、漂移成两套(Hardy:别只一两个功能用上 context engineering)。
"""
from __future__ import annotations


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


__all__ = ["overlap_score"]
