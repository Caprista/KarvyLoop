"""cognition.recall — agentic 召回（cognition/recall.py）。

规格：docs/modules/cognition-memory.md §3 recall.py + §4 "agentic-search 优先"
- 不上向量库：grep over markdown(业界实证：让模型自己 grep 优于 RAG)
- 私人 vs 域分路径(spec §4):personal → memory/personal/,domain → domains/<id>/memory/
- 召回带 frontmatter provenance(HR-7)

M1 v1 简版:
- 数据源:MemoryIndex(内存 dict[id, Belief]);生产=写 markdown 落盘 + index.sqlite
- 召回算法:case-insensitive 子串匹配(等同于 grep 的子集),关键词取 query 词集
- 排序:命中词数多的 Belief 优先;ties → freshness_ts 较新的优先
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from karvyloop.schemas import Belief


_STOP_PUNCT = re.compile(r"[^\w\s]+")
_STOP_WS = re.compile(r"\s+")


def _tokenize(text: str) -> list[str]:
    s = (text or "").lower()
    s = _STOP_PUNCT.sub(" ", s)
    s = _STOP_WS.sub(" ", s).strip()
    return [t for t in s.split() if t]


@dataclass
class RecallHit:
    """召回一条命中(便于上层 + 测试断言)。"""
    belief: Belief
    score: int  # 命中词数


class MemoryIndex:
    """Belief 的内存索引(私有 + 域分离)。

    spec 路径:personal → memory/personal/,domain → domains/<id>/memory/
    M1 v1 内存版:按 scope 分类(域用 scope 字段里的 domain 标记)。
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Belief] = {}
        self._pinned: set[str] = set()
        self._lock = threading.Lock()

    def put(self, belief: Belief, *, pinned: bool = False) -> None:
        with self._lock:
            # **单 key 收敛(潜伏 bug 面拆除)**:旧实现同时按 provenance["id"] 和 content 两个
            # key 存**同一对象** → all() 返回重复,催生 6 处手工 id(b) 去重;更毒的是 remove()
            # 只 pop content key —— 一旦哪个 producer 开始带 id,归档的 Belief 会借 id key 还魂
            # (重启复活/落盘 2N)。全仓核实:没有任何 producer 写 provenance["id"]、没有任何
            # caller 按 id get(distill/测试都按 content 取)→ 收敛为 content 单 key 是安全的。
            # (既有的 id(b) 去重循环保留 —— 对单 key 是无害的保险带。)
            self._by_id[belief.content] = belief
            if pinned:
                self._pinned.add(belief.content)

    def get(self, key: str) -> Optional[Belief]:
        with self._lock:
            return self._by_id.get(key)

    def is_pinned(self, belief: Belief) -> bool:
        with self._lock:
            return belief.content in self._pinned

    def all(self, scope: str) -> list[Belief]:
        with self._lock:
            return [b for b in self._by_id.values() if b.scope == scope]

    def remove(self, belief: Belief) -> None:
        """归档:从索引移除(但 pind 集合保留以便恢复后还能判定 pinned)。"""
        with self._lock:
            self._by_id.pop(belief.content, None)


def recall(query: str, index: MemoryIndex, *,
           scope: str = "personal",
           limit: int = 10,
           include_invalid: bool = False) -> list[RecallHit]:
    """agentic 召回:case-insensitive 子串匹配(query 词集 ∈ belief.content)。

    排序:score (命中词数) desc,freshness_ts desc。
    失效过滤:`invalid_at` 已置的默认不返回(与 recall_block 同规则;失效不删,审计面另查)。
    """
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []
    hits: list[RecallHit] = []
    for b in index.all(scope):
        if not include_invalid and getattr(b, "invalid_at", None) is not None:
            continue
        c_tokens = _tokenize(b.content)
        c_set = set(c_tokens)
        score = sum(1 for t in q_tokens if t in c_set)
        if score > 0:
            hits.append(RecallHit(belief=b, score=score))
    # 排序:score desc, 然后 freshness_ts desc
    hits.sort(key=lambda h: (h.score, h.belief.freshness_ts), reverse=True)
    return hits[:limit]


__all__ = ["MemoryIndex", "RecallHit", "recall", "_tokenize"]
