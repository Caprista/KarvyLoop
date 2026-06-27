"""主循环 driver — 把 recall ↔ slow-brain ↔ observe ↔ crystallize 拼成一条线（cli/main_loop.py）。

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
from karvyloop.crystallize import (
    InMemoryUsageStore,
    SkillIndex,
    UsageStore,
    VerifyStore,
    crystallize as crystallize_skill,
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
        self.skill_index = skill_index if skill_index is not None else SkillIndex()
        self.scope = scope
        # 时钟(测试用);默认 wall clock。生产路径不传,observe/maybe_promote
        # 仍按 wall clock 走 —— 这个 clock 主要给"可复现"用,不影响真实使用。
        self._clock = clock if clock is not None else time.time
        self.stats = DriveStats()
        # M3+ 批 6:Trace 持久化底座(append-only)。生产路径走 SqliteTraceStore;
        # 测试/MainLoop 单跑默认 InMemoryTraceStore,行为不变。
        self.trace = trace if trace is not None else TraceStore(clock=self._clock)
        # 拍 9.3c(修 D1):漏斗原文层(fastbrain.TraceIndex,duck-type 只调 append_raw)。
        # 注入则每次 drive 把事件落原文层 → 提炼器异步 原文→摘要→习惯(docs/27)。
        # 默认 None = 不写漏斗(0 回归;此前原文层无写入者是断链根因)。
        self._trace_funnel = trace_funnel
        # §13.3:结果可缓存性的语义判定器(intent, answer, tool_calls)→ "stable"|"dynamic"。
        # 控制台注入(它有 gateway);无注入(测试/--no-llm)→ 默认 dynamic(宁重跑不投毒)。
        self._result_classifier = result_classifier

    def set_trace_funnel(self, funnel: object) -> None:
        """接线漏斗原文层(entry 把 IntentAnalyst 共享的 TraceIndex 接进来,9.3c)。"""
        self._trace_funnel = funnel

    def _emit_funnel_event(self, payload: dict) -> None:
        """把一个事件落漏斗原文层(docs/27 TR-1:trace 是提炼真相源)。失败不阻断。"""
        if self._trace_funnel is None:
            return
        try:
            self._trace_funnel.append_raw(payload)
        except Exception:
            pass

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
    ) -> DriveResult:
        """跑一次主循环。返回 DriveResult。

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
        )
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
            brain_intent = (
                "[已有方法 —— 上次解决同类任务证明可行的打法,照它的步骤做,"
                "但**必须用当前输入重新得出结果,绝不照搬旧结论/旧数据**]\n"
                f"{guided_skill.body.strip()}\n\n[当前任务]\n{intent}"
            )
        self.stats.slow_brain_runs += 1
        if ctx is not None and _slow_brain_accepts_ctx(slow_brain):
            text, run = slow_brain(brain_intent, ctx=ctx)
        else:
            text, run = slow_brain(brain_intent)
        result.text = text
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

        # 3c. docs/02 §14:atom 层结晶裁判 = role 多维分级满意度。**这一跑是否被核验**(achievement
        #     满分前提)= 与下方 mark_verified 同一判据,在此先算好 → 避免"observe 先于 mark_verified
        #     → 首跑被错记 0.5"的时序滞后(对抗验收 C1)。按本跑核验,不查 sig 历史门。信用按 sig 隔离。
        # 跑评分离(Hardy):热路径**只记确定性满意度**(达成 from verify+success / 效率 from 步数,
        # 零 LLM、零延迟)。做好·质量维(LLM)是 **trace 的异步消费者**(quality_eval.py),不在此。
        this_run_verified = bool(run.success and not ctx_dependent and run.tool_calls)
        try:
            from karvyloop.crystallize import record_run as _record_satisfaction
            _record_satisfaction(self.satisfaction, run, sig,
                                 has_proof=this_run_verified, clock=lambda: now)
        except Exception:  # 护城河信号是增益不是命脉:不拖垮 drive,但**绝不静默**(对抗验收 C2)
            logger.warning("[atom_critic] 满意度记账失败(sig=%s);drive 继续", sig[:8], exc_info=True)

        # 4. 验证门 + 结晶
        #    CV-11:上下文依赖句(它=文件X 这种临时映射)**绝不**结晶进永久库
        #    —— 它的 signature 含未消解的指代,凝出来是垃圾技能(下次"它"指别的就错)。
        # brick3:**只结晶真干了活的 run(用了工具)**。纯对话/问候/"你是谁"这类没动工具的回复
        # 不是可复用技能,结晶进去 = 污染技能库 + 被快脑跨场 replay(Hardy 抓到的串味真 bug:
        # 一句"你是谁"在业务域被结晶成 user 全局技能,私聊时冒充设计师)。技能 = 用工具的任务流程。
        if run.success and not ctx_dependent and run.tool_calls:
            # M1 v1 简化:慢脑成功 → 自动 mark_verified(实战可接 executor 的
            # verify_proof 字段;这里先打通端到端路径)
            self.verify.mark_verified(
                sig, run.trace_ref, note="slow-brain success",
                clock=self._clock,
            )
            decision = maybe_promote(sig, self.store, self.verify, now=now,
                                     thresholds=self.thresholds)
            if decision.kind.value == "ready":
                # 自动结晶(用 intent 当 name/when_to_use 兜底,实战由访谈填)
                name = f"skill_{sig[:8]}"
                # 已有同 name 不重写(register 会覆盖,但 SKILL.md 写盘要看存在否)
                if name not in self.skill_index:
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
        """
        from karvyloop.crystallize import evict_stale, write_critiques_to_skill_md
        now = self._clock()
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
    persona: object = None,   # 9.4e 方案 A:人格 system prompt(CodingPrompt);None=默认 coding 提示
    enable_compression: bool = True,  # loop step4a:对话/长任务慢脑默认开上下文治理(防 O(n²) 烧 token)
    mcp_tools: object = None,  # A:MCP 工具(console 启动时连好、注入 runtime_kwargs);None=无
    images: object = None,  # 多模态:[{data, media_type}];带进首条 user 消息(需视觉模型)
) -> SlowBrain:
    """生产慢脑工厂:把 forge.generate_and_run 包成同步 SlowBrain 协议。

    forge 是 async,这里用 asyncio.run 同步化(M1 v1:单次任务为主,先打通;
    后续 P1 真要并发再切回 async driver)。

    拍 9.2b:`governance`(业务域 value.md,CV-14)拼在最前 —— 让同一角色在不同企业
    受不同价值观约束。私聊(无 governance)行为同旧路径。
    """
    import asyncio
    from karvyloop.coding.forge import generate_and_run

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
                system_prompt=persona,  # 9.4e 方案 A:人格 prompt(None=默认 coding)
                enable_compression=enable_compression,  # step4a:上下文治理
                extra_tools=mcp_tools or None,  # A:把连好的 MCP 工具并进 agent 工具集
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
        # 预算耗尽、断路 —— 都别把半截结果当"做完了"丢回去(Coze 大任务实测:30 轮被切,
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
