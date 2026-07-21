"""主循环 driver — 把 recall ↔ slow-brain ↔ observe ↔ crystallize 拼成一条线（runtime/main_loop.py,P2-f 自 cli/ 搬入）。

规格:docs/modules/workbench-cli.md §3 main_loop.py + #6 M1 验收门(快脑命中率)
- 一句话 intent 进来 → 走"快脑(已结晶)/ 慢脑(forge)" 二选一
- 慢脑跑完后 → observe 投影到 UsageStats
- 触发两关 → 自动 crystallize(下次同 intent 走快脑)
- 这是把 M1 楔子"代码"变成"产品故事"的关键桥 —— 没有这一步,recall/crystallize
  永远只是两段分离的代码;M1 验收门("5-10 个用户用两周")也跑不起来

设计原则:
  - 完全同步接口(`drive()` 是同步函数)。生产路径里慢脑本身就是 async,
    由 cmd_run 在调 drive() 前用 asyncio.run 包好;测试里用 sync stub,无需 asyncio。
  - 依赖全部注入(slow_brain / clock / store / verify / skill_index / skills_dir)。
    没注入就用合理的默认(InMemoryUsageStore / VerifyStore / SkillIndex.rebuild_from_disk)。
  - 返回 DriveResult:path(brain_used) + 是否触发结晶 + 命中/失败统计点。
  - **不**包含任何 I/O 副作用决定(慢脑调不调 LLM 由 caller 决定);driver 只负责
    "已经发生的事"。

北极星指标:
  fast_brain_hit_rate = fast_brain_hits / drive_calls
  M1 验收门:同一意图用得越多,fast_brain_hit_rate 应单调上升。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Protocol

from karvyloop.schemas import AtomRun

from karvyloop.cognition import TraceEntry, TraceStore

logger = logging.getLogger(__name__)

# docs/27 原文层容量环:**每个 sig(子目标/任务类型,≈ 随 role 数)保留多少条大块原文**。
# 总上限 = 这个 × sig 数 → **随系统多样性/角色数正相关增长**(Hardy:固定上限会让忙 role 挤掉
# 安静 role 还没被消费的上下文)。eval_fact(未消费)+ 提炼物不在内、永久。
TRACE_RAW_PER_SIG = 400
TRACE_RAW_MIN = 2000   # 地板:sig 还没几个时也留够近期工作量
from karvyloop.crystallize import (
    InMemoryUsageStore,
    SkillIndex,
    UsageStore,
    VerifyStore,
    crystallize as crystallize_skill,
    load_bound_skills,
    maybe_promote,
    observe,
    recall,
)


def _method_body(intent: str, run: object) -> str:
    """§13.2/13.5:从成功 run 抽**方法**(Goal + 证明过的工具序列),而非存答案。

    这是技能的"过程"镜像:命中后喂慢脑当制导,省 token 又不吐 stale。
    v1 用工具序列 + 输入摘要(已是可复用打法);更深的 schema 归纳(参数化/泛化)后续在此扩。
    """
    steps = []
    for i, tc in enumerate(getattr(run, "tool_calls", None) or [], 1):
        name = tc.get("name", "?") if isinstance(tc, dict) else getattr(tc, "name", "?")
        inp = tc.get("input", {}) if isinstance(tc, dict) else getattr(tc, "input", {})
        hint = ""
        if isinstance(inp, dict) and inp:
            k = next(iter(inp))
            hint = f"（{k}=…）"
        steps.append(f"{i}. {name}{hint}")
    steps_txt = "\n".join(steps) if steps else "(无工具调用;以推理为主)"
    return f"## Goal\n{intent}\n\n## Steps(上次证明可行的打法)\n{steps_txt}"


def _slow_brain_accepts_ctx(fn: object) -> bool:
    """slow_brain 是否接受 `ctx` 关键字(决定 drive 要不要传上下文)。

    拍 9.1c:向后兼容老 `def slow_brain(intent)` —— 不接 ctx 的就只传 intent(0 回归)。
    """
    import inspect
    try:
        params = inspect.signature(fn).parameters.values()
    except (ValueError, TypeError):
        return False
    for p in params:
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if p.name == "ctx" and p.kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            return True
    return False


class Brain(str, Enum):
    """一次 drive() 走的是哪条脑。"""
    FAST = "fast"   # 召回命中 → 直接给已结晶技能(快脑)
    SLOW = "slow"   # recall miss → 跑 slow_brain(forge)


@dataclass
class DriveResult:
    """一次 drive() 的完整结果(给上层展示 / 打日志 / 喂统计)。"""
    brain: Brain                  # 走了哪条脑
    intent: str                   # 入参
    text: str = ""                # 慢脑的最终输出文本;快脑是 skill body
    sig: str = ""                 # 这次算出来的 sig(快脑/慢脑都填)
    skill_name: str = ""          # 命中技能名(快脑;慢脑为空,除非事后结晶)
    restored: bool = False        # 命中归档技能时是否被 auto-restore
    crystallized: bool = False    # 这次慢脑跑完后是否触发了结晶
    fast_brain_hit: bool = False  # 这次是否走快脑(brain==FAST 的简写)
    ctx_dependent: bool = False   # 拍 9.1b:本句是否被上下文依赖门判为强依赖(跳快脑+不结晶)
    task_id: str = ""             # M3+ 批 6:本次 drive 的 trace task_id(给 cmd_replay 用)
    terminal: str = ""            # docs/02 §15:慢脑终止语义(Terminal.value)上冒;空=快脑/正常
    # 北极星统计点(每个 MainLoop 实例累加)
    stats: "DriveStats" = field(default_factory=lambda: DriveStats())


@dataclass
class DriveStats:
    """主循环运行统计 — 北极星指标的最小实现。"""
    drive_calls: int = 0
    fast_brain_hits: int = 0
    slow_brain_runs: int = 0
    crystallizations: int = 0
    auto_restores: int = 0

    @property
    def fast_brain_hit_rate(self) -> float:
        if self.drive_calls == 0:
            return 0.0
        return self.fast_brain_hits / self.drive_calls


class SlowBrain(Protocol):
    """慢脑协议:接 intent,返回 (text, AtomRun) 二元组。"""

    def __call__(self, intent: str) -> "tuple[str, AtomRun]": ...


# ---- 主循环 ----

class MainLoop:
    """主循环 driver。把 recall ↔ slow-brain ↔ observe ↔ crystallize 拼成一条线。

    用法:
        ml = MainLoop(skills_dir=Path("~/.karvyloop/skills"))
        ml.bootstrap()  # 首次:从磁盘重建 SkillIndex
        result = ml.drive("summarize this report", slow_brain=my_slow_brain)
        print(result.brain, result.text)
    """

    def __init__(
        self,
        *,
        skills_dir: Path,
        store: Optional[UsageStore] = None,
        verify: Optional[VerifyStore] = None,
        skill_index: Optional[SkillIndex] = None,
        scope: str = "user",
        clock: Optional[Callable[[], float]] = None,
        trace: Optional[TraceStore] = None,
        trace_funnel: Optional[object] = None,
        thresholds: Optional[object] = None,
        result_classifier: Optional[Callable[[str, str, list], str]] = None,  # §13.3 语义判 stable|dynamic;None→默认 dynamic
    ) -> None:
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        # 9.4:结晶灵敏度旋钮(config 可调;默认 = 原硬编码值)
        from karvyloop.crystallize.crystallize import DEFAULT_THRESHOLDS
        self.thresholds = thresholds if thresholds is not None else DEFAULT_THRESHOLDS
        self.store = store or InMemoryUsageStore()
        self.verify = verify or VerifyStore()
        # docs/02 §14:atom 层结晶裁判 = role 多维分级满意度(达成 from verify+success / 效率 from 步数)。
        # 与 verify/usage 并行的独立存储,信用按 sig(子目标)隔离。
        from karvyloop.crystallize import SatisfactionStore
        self.satisfaction = SatisfactionStore()
        self._last_task_id = ""   # drive 记下,background_review 据此只评最近 task(控开销)
        # docs/44 断⑧:dynamic 重跑计数的去抖水位(sig → 上次计数时刻;防爆发灌计数,同 observe 窗口)
        self._rerun_accounted_at: dict[str, float] = {}
        self.skill_index = skill_index if skill_index is not None else SkillIndex()
        self.scope = scope
        # 时钟(测试用);默认 wall clock。生产路径不传,observe/maybe_promote
        # 仍按 wall clock 走 —— 这个 clock 主要给"可复现"用,不影响真实使用。
        self._clock = clock if clock is not None else time.time
        self.stats = DriveStats()
        # M3+ 批 6:Trace 持久化底座(append-only)。生产路径走 SqliteTraceStore;
        # 测试/MainLoop 单跑默认 InMemoryTraceStore,行为不变。
        self.trace = trace if trace is not None else TraceStore(clock=self._clock)
        # B-5 标定埋点(docs/68 P1 未标定常数族):把本 loop 的 Trace 装成**进程级**标定 sink——
        # 散在 conversation/channels/console 等无 Trace 句柄模块里的常数分布埋点由此落账。
        # fail-soft 观测面,不反哺业务面;生产恰好一个 MainLoop(与周报/评价共用同一份事件底座),
        # 测试多实例 last-wins 只影响标定数据归属,不影响任何行为。弱引用,不给弃店续命。
        try:
            from karvyloop.cognition.calibration import set_calibration_trace
            set_calibration_trace(self.trace)
        except Exception:
            pass
        # 拍 9.3c(修 D1):漏斗原文层(fastbrain.TraceIndex,duck-type 只调 append_raw)。
        # 注入则每次 drive 把事件落原文层 → 提炼器异步 原文→摘要→习惯(docs/27)。
        # 默认 None = 不写漏斗(0 回归;此前原文层无写入者是断链根因)。
        self._trace_funnel = trace_funnel
        # §13.3:结果可缓存性的语义判定器(intent, answer, tool_calls)→ "stable"|"dynamic"。
        # 控制台注入(它有 gateway);无注入(测试/--no-llm)→ 默认 dynamic(宁重跑不投毒)。
        self._result_classifier = result_classifier
        # 命名可读性(S):可读技能名生成器(intent)->kebab 短名。与 result_classifier 同一套注入
        # (console 有 gateway 时接);None=确定性 kebab(intent)兜底,再空退 skill_<hash>(0 回归)。
        self._skill_namer = None
        # docs/40 §1:重启后从 Trace 的 satisfaction 结果重建满意度水位 + 样本,
        # 否则持久 Trace 里的历史 eval_fact 会被重复评(对抗验收 CRITICAL #1 重启双计)。
        try:
            from karvyloop.crystallize import rehydrate as _rehydrate_sat
            _rehydrate_sat(self.trace, self.satisfaction)
        except Exception:
            logger.warning("[trace_eval] 满意度水位重建失败;评价从空水位起(可能重评历史)", exc_info=True)
        # §14.2 做好·质量维(慢侧):同步 callable(intent,产出)→(quality,critique)。
        # console 用 gateway 桥成同步后经 set_atom_quality_judge 注入;None=只确定性评(0 回归)。
        self._atom_quality_judge = None
        # docs/40 §6 丙 跨-run 经验蒸馏裁判(更慢):同步 callable(对比材料)→ lesson_text;None=不蒸。
        self._lesson_judge = None
        # Trace-conditioned 技能修订(crystallize.revision,慢侧):同步 callable(材料)→ 修订原文;
        # None=不修(0 回归)。大改 H2A 卡出口 = proposal_sink(接 PendingProposalRegistry.register)。
        self._revision_judge = None
        self._revision_proposal_sink = None

    def set_atom_quality_judge(self, judge) -> None:
        """接线 atom 质量裁判(慢侧;§14.2)。judge: (intent, output_text) → (quality, critique)。"""
        self._atom_quality_judge = judge

    def set_skill_namer(self, namer) -> None:
        """接线可读技能名生成器(命名可读性 S)。namer: (intent) → kebab 短名;None=确定性 kebab 兜底。"""
        self._skill_namer = namer

    def quality_review(self) -> int:
        """**慢侧**(daily_poll 节奏,docs/40 §3)质量评:读 Trace 里已确定性评、做对站住的 run →
        LLM 评质量 → 补到样本 + 回写 Trace。无注入裁判 → 0(确定性满意度照常,质量维待接 gateway)。"""
        judge = self._atom_quality_judge
        if judge is None:
            return 0
        from karvyloop.crystallize import judge_pending_quality
        try:
            return judge_pending_quality(self.trace, self.satisfaction, judge=judge, clock=self._clock)
        except Exception:
            logger.warning("[trace_eval] 质量评失败;维护继续", exc_info=True)
            return 0

    def pending_quality_count(self, *, cap: Optional[int] = None) -> int:
        """待 LLM 质量评的 run 数(纯计数,不调 LLM)。给自适应节奏判积压:活跃用户攒够了就提前评,
        不等满 24h(否则差技能污染召回排序最多 24h)。`cap`=数到够就停。无注入裁判仍可数(0 回归)。"""
        from karvyloop.crystallize import pending_quality_count as _count
        try:
            return _count(self.trace, self.satisfaction, cap=cap)
        except Exception:
            return 0

    def set_lesson_judge(self, judge) -> None:
        """接线跨-run 经验蒸馏裁判(慢侧;docs/40 §6 丙)。judge: (对比材料) → lesson_text。"""
        self._lesson_judge = judge

    def lessons_review(self) -> int:
        """**慢侧**(daily_poll)经验学习一轮:① 戊·**验证**已落地的 provisional lesson(纯测量,
        真提升→confirm / 没提升→reject 并撤出 SKILL.md);② 丙·**蒸馏**新规律(避开被拒缓冲 +
        编辑预算)。返回本轮新蒸出的规律数。无注入裁判仍会跑①验证(0 回归)。"""
        from karvyloop.crystallize import distill_lessons, validate_lessons
        try:
            v = validate_lessons(self.trace, self.satisfaction, skills_dir=self.skills_dir,
                                 skill_index=self.skill_index, clock=self._clock)
            if v.get("reverted"):
                logger.info("[lessons] 忠实自进化:撤回有害规律 %s 条", v.get("reverted"))
        except Exception:
            logger.warning("[lessons] lesson 验证失败;维护继续", exc_info=True)
        judge = self._lesson_judge
        if judge is None:
            return 0
        try:
            return distill_lessons(self.trace, self.satisfaction, judge=judge,
                                   skills_dir=self.skills_dir, skill_index=self.skill_index,
                                   clock=self._clock)
        except Exception:
            logger.warning("[lessons] 跨-run 蒸馏失败;维护继续", exc_info=True)
            return 0

    def set_revision_judge(self, judge) -> None:
        """接线技能修订裁判(慢侧)。judge: (材料:现方法+失败 Trace 摘要) → LLM 原文。"""
        self._revision_judge = judge

    def set_revision_proposal_sink(self, sink) -> None:
        """接线大改 H2A 卡出口。sink: callable(Proposal)(通常 = PendingProposalRegistry.register)。"""
        self._revision_proposal_sink = sink

    def revision_review(self) -> dict:
        """**慢侧**(daily_poll 节奏)技能修订一轮:客观信号差的技能(从 Trace 派生的满意度)→
        LLM 修 Steps;小改自动落 + SKILL.md Changelog 审计,大改出 revise_skill H2A 卡。
        无注入裁判 → {"revised":0,"proposed":0}(0 回归)。drive 热路径零改动(跑评分离)。"""
        judge = self._revision_judge
        if judge is None:
            return {"revised": 0, "proposed": 0}
        from karvyloop.crystallize import revise_underperforming
        try:
            return revise_underperforming(
                self.trace, self.satisfaction, judge=judge,
                skills_dir=self.skills_dir, skill_index=self.skill_index,
                proposal_sink=self._revision_proposal_sink, clock=self._clock)
        except Exception:
            logger.warning("[revision] 技能修订失败;维护继续", exc_info=True)
            return {"revised": 0, "proposed": 0}

    def set_trace_funnel(self, funnel: object) -> None:
        """接线漏斗原文层(entry 把 IntentAnalyst 共享的 TraceIndex 接进来,9.3c)。"""
        self._trace_funnel = funnel

    # 漏斗原文事件单字段字节上限:漏斗要的是**模式**不是全文,大输出(编码几十KB)若整段落进
    # 10MB 环,几百条就滚完、提炼器(daily)还没消费就丢了。截断稳住"环里装的事件数"(回 Hardy 10MB 问)。
    _FUNNEL_FIELD_BYTES = 2000

    def _emit_funnel_event(self, payload: dict) -> None:
        """把一个事件落漏斗原文层(docs/27 TR-1:trace 是提炼真相源)。失败不阻断。
        大字符串字段先过 HR-9 截断(基建),防大输出把容量环冲爆。"""
        if self._trace_funnel is None:
            return
        try:
            from karvyloop.context.truncate import truncate_str_utf8
            cap = self._FUNNEL_FIELD_BYTES

            def _slim(v, depth=0):
                if isinstance(v, str) and len(v) > cap:
                    return truncate_str_utf8(v, cap)[0]
                if isinstance(v, dict) and depth < 2:     # 递归一两层:防嵌套大字段(如 output.text)漏网
                    return {k: _slim(x, depth + 1) for k, x in v.items()}
                return v

            self._trace_funnel.append_raw({k: _slim(v) for k, v in payload.items()})
        except Exception:
            pass

    # ---- docs/44 断⑧:召回命中/重跑的 usage 记账(早返回路径此前跳过 observe → usage 冻结)----

    def _touch_usage(self, sig: str, now: float) -> None:
        """召回命中 = "用进":只刷 last_used_at(不动计数,计数归 observe/重跑记账)。

        只用既有 get/put 接口 → InMemory / Sqlite 两个后端免改即生效。失败不阻断 drive。
        """
        try:
            st = self.store.get(sig)
            if st is not None and now > st.last_used_at:
                self.store.put(sig, st.model_copy(update={"last_used_at": now}))
        except Exception:
            logger.warning("[evict] 刷新 last_used_at 失败(sig=%s);drive 继续", sig[:8], exc_info=True)

    def _account_rerun(self, sig: str, *, success: bool, now: float) -> None:
        """dynamic 命中重跑的 usage/成败记账(29112e9 只补了 eval_fact,这里补 usage 半边)。

        与 observe 同一去抖窗口(防爆发灌计数)—— 窗口内只保留 _touch_usage 刷过的
        last_used_at,不重复 +1(去抖水位记在实例内 `_rerun_accounted_at`,不能借
        last_used_at:_touch_usage 在前,它已被推到本轮 now)。技能已结晶,这里的计数
        喂的是 evict("用进废退")与 success_rate 的诚实账本。失败不阻断 drive。
        """
        try:
            last = self._rerun_accounted_at.get(sig, 0.0)
            if (now - last) < self.thresholds.usage_debounce_sec:
                return   # 爆发窗口内:last_used_at 已刷,计数去抖
            st = self.store.get(sig)
            if st is None:
                return
            self.store.put(sig, st.model_copy(update={
                "usage_count": st.usage_count + 1,
                "success_count": st.success_count + (1 if success else 0),
                "failure_count": st.failure_count + (0 if success else 1),
                "last_used_at": max(now, st.last_used_at),
            }))
            self._rerun_accounted_at[sig] = now
        except Exception:
            logger.warning("[evict] 技能重跑 usage 记账失败(sig=%s);drive 继续", sig[:8], exc_info=True)

    def _fold_bound_supports(self, hit: object, prefer: "list[str]") -> None:
        """把角色**绑定**但未成主命中/未被 overlap 选中的技能,并进 hit.supports(保证在场)。

        绑定 = 显式声明,优先级高于 overlap 碰出来的支持:绑定占位在前、overlap 支持补足到组合上限。
        绑定技能名查不到(归档/删)静默跳过(load_bound_skills 已处理),不阻断 drive。失败不阻断。
        """
        try:
            from karvyloop.crystallize.recall import _MAX_SUPPORTS
            bound = load_bound_skills(list(prefer or []), skills_dir=self.skills_dir,
                                      skill_index=self.skill_index)
            if not bound:
                return
            existing = getattr(hit, "supports", None) or []
            seen_names = {hit.name} | {s.name for s in existing}
            # 绑定技能里排除:已是主命中 / 已在 overlap supports 里的(去重)
            fresh_bound = [b for b in bound if b.name not in seen_names]
            if not fresh_bound:
                return
            # 绑定占位在前,overlap 支持补足;整体裁到组合上限(保守有界)
            merged = fresh_bound + list(existing)
            hit.supports = merged[:_MAX_SUPPORTS]
        except Exception:
            logger.warning("[recall] 折叠角色绑定支持技能失败;drive 继续", exc_info=True)

    # ---- 启动:从磁盘重建索引 ----

    def bootstrap(self) -> int:
        """从 skills_dir 重建 SkillIndex(读所有 SKILL.md frontmatter.signature)。
        首次接入主循环时调一次。返回收进索引的条数。
        """
        return self.skill_index.rebuild_from_disk(self.skills_dir)

    # ---- 核心:跑一次 ----

    def drive(
        self,
        intent: str,
        *,
        slow_brain: SlowBrain,
        ctx: object = None,
        scope: "Optional[str]" = None,   # brick3+:场-scoped 召回/结晶(None=用 self.scope,0 回归)
        fresh: bool = False,   # True=一次性步骤(workflow/圆桌):跳过 recall+observe+结晶,纯跑慢脑
        prefer: "Optional[list[str]]" = None,   # 角色**绑定**技能名(COMPOSITION.yaml skills:)→ 召回优先
    ) -> DriveResult:
        """跑一次主循环(可观测性①:drive 是 run_id 的生成入口)。返回 DriveResult。

        run_scope 在此裹一次:本次 drive 链上(慢脑/forge → gateway.complete → 工具)写的
        Trace 条目 + token 账本行都自动带同一个 run_id(contextvar 透传,零 I/O 零行为变化);
        `karvyloop replay --run <id>` 据此把一次 run 串起来看。
        """
        from karvyloop.cognition.trace import run_scope
        with run_scope():
            return self._drive(intent, slow_brain=slow_brain, ctx=ctx,
                               scope=scope, fresh=fresh, prefer=prefer)

    def _drive(
        self,
        intent: str,
        *,
        slow_brain: SlowBrain,
        ctx: object = None,
        scope: "Optional[str]" = None,
        fresh: bool = False,
        prefer: "Optional[list[str]]" = None,
    ) -> DriveResult:
        """drive 的本体(run_scope 之内)。返回 DriveResult。

        流程:
          0. 上下文依赖门(CV-9):intent 强依赖当前对话(指代/省略)→ 跳快脑 + 不结晶
          1. recall(intent) → 命中?(快脑)
          2. miss → slow_brain(intent) → 拿到 (text, run)
          3. observe([run]) → store 更新
          4. maybe_promote + 失败计数(自动 verify.mark_verified if success)
             + 触发关 1+关 2 → crystallize()(写盘 + SkillIndex.register)

        Args:
            ctx: 当前对话上下文的**只读**视图(Conversation.context_view() 的结果,
                 或任何"真值=有前文"的对象)。默认 None = 无上下文(向后兼容,
                 行为与拍 9.1a 之前完全一致,0 回归)。drive **不**在内部攒状态(CV-5)。
        """
        self.stats.drive_calls += 1
        eff_scope = scope or self.scope   # brick3+:本轮场作用域(私聊=user,业务域=domain)
        result = DriveResult(
            intent=intent, brain=Brain.SLOW, stats=self.stats,
            task_id=uuid.uuid4().hex[:16],
        )
        now = self._clock()

        # 0. 上下文依赖门(CV-9 / FB-9):强依赖句只有慢脑能消解指代
        #    has_context=bool(ctx) — None / 空视图 → False → 行为同旧路径
        from karvyloop.karvy.fastbrain.context_gate import is_context_dependent
        ctx_dependent = is_context_dependent(intent, has_context=bool(ctx))
        result.ctx_dependent = ctx_dependent

        # 1. 召回(快脑)—— 上下文依赖句 / fresh(一次性步骤)**不走**快脑(跳过 recall)
        hit = None if (ctx_dependent or fresh) else recall(
            intent,
            skills_dir=self.skills_dir,
            scope=eff_scope,
            store=self.store,
            skill_index=self.skill_index,
            satisfaction=self.satisfaction,   # docs/40:飞轮产物回到行为(满意度影响召回排序)
            prefer=prefer,   # 角色绑定技能(COMPOSITION.yaml skills:)→ 弱匹配也加权胜出(绑定优先于碰运气)
        )
        # docs/44 断⑧(evict 误杀):**任何**召回命中都是"用进" —— 两条命中路径(stable 回放 /
        # dynamic 重跑)都早返回跳过 observe,usage 冻结在结晶时刻,天天用的技能 30 天照样被
        # 归档,再靠命中 restore 兜底 → archive/restore 横跳 + 错 badge。这里用 drive 自己的
        # 时钟刷新 last_used_at(recall_count_inc 已在 recall() 里 +1,但它不带时钟 ——
        # 时间戳归 drive 记,与 observe/evict 同一时钟)。
        if hit is not None and hit.sig:
            self._touch_usage(hit.sig, now)
        # 角色绑定技能接通(load_bound_skills 真接进 drive):绑定 = 随身声明、不靠模糊召回碰运气。
        # dynamic 命中时,把角色**绑定**但**未成为主命中/未被 overlap 选中**的技能,作为**保证在场**的
        # 支持技能并进 supports(绑定优先于 overlap 挑出的支持:显式声明 > 碰匹配)。总量仍受组合上限约束
        # (保守:绑定占位在前,overlap 支持补足到上限)。stable 回放路径不组合(回放缓存正文,§13.6)。
        if (hit is not None and prefer
                and (hit.result_reuse or "dynamic").lower() != "stable"):
            self._fold_bound_supports(hit, prefer)
        # #2 §13.6:命中后分两条路 —— stable 才回放缓存结果;dynamic(默认)**不回放**,拿当前输入重跑。
        guided_skill = None   # 非 None = 命中了 dynamic 技能 → 重跑(产出新鲜),且不再 observe/结晶
        if hit is not None and (hit.result_reuse or "dynamic").lower() == "stable":
            # 可复现模式(罕见):结果语义稳定 → 回放缓存正文(这才是真"快脑命中")
            result.brain = Brain.FAST
            result.fast_brain_hit = True
            result.text = hit.body
            result.sig = hit.sig
            result.skill_name = hit.name
            result.restored = hit.restored
            self.stats.fast_brain_hits += 1
            if hit.restored:
                self.stats.auto_restores += 1
            self.trace.append(TraceEntry(
                task_id=result.task_id, kind="fast_brain_hit",
                payload={"intent": intent, "sig": hit.sig, "skill_name": hit.name,
                         "restored": hit.restored},
                ts=now, source="main_loop",
            ))
            self._emit_funnel_event({
                "kind": "intent", "intent": intent, "brain": "fast",
                "skill_name": hit.name, "sig": hit.sig, "ts": now,
            })
            return result
        if hit is not None:
            # dynamic(默认,§13.6):命中只说明"这类活儿干过"。**绝不回放旧答案**(那是投毒) ——
            # 拿当前输入重跑出新结果。slice1 先保证"不吐 stale";省 token 的"带方法制导"待 body 改存方法。
            guided_skill = hit
            result.skill_name = hit.name
            result.restored = hit.restored
            if hit.restored:
                self.stats.auto_restores += 1


        # 2. 慢脑 —— 慢脑读 ctx 消解多轮指代(CV-8)。
        #    向后兼容:slow_brain 若不接 ctx kwarg,退回 slow_brain(intent)(0 回归)。
        # §13.6:dynamic 命中 → 把技能的**方法**作前缀喂慢脑制导(省 token),但明令"用当前输入重得结果、
        #        绝不照搬旧结论" → 既省 token 又不吐 stale。
        brain_intent = intent
        if guided_skill is not None and (guided_skill.body or "").strip():
            # 修订闭环"读"半条腿:方法段与 improve.py 写回的偏好/纠正段**分开标注**
            # (纠正段必须遵守、冲突时优先),否则 `## Remove` 混在"上次的打法"里被照抄。
            from karvyloop.crystallize import compose_rerun_context
            brain_intent = compose_rerun_context(guided_skill, intent)
        self.stats.slow_brain_runs += 1
        try:
            if ctx is not None and _slow_brain_accepts_ctx(slow_brain):
                text, run = slow_brain(brain_intent, ctx=ctx)
            else:
                text, run = slow_brain(brain_intent)
        except Exception as e:
            # 可观测性②:慢脑代码缺陷 fail-loud 上冒(执行器已按白名单把 TypeError 等放上来)——
            # 真因(异常类名 + traceback)落 Trace 再上冒,别让"误诊成模型/网络调不通"再发生;
            # 用户可见文案由上层(桥/handler)兜,内部记录必须带真因。
            import traceback as _tb
            try:
                self.trace.append(TraceEntry(
                    task_id=result.task_id, kind="error",
                    payload={
                        "error_type": type(e).__name__,
                        "error": str(e),
                        "traceback": _tb.format_exc(),
                        "stage": "slow_brain",
                    },
                    ts=self._clock(), source="main_loop",
                ))
            except Exception:
                logger.warning("[drive] 慢脑异常落 Trace 失败(仍上冒原异常)", exc_info=True)
            raise
        result.text = text
        result.terminal = getattr(run, "terminal", None) or ""  # docs/02 §15:终止语义上冒到 DriveResult
        result.sig = run.trace_ref  # 慢脑的 sig 由 observe 算出(下面再填)
        # 实际 sig:用 signature 模块从 run 算 —— 比 trace_ref 更准
        from karvyloop.crystallize.signature import compute_signature
        sig = compute_signature(run)
        result.sig = sig

        # M3+ 批 6:慢脑产出先入 trace,observe 路径不变(向后兼容;M1 v1 简化不
        # 改 observe 签名 — observe 直接拿 [run] 是文档化的契约,改它会破坏 11+ 测试)。
        self.trace.append(TraceEntry(
            task_id=result.task_id,
            kind="atom_run",
            payload={
                "atom_id": run.atom_id,
                "input": dict(run.input) if isinstance(run.input, dict) else {},
                "output": dict(run.output) if isinstance(run.output, dict) else run.output,
                "success": run.success,
                "tool_calls": list(run.tool_calls),
                "trace_ref": run.trace_ref,
                "ts": run.ts,
                "terminal": getattr(run, "terminal", None) or "",  # §15:终止语义入 Trace(不可行报告卡溯源)
            },
            ts=run.ts or now,
            source="main_loop",
            agent="",  # intent 原文不存(replay 看 task_id 链回去)
        ))

        # 到此为止、**不** observe/结晶 的两种情况:
        #  - fresh:一次性协作步骤(workflow/圆桌),否则共享 boilerplate 前缀被聚类归并 → 串味。
        #  - guided_skill:命中了已存的 dynamic 技能、刚拿当前输入重跑出新结果 —— 技能已在,别再结晶一份。
        if fresh or guided_skill is not None:
            if guided_skill is not None:
                result.sig = guided_skill.sig   # 归属到被复用的那个技能
                # docs/44 断⑧:重跑也是"用进" —— 补 usage/成败记账(带去抖),否则技能的
                # usage 冻结在结晶时刻,evict 只看 usage_score → 天天重跑照样 30 天被归档。
                if guided_skill.sig:
                    self._account_rerun(guided_skill.sig, success=bool(run.success), now=now)
                # 修订闭环数据源:技能重跑的客观信号也必须进 Trace(此前这条早返回把
                # eval_fact 漏掉了 → 技能重跑成败/满意度从不入账,Trace 驱动修订无米下锅)。
                # sig 归属被复用的技能;埋在既有 eval_fact 写入路径上,评价器零改动即可消费。
                if guided_skill.sig:
                    try:
                        from karvyloop.crystallize import EVAL_FACT_KIND
                        self.trace.append(TraceEntry(
                            task_id=result.task_id, kind=EVAL_FACT_KIND,
                            payload={
                                "sig": guided_skill.sig,
                                "success": bool(run.success),
                                "verified": bool(run.success and not ctx_dependent
                                                 and run.tool_calls),
                                "steps": len(run.tool_calls),
                                "trace_ref": run.trace_ref,
                                "skill_name": guided_skill.name,
                                "skill_rerun": True,   # 标记:这是召回命中后的重跑(审计/修订可辨)
                            },
                            ts=now, source="main_loop",
                        ))
                        self._last_task_id = result.task_id
                    except Exception:
                        logger.warning("[atom_critic] 写技能重跑 eval_fact 失败(sig=%s);drive 继续",
                                       guided_skill.sig[:8], exc_info=True)
            self._emit_funnel_event({
                "kind": "intent", "intent": intent, "brain": "slow",
                "success": run.success, "crystallized": False,
                "ctx_dependent": ctx_dependent, "ts": now,
            })
            return result

        # 3. 投影(去抖 + token-overlap 累积聚类,均走可配置旋钮)
        observe([run], self.store, clock=self._clock,
                debounce_sec=self.thresholds.usage_debounce_sec,
                cluster_threshold=self.thresholds.cluster_overlap_threshold)

        # 3b. 对齐 cluster sig:observe 可能把本次意图**归并**到已有 cluster(token-overlap),
        #     其 sig != 本次精确签名。结晶判定/验证门必须用 observe 实际累积的那个 cluster sig,
        #     否则查的是 usage=1 的精确签名 → 永不结晶(门1 真机抓到的对齐 bug)。
        if (self.thresholds.cluster_overlap_threshold > 0
                and isinstance(run.input, dict) and run.input.get("intent")):
            from karvyloop.crystallize.cluster import match_cluster
            matched = match_cluster(
                run.input["intent"],
                ((s, st.intent_repr) for s, st in self.store.all()),
                self.thresholds.cluster_overlap_threshold,
            )
            if matched:
                sig = matched
                result.sig = sig

        # 3c. 跑评分离(docs/40 §3):drive **只管跑** —— 把"评价事实"(对齐后的 sig、是否核验、
        #     步数、回链 trace_ref)写进 Trace,**不在热路径算任何满意度**。是否核验 = 与下方
        #     mark_verified 同一判据(success + 非 ctx_dependent + 用了工具),在此先算好写进事实。
        #     异步评价器(crystallize.trace_eval)离热路径读这些事实算分(经 background_review/daily_poll),
        #     评价只从 Trace 派生(docs/40 §1),不旁路、不弱化 Trace。
        this_run_verified = bool(run.success and not ctx_dependent and run.tool_calls)
        try:
            from karvyloop.crystallize import EVAL_FACT_KIND
            self.trace.append(TraceEntry(
                task_id=result.task_id, kind=EVAL_FACT_KIND,
                payload={
                    "sig": sig,
                    "success": bool(run.success),
                    "verified": this_run_verified,
                    "steps": len(run.tool_calls),
                    "trace_ref": run.trace_ref,
                },
                ts=now, source="main_loop",
            ))
            self._last_task_id = result.task_id
        except Exception:  # 事实落 Trace 失败不拖垮 drive,但**绝不静默**(对抗验收 C2)
            logger.warning("[atom_critic] 写 eval_fact 失败(sig=%s);drive 继续", sig[:8], exc_info=True)

        # 4. 验证门 + 结晶
        #    CV-11:上下文依赖句(它=文件X 这种临时映射)**绝不**结晶进永久库
        #    —— 它的 signature 含未消解的指代,凝出来是垃圾技能(下次"它"指别的就错)。
        # brick3:**只结晶真干了活的 run(用了工具)**。纯对话/问候/"你是谁"这类没动工具的回复
        # 不是可复用技能,结晶进去 = 污染技能库 + 被快脑跨场 replay(Hardy 抓到的串味真 bug:
        # 一句"你是谁"在业务域被结晶成 user 全局技能,私聊时冒充设计师)。技能 = 用工具的任务流程。
        if run.success and not ctx_dependent and run.tool_calls:
            # 自报验据(docs/44 断⑭:名实如实 —— 这是执行器自报成功,不是独立验收;
            # note 用常量,与 checker verdict 回流的 INDEPENDENT_NOTE 分开存)
            from karvyloop.crystallize import SELF_REPORT_NOTE
            self.verify.mark_verified(
                sig, run.trace_ref, note=SELF_REPORT_NOTE,
                clock=self._clock,
            )
            # 闸门升级(docs/44 断⑭):从"自报成功 N 次"→"跑成且**没被打差评** N 次"。
            # satisfaction 是 Trace 派生的(含独立验收 FAIL 回流的 0 分样本);无样本=旧行为。
            decision = maybe_promote(sig, self.store, self.verify, now=now,
                                     thresholds=self.thresholds,
                                     satisfaction=self.satisfaction)
            if decision.kind.value == "ready":
                # 命名可读性(S):不再裸 `skill_<hash>` —— 用**已在调的那次 LLM**(判 stable/dynamic 的
                # namer,注入才用;无注入→确定性 kebab(intent))顺手起个人类可读名,进匹配 token + 面板可读。
                # 幂等键是 **sig 不是 name**:同 sig 已结晶过 → 复用旧名(否则 LLM 每次出不同名 = 重复结晶)。
                # 无注入 namer → 保持确定性 `skill_<hash>` 兜底(0 回归);注入了才用 LLM 起 kebab 可读名。
                existing_name = self.skill_index.name_for_sig(sig)
                if existing_name is not None:
                    name = existing_name
                elif self._skill_namer is not None:
                    from karvyloop.crystallize import readable_skill_name
                    name = readable_skill_name(
                        intent, sig, namer=self._skill_namer,
                        taken={e.name for e in self.skill_index.all()},
                    )
                else:
                    name = f"skill_{sig[:8]}"
                # 同 sig 未结晶过才写盘(register 会覆盖,但 SKILL.md 写盘要看存在否)
                if existing_name is None:
                    try:
                        # §13.3:语义判可缓存性(模型判;无判定器→默认 dynamic,宁重跑不投毒)。
                        reuse = "dynamic"
                        if self._result_classifier is not None:
                            try:
                                r = self._result_classifier(intent, text, list(run.tool_calls))
                                reuse = "stable" if str(r).lower() == "stable" else "dynamic"
                            except Exception:
                                reuse = "dynamic"
                        # §13.2:dynamic 存**方法**(过程/打法),命中重跑;stable 才存**结果**供回放。
                        skill_body = (text or "(no text)") if reuse == "stable" else _method_body(intent, run)
                        s = crystallize_skill(
                            sig,
                            name=name,
                            description=intent,
                            body=skill_body,
                            when_to_use=intent,
                            arguments=None,
                            store=self.store,
                            verify=self.verify,
                            skills_dir=self.skills_dir,
                            scope=eff_scope,
                            now=now,
                            thresholds=self.thresholds,   # 与上面 maybe_promote 同一套阈值(否则配置旋钮半接)
                            result_reuse=reuse,
                            satisfaction=self.satisfaction,   # 同一份满意度(否则闸门半接,重判不一致会抛)
                        )
                        # 写进 SkillIndex(下次 recall 直接命中)
                        self.skill_index.register(
                            name=s.name, sig=sig, scope=eff_scope,
                            when_to_use=intent, description=intent,
                            path=s.manifest.get("path", str(self.skills_dir / name / "SKILL.md")),
                        )
                        result.crystallized = True
                        result.skill_name = s.name
                        self.stats.crystallizations += 1
                        # M3+ 批 6:结晶事件进 trace
                        self.trace.append(TraceEntry(
                            task_id=result.task_id,
                            kind="crystallize",
                            payload={
                                "sig": sig,
                                "name": s.name,
                                "when_to_use": intent,
                                "trace_ref": run.trace_ref,
                            },
                            ts=now,
                            source="main_loop",
                        ))
                    except (ValueError, OSError) as e:
                        # fail-loud(§0.7):结晶写盘/校验失败别静默吞 —— 否则"明明 ready 却不结晶"
                        # 永远查不出(VM 真机就因这个静默 except 多花了好几轮才定位)。
                        logger.warning("[crystallize] ready 但写入失败,本次不结晶(sig=%s): %s",
                                       sig[:8], e)
                elif decision.kind.value != "ready":
                    logger.info("[crystallize] 未结晶(sig=%s usage=%s): %s",
                                sig[:8], getattr(self.store.get(sig), "usage_count", "?"),
                                getattr(decision, "reason", ""))
        elif run.success and run.tool_calls and ctx_dependent:
            logger.warning("[crystallize] 跳过:本次意图被判 ctx_dependent(含未消解指代,不结晶)")

        # 9.3c:慢脑事件落漏斗原文层(提炼器异步 原文→摘要→习惯,docs/27)
        self._emit_funnel_event({
            "kind": "intent", "intent": intent, "brain": "slow",
            "success": run.success, "crystallized": result.crystallized,
            "ctx_dependent": ctx_dependent, "ts": now,
        })

        return result

    # ---- 独立验收 verdict 回流(docs/44 断⑭:验证门不再名实不符)----

    def record_verdict(
        self,
        sig: str,
        *,
        passed: bool,
        feedback: str = "",
        task_id: str = "",
        trace_ref: str = "",
    ) -> bool:
        """独立验收者(coding/checker)的 verdict 回流 —— 此前 verdict 只用于 replan,
        VerifyStore/eval_fact 全不知情,"验证门"实际只有执行器自报(docs/44 断⑭)。

        - PASS → VerifyStore 记一条 note=INDEPENDENT_NOTE 的独立验据(与自报分开存,
          has_independent 据此判);技能已落盘且 frontmatter 有 verified 标 → 翻成 true。
        - PASS/FAIL 都写一条 eval_fact 进 Trace(kind 同 drive,评价器零改动即可消费):
          FAIL = success:false → 满意度 0 分样本 → 结晶闸门的"被打差评的不晋升"有了数据源。
        - inconclusive 的 verdict **不该**进来(没证据≠差评)—— 由调用方过滤。

        返回是否真记了账(sig 为空 / 全部落账失败 → False)。绝不抛(不阻断 pursue/handler)。
        """
        if not sig:
            return False
        ok = False
        note_fb = (feedback or "").strip().replace("\n", " ")[:120]
        vref = trace_ref or f"verdict://{task_id or uuid.uuid4().hex[:12]}/{uuid.uuid4().hex[:8]}"
        if passed:
            try:
                from karvyloop.crystallize import INDEPENDENT_NOTE
                note = f"{INDEPENDENT_NOTE}: {note_fb}" if note_fb else INDEPENDENT_NOTE
                self.verify.mark_verified(sig, vref, note=note, clock=self._clock)
                ok = True
            except Exception:
                logger.warning("[verify] 独立验据回流失败(sig=%s)", sig[:8], exc_info=True)
            # 诚实标跟着事实走:结晶时无独立验据标了 verified:false,现在真验过了 → 翻 true(幂等)
            try:
                name = self.skill_index.name_for_sig(sig)
                if name:
                    entry = self.skill_index.lookup_by_name(name)
                    if entry is not None and entry.path:
                        from karvyloop.crystallize import mark_skill_verified
                        mark_skill_verified(Path(entry.path))
            except Exception:
                logger.warning("[verify] 翻 verified 标失败(sig=%s);验据已入库", sig[:8], exc_info=True)
        # eval_fact:自报与独立验据分开存 —— trace_ref 不同(verdict:// 前缀),水位不撞。
        try:
            from karvyloop.crystallize import EVAL_FACT_KIND
            self.trace.append(TraceEntry(
                task_id=task_id or uuid.uuid4().hex[:16], kind=EVAL_FACT_KIND,
                payload={
                    "sig": sig,
                    "success": bool(passed),
                    "verified": bool(passed),
                    "steps": 0,   # verdict 无步数语义;baseline 忽略 0 步样本,不污染效率基线
                    "trace_ref": vref,
                    "checker_verdict": True,   # 标记:独立验收回流(审计/修订可辨,区分自报)
                    "feedback": note_fb,
                },
                ts=self._clock(), source="main_loop.verdict",
            ))
            ok = True
        except Exception:
            logger.warning("[verify] verdict eval_fact 落 Trace 失败(sig=%s)", sig[:8], exc_info=True)
        return ok

    # ---- 后台维护(可选)----

    def background_review(self) -> int:
        """跑一次 evict + **atom improve**(每轮主循环结束 / 后台 tick 时调)。

        返回被归档的 sig 数。

        **docs/02 §14(slice-b)拆接反点**:旧实现用 `steered_by_user`(人的纠正)force-improve
        写回 SKILL.md —— 这是"**人训 atom**",接反了问责链(atom 对 role 负责,不对人)。而且那条
        本就是**死路**:全代码库无任何写入者,`steered_by_user` 永远为空,improve 从没真跑过。
        现改为 atom 的 improve 由 **role 的质量评语**(满意度 critique,§14.2 第 3 条)驱动:
        把每个技能近期评语写回 SKILL.md 的 `Role critique` 段(`write_critiques_to_skill_md`
        按内容幂等,后台可反复跑)。人的纠正归 role 层决策偏好(§11),不在此。

        可观测性①:后台维护是**非 drive 入口**,自带一个 run_scope —— 本轮维护写的
        Trace 条目(quality/lesson 等)带同一 run_id,与 drive 的 run 不串。
        """
        from karvyloop.cognition.trace import run_scope
        with run_scope():
            return self._background_review()

    def _background_review(self) -> int:
        """background_review 的本体(run_scope 之内)。"""
        from karvyloop.crystallize import evict_stale, evaluate_pending, write_critiques_to_skill_md
        now = self._clock()
        # docs/40 §3 跑评分离:先跑 Trace-派生评价器(离执行热路径)——读 drive 写下的 eval_fact,
        # 算确定性满意度进 SatisfactionStore,并把结果回写 Trace(自反 + 重启水位源)。
        # 扫**所有待评**(tasks=None,自愈):水位按 trace_ref 去重,跳过/失败/并发都不会让 run 变孤儿
        # (对抗验收 CRITICAL #2)。优化:按 task 高水位只读新事实,留后(perf 非 correctness)。
        try:
            evaluate_pending(self.trace, self.satisfaction, clock=self._clock)
        except Exception:
            logger.warning("[trace_eval] 异步评价失败;维护继续", exc_info=True)
        for sig, _stats in self.store.all():
            if self.store.is_archived(sig):
                continue
            name = self.skill_index.name_for_sig(sig)
            if not name:
                continue
            crits = self.satisfaction.critiques(sig)
            if not crits:
                continue
            try:
                write_critiques_to_skill_md(self.skills_dir / name / "SKILL.md", crits, now=now)
            except Exception:
                pass  # 单个技能 improve 失败不拖垮整轮维护
        # docs/27 原文层容量环:cognition.trace 此前无界,串联越来越多(eval_fact/satisfaction/
        # lesson…)会一直涨。保提炼物、原文超额丢最旧(宽松上限,不碰近期工作的评价/蒸馏)。
        try:
            prune = getattr(self.trace, "prune_raw", None)
            if callable(prune):
                # 容量随 sig 数(≈ role/任务多样性)涨:role 越多、留得越多,不让忙 role 挤掉安静 role。
                n_sigs = max(1, len(self.satisfaction.sigs()))
                prune(max(TRACE_RAW_MIN, TRACE_RAW_PER_SIG * n_sigs))
        except Exception:
            logger.warning("[trace] 原文层容量环修剪失败;维护继续", exc_info=True)
        archived = evict_stale(self.store, skills_dir=self.skills_dir, now=now)
        return len(archived)


# ---- 默认慢脑(生产路径)----

def forge_slow_brain_factory(
    *,
    token,                 # CapabilityToken
    sandbox,               # Sandbox
    gateway,               # GatewayClient
    workspace_root: str,
    model_ref: str = "",
    max_turns: int = 30,
    governance: str = "",
    emitter: object = None,
    renderer: object = None,  # I(内测 U-05):cli.render.Renderer —— 默认 CLI 路径也实时流(emitter 在场时 emitter 优先)
    persona: object = None,   # 9.4e 方案 A:人格 system prompt(CodingPrompt);None=默认 coding 提示
    enable_compression: bool = True,  # loop step4a:对话/长任务慢脑默认开上下文治理(防 O(n²) 烧 token)
    mcp_tools: object = None,  # A:MCP 工具(console 启动时连好、注入 runtime_kwargs);None=无
    images: object = None,  # 多模态:[{data, media_type}];带进首条 user 消息(需视觉模型)
    atom_registry: object = None,  # §15.5:给了就把 create_atom 工具挂进工具集(role 无 atom 可用时自造)
    role_registry: object = None,  # §15.5:沉淀时把自造 atom 加进创建 role 的 composition
    self_create_role: str = "",    # §15.5:创建 role 的 id(沉淀归属);空=不归属某 role
    self_create_minted: object = None,  # §15.5:list 收集本次新造的 atom_id(调用方收尾沉淀用)
) -> SlowBrain:
    """生产慢脑工厂:把 forge.generate_and_run 包成同步 SlowBrain 协议。

    forge 是 async,这里用 asyncio.run 同步化(M1 v1:单次任务为主,先打通;
    后续 P1 真要并发再切回 async driver)。

    拍 9.2b:`governance`(业务域 value.md,CV-14)拼在最前 —— 让同一角色在不同企业
    受不同价值观约束。私聊(无 governance)行为同旧路径。
    """
    import asyncio
    from karvyloop.coding.forge import generate_and_run

    # §15.5:role 无现有 atom 可用时自造 —— 把 create_atom 工具并进工具集(与 MCP 工具同走 extra_tools)。
    # 仅在注入了 atom_registry 时挂(CLI/无注册表路径 0 回归)。
    _extra_tools = dict(mcp_tools) if isinstance(mcp_tools, dict) else {}
    if atom_registry is not None:
        try:
            from karvyloop.atoms.self_create import make_self_create_tool
            _extra_tools["create_atom"] = make_self_create_tool(
                gateway=gateway, atom_registry=atom_registry, role_registry=role_registry,
                model_ref=model_ref, role_id=self_create_role or None,
                minted=self_create_minted if isinstance(self_create_minted, list) else None)
        except Exception:
            pass  # 自造能力挂不上不该拖垮主执行路径

    def slow_brain(intent: str, *, ctx: object = None) -> tuple[str, AtomRun]:
        # 拍 9.1c:ctx(当前对话最近 N 轮)拼成前缀,让慢脑消解多轮指代(CV-8)。
        # 拍 9.2b:governance(域 value.md)拼最前(CV-14)。
        parts: list[str] = []
        if governance:
            parts.append(governance)
        prefix = _render_ctx_prefix(ctx)
        if prefix:
            parts.append(prefix)
        if parts:
            parts.append(f"当前请求:{intent}")
            effective_intent = "\n\n".join(parts)
        else:
            effective_intent = intent
        # 拍 9.3a:标 token 来源=forge(账本按 source 归属)
        from karvyloop.llm.token_ledger import token_source
        with token_source("forge"):
            rr = asyncio.run(generate_and_run(
                effective_intent, token, sandbox,
                gateway=gateway, workspace_root=workspace_root,
                model_ref=model_ref, max_turns=max_turns,
                emitter=emitter,  # 9.4:渲染事件收集器(None=旧行为,0 回归)
                renderer=renderer,  # I(内测 U-05):人读终端实时流(emitter 在场时 forge 内 emitter 优先)
                system_prompt=persona,  # 9.4e 方案 A:人格 prompt(None=默认 coding)
                enable_compression=enable_compression,  # step4a:上下文治理
                extra_tools=_extra_tools or None,  # A:MCP 工具 + §15.5 create_atom 并进 agent 工具集
                images=images or None,  # 多模态:首条 user 消息带图块
            ))
        # 结晶身份**只认裸用户意图**:LLM 收 effective_intent(带 governance/ctx 前缀),
        # 但 run.input["intent"] 必须还原成裸 intent —— 否则 compute_signature/cluster 把
        # governance/prealign 前缀算进去,所有同 governance 的 drive collapse 成一个 sig,
        # 真技能永不结晶 + 技能库被 governance 文本污染(VM 真机抓到:usage=16 全压在 prealign 块上)。
        # governance="" 且无 ctx 时 effective_intent==intent → 本行 no-op,0 回归(CLI 路径不变)。
        try:
            if effective_intent is not intent and isinstance(getattr(rr.run, "input", None), dict):
                rr.run.input["intent"] = intent
        except Exception:
            pass
        # fail-loud:**截断/异常终止要老实说**(原 run_loop.py 标注的 P1)。max_turns 被切、
        # 预算耗尽、断路 —— 都别把半截结果当"做完了"丢回去(大任务实测:30 轮被切,
        # 只写了 3/9 个文件却无任何"未完成"提示)。让用户/小卡知道"没干完,继续即可接着做"。
        return (_annotate_terminal(rr.text, getattr(rr, "terminal", None)), rr.run)

    return slow_brain


def _annotate_terminal(text: str, terminal: object) -> str:
    """非正常终止 → 在结果后追加一句诚实提示(COMPLETED 不动,0 回归)。"""
    from karvyloop.atoms.terminal import Terminal
    if terminal is None or terminal == Terminal.COMPLETED:
        return text
    notes = {
        Terminal.MAX_TURNS: "⚠ 达到单次执行的步数上限,这个任务还没做完 —— 跟我说「继续」我就接着做。",
        Terminal.BLOCKING_LIMIT: "⚠ token/成本预算用尽,任务可能没做完 —— 可继续或调高预算。",
        Terminal.CIRCUIT_OPEN: "⚠ 连续失败触发断路,已停下 —— 多半是哪步卡住了,我们看看再继续。",
        Terminal.ABORTED_STREAMING: "⚠ 生成被中断,结果可能不完整。",
        Terminal.ABORTED_TOOLS: "⚠ 工具执行阶段被中断,结果可能不完整。",
        Terminal.HOOK_STOPPED: "⚠ 被规则/钩子拦下停止。",
        Terminal.INFRA_DEAD: "⚠ 基础能力暂时不可用(模型/网络调不通),这不是任务本身的问题 —— 检查模型配置/网络后再试。",
    }
    note = notes.get(terminal, f"⚠ 非正常结束({getattr(terminal, 'value', terminal)}),结果可能不完整。")
    return ((text or "").rstrip() + "\n\n" + note) if text else note


# 拍 9.3b:对话 ctx 喂慢脑的 token 预算(docs/28 TK-2 分层索引 tier-1:工作记忆)
DEFAULT_CTX_TOKEN_BUDGET = 2000


def _render_ctx_prefix(ctx: object, *, token_budget: int = DEFAULT_CTX_TOKEN_BUDGET) -> str:
    """把对话上下文渲染成喂慢脑的前缀文本,**按 token 预算裁剪**(docs/28 TK-2)。

    ctx 是 duck-type:可迭代,每项有 .user_intent / .agent_response(Conversation.Turn)。
    分层索引 tier-1(工作记忆):最近的轮**逐字保留**,从新往旧累积到 token 预算就停 ——
    更早的丢(tier-2 LLM 摘要由 trace 漏斗异步做,见 docs/27/28,9.3c)。

    避免"对话越长每轮越贵(O(n²))"—— 每轮喂慢脑的 ctx 被 token 预算封顶。
    非该形态 / 空 → 返空串(0 回归)。
    """
    if not ctx:
        return ""
    try:
        from karvyloop.context.budget import count_tokens_text
        turns = list(ctx)
    except (TypeError, Exception):
        return ""
    if not turns:
        return ""
    # 从最新往最旧累积,超预算停(保留最近的逐字)
    kept_rev: list[str] = []
    used = 0
    truncated = False
    for t in reversed(turns):
        u = getattr(t, "user_intent", "")
        a = getattr(t, "agent_response", "")
        block_lines = []
        if u:
            block_lines.append(f"用户:{u}")
        if a:
            block_lines.append(f"小卡:{a}")
        if not block_lines:
            continue
        block = "\n".join(block_lines)
        cost = count_tokens_text(block)
        if used + cost > token_budget and kept_rev:
            truncated = True
            break
        kept_rev.append(block)
        used += cost
    if not kept_rev:
        return ""
    body = "\n".join(reversed(kept_rev))
    head = "对话上下文(最近几轮,供你消解指代/承接):\n"
    if truncated:
        head = "对话上下文(更早的已省略,以下是最近几轮):\n"
    return head + body


__all__ = [
    "Brain", "DriveResult", "DriveStats", "MainLoop", "SlowBrain",
    "forge_slow_brain_factory",
]
