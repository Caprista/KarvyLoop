"""cognition.memory — MemoryManager 单一集成点（cognition/memory.py）。

规格：docs/modules/cognition-memory.md §3 memory.py + §4
- 参照业界:providers 列表 + 同时只允许一个外部 provider
- prefetch_all / sync_all / write(主接口)
- private vs domain 路径分离(scope 字段)
- write 必带 provenance(HR-7)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

from karvyloop.schemas import Belief

from .fence import fence
from .provider import BuiltinProvider, MemoryProvider
from .recall import MemoryIndex


class MultipleExternalProvidersError(ValueError):
    """同时配多个外部 provider → 拒绝(参照业界单外部限制)。"""


def belief_recency_ts(b: Belief) -> float:
    """Belief 的"沉淀时刻":provenance.ts 优先(写入时刻),缺/坏则退 freshness_ts。
    只读辅助(recent 排序 + API 展示共用一个口径,不各算各的)。"""
    ts = (b.provenance or {}).get("ts")
    try:
        if ts is not None:
            return float(ts)
    except (TypeError, ValueError):
        pass
    try:
        return float(b.freshness_ts or 0.0)
    except (TypeError, ValueError):
        return 0.0


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

    def __init__(self, *, index: Optional[MemoryIndex] = None, store: object = None,
                 concept_cache: object = None, trace: object = None) -> None:
        self._index = index or MemoryIndex()
        self._builtin = BuiltinProvider(self._index)
        self._externals: list[MemoryProvider] = []
        self._lock = threading.Lock()
        # loop step4b 地基:可选落盘(重启不丢)。store=BeliefStore;启动加载,write 后持久。
        self._store = store
        # #61 研判①:概念标签缓存(ConceptCache,写入侧 LLM 打一次、召回侧只读)。
        # 接了 → recall_block 的种子多一层"语义标签重叠"(同义改写能召回)、
        # supersede 候选筛选带标签;没接/老库无标签 → 纯词面,行为不回归。
        self.concept_cache = concept_cache
        # mesh 同步(docs/74):写入钩子(outbox 式)—— 本地写成功后发一条 MeshLog 事件,
        # "A 学的 B 拿到"。None = 未接 mesh(默认,零回归);由 mesh.cognition_bridge 挂。
        # 钩子异常绝不打断写入(同步是锦上添花,写入是地基)。
        self.on_write = None
        if store is not None and concept_cache is None:
            # P0③ 断接可见性:持久化形态(store 接了 = 生产样)却没接概念标签缓存 →
            # 语义标签层整体缺席:新条永无标签、同义改写召回退化纯词面、daily 回填
            # (belief_tags_tick)也无处可写。构造时响一次(不刷屏;纯内存测试形态不响)。
            # 不默认自建 —— 缓存路径归入口定(console=config 目录 / cli=~/.karvyloop),
            # 在这儿猜路径会造出第二份缓存,两入口就漂移了。
            logger.warning("[memory] concept_cache 未接:新沉淀的知识不会有概念标签,"
                           "同义改写召回退化为纯词面(daily 标签回填也无处可写)")
        # trace 句柄(可选,P0②审计面):_persist 失败时落一条 Trace(belief_persist_failed);
        # 没接 = 只 log + persist_error + 返回值,契约不变。
        self.trace = trace
        # fail-loud(闭环审计断⑥):最近一次落盘失败的原因(成功清 None)——
        # 上层(routes/doctor/调用方)能感知"记忆没存上",不再静默丢。
        self.persist_error: Optional[str] = None
        # 使用信号脏标(recall_block 轻量刷 last_recalled_ts/recall_count 后置位;
        # 不在召回热路径落盘 —— 靠下一次 write 的全量 _persist 顺带带上,或 flush_usage 批量刷)。
        self._usage_dirty: bool = False
        # P0② 重试标脏:_persist 失败置位、成功清位。取舍 = **失败不回滚内存**(进程内
        # 召回/失效行为一致),但必须标脏 —— 否则失效/归档若之后再无新写,就永远只活在
        # 内存里,重启即"复活"(supersede 幽灵循环)。flush_usage(daily 慢侧)兜底重试。
        self._persist_dirty: bool = False
        if store is not None:
            for belief, pinned in store.load_all():
                self._index.put(belief, pinned=pinned)

    def _persist(self, *, op: str = "write") -> bool:
        """把当前 index 全量写盘(write/archive 后调)。无 store 则 noop(True)。

        去重 by id(b):MemoryIndex.put 把同一 belief 存在两个 key 下(provenance['id'] 与
        content),index.all 会返回**同一对象两次** → 不去重会落盘 2N 条、召回也重复
        (独立 checker 抓到的 CRITICAL 地雷:今天 ingest 不带 id 故潜伏,带 id 立刻发作)。

        失败(P0② fail-loud 不静默):①log.error 带上下文(op + 条数)②置 _persist_dirty
        (内存态不回滚;flush_usage / 下次任意写路径全量重写自愈)③有 trace 句柄时落一条
        kind=belief_persist_failed。成功清 error + 清脏。
        """
        if self._store is None:
            return True
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
            self.persist_error = None
            self._persist_dirty = False
            return True
        except Exception as e:
            # 落盘失败不阻塞主流程(内存态仍在),但**重启即丢** —— 必须响(断⑥):
            # logger.error + persist_error 供上层感知(write/archive 也把结果返给调用方)。
            self.persist_error = f"{type(e).__name__}: {e}"
            self._persist_dirty = True   # 标脏:等 flush_usage/下次写盘重试(盘恢复即自愈)
            logger.error(f"[memory] Belief 落盘失败(op={op},{len(items)} 条;内存态仍在,"
                         f"**重启会丢这批记忆**;已标脏待重试): {e}")
            tr = getattr(self, "trace", None)
            if tr is not None:
                try:
                    from karvyloop.cognition.trace import TraceEntry
                    tr.append(TraceEntry(
                        task_id="memory_persist", kind="belief_persist_failed",
                        payload={"op": op, "error": self.persist_error, "items": len(items)},
                        source="memory"))
                except Exception:
                    pass   # Trace 是审计不是命脉:落不进不改变返回契约
            return False

    def invalidate(self, belief: Belief, *, reason: str = "", now: Optional[float] = None) -> bool:
        """**失效不删**(Graphiti 式):给一条 Belief 打 `invalid_at` 标记 + 落盘。

        条目**留在库里**(recent/审计面还查得到、可翻案),但召回(recall_block/recall/
        prefetch_all)默认过滤。supersede 消解与"过时归档"H2A 卡的兑现都走这里——
        物理删除只保留给 purge/remove_by_content 等既有显式路径。返回落盘是否成功(断⑥)。
        失败语义(P0②):内存态**不回滚**(进程内召回立即看不见它),但已响(log/Trace/返 False)
        + 标脏 —— flush_usage(daily)或下次任意写路径重试,盘恢复后失效标记自动补上盘。
        """
        if now is None:
            now = time.time()
        with self._lock:
            belief.invalid_at = float(now)
            belief.invalid_reason = (reason or "").strip()[:300]
            return self._persist(op="invalidate")

    def set_pinned(self, content: str, pinned: bool) -> bool:
        """改一条已有 Belief 的 pin 态 + 落盘(记忆主权面板:📌 锁定防归档/防自动失效)。

        按 content 精确找(index 单 key 即 content);找不到 → False(面板报"没这条")。
        落盘失败语义同 invalidate(内存态已改 + 标脏,flush_usage 自愈)。
        """
        with self._lock:
            # key 口径:先原串后 strip(index 单 key=content 原串;库里可能存过带空白的)
            b = self._index.get(content) or self._index.get((content or "").strip())
            if b is None:
                return False
            self._index.set_pinned(b, pinned)
            return self._persist(op="set_pinned")

    def mark_promoted(self, content: str, promoted_key: str) -> bool:
        """兵法回流(docs/78 §3.6):源条打 `provenance.promoted_to` 幂等标记——
        已升过的经验不再进升层候选。形态同 set_pinned(按 content 找、内存改+落盘)。"""
        with self._lock:
            b = self._index.get(content) or self._index.get((content or "").strip())
            if b is None:
                return False
            prov = dict(b.provenance or {})
            prov["promoted_to"] = str(promoted_key)
            b.provenance = prov
            return self._persist(op="mark_promoted")

    def flush_usage(self) -> bool:
        """把召回使用信号(last_recalled_ts/recall_count)批量落盘(daily 慢侧调;
        热路径 recall_block 只改内存置脏标,绝不在每次召回写盘)。无脏 → noop True。

        兼任 P0② 落盘失败的重试兜底:上次 _persist 失败(_persist_dirty)也从这里全量重写
        —— invalidate/归档失败后,daily 一到、盘恢复即自愈,失效标记不再只活在内存
        (否则重启"复活"、knowledge_tick 读脏 recall_count 误判"一年无用")。"""
        if not (self._usage_dirty or self._persist_dirty):
            return True
        with self._lock:
            ok = self._persist(op="flush_usage")
            if ok:
                self._usage_dirty = False
            return ok

    def archive(self, belief: Belief) -> bool:
        """归档(从 index 移除)+ 落盘。distill 的 MEMORY_ARCHIVE 走这里,否则归档不持久
        → 重启复活(独立 checker 抓到的 MEDIUM:持久化契约洞)。返回落盘是否成功(断⑥)。"""
        self._index.remove(belief)
        return self._persist(op="archive")

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
                self._persist(op="purge_domain")
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
                self._persist(op="purge_source_ref")
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
                self._persist(op="remove_by_content")
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
                           limit: int = 10, include_invalid: bool = False) -> Context:
        """轮前召回:所有可用 provider 召回 → 合并 → 围栏。

        **诚实状态(死代码处置说明)**:生产 drive 路径今天走同步 `recall_block`;本方法是
        MemoryProvider 协议(单外部 provider 限制)的**集成缝**——外部 provider(letta/mem0 类)
        接入时的召回汇合点,且有端到端测试锁。**留**而不删;失效过滤与 recall_block 同规则
        (默认排除 invalid_at 已置的),两条路不漂移。
        """
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
        if not include_invalid:
            beliefs = [b for b in beliefs if getattr(b, "invalid_at", None) is None]
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

    def write(self, belief: Belief, *, pinned: bool = False) -> bool:
        """写一条 Belief(HR-7:provenance 必带;freshness_ts 必填)。

        返回**落盘**是否成功(断⑥ fail-loud):False = 内存态已写入但没持久化(重启会丢),
        调用方可凭返回值/`persist_error` 上冒;校验失败仍抛 ValueError(契约不变)。"""
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
            ok = self._persist(op="write")
            if self.on_write is not None:
                try:
                    self.on_write(belief)   # mesh outbox(docs/74):发同步事件;失败绝不打断写入
                except Exception:  # noqa: BLE001
                    logger.warning("[memory] mesh on_write 钩子失败(同步事件没发出,写入本身成功)")
            return ok

    def recall_block(self, query: str, *, scope: str = "personal", limit: int = 8,
                     domain: str = "", include_invalid: bool = False,
                     as_of: Optional[float] = None,
                     explain_sink: Optional[list] = None) -> str:
        """**同步**召回(只读 index)→ 围栏块,供 drive 前注入上下文(token 纪律:封顶 limit 条)。

        简化打分:query 词与 belief.content 的字符重叠命中加分,平手按 freshness 新的优先。
        不走 async provider(builtin 召回本就是内存过滤);async prefetch_all 仍是全 provider 路径。

        **§2.6 认知两层(域隔离)**:带 `provenance.applies.domain` 的 = 域专属(私有)认知,
        **只在它自己的域召回**(A 域机密不漏到 B);无 applies.domain 的 = 通用/共享层,处处可召。
        `domain=""`(私聊/l0)→ 只召共享层;`domain=D` → 召共享层 + D 的私有层(继承+覆盖)。

        **失效过滤(冲突消解接线)**:`invalid_at` 已置的 Belief(被 supersede/归档失效)
        默认**不召回**——过时记忆不再顶掉新事实;`include_invalid=True` 才带上(审计/翻案面)。
        命中条顺带轻量刷使用信号(last_recalled_ts/recall_count,只改内存置脏标,不写盘)。

        **`as_of` 时点召回(docs/66 §技术底,Graphiti 双时态的薄版)**:给了 → 按"T 时刻它算数吗"
        过滤:`valid_from ≤ T`(缺省退 provenance.ts)且(未失效 或 `invalid_at > T`)。
        回答"我三月对 X 的看法"——当时还没学到/已被推翻的不出现;整个能力就这一个谓词,
        不建时点查询语言(个人尺度够用即止)。None(默认)= 今天的行为,一字不变。

        **`explain_sink` 召回解释(Q1 可见化)**:给了 → 按入选顺序 append 每条命中的解释
        {content_preview(80 字截断,不塞全文), provenance_ts, source, belief_key,
        surface_terms, concept_tags, via_spread, hops, score} —— 供 drive 路径把"这次回答
        垫了哪几条记忆、每条为什么被想起"回给前端。None(默认)= 行为一字不变。
        """
        # 去重 by id(b):index.all 因双 key 可能返回同一对象两次(同 _persist 的坑)
        beliefs, _seen = [], set()
        for b in self._index.all(scope):
            if id(b) in _seen:
                continue
            if as_of is not None:
                prov = b.provenance or {}
                vf = prov.get("valid_from", prov.get("ts"))
                try:
                    if vf is not None and float(vf) > as_of:
                        continue   # T 时刻还没成立/还没学到
                except (TypeError, ValueError):
                    pass           # 坏时间戳当不可判,不因此丢条
                if b.invalid_at is not None and b.invalid_at <= as_of:
                    continue       # T 时刻已被推翻
            elif not include_invalid and b.invalid_at is not None:
                continue   # 失效不删:留库可审计,但默认不进召回
            bd = (b.provenance.get("applies") or {}).get("domain", "") if b.provenance else ""
            if bd and bd != domain:
                continue   # 域私有认知:只在本域召回(跨域不漏)
            # docs/78 §3.6 谓词②:角色经验/镜像兵法归**经验通道**(roles/experience.py 分层注入),
            # 不进通用召回当噪音(否则升层的兵法会漏进私聊/别的角色的上下文;域内经验也会和
            # experience_block 双份注入)。只挡 role_experience 源 —— decision_pref 等其他带
            # applies.role 的形态不受影响(开工前实核过 applies 分布,审稿注兑现)。
            if (b.provenance or {}).get("source") == "role_experience" and \
               ((b.provenance or {}).get("applies") or {}).get("role"):
                continue
            _seen.add(id(b))
            beliefs.append(b)
        if not beliefs:
            return ""
        # 认知网状检索:**激活扩散 / Personalized PageRank**(spread.py)——种子=query 相关度
        # (词面切分共享 relevance,含 CJK bigram + IDF,不跟决策标准召回漂移),再沿认知图谱
        # (共享概念/词面边)一跳跳扩散,把"弱字面命中但强关联命中点"的知识抬上来(多跳关联)。
        # 无边/无命中时严格退化为扁平 overlap / 返空(不回归)。零 LLM(图用词面/缓存概念边)。
        from karvyloop.cognition.spread import spreading_activation_recall

        # #61 研判①c:预计算概念标签进种子(语义层)——同义改写("夜间模式"↔库里"深色主题")
        # 零词面交集也能召回(实测 recall@8 0.00→1.00)。只读缓存(memo 化,零 LLM 零盘 IO);
        # 缓存没接/老库没标签 → None,纯词面不回归(渐进增强,daily 慢侧补抽)。
        concepts = None
        cc = getattr(self, "concept_cache", None)
        if cc is not None:
            try:
                concepts = [cc.tags_for(b.content) for b in beliefs]
            except Exception:
                concepts = None   # 标签层是增益不是命脉:读缓存失败退回纯词面,不挂召回

        spread_sink: Optional[list] = [] if explain_sink is not None else None
        ranked = spreading_activation_recall(beliefs, query, concepts=concepts,
                                             top_k=max(0, limit),
                                             explain_sink=spread_sink)
        # Q1 召回解释:spread 的算法级解释(词面/标签/扩散)+ 本层补溯源与定位字段。
        # belief_key:index 以 content 为唯一 key(provenance["id"] 全仓无 producer 写),
        # 但 payload 不塞全文 → 用 provenance.id(若有)否则 content 短 hash 做稳定标识。
        if explain_sink is not None and spread_sink:
            import hashlib
            for e, b in zip(spread_sink, ranked):
                prov = b.provenance or {}
                content = b.content or ""
                key = prov.get("id") or (
                    "sha1:" + hashlib.sha1(content.encode("utf-8")).hexdigest()[:16])
                explain_sink.append({
                    "content_preview": content[:80],
                    "provenance_ts": belief_recency_ts(b),
                    "source": str(prov.get("source", "") or ""),
                    "belief_key": str(key),
                    "surface_terms": list(e.get("surface_terms") or []),
                    "concept_tags": list(e.get("concept_tags") or []),
                    "via_spread": bool(e.get("via_spread", False)),
                    "hops": int(e.get("hops", 0)),
                    "score": e.get("score", 0.0),
                })
        # 使用信号:命中即刷(fire-and-forget,内存置脏;落盘搭下次 write / flush_usage 的车,
        # 绝不在 drive 热路径写盘/算分 —— last_recalled_ts 不参与排序)。
        if ranked:
            now = time.time()
            for b in ranked:
                b.last_recalled_ts = now
                b.recall_count += 1
            self._usage_dirty = True
        return fence(ranked)

    def recent(self, *, limit: int = 20, scope: Optional[str] = None,
               domain: str = "") -> list[Belief]:
        """只读查询(P1.5 灵魂缺口②"它记得你且你看得见"):最近沉淀的 Belief,
        按 provenance.ts(缺则 freshness_ts)降序,封顶 limit 条。零副作用、不落盘。

        - `scope`:"personal"/"domain" 只看一层;None/其他 → 两层都看(去重 by id(b),
          index 双 key 会把同一对象返回两次 —— 同 _persist 的坑)。
        - `domain`:给了 → 只看 provenance.applies.domain == domain 的域专属认知;
          空 → 不按域过滤(通用 + 各域都列,这是管理面视角,非跨域召回)。
        """
        scopes = (scope,) if scope in ("personal", "domain") else ("personal", "domain")
        out, seen = [], set()
        for sc in scopes:
            for b in self._index.all(sc):
                if id(b) in seen:
                    continue
                seen.add(id(b))
                if domain:
                    bd = (b.provenance.get("applies") or {}).get("domain", "") if b.provenance else ""
                    if bd != domain:
                        continue
                out.append(b)
        out.sort(key=belief_recency_ts, reverse=True)
        return out[:max(0, int(limit))]

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
    "belief_recency_ts",
]
