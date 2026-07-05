"""test_relevance — 共享相关性打分(知识召回 + 决策召回同用,无向量)。"""
from __future__ import annotations

from karvyloop.context.relevance import idf_weighted_scores, overlap_score


def test_latin_word_overlap():
    assert overlap_score("deploy prod backup", "always backup before touching prod") >= 2


def test_cjk_bigram_overlap():
    # "生产""数据" bigram 命中 → 相关
    assert overlap_score("要直接改生产数据库吗", "动生产数据前必须先备份") >= 1
    # 完全不相关 → 0
    assert overlap_score("配色用什么蓝", "动生产数据前必须先备份") == 0


def test_empty_query_or_content_zero():
    assert overlap_score("", "anything") == 0
    assert overlap_score("anything", "") == 0


# ---- idf_weighted_scores(#61 研判②:批量/语料感知版,手写 IDF 降权高频词)----

def test_idf_rare_term_outweighs_hub_term():
    # "望远镜"只在 1 条出现(罕见,高权),"流程"全库都有(hub,权趋 0)
    contents = ["星轨望远镜 流程"] + [f"第{i}项 流程" for i in range(9)]
    scores = idf_weighted_scores("望远镜 流程", contents)
    assert scores[0] == max(scores)                       # 罕见词命中的条最高
    assert scores[0] > scores[1] * 2                      # 且明显高于纯 hub 命中


def test_idf_empty_inputs():
    assert idf_weighted_scores("", ["a", "b"]) == [0.0, 0.0]
    assert idf_weighted_scores("query", []) == []


def test_idf_zero_when_no_overlap():
    scores = idf_weighted_scores("夜间模式", ["档案室编号每季度轮换", "货架标签超期作废"])
    assert scores == [0.0, 0.0]
