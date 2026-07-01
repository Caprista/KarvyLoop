"""认知网状检索(激活扩散 / PPR 召回)契约测试。

锁三件:
1. **多跳关联**:弱字面命中、但强关联到命中点的知识点被抬进 top_k(扁平 overlap 做不到)。
2. **严格退化**:无任何字面命中 → 退回 freshness top_k(不回归);无边 → 退回纯种子相关度。
3. **不投毒**:和 query 完全无关、又不连通命中点的孤立点,不被凭空捞上来。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.spread import spreading_activation_recall  # noqa: E402


class _B:
    def __init__(self, content, ts=0.0):
        self.content = content
        self.freshness_ts = ts
        self.provenance = {}


def test_multi_hop_pulls_in_associated_belief():
    # 桥接点 B 共享概念把"query 命中的 A"和"几乎不命中 query 的 C"连起来。
    # A 直接命中 query;C 不含 query 词,但经 A-B-C 关联应被激活抬上来,挤掉无关的 D。
    a = _B("Hardy 偏好 minimax 模型做编码")          # 命中 query
    b = _B("minimax 模型 编码 偏好 设置")              # 桥:与 A 共享 minimax/模型/编码/偏好
    c = _B("编码 偏好 沉淀成 技能 结晶")               # 与 B 共享 编码/偏好 → 经桥关联
    d = _B("今天天气晴朗 适合散步 公园")               # 完全无关、不连通
    beliefs = [a, b, c, d]
    out = spreading_activation_recall(beliefs, "minimax 模型", top_k=3)
    assert a in out                                   # 直接命中必在
    assert c in out                                   # 关联点被多跳抬上来
    assert d not in out                               # 无关孤立点不进


def test_isolated_irrelevant_not_pulled():
    # 无关且不连通命中点的点,绝不被凭空捞(防投毒)
    hit = _B("minimax 模型 编码")
    junk = _B("烤面包 牛奶 早餐")
    out = spreading_activation_recall([hit, junk], "minimax 模型", top_k=8)
    assert hit in out
    assert junk not in out


def test_no_literal_match_returns_empty():
    # 一个字面都没命中 → 返回空(真实压测 J10 揪出:别拿"最新"凭空塞无关知识 = 串台/投毒)
    b1 = _B("苹果 香蕉", ts=1.0)
    b2 = _B("橘子 葡萄", ts=2.0)
    out = spreading_activation_recall([b1, b2], "zzz 完全不相关", top_k=1)
    assert out == []                                  # 没相关 = 不注入,好过塞最新无关的


def test_no_edges_degenerates_to_overlap():
    # 互不关联(无共享 token)→ 无边 → 纯按种子相关度,等价扁平 overlap
    strong = _B("minimax 模型 编码 偏好")              # 多命中
    weak = _B("minimax 单词")                          # 少命中
    none = _B("毫不相关 内容")                          # 不命中
    out = spreading_activation_recall([none, weak, strong], "minimax 模型 编码", top_k=2)
    assert out[0] is strong                           # 强命中排第一
    assert none not in out


def test_dense_cluster_does_not_bury_isolated_hit():
    # 对抗验收回归(hub-bias):直接命中度**打平**时,一个孤立的正确命中绝不能被一坨字面相近、
    # 互相抱团的簇挤出 top_k。query 两词各命中一边(vault↔vault,config↔簇),overlap 都=1。
    # 求和式 PPR 会让簇互相累加顶上去、把 vault 挤到末位跌出 top-k;最强路径传播守住:簇内点
    # 彼此只能给 decay×自身(<自身),无法越过种子分 → vault 与簇 act 同为 1,freshness 让 vault 居前。
    vault = _B("vault unseal key 离线 保存", ts=100.0)        # 孤立、命中 "vault",最新
    cluster = [_B(f"config 设置 项目 {i} config 设置 项目", ts=float(i)) for i in range(13)]
    beliefs = [vault] + cluster
    out = spreading_activation_recall(beliefs, "vault config", top_k=8)
    assert vault in out          # 正确答案没被簇淹掉(求和式 PPR 会把它挤到第 13)


def test_empty_and_topk_zero():
    assert spreading_activation_recall([], "q") == []
    assert spreading_activation_recall([_B("minimax")], "minimax", top_k=0) == []
