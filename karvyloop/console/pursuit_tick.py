"""console.pursuit_tick — 外环 Pursuit(跨天持久目标)的推进 tick(docs/88 §4/§9 第一刀件④)。

挂在慢侧维护 loop(**非热路径**,照 _maintenance_item_failed 兜异常):每次醒来遍历活跃、
已 committed 的 Pursuit,对每个(**到推进节拍时才做工**,节流窗内整条跳过——含 verify 子进程):

  ① 确定性 verify(招牌硬核):`PursuitManager.is_done` 求值 verify_gate —— **绝不触发 LLM**
     (test_pass 跑子进程 / file_exists 查文件)。过了 → 自动 done + 完成回执 + 归档。
  ② 修订判定:revision_trigger 命中 → 升 KIND_PURSUIT_REVISE 卡挂起等人拍(**系统不自动改方向**)。
  ③ 都没有 → `pursue()` 推进一拍:派生一条 TaskRecord(who=owner,关联 pursuit_id + 回填 drive
     trace_id)+ 写 Trace(run_scope)。推进后**当拍再验一次**(drive 可能刚把它做完)→ 过则即刻
     done。infeasible → 升现有 KIND_INFEASIBLE_REPORT 挂起;infra-dead → 记一笔不重试。

**成本地板(docs/88 真伤1,和"预算/infra-dead 确定性地板"同构)**:
  - `advances` 累计**真推进**次数(outcome=None/异常不计),达 `PURSUIT_MAX_ADVANCES` → 挂起 + 升
    H2A 卡("推进 N 次仍没完成:你来定")—— 不靠用户 revision_trigger(DSL 弱、无 > 比较)。
  - 推进节拍 throttle 的时间戳 `last_advance_ts` **在异常路径也写**(pursue 抛异常绝不旁路节流,
    否则每 10min 一次 =144 次/天)。verify 子进程随整条 tick 工作一起被节流。

**核心判断(docs/88 §0)**:把 `pursue()`(内环执行器)当外环 Pursuit 的"每 tick 执行器",不另造
账——每次推进派生一条 task(复用任务账)、历史进 Trace(复用审计账)。`pursue()` 同步(内含
asyncio.run 独立验收)→ 必须下线程跑,不嵌套事件循环。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# context 白名单(docs/88 §4「第一刀 context key 固定小白名单防漂移」):assemble_context 只喂这几个
# 确定性信号(从 record 存的上次 outcome 探针 + 时间预算读),不开放式。revision_triggers 只能引它们。
_CONTEXT_KEYS = ("terminal", "verdict_passed", "done", "budget_exhausted",
                 "days_running", "infra_dead")

# 推进硬地板(docs/88 真伤1①,待标定):一个 committed 目标真推进这么多次仍没过 verify_gate,
# 就别再无声烧钱——挂起 + 升 H2A 卡请你定夺(继续/改方向/放弃)。首版 20;和预算/infra-dead 同类的
# 确定性兜底,不依赖用户写 revision_trigger。
PURSUIT_MAX_ADVANCES = 20


def assemble_context(app: Any, rec: Any, *, now: Optional[float] = None) -> dict:
    """PursuitManager 的唯一悬空输入(咬合点)。**确定性**从现有账读,零 LLM、零新造存储:
    上次 pursue 推进的结果探针(terminal/verdict/infeasible/infra_dead 存在 record 上)
    + 时间预算(days_running)。key 固定小白名单防漂移。"""
    n = now if now is not None else time.time()
    days = int(max(0.0, (n - float(getattr(rec, "created_ts", n)))) // 86400)
    ctx = {
        "terminal": getattr(rec, "last_terminal", "") or "",
        "verdict_passed": bool(getattr(rec, "last_verdict_passed", False)),
        "done": bool(getattr(rec, "last_verdict_passed", False)),
        "budget_exhausted": bool(getattr(rec, "last_infeasible", False)),
        "days_running": days,
        "infra_dead": bool(getattr(rec, "last_infra_dead", False)),
    }
    return {k: ctx[k] for k in _CONTEXT_KEYS}


def _owner_display(owner: str) -> str:
    o = (owner or "").strip()
    return "小卡" if o in ("", "karvy", "l0") else o


def _advance_sync(app: Any, rec: Any) -> Any:
    """在**线程**里推进一个 committed Pursuit 一拍:建 TaskRecord → run_scope+token_task 下
    pursue() 一次 → 落任务终态 + **回填 drive trace_id** → 把结果探针写回 record。返回
    PursuitOutcome(None=没法推进/异常;调用方据此不计 advances、但节流时间戳照写)。

    pursue() 同步(内含 asyncio.run 独立验收)→ 必须在无运行事件循环的线程里跑(to_thread)。
    """
    ml = getattr(app.state, "main_loop", None)
    if ml is None:
        rec.last_infra_dead = True
        rec.progress_note = "未接 main_loop(--no-llm?),无法推进"
        return None
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    statement = rec.pursuit.statement
    did = rec.domain_id or "l0"
    owner = rec.owner or "karvy"
    role = "" if owner in ("karvy", "l0") else owner

    task_reg = getattr(app.state, "task_registry", None)
    tid = None
    if task_reg is not None:
        tid = task_reg.start(who=_owner_display(owner), domain_id=did, role=role,
                             intent=statement, pursuit_id=rec.id)

    from karvyloop.cli.pursuit_loop import pursue
    from karvyloop.cognition.trace import run_scope
    from karvyloop.console.decision_wire import assemble_governance
    from karvyloop.llm.token_ledger import token_task
    from karvyloop.runtime.main_loop import forge_slow_brain_factory

    # 治理串:你的决策标准(prealign)+ 相关知识(assemble_governance);域级还叠 value.md 前缀。
    base = ""
    persona = None
    if did == "l0" or owner in ("karvy", "l0"):
        # l0/小卡自持追求 → 用小卡人格(输出是小卡的声音,不是 CodingResult 八股)
        try:
            from karvyloop.coding.persona import build_karvy_persona_prompt
            persona = build_karvy_persona_prompt(cwd=rk.get("workspace_root", "/"))
        except Exception:
            persona = None
    gov = assemble_governance(app, intent=statement, domain=("" if did == "l0" else did),
                              role=role, base=base)
    try:
        slow_brain = forge_slow_brain_factory(governance=gov, persona=persona, **rk)
    except Exception as e:
        logger.warning(f"[pursuit_tick] 慢脑构造失败({rec.id}): {e}")
        if task_reg is not None and tid:
            task_reg.finish(tid, error=f"慢脑构造失败: {e}")
        rec.last_infra_dead = True
        return None

    outcome = None
    try:
        # per-tick token 归因:这一拍烧的每个 token 记到该 task 名下(成本可查)。
        with run_scope():
            with token_task(tid or ""):
                outcome = pursue(statement, ml=ml, slow_brain=slow_brain, rk=rk)
    except Exception as e:
        logger.warning(f"[pursuit_tick] pursue 异常({rec.id}): {e}")
        if task_reg is not None and tid:
            task_reg.finish(tid, error=str(e))
        rec.progress_note = f"推进出错: {e}"
        return None

    checked = outcome.checked
    result = getattr(checked, "result", None)
    err = (getattr(result, "error", "") or "")
    txt = (getattr(result, "text", "") or "")
    if task_reg is not None and tid:
        task_reg.finish(tid, result=txt, error=err)
        # 真伤2(J1 式断链修):回填 drive 写 Trace 用的 task_id → 派生 task 的 /api/task/{tid}/trace
        # 才钻得到执行时间线(照 routes.py:1087 l0 私聊路径;此前漏了 → entries 恒 0)。
        drive_tid = getattr(result, "task_id", "") or ""
        if drive_tid:
            try:
                task_reg.set_conversation(tid, "", trace_id=drive_tid)
            except Exception:
                pass
        rec.note_task(tid)

    # 结果探针写回 record(下 tick assemble_context 确定性读它,零 LLM)。
    verdict = getattr(checked, "verdict", None)
    rec.last_terminal = "completed" if not (outcome.infeasible or outcome.infra_dead or err) else (
        "infra_dead" if outcome.infra_dead else ("error" if err else "infeasible"))
    rec.last_verdict_passed = bool(verdict is not None and getattr(verdict, "passed", False)
                                   and not getattr(verdict, "inconclusive", True))
    rec.last_infeasible = bool(outcome.infeasible)
    rec.last_infra_dead = bool(outcome.infra_dead)
    _n_attempts = len(getattr(outcome, "attempts", []) or [])
    if outcome.infra_dead:
        rec.progress_note = "基础能力暂不可用(模型/网络/沙箱),下轮再试"
    elif outcome.infeasible:
        rec.progress_note = f"自助重规划 {_n_attempts} 次仍没拿下(已升不可行报告卡)"
    else:
        _fb = (getattr(verdict, "feedback", "") or "").strip().replace("\n", " ")
        rec.progress_note = (f"推进一拍:{'验收通过' if rec.last_verdict_passed else (_fb[:80] or '已跑一轮')}")
    return outcome


async def _raise_card(app: Any, card: Any) -> None:
    from karvyloop.console.proposals import broadcast_proposal
    await broadcast_proposal(app, card)   # register + 静音判定(REVISE/INFEASIBLE ∈ HIGH_RISK,永不静音)


def _emit_done_trace(app: Any, rec: Any) -> None:
    """完成回执之一:落 Trace(评价唯一数据源;周报/飞轮的 hook)。无 Trace 源不阻断。"""
    try:
        _ml = getattr(app.state, "main_loop", None)
        trace = getattr(_ml, "trace", None) if _ml is not None else None
        if trace is None:
            trace = getattr(app.state, "trace", None)
        if trace is None:
            return
        from karvyloop.cognition.trace import TraceEntry
        trace.append(TraceEntry(
            task_id=rec.id, kind="pursuit_done",
            payload={"pursuit_id": rec.id, "statement": rec.pursuit.statement,
                     "level": rec.pursuit.level, "verify_gate": dict(rec.pursuit.verify_gate or {})},
            agent=rec.owner or "karvy", source="pursuit_tick"))
    except Exception as e:
        logger.debug(f"[pursuit_tick] 完成 Trace 落账失败(不阻断): {e}")


def _complete(app: Any, rec: Any) -> None:
    """gate 通过 → 自动 done + 完成回执 + 归档(docs/88 §4 DONE)。"""
    from karvyloop import i18n
    rec.pursuit = rec.pursuit.model_copy(update={"status": "done"})
    rec.suspended = False
    rec.progress_note = i18n.t("pursuit.progress.done")
    # ① 完成记账:manager.persist(atom/role → Belief 私人库;domain → 域 KB)。失败不阻断。
    try:
        mgr = getattr(app.state, "pursuit_manager", None)
        if mgr is not None:
            mgr.persist(rec.pursuit)
    except Exception as e:
        logger.debug(f"[pursuit_tick] 完成 persist 失败(不阻断): {e}")
    # ② 完成回执:落 Trace(周报 hook)+ 任务看板一条 done 记录(用户可见回执,复用任务账)。
    _emit_done_trace(app, rec)
    task_reg = getattr(app.state, "task_registry", None)
    if task_reg is not None:
        try:
            receipt = i18n.t("pursuit.receipt.done", statement=rec.pursuit.statement[:60])
            tid = task_reg.start(who=_owner_display(rec.owner), domain_id=rec.domain_id or "l0",
                                 role="", intent=receipt, pursuit_id=rec.id)
            task_reg.finish(tid, result=receipt)
            rec.note_task(tid)
        except Exception as e:
            logger.debug(f"[pursuit_tick] 完成回执任务失败(不阻断): {e}")
    logger.info(f"[pursuit_tick] Pursuit {rec.id} 达成(gate 通过)→ 自动 done + 回执")


async def _raise_revise(app: Any, rec: Any, *, reason: str, ts: float) -> None:
    """升 KIND_PURSUIT_REVISE 卡(修订/挂起决策归人)。**persist 在 broadcast 之前**(边角修:
    否则 broadcast 抛异常时内存已改、盘上仍旧态,重启会不一致)。卡 id 按 pursuit_id+reason 幂等。"""
    from karvyloop.karvy.proposal_registry import proposal_for_pursuit_revise
    rec.revision_reason = reason
    getattr(app.state, "pursuit_store").put(rec)   # 先落盘(状态是真理来源)
    card = proposal_for_pursuit_revise(
        pursuit_id=rec.id, statement=rec.pursuit.statement,
        revision_reason=reason, domain_id=rec.domain_id, ts=ts)
    await _raise_card(app, card)                    # 卡幂等(id 稳定),失败也不会重复升


async def pursuit_tick(app: Any, *, now: Optional[float] = None) -> dict:
    """遍历活跃 committed Pursuit,到推进节拍时做工。返回计数 dict(供 log / 测试断言)。

    - active(待承诺)/ revised(挂起等人拍)→ **不动**(等人)。
    - 节流窗内(距上次做工 < interval)→ 整条跳过(含 verify 子进程,防 144 次/天)。
    - committed 未挂起且到点 → 确定性 verify(zero-LLM);revision_trigger 命中 → 升 REVISE 卡挂起;
      否则 pursue() 推进一拍 → **推进后当拍再验一次**(过则即刻 done);达 PURSUIT_MAX_ADVANCES → 挂起升卡。
    - committed 已挂起(infeasible/达上限)且到点 → 仅确定性验完成(外部可能已修好),不再 pursue。
    """
    import asyncio

    store = getattr(app.state, "pursuit_store", None)
    manager = getattr(app.state, "pursuit_manager", None)
    counts = {"checked": 0, "done": 0, "revised": 0, "advanced": 0, "infeasible": 0}
    if store is None or manager is None:
        return counts
    n_ts = now if now is not None else time.time()
    advance_interval = 0.0
    try:
        advance_interval = float(getattr(app.state, "pursuit_advance_interval_s", 0.0) or 0.0)
    except (TypeError, ValueError):
        advance_interval = 0.0

    def _due(rec: Any) -> bool:
        # 推进节拍 throttle(真伤1③:verify 子进程随整条 tick 工作一起节流,不再每 tick 都跑)。
        return advance_interval <= 0 or (n_ts - float(getattr(rec, "last_advance_ts", 0.0) or 0.0)) >= advance_interval

    for rec in store.active():
        try:
            if rec.pursuit.status != "committed":
                continue   # active=等承诺卡 / revised=挂起等人拍 → 不自动动
            if not _due(rec):
                continue   # 节流窗内,整条跳过(含 verify 子进程)

            ctx = assemble_context(app, rec, now=n_ts)

            # 挂起(infeasible/达上限):到点仅确定性验完成(外部可能已修好),绝不再 pursue 烧 token。
            if rec.suspended:
                rec.last_advance_ts = n_ts   # 记一次"做工"以喂节流(verify 也算做工)
                if manager.is_done(rec.pursuit, ctx):
                    _complete(app, rec)
                    counts["done"] += 1
                store.put(rec)
                continue

            counts["checked"] += 1
            p2 = manager.step(rec.pursuit, ctx)   # is_done(gate,zero-LLM)→done;revise→revised;else 维持
            if p2.status == "done" and rec.pursuit.status != "done":
                _complete(app, rec)
                store.put(rec)
                counts["done"] += 1
                continue
            if p2.status == "revised" and rec.pursuit.status != "revised":
                # revision_trigger 命中:系统不自动改方向,升 REVISE 卡挂起等人拍(docs/88 §5)。
                from karvyloop import i18n
                rec.pursuit = p2
                rec.suspended = True
                await _raise_revise(app, rec,
                                    reason=(rec.revision_reason or i18n.t("pursuit.revise.reason_trigger")),
                                    ts=n_ts)
                counts["revised"] += 1
                logger.info(f"[pursuit_tick] Pursuit {rec.id} 命中修订触发 → 升 REVISE 卡挂起等人拍")
                continue

            # committed 维持:pursue() 推进一拍。**先记节流时间戳并落盘**(真伤1②:异常路径也已写,
            # pursue 抛异常绝不旁路 6h 节流 → 不再每 10min 重试)。
            rec.last_advance_ts = n_ts
            store.put(rec)
            try:
                outcome = await asyncio.to_thread(_advance_sync, app, rec)
            except Exception as e:   # _advance_sync 内部已兜;这里再兜一层,绝不让节流被旁路
                outcome = None
                logger.warning(f"[pursuit_tick] 推进线程异常({rec.id}): {e}")
            counts["advanced"] += 1

            if outcome is not None:
                rec.advances += 1   # 真推进才 +1(outcome=None/异常不 +1,免虚高触发硬地板)
                if outcome.infeasible:
                    # 带真实尝试轨迹升「不可行报告卡」(复用现有 KIND_INFEASIBLE_REPORT)。persist 先于 broadcast。
                    rec.suspended = True
                    store.put(rec)
                    try:
                        from karvyloop.karvy.proposal_registry import proposal_for_infeasible_report
                        card = proposal_for_infeasible_report(
                            goal=rec.pursuit.statement, role=(rec.owner or "小卡"),
                            attempts=list(outcome.attempts), ts=n_ts, domain_id=rec.domain_id)
                        await _raise_card(app, card)
                    except Exception as e:
                        logger.warning(f"[pursuit_tick] 升不可行卡失败({rec.id}): {e}")
                    counts["infeasible"] += 1
                    logger.info(f"[pursuit_tick] Pursuit {rec.id} 不可行 → 升不可行报告卡挂起")
                    continue
                # 推进后**当拍再验一次**:drive 可能刚把它做完 → 即刻 done(不必等下 tick)。
                if manager.is_done(rec.pursuit, assemble_context(app, rec, now=n_ts)):
                    _complete(app, rec)
                    store.put(rec)
                    counts["done"] += 1
                    continue

            # 成本硬地板(真伤1①):真推进达上限仍没过门 → 挂起 + 升 H2A 卡(你来定:继续/改方向/放弃)。
            if rec.advances >= PURSUIT_MAX_ADVANCES:
                from karvyloop import i18n
                rec.pursuit = rec.pursuit.model_copy(update={"status": "revised"})
                rec.suspended = True
                await _raise_revise(app, rec,
                                    reason=i18n.t("pursuit.revise.reason_max_advances", n=rec.advances),
                                    ts=n_ts)
                counts["revised"] += 1
                logger.info(f"[pursuit_tick] Pursuit {rec.id} 达推进上限 {rec.advances} → 挂起升 H2A 卡")
                continue

            store.put(rec)
        except Exception as e:
            logger.warning(f"[pursuit_tick] Pursuit {getattr(rec, 'id', '?')} 推进异常(跳过): {e}")
    if counts["done"] or counts["revised"] or counts["infeasible"]:
        logger.info(f"[pursuit_tick] {counts}")
    return counts


__all__ = ["pursuit_tick", "assemble_context", "PURSUIT_MAX_ADVANCES"]
