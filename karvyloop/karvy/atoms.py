"""atoms — 小卡的 N 原子 agent(K7 灵魂级:不参与 A2A)。

**核心不变量**(doc §4):
- K7 原子 agent 不参与 A2A(它们是小卡的内部组件)

设计:docs/20 §3.4。

本拍 v0 实现 5 个最小原子 agent:
  - TaskTracker(任务追踪)
  - BoardAggregator(看板聚合)
  - DataCourier(数据搬运)
  - Overseer(运维)
  - IntentAnalyst(意图分析 — 拍 9.0c 实做,2026-06-17)

注:本模块**不**依赖 karvyloop.a2a 路由层(具体见 K7 边界)。

**小卡私有域纪律**(docs/20 §3.10,2026-06-17 拍板):
- 5 个原子 agent **全部小卡私有**
- **不**通过外部 import 暴露给其它 agent / role / 用户工作流
- 任何"我也想用 intent 分析" = **自己写**,**不**复用本模块
- 公共快脑工具在 `karvyloop/karvy/fastbrain/`(本模块**不**是)
- IntentAnalyst 调 HabitStore / TraceIndex / BehaviorPatternAnalyzer(公共快脑)— 走 fastbrain 公共 API
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import time
from typing import Callable, Optional

from karvyloop.a2a import Envelope
from karvyloop.domain import Address

from .core import KarvyCore
from .observer import WorkbenchObserver

logger = logging.getLogger(__name__)


# ---- 4 个最小原子 agent(无 LLM,纯注入)----


@dataclasses.dataclass(frozen=True)
class TaskTracker:
    """任务追踪器(订阅 TASK_*,但本拍 v0 只读 BROADCAST 拿任务快照)。

    K7:不接 A2A 路由,只读 WorkbenchObserver。
    """
    workbench: WorkbenchObserver

    def tracked_tasks(self, domain_id: str) -> tuple[Envelope, ...]:
        """取域内所有 task 相关广播(K7 边界:只读)。"""
        items = self.workbench.fetch_broadcasts(domain_id)
        return tuple(e for e in items if e.type.startswith("task."))


@dataclasses.dataclass(frozen=True)
class BoardAggregator:
    """看板数据源(只读 BROADCAST,聚合 Pursuit 状态)。"""
    workbench: WorkbenchObserver

    def aggregate(self, domain_id: str) -> dict:
        """聚合工作台数据(只读,K7 边界)。"""
        snap = self.workbench.snapshot(domain_id)
        return {
            "domain_id": domain_id,
            "karvy_role": snap.karvy_role,
            "broadcast_count": snap.unread_count,
        }


@dataclasses.dataclass(frozen=True)
class DataCourier:
    """数据搬运工(接 H2A,回答用户问题)。

    K7:不接 A2A 路由,只走 H2A。
    """
    workbench: WorkbenchObserver

    def answer(self, domain_id: str, user_question: str) -> dict:
        """回答用户问题(只读,从工作台数据搬运)。"""
        snap = self.workbench.snapshot(domain_id)
        return {
            "domain_id": domain_id,
            "question": user_question,
            "snapshot": {
                "broadcast_count": snap.unread_count,
                "karvy_role": snap.karvy_role,
            },
        }


@dataclasses.dataclass(frozen=True)
class Overseer:
    """运维(健康检查 + 自动重连)。

    K7:不接 A2A,只检查 workbench 自身状态。
    """
    workbench: WorkbenchObserver
    health_threshold: int = 1000  # 超过此未读数 = 不健康

    def is_healthy(self) -> bool:
        """健康检查(K7 边界:只看 workbench)。"""
        for domain_id in self.workbench.list_domains():
            if self.workbench.snapshot(domain_id).unread_count > self.health_threshold:
                return False
        return True


# ---- 第 5 个原子 agent:意图分析(拍 9.0c 实做,2026-06-17)----


# 触发来源常量(用户原话"事件驱动 + 每天定时 + 启动时一次")
TRIGGER_EVENT = "event"
TRIGGER_BOOT = "boot"
TRIGGER_DAILY = "daily"


# 9.0c can_propose 快脑门控 — 信号关键词(payload.kind 在此集合 = 有用户行为信号)
_SIGNAL_KINDS = frozenset({
    "intent",        # 用户明确表达意图
    "task",          # 用户在做任务
    "drive",         # MainLoop 慢脑路径
    "user_action",   # 用户主动行为
    # 摘要层的真实生产产物(修"predict 永远空":此前门控只认上面 4 个 kind,
    # 而摘要层真正落的是这两种 → 全被拒之门外,analyst 永远沉默):
    "distilled_summary",      # trace_poll.distill_raw_to_summary(原文事件聚合,含 recent_intents)
    "conversation_summary",   # ConversationManager 旧对话轮换时喂的对话摘要(CV-4)
})


@dataclasses.dataclass(frozen=True)
class TraceChunk:
    """一段 trace 摘要(IntentAnalyst 的输入单位)。

    9.0c 落地:TraceIndex.list_summary() 取一批 TraceRecord 包成 TraceChunk
    """
    summaries: tuple  # tuple[TraceRecord, ...] — 不直接 import 避免循环
    source: str  # TRIGGER_EVENT / TRIGGER_BOOT / TRIGGER_DAILY
    ts: float

    def __post_init__(self) -> None:
        if self.source not in (TRIGGER_EVENT, TRIGGER_BOOT, TRIGGER_DAILY):
            raise ValueError(
                f"source must be one of {TRIGGER_EVENT}/{TRIGGER_BOOT}/{TRIGGER_DAILY}, got {self.source!r}"
            )


@dataclasses.dataclass(frozen=True)
class Proposal:
    """IntentAnalyst 产生的 PROPOSE 候选(9.0c 落地,9.0d 推 console 给 H2A)。

    fields:
        summary: 候选描述("用户可能想 X")
        options: 候选选项(H2A 三选一:ACCEPT/DEFER/REJECT)
        strength: 强度 0-1(超过 threshold 才生成 Proposal)
        evidence_refs: 凝出用的 trace 摘要 seq(可空)
        habit_id: 关联的 habit id(可选;0 表示未关联)
        model_ref: 凝出用的 model
        ts: 生成时间
        kind: 建议类型(docs/30 PR-1:crystallize_skill / run_task /
              route_to_role / resolve_conflict …)— 决定 ACCEPT 怎么兑现;默认
              crystallize_skill(9.0c 老 Proposal 全是"沉淀技能"语义,向后兼容)
        payload: 兑现所需数据(按 kind 不同:sig / intent / Address / 冲突项…)
        proposal_id: 稳定 id(docs/30 PR-2:ACCEPT 凭此查回原 Proposal)。留空则
              由 (kind, habit_id, summary) 稳定派生(无随机,可测、跨进程一致)
    """
    summary: str
    options: tuple  # tuple[str, ...]
    strength: float
    evidence_refs: tuple  # tuple[int, ...]
    habit_id: int  # 0 = 未关联
    model_ref: str
    ts: float
    kind: str = "crystallize_skill"  # docs/30 PR-1;默认向后兼容 9.0c
    payload: dict = dataclasses.field(default_factory=dict)
    proposal_id: str = ""  # 空 → __post_init__ 稳定派生
    # ch4 工作台:每个"要我拍的板"必须带**决策依据**(为什么)+ **上下文跳转**(去哪看全貌)。
    # 否则"老板付 10 万接不接"凭啥拍(Hardy #6.1)。
    basis: str = ""                  # 人话决策依据:为什么提这个、发生了什么
    context_ref: dict = dataclasses.field(default_factory=dict)  # 跳转目标 {"kind":"task/conversation","id":...}
    # docs/92 刀1(同链合并):同一件事派生出的多张卡共享 chain_id(链根 = 最早那张的
    # proposal_id)。**纯视觉收纳**:前端右栏按它分组折叠,每张卡仍独立拍(独立 h2a_decision
    # 流水,不丢拍板粒度)。老卡/不带链的卡 = 空串,前端按单卡渲染(0 视觉回归)。
    chain_id: str = ""

    def __post_init__(self):
        if not self.proposal_id:
            # 稳定派生:无随机数(可测 + 跨进程一致);frozen 用 object.__setattr__
            digest = hashlib.sha1(
                f"{self.kind}|{self.habit_id}|{self.summary}".encode("utf-8")
            ).hexdigest()[:8]
            object.__setattr__(self, "proposal_id", f"{self.kind}-{self.habit_id}-{digest}")

    def to_dict(self) -> dict:
        """序列化为 dict(9.0d console push 用)。"""
        return {
            "summary": self.summary,
            "options": list(self.options),
            "strength": self.strength,
            "evidence_refs": list(self.evidence_refs),
            "habit_id": self.habit_id,
            "model_ref": self.model_ref,
            "ts": self.ts,
            "kind": self.kind,
            "payload": dict(self.payload),
            "proposal_id": self.proposal_id,
            "basis": self.basis,              # ch4:决策依据(为什么)
            "context_ref": dict(self.context_ref),  # ch4:上下文跳转目标
            "chain_id": self.chain_id,       # docs/92 刀1:同链合并(空=无链,前端单卡渲染)
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        """从 to_dict() 还原(待决卡落盘 → 重启恢复)。tuple 字段从 list 复原;
        proposal_id 已在 → __post_init__ 不重派生(跨进程稳定一致)。"""
        return cls(
            summary=str(d.get("summary", "") or ""),
            options=tuple(d.get("options", ()) or ()),
            strength=float(d.get("strength", 0.0) or 0.0),
            evidence_refs=tuple(d.get("evidence_refs", ()) or ()),
            habit_id=int(d.get("habit_id", 0) or 0),
            model_ref=str(d.get("model_ref", "") or ""),
            ts=float(d.get("ts", 0.0) or 0.0),
            kind=str(d.get("kind", "crystallize_skill") or "crystallize_skill"),
            payload=dict(d.get("payload", {}) or {}),
            proposal_id=str(d.get("proposal_id", "") or ""),
            basis=str(d.get("basis", "") or ""),
            context_ref=dict(d.get("context_ref", {}) or {}),
            chain_id=str(d.get("chain_id", "") or ""),  # docs/92 刀1;老落盘文件无此键 → ""
        )


# 解析 model_ref 的函数类型(避免 IntentAnalyst 直接依赖 fastbrain.trace_habit)
ModelRefResolver = Callable[[str], object]
"""`(agent_name) -> ModelRef` 签名 — 9.0b 提供的 resolve_model_ref 直接满足。"""


@dataclasses.dataclass(frozen=True)
class IntentAnalyst:
    """意图分析器(小卡私有原子 agent — 拍 9.0c 实做,2026-06-17)。

    **职责**(设计稿 docs/20 §3.3.5,docs/25):
    - 读 `HabitStore`(已有习惯)+ 读 `TraceIndex` 摘要层(新事件)
    - 调 `BehaviorPatternAnalyzer`(LLM 慢脑)从摘要凝习惯
    - 强度到 `strength_threshold` → 推 `Proposal` 给 9.0d console
    - 强度不到 → 沉默(返 None)

    **三种触发**(用户原话 2026-06-17):
    - `on_event(trace_chunk)`:事件驱动,新 trace 来了立刻分析
    - `boot_poll()`:启动时跑一次(读最近 N 条摘要)
    - `daily_poll()`:每天定时跑一次(读最近 N 条摘要)

    **内部两段式**(快脑 + 慢脑):
    - 快脑(`can_propose`):"这事能不能凝成 PROPOSE?"(0.1.0 纯规则)
    - 慢脑(`analyze`):真分析习惯层 + 此刻 trace,生成 proposal

    **灵魂铁律**:
    - K7:不参与 A2A(只读 WorkbenchObserver,不动 Courier)
    - K5:不替用户决策 — Proposal 由用户点 ACCEPT / REJECT(9.0d H2A)
    - K1:小卡永远 observer,意图分析器也是 observer 一部分
    - **小卡私有** — 其它 agent / role 不得 import / 复用(docs/20 §3.10)

    **依赖**(公共快脑 9.0a/9.0b 落地):
    - `habit_store`:HabitStore(读现有习惯)
    - `trace_index`:TraceIndex(读 trace 摘要层)
    - `behavior_analyzer`:BehaviorPatternAnalyzer(LLM 慢脑)
    - `model_ref_resolver`:resolve_model_ref(per-agent 覆盖 → 全局默认 → 硬编码)
    """

    workbench: WorkbenchObserver
    habit_store: object  # HabitStore(duck type;不直接 import 避免循环)
    trace_index: object  # TraceIndex(duck type)
    behavior_analyzer: object  # BehaviorPatternAnalyzer(duck type)
    model_ref_resolver: ModelRefResolver = staticmethod(lambda agent: None)  # type: ignore[arg-type,return-value]
    agent_name: str = "intent_analyst"
    strength_threshold: float = 0.7  # 念头强度超此值才推 PROPOSE
    clock: Callable[[], float] = time.time

    # ---- 快脑门控(can_propose)----

    def can_propose(self, chunk: TraceChunk) -> bool:
        """快脑门控:这事能不能凝成 PROPOSE?

        0.1.0 MVP 规则(CLAUDE.md §少脚手架):
        - chunk.summaries 空 → False
        - chunk.source = "boot" 且无摘要 → False(刚启动数据不够)
        - 任一 summary 的 payload.kind 在 _SIGNAL_KINDS → True
        - 否则 → False(让 LLM 慢脑不必跑)

        0.2.0 计划:升级小模型意图分类兜底(CLAUDE.md §少脚手架,先规则后模型)
        """
        if not chunk.summaries:
            return False
        if chunk.source == TRIGGER_BOOT and len(chunk.summaries) < 1:
            return False
        for rec in chunk.summaries:
            # rec 是 TraceRecord(duck type):有 .payload 字段
            payload = getattr(rec, "payload", None)
            if not isinstance(payload, dict):
                continue
            kind = payload.get("kind", "")
            if kind in _SIGNAL_KINDS:
                return True
        return False

    # ---- 慢脑主路径(analyze)----

    def analyze(self, chunk: TraceChunk) -> Optional[Proposal]:
        """慢脑:真分析产生 PROPOSE 候选。

        流程:
            1. can_propose 快脑门控 — fail 直接返 None
            2. 解析 model_ref(per-agent 覆盖 → 全局默认 → 硬编码)
            3. 调 behavior_analyzer.analyze(summaries, model_ref)
            4. 选最强 Habit — 强度 >= threshold → Proposal,否则 None

        Returns:
            None = 沉默;Proposal = 候选(9.0d 推 console 给用户)
        """
        if not self.can_propose(chunk):
            return None

        # 2. 解析 model_ref
        model_ref = self.model_ref_resolver(self.agent_name)
        # 3. 调 LLM 慢脑(可能返 [],可能抛 NotImplementedError 9.0c 实做前)
        try:
            habits = self.behavior_analyzer.analyze(chunk.summaries, model_ref)
        except NotImplementedError:
            # 9.0b 骨架 + 9.0c IntentAnalyst 落地前的 graceful degradation
            logger.debug(
                f"[IntentAnalyst] behavior_analyzer 9.0b 骨架,无 LLM 接入 — 返 None(等 9.0c 实做)"
            )
            return None
        if not habits:
            return None

        # 4. 选最强 habit
        best = max(habits, key=lambda h: h.strength)
        if best.strength < self.strength_threshold:
            return None

        return Proposal(
            summary=best.pattern,
            options=("ACCEPT", "DEFER", "REJECT"),
            strength=best.strength,
            evidence_refs=best.evidence_refs,
            habit_id=best.id,
            model_ref=best.model_ref or (getattr(model_ref, "name", "") or ""),
            ts=self.clock(),
        )

    # ---- 三种触发包装(用户原话 2026-06-17)----

    def on_event(self, chunk: TraceChunk) -> Optional[Proposal]:
        """事件驱动:新 trace 来了立刻分析。

        Args:
            chunk: trace_chunk 必带 `source=TRIGGER_EVENT`
        """
        if chunk.source != TRIGGER_EVENT:
            raise ValueError(
                f"on_event 必须用 source=TRIGGER_EVENT,got {chunk.source!r}"
            )
        return self.analyze(chunk)

    def boot_poll(self, recent_n: int = 20) -> Optional[Proposal]:
        """启动时跑一次:从 trace 摘要层读最近 N 条 → 凝一次。

        Returns:
            Proposal or None
        """
        chunk = self._build_chunk(source=TRIGGER_BOOT, recent_n=recent_n)
        return self.analyze(chunk)

    def daily_poll(self, recent_n: int = 50) -> Optional[Proposal]:
        """每天定时跑一次(9.0b trace_poll.install_pollers 调度入口)。

        Args:
            recent_n: 取最近 N 条摘要(默认 50 — 一天的量级)
        """
        chunk = self._build_chunk(source=TRIGGER_DAILY, recent_n=recent_n)
        return self.analyze(chunk)

    def _build_chunk(self, source: str, recent_n: int) -> TraceChunk:
        """从 trace_index 摘要层读最近 N 条,包成 TraceChunk。"""
        summaries = self.trace_index.list_summary(limit=recent_n)
        return TraceChunk(
            summaries=tuple(summaries),
            source=source,
            ts=self.clock(),
        )
