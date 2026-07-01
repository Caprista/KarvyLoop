"""cognition.memory — MemoryManager 单一集成点（cognition/memory.py）。

规格：docs/modules/cognition-memory.md §3 memory.py + §4
- 参照业界:providers 列表 + 同时只允许一个外部 provider
- prefetch_all / sync_all / write(主接口)
- private vs domain 路径分离(scope 字段)
- write 必带 provenance(HR-7)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from karvyloop.schemas import Belief

from .fence import fence
from .provider import BuiltinProvider, MemoryProvider
from .recall import MemoryIndex


class MultipleExternalProvidersError(ValueError):
    """同时配多个外部 provider → 拒绝(参照业界单外部限制)。"""


@dataclass
class Context:
    """prefetch_all 的产物:围栏后的字符串 + 命中 Belief(供上层用)。"""
    fenced: str
    beliefs: list[Belief]


class MemoryManager:
    """认知记忆的单一集成点。

    - 至少一个 builtin(永远可用,代表本地 markdown+grep)
    - 0 或 1 个外部 provider(同时只一个)
    - write 必带 provenance + freshness_ts(scope 必填)
    """

    def __init__(self, *, index: Optional[MemoryIndex] = None, store: object = None) -> None:
        self._index = index or MemoryIndex()
        self._builtin = BuiltinProvider(self._index)
        self._externals: list[MemoryProvider] = []
        self._lock = threading.Lock()
        # loop step4b 地基:可选落盘(重启不丢)。store=BeliefStore;启动加载,write 后持久。
        self._store = store
        if store is not None:
            for belief, pinned in store.load_all():
                self._index.put(belief, pinned=pinned)

    def _persist(self) -> None:
        """把当前 index 全量写盘(write/archive 后调)。无 store 则 noop。

        去重 by id(b):MemoryIndex.put 把同一 belief 存在两个 key 下(provenance['id'] 与
        content),index.all 会返回**同一对象两次** → 不去重会落盘 2N 条、召回也重复
        (独立 checker 抓到的 CRITICAL 地雷:今天 ingest 不带 id 故潜伏,带 id 立刻发作)。
        """
        if self._store is None:
            return
        seen: set[int] = set()
        items = []
        for sc in ("personal", "domain"):
            for b in self._index.all(sc):
                if id(b) in seen:
                    continue
                seen.add(id(b))
                items.append((b, self._index.is_pinned(b)))
        try:
            self._store.save_all(items)
        except Exception:
            pass  # 落盘失败不阻塞主流程(内存态仍在)

    def archive(self, belief: Belief) -> None:
        """归档(从 index 移除)+ 落盘。distill 的 MEMORY_ARCHIVE 走这里,否则归档不持久
        → 重启复活(独立 checker 抓到的 MEDIUM:持久化契约洞)。"""
        self._index.remove(belief)
        self._persist()

    def purge_domain(self, domain: str) -> int:
        """§2.6 ⑤:删/归档业务域时,清掉**该域的私有认知层**(applies.domain==domain 的 Belief)。
        通用/共享层(无 applies.domain)不动 —— 角色回公共库、本职认知留着。返回清除条数。"""
        if not domain:
            return 0
        victims, seen = [], set()
        for sc in ("personal", "domain"):
            for b in self._index.all(sc):
                if id(b) in seen:
                    continue
                seen.add(id(b))
                bd = (b.provenance.get("applies") or {}).get("domain", "") if b.provenance else ""
                if bd == domain:
                    victims.append(b)
        with self._lock:   # #6 并发:删 + 落盘 与 write 串行,避免互相盖
            for b in victims:
                self._index.remove(b)
            if victims:
                self._persist()
        return len(victims)

    def count_source_ref(self, source_ref: str) -> int:
        """某来源(URL/材料指纹)已沉淀几条 —— feed 时判"这份资料喂过了吗"。"""
        if not source_ref:
            return 0
        n, seen = 0, set()
        for sc in ("personal", "domain"):
            for b in self._index.all(sc):
                if id(b) in seen:
                    continue
                seen.add(id(b))
                if (b.provenance or {}).get("source_ref", "") == source_ref:
                    n += 1
        return n

    def purge_source_ref(self, source_ref: str, *, before_ts: Optional[float] = None) -> int:
        """删掉某来源沉淀的 Belief —— 同一资料重喂时 **supersede 换新版、不叠加重复**(Hardy)。
        `before_ts` 给了 → 只删该时刻**之前**的(旧版),保住本次新写的(新版 ts≥before_ts);
        供"先写新、再删旧"用(避免写 0 时把旧的也误删 = 净丢失)。返回清除条数。"""
        if not source_ref:
            return 0
        victims, seen = [], set()
        for sc in ("personal", "domain"):
            for b in self._index.all(sc):
                if id(b) in seen:
                    continue
                seen.add(id(b))
                if (b.provenance or {}).get("source_ref", "") != source_ref:
                    continue
                if before_ts is not None:
                    ts = (b.provenance or {}).get("ts")
                    if ts is None or ts >= before_ts:
                        continue   # 本次新写的,不删
                victims.append(b)
        with self._lock:   # #6 并发:删 + 落盘 与 write 串行,避免互相盖
            for b in victims:
                self._index.remove(b)
            if victims:
                self._persist()
        return len(victims)

    def count_beliefs_by_content(self, contents: set) -> int:
        """有几条 Belief 的 content 命中给定集合(consolidate apply 前校验成员真实存在)。"""
        if not contents:
            return 0
        n, seen = 0, set()
        for sc in ("personal", "domain"):
            for b in self._index.all(sc):
                if id(b) in seen:
                    continue
                seen.add(id(b))
                if b.content in contents:
                    n += 1
        return n

    def remove_by_content(self, contents: set) -> int:
        """按 content 精确删 Belief —— 知识合并(consolidate)兑现时删被并的旧条。返回删除条数。"""
        if not contents:
            return 0
        victims, seen = [], set()
        for sc in ("personal", "domain"):
            for b in self._index.all(sc):
                if id(b) in seen:
                    continue
                seen.add(id(b))
                if b.content in contents:
                    victims.append(b)
        with self._lock:   # #6 并发:删 + 落盘 与 write 串行,避免互相盖
            for b in victims:
                self._index.remove(b)
            if victims:
                self._persist()
        return len(victims)

    @property
    def index(self) -> MemoryIndex:
        return self._index

    @property
    def providers(self) -> list[MemoryProvider]:
        return [self._builtin] + list(self._externals)

    def add_external(self, provider: MemoryProvider) -> None:
        """加外部 provider;已经有一个则拒(spec §4 单外部限制)。"""
        with self._lock:
            if provider.name == "builtin":
                raise ValueError("builtin 是隐式的,不要再 add")
            if self._externals:
                raise MultipleExternalProvidersError(
                    f"已有外部 provider {self._externals[0].name!r},"
                    f"不能再加 {provider.name!r}"
                )
            self._externals.append(provider)

    def remove_external(self, name: str) -> bool:
        with self._lock:
            for i, p in enumerate(self._externals):
                if p.name == name:
                    self._externals.pop(i)
                    return True
            return False

    async def prefetch_all(self, query: str, *, scope: str = "personal",
                           limit: int = 10) -> Context:
        """轮前召回:所有可用 provider 召回 → 合并 → 围栏。"""
        beliefs: list[Belief] = []
        for p in self.providers:
            if not p.is_available():
                continue
            try:
                got = await p.prefetch(query, scope=scope, limit=limit)
            except Exception:
                # 任一 provider 失败不阻塞其他(spec 没写;保守 fail-soft)
                continue
            beliefs.extend(got)
        # 同一论断多条 → 消解去重(简单按 content 去重,保留 freshness 最大的)
        dedup: dict[str, Belief] = {}
        for b in beliefs:
            cur = dedup.get(b.content)
            if cur is None or b.freshness_ts > cur.freshness_ts:
                dedup[b.content] = b
        merged = list(dedup.values())
        # 排序:freshness desc(最近的最先)
        merged.sort(key=lambda b: b.freshness_ts, reverse=True)
        return Context(fenced=fence(merged), beliefs=merged)

    async def sync_all(self, user: str, assistant: str) -> None:
        """轮后异步写入。M1 v1:只触发 provider.sync_turn(主动 extract 在 distill)。"""
        for p in self.providers:
            if not p.is_available():
                continue
            try:
                await p.sync_turn(user, assistant)
            except Exception:
                continue

    def write(self, belief: Belief, *, pinned: bool = False) -> None:
        """写一条 Belief(HR-7:provenance 必带;freshness_ts 必填)。"""
        if not belief.provenance:
            raise ValueError("Belief.provenance 必填(HR-7)")
        # 用 is None 而非 falsy:0.0 是合法的 epoch 时刻(否则 now=0.0 → 静默吞写,
        # 独立 checker 抓到的 HIGH:invisible data loss)。
        if belief.freshness_ts is None:
            raise ValueError("Belief.freshness_ts 必填")
        if belief.scope not in ("personal", "domain"):
            raise ValueError(f"Belief.scope 必填 personal/domain,得到 {belief.scope!r}")
        # #6 并发:put + 全量落盘是 read-modify-write,并行写(多协作同时沉淀)不加锁会互相盖/丢。
        # _persist 不自锁 → 这里持锁安全(不重入)。
        with self._lock:
            self._index.put(belief, pinned=pinned)
            self._persist()

    def recall_block(self, query: str, *, scope: str = "personal", limit: int = 8,
                     domain: str = "") -> str:
        """**同步**召回(只读 index)→ 围栏块,供 drive 前注入上下文(token 纪律:封顶 limit 条)。

        简化打分:query 词与 belief.content 的字符重叠命中加分,平手按 freshness 新的优先。
        不走 async provider(builtin 召回本就是内存过滤);async prefetch_all 仍是全 provider 路径。

        **§2.6 认知两层(域隔离)**:带 `provenance.applies.domain` 的 = 域专属(私有)认知,
        **只在它自己的域召回**(A 域机密不漏到 B);无 applies.domain 的 = 通用/共享层,处处可召。
        `domain=""`(私聊/l0)→ 只召共享层;`domain=D` → 召共享层 + D 的私有层(继承+覆盖)。
        """
        # 去重 by id(b):index.all 因双 key 可能返回同一对象两次(同 _persist 的坑)
        beliefs, _seen = [], set()
        for b in self._index.all(scope):
            if id(b) in _seen:
                continue
            bd = (b.provenance.get("applies") or {}).get("domain", "") if b.provenance else ""
            if bd and bd != domain:
                continue   # 域私有认知:只在本域召回(跨域不漏)
            _seen.add(id(b))
            beliefs.append(b)
        if not beliefs:
            return ""
        # 认知网状检索:**激活扩散 / Personalized PageRank**(spread.py)——种子=query 相关度
        # (仍用共享的 overlap_score,含 CJK bigram,不跟决策标准召回漂移),再沿认知图谱(共享概念/
        # 词面边)一跳跳扩散,把"弱字面命中但强关联命中点"的知识抬上来(多跳关联,非只字面命中)。
        # 无边/无命中时严格退化为扁平 overlap / freshness(不回归)。零 LLM(图用词面/缓存概念边)。
        from karvyloop.cognition.spread import spreading_activation_recall

        ranked = spreading_activation_recall(beliefs, query, top_k=max(0, limit))
        return fence(ranked)

    async def consolidate_all(self) -> None:
        for p in self.providers:
            if not p.is_available():
                continue
            try:
                await p.consolidate()
            except Exception:
                continue


__all__ = [
    "MemoryManager", "Context", "MultipleExternalProvidersError",
]
