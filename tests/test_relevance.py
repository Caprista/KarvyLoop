"""test_relevance — 共享相关性打分(知识召回 + 决策召回同用,无向量)。"""
from __future__ import annotations

from karvyloop.context.relevance import overlap_score


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
