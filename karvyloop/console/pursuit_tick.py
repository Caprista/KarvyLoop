"""console.pursuit_tick — 外环 Pursuit(跨天持久目标)的推进 tick(docs/88 §4/§9 第一刀件④)。

挂在慢侧维护 loop(**非热路径**,照 _maintenance_item_failed 兜异常):每次醒来遍历活跃 Pursuit
(committed / revised),对每个(跑评分离 docs/88 真伤1——**只节流烧钱/贵计算**,不节流记账/完成判定):

  ① 确定性 verify(招牌硬核):`PursuitManager.is_done` 求值 verify_gate —— **绝不触发 LLM**。
     **廉价门(file_exists / predicate)每 tick 都验**(零子进程/微秒级)→ 外部刚满足即刻 done;
     **test_pass 门(走沙箱子进程,贵)受 `_due` 节流** + offload 到线程(不冻结事件循环)。
     过了 → 自动 done + 完成回执 + 归档。
  ② 修订判定:revision_trigger 命中 → 升 KIND_PURSUIT_REVISE 卡挂起等人拍(**系统不自动改方向**)。
  ③ 都没有 → `pursue()` 推进一拍:派生一条 TaskRecord(who=owner,关联 pursuit_id + 回填 drive
     trace_id)+ 写 Trace(run_scope)。推进后**当拍再验一次**(drive 可能刚把它做完)→ 过则即刻
     done。infeasible → 升现有 KIND_INFEASIBLE_REPORT 挂起;infra-dead → 记一笔不重试。

**成本地板(docs/88 真伤1,和"预算/infra-dead 确定性地板"同构)**:
  - `advances` 累计**真推进**次数(outcome=None/异常不计),达 `PURSUIT_MAX_ADVANCES` → 挂起 + 升
    H2A 卡("推进 N 次仍没完成:你来定")—— 不靠用户 revision_trigger(DSL 弱、无 > 比较)。
  - 推进节拍 throttle 的时间戳 `last_advance_ts` **在异常路径也写**(pursue 抛异常绝不旁路节流,
    否则每 10min 一次 =144 次/天)。**贵的 test_pass 子进程**随推进一起被节流;廉价门不受节流。
  - `consecutive_failures` 累计**连续失败**(pursue 抛异常/明确报错 +1;真推进成功清零;确定性
    infra 故障不计),达 `PURSUIT_MAX_CONSECUTIVE_FAILURES` → 同款挂起升卡 —— 堵"pursue 每拍都炸
    → advances 永不 +1 → 硬地板永不触发 → 以节流上限无限静默重试、人永远不知道"的静默洞
    (P2 残余;fail-loud:人只该拍板,不该当心跳)。

**核心判断(docs/88 §0)**:把 `pursue()`(内环执行器)当外环 Pursuit 的"每 tick 执行器",不另造
账——每次推进派生一条 task(复用任务账)、历史进 Trace(复用审计账)。`pursue()` 同步(内含
asyncio.run 独立验收)→ 必须下线程跑,不嵌套事件循环。

**跨设备接管(docs/88 第三刀 #3)**:relay 挂了才有 mesh(跨网同步 = 远程访问同一决定的延伸)。
每 tick 先 `publish_pursuit_tasks` 对账:committed 上 mesh 板(offer+自认领,payload 带
checkpoint)/心跳续租/checkpoint 刷新;别台已接管 → rec.transferred_to 站开(循环跳过,单 owner
不双跑);别台追完 → 折回本地终态。tick 尾有真变化再对账一次,让 checkpoint 尽快上板。
接管弹卡/ACCEPT 收编在 mesh_task_board(复用 KIND_MESH_TAKEOVER,H2A 人拍,绝不自动接管)。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 门成本分层(docs/88 真伤1):廉价确定性门零 LLM/零子进程/微秒级 → **每 tick 都验**(完成能立刻收官);
# 贵门(沙箱子进程)受 _due 节流 + offload 到线程(真伤6:别冻结 console 事件循环)。
_CHEAP_GATE_TYPES = ("file_exists", "predicate")

# 真伤7:cognition 层只出 GATE_NOTE_* 码,console 层(有 i18n)映射成人话写进 progress_note(分层)。
_GATE_NOTE_I18N = {
    "no_isolation": "pursuit.gate_note.no_isolation",
    "net_downgrade": "pursuit.gate_note.net_downgrade",
    "timed_out": "pursuit.gate_note.timed_out",
    "net_suspect": "pursuit.gate_note.net_suspect",
}

# context 白名单(docs/88 §4「第一刀 context key 固定小白名单防漂移」):assemble_context 只喂这几个
# 确定性信号(从 record 存的上次 outcome 探针 + 时间预算读),不开放式。revision_triggers 只能引它们。
_CONTEXT_KEYS = ("terminal", "verdict_passed", "done", "budget_exhausted",
                 "days_running", "infra_dead", "consecutive_failures")

# 推进硬地板(docs/88 真伤1①,待标定):一个 committed 目标真推进这么多次仍没过 verify_gate,
# 就别再无声烧钱——挂起 + 升 H2A 卡请你定夺(继续/改方向/放弃)。首版 20;和预算/infra-dead 同类的
# 确定性兜底,不依赖用户写 revision_trigger。
PURSUIT_MAX_ADVANCES = 20

# 连续失败硬地板(P2 残余,常数待 Trace 真数据标定):pursue 连着这么多拍抛异常/明确报错(真推进
# 成功即清零;确定性 infra 故障不计)→ 挂起 + 升同款 REVISE 卡。补 PURSUIT_MAX_ADVANCES 盖不住的洞:
# 异常不计 advances → 全炸的 pursuit 永远到不了推进上限,只会以节流上限无限静默重试、人永远不知道。
# 首版 5。
PURSUIT_MAX_CONSECUTIVE_FAILURES = 5


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
        # P2 残余小扩:连败计数进白名单(revision_triggers 可引它,如 "consecutive_failures == 3")。
        # 白名单是防漂移设计——只加这一个键,别开闸。
        "consecutive_failures": int(getattr(rec, "consecutive_failures", 0) or 0),
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
                             intent=statement, pursuit_id=rec.id,
                             kind="pursuit")   # docs/90 刀3a:显式任务类型(停止按钮按它路由)

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
        # docs/90 刀3a:pursuit 一拍也登 running-run 注册表(本函数已在 to_thread 线程里,
        # contextvar 穿线程内 asyncio.run 直达 executor)→ /api/task/cancel 能停正跑的一拍。
        from karvyloop.atoms.abort import abort_scope
        with abort_scope(tid or ""), run_scope():
            with token_task(tid or ""):
                outcome = pursue(statement, ml=ml, slow_brain=slow_brain, rk=rk)
    except Exception as e:
        logger.warning(f"[pursuit_tick] pursue 异常({rec.id}): {e}")
        if task_reg is not None and tid:
            task_reg.finish(tid, error=str(e))
        rec.progress_note = f"推进出错: {e}"
        # P2 残余(连败计数):pursue 抛出的异常**没法确定性区分** infra 故障 vs pursuit 自身问题
        # (确定性 infra 检测只盖 is_infra_dead terminal / ml 缺失 / 慢脑构造失败,那些路径不计数)。
        # 取舍:计入 —— 连炸就该响,哪怕是 infra 在连炸,响也比无限静默重试对(宁响勿哑)。
        rec.consecutive_failures += 1
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
    # P2 残余(连败计数)—— 与 last_terminal 同一优先级序:
    #   infra_dead(确定性检测)= infra 的错不算 pursuit 的 → 不计数也不清零(等 infra 恢复);
    #   err = 返回明确失败 → +1;infeasible = 已走响亮的不可行卡路径挂起 → 计数不动;
    #   其余(真推进走完没报错)→ 清零(地板只逮"连续"失败)。
    if outcome.infra_dead:
        pass
    elif err:
        rec.consecutive_failures += 1
    elif outcome.infeasible:
        pass
    else:
        rec.consecutive_failures = 0
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
            # 真伤3:把**真域** rec.domain_id 线程进 persist(别让 domain 级完成归档进随机 uuid)。
            mgr.persist(rec.pursuit, domain_id=getattr(rec, "domain_id", "") or "")
    except Exception as e:
        logger.debug(f"[pursuit_tick] 完成 persist 失败(不阻断): {e}")
    # ② 完成回执:落 Trace(周报 hook)+ 任务看板一条 done 记录(用户可见回执,复用任务账)。
    _emit_done_trace(app, rec)
    task_reg = getattr(app.state, "task_registry", None)
    if task_reg is not None:
        try:
            receipt = i18n.t("pursuit.receipt.done", statement=rec.pursuit.statement[:60])
            tid = task_reg.start(who=_owner_display(rec.owner), domain_id=rec.domain_id or "l0",
                                 role="", intent=receipt, pursuit_id=rec.id,
                                 kind="pursuit")   # docs/90 刀3a(完成回执,即起即落)
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


def _apply_gate_note(rec: Any, ctx: dict) -> None:
    """真伤7:读 cognition 层写进 ctx 的 GATE_NOTE_* 码 → i18n 出人话进 rec.progress_note。
    降级平台上 test_pass 门永不可完成,让"为什么"到用户可见处(下钻页渲染 progress_note),不只在日志。"""
    from karvyloop.cognition.pursuit import GATE_NOTE_KEY
    if not isinstance(ctx, dict):
        return
    code = ctx.get(GATE_NOTE_KEY)
    key = _GATE_NOTE_I18N.get(code or "")
    if key:
        from karvyloop import i18n
        rec.progress_note = i18n.t(key)


async def _is_done_safe(manager: Any, rec: Any, ctx: dict, *, offload: bool) -> tuple[bool, bool]:
    """确定性 verify_gate 求值,返回 (done, errored)。

    - offload=True(test_pass:走沙箱子进程,贵)→ `asyncio.to_thread` 下线程跑,**绝不冻结 console
      事件循环**(真伤6:桥的 th.join 会阻塞调用线程整 gate 时长,聊天/HTTP/WS 全卡)。
    - offload=False(廉价门:微秒级)→ 主线程直接跑(offload 开销 > 跑本身)。
    - 任何异常 → (False, True):门求值绝不冒穿 tick(真伤4③);调用方据 errored 计一次失败 + 喂节流。
    """
    try:
        if offload:
            done = await asyncio.to_thread(manager.is_done, rec.pursuit, ctx)
        else:
            done = manager.is_done(rec.pursuit, ctx)
        return bool(done), False
    except Exception as e:  # noqa: BLE001 — 门求值异常不冒穿 tick(真伤4③)
        logger.warning(f"[pursuit_tick] verify_gate 求值异常({getattr(rec, 'id', '?')}): {e}")
        return False, True


async def _suspend_and_revise(app: Any, rec: Any, *, reason: str, ts: float, counts: dict) -> None:
    """挂起 + 标 revised + 升 REVISE 卡(_raise_revise 内含 store.put 落盘)。"""
    rec.pursuit = rec.pursuit.model_copy(update={"status": "revised"})
    rec.suspended = True
    await _raise_revise(app, rec, reason=reason, ts=ts)
    counts["revised"] += 1


async def _hit_cost_floor(app: Any, rec: Any, *, n_ts: float, counts: dict) -> bool:
    """连败/推进 硬地板:命中任一 → 挂起升 H2A 卡,返回 True(调用方 continue,rec 已落盘)。

    连败在前(堵"每拍都炸→advances 永不 +1→推进上限永不触发→节流上限无限静默重试");推进在后
    (真推进达上限仍没过门 → 你来定:继续/改方向/放弃)。fail-loud:人只该拍板,不该当心跳。
    """
    from karvyloop import i18n
    if rec.consecutive_failures >= PURSUIT_MAX_CONSECUTIVE_FAILURES:
        await _suspend_and_revise(
            app, rec, ts=n_ts, counts=counts,
            reason=i18n.t("pursuit.revise.reason_consecutive_failures", n=rec.consecutive_failures))
        logger.info(f"[pursuit_tick] Pursuit {rec.id} 连续失败 {rec.consecutive_failures} 次 → 挂起升 H2A 卡")
        return True
    if rec.advances >= PURSUIT_MAX_ADVANCES:
        await _suspend_and_revise(
            app, rec, ts=n_ts, counts=counts,
            reason=i18n.t("pursuit.revise.reason_max_advances", n=rec.advances))
        logger.info(f"[pursuit_tick] Pursuit {rec.id} 达推进上限 {rec.advances} → 挂起升 H2A 卡")
        return True
    return False


async def pursuit_tick(app: Any, *, now: Optional[float] = None) -> dict:
    """遍历活跃 Pursuit(committed / revised),到节拍时做工。返回计数 dict(供 log / 测试断言)。

    跑评分离的正形(docs/88 真伤1)——把节流从"记账/完成判定"上摘掉,**只节流烧钱/贵计算**:
    - **廉价确定性门(file_exists / predicate)每 tick 都验**(零 LLM/零子进程/微秒级):committed /
      revised / suspended 一视同仁 —— 外部世界可能刚满足它 → **立即 done**(不等 6h;revised 也不再
      是永久僵尸,真伤2)。过了收官,门求值抛异常也计一次失败喂节流(真伤4③),绝不冒穿/每 10min 重炸。
    - **只有 test_pass 门(走沙箱子进程,贵)和 pursue() 推进受 `_due` 节流**;且 test_pass 的求值 +
      pursue offload 到线程,绝不冻结 console 事件循环(真伤6)。
    - active(待承诺卡)→ 不动;revised / suspended → 只确定性验完成,**绝不再 pursue**(等人拍)。
    """
    store = getattr(app.state, "pursuit_store", None)
    manager = getattr(app.state, "pursuit_manager", None)
    counts = {"checked": 0, "done": 0, "revised": 0, "advanced": 0, "infeasible": 0}
    if store is None or manager is None:
        return counts
    n_ts = now if now is not None else time.time()
    # ── mesh 对账(docs/88 第三刀 #3:跨设备接管)——**推进前**先对账:committed 上板/心跳续租、
    # 别台已接管 → 标 transferred(下面循环跳过,单 owner 不双跑)、别台已追完 → 折回本地终态。
    # 只在 relay 挂了才跑(与 mesh_tick 同一门:跨网同步 = 远程访问同一决定的延伸,不另开开关);
    # 失败绝不挡推进(幂等对账,下轮补账)。
    mesh_on = bool(getattr(app.state, "relay_url", ""))
    if mesh_on:
        try:
            from karvyloop.console.mesh_task_board import publish_pursuit_tasks
            counts["mesh"] = publish_pursuit_tasks(app, now_ms=int(n_ts * 1000))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[pursuit_tick] mesh 对账失败(下轮补账): {e}")
    advance_interval = 0.0
    try:
        advance_interval = float(getattr(app.state, "pursuit_advance_interval_s", 0.0) or 0.0)
    except (TypeError, ValueError):
        advance_interval = 0.0

    def _due(rec: Any) -> bool:
        # 推进节拍 throttle(真伤1:只节流贵计算 —— test_pass 子进程 + pursue;廉价门不受它辖)。
        return advance_interval <= 0 or (n_ts - float(getattr(rec, "last_advance_ts", 0.0) or 0.0)) >= advance_interval

    for rec in store.active():
        try:
            status = rec.pursuit.status
            if status not in ("committed", "revised"):
                continue   # active = 等承诺卡 → 不自动动
            if getattr(rec, "transferred_to", ""):
                continue   # 已被同主人另一台设备接管(mesh lease 归属清晰)→ 本机站开:
                           # 不推进不验不落终态(单 owner 不双跑;账回来/远端完成由 mesh 对账折回)
            ctx = assemble_context(app, rec, now=n_ts)
            gate_type = (rec.pursuit.verify_gate or {}).get("type")
            cheap = gate_type in _CHEAP_GATE_TYPES

            # ── 真伤1 + 真伤2:廉价确定性门**每 tick 都验**(不受 _due 节流)──
            # committed / revised / suspended 一视同仁:外部可能刚满足 → 立即 done(不等 6h、不留僵尸)。
            if cheap:
                done, errored = await _is_done_safe(manager, rec, ctx, offload=False)
                if done:
                    _complete(app, rec)
                    store.put(rec)
                    counts["done"] += 1
                    continue
                if errored:
                    # 真伤4③:门求值抛异常 → 计一次失败 + 喂节流(廉价门不受节流、每 tick 都跑 →
                    # 只在到点时记,否则每 tick 计一次失败会瞬间刷爆连败地板);地板兜得住,不静默每 10min 重炸。
                    if _due(rec):
                        rec.last_advance_ts = n_ts
                        rec.consecutive_failures += 1
                        if await _hit_cost_floor(app, rec, n_ts=n_ts, counts=counts):
                            continue
                        store.put(rec)
                    continue

            # ── revised / suspended(infeasible/达地板)→ 绝不再 pursue(等人拍)──
            # 廉价门上面已每 tick 验过;test_pass 完成验证受节流(贵),到点 offload 验一次。
            if rec.suspended or status == "revised":
                if not cheap and _due(rec):
                    rec.last_advance_ts = n_ts
                    done, _err = await _is_done_safe(manager, rec, ctx, offload=True)
                    _apply_gate_note(rec, ctx)
                    if done:
                        _complete(app, rec)
                        counts["done"] += 1
                    store.put(rec)
                continue

            # ── committed 未挂起:推进路径(受 _due 节流)──
            if not _due(rec):
                continue
            counts["checked"] += 1

            # test_pass 完成门(贵):此处**节流** + offload 到线程(真伤6)。廉价门上面已每 tick 验过 → 跳过重复验。
            if not cheap:
                done, errored = await _is_done_safe(manager, rec, ctx, offload=True)
                _apply_gate_note(rec, ctx)
                if done:
                    _complete(app, rec)
                    store.put(rec)
                    counts["done"] += 1
                    continue
                if errored:
                    rec.last_advance_ts = n_ts
                    rec.consecutive_failures += 1
                    if await _hit_cost_floor(app, rec, n_ts=n_ts, counts=counts):
                        continue
                    store.put(rec)
                    continue

            # 修订判定(revision_trigger 命中 → 系统不自动改方向,升 REVISE 卡挂起等人拍,docs/88 §5)。
            if manager.should_revise(rec.pursuit, ctx):
                from karvyloop import i18n
                rec.pursuit = rec.pursuit.model_copy(update={"status": "revised"})
                rec.suspended = True
                await _raise_revise(app, rec,
                                    reason=(rec.revision_reason or i18n.t("pursuit.revise.reason_trigger")),
                                    ts=n_ts)
                counts["revised"] += 1
                logger.info(f"[pursuit_tick] Pursuit {rec.id} 命中修订触发 → 升 REVISE 卡挂起等人拍")
                continue

            # committed 维持:pursue() 推进一拍。**先记节流戳并落盘**(真伤1:异常路径也已写,
            # pursue 抛异常绝不旁路节流 → 不再每 10min 重试)。pursue offload 到线程(真伤6)。
            rec.last_advance_ts = n_ts
            store.put(rec)
            try:
                outcome = await asyncio.to_thread(_advance_sync, app, rec)
            except Exception as e:   # _advance_sync 内部已兜;这里再兜一层,绝不让节流被旁路
                outcome = None
                # 装配层/线程炸(逃过 _advance_sync 内层兜的)也是一次推进失败 —— 不计就又是
                # 无限静默重试(P2 残余);_advance_sync 内层已计过的路径不会走到这里,不双计。
                rec.consecutive_failures += 1
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
                pctx = assemble_context(app, rec, now=n_ts)
                done, _err = await _is_done_safe(manager, rec, pctx, offload=not cheap)
                _apply_gate_note(rec, pctx)
                if done:
                    _complete(app, rec)
                    store.put(rec)
                    counts["done"] += 1
                    continue

            # 连败/推进 硬地板(真伤1① + P2 残余):命中 → 挂起升 H2A 卡(rec 已落盘)。
            if await _hit_cost_floor(app, rec, n_ts=n_ts, counts=counts):
                continue

            store.put(rec)
        except Exception as e:
            logger.warning(f"[pursuit_tick] Pursuit {getattr(rec, 'id', '?')} 推进异常(跳过): {e}")
    # ── mesh 对账·收尾:本 tick 有真变化(推进/完成/挂起)→ 立刻把新 checkpoint 刷上板,
    # 别等下 tick(lease 窗内 checkpoint 越新,接管方拿到的 advances 越不落后)。幂等,失败下轮补。
    if mesh_on and (counts["advanced"] or counts["done"] or counts["revised"] or counts["infeasible"]):
        try:
            from karvyloop.console.mesh_task_board import publish_pursuit_tasks
            publish_pursuit_tasks(app, now_ms=int(n_ts * 1000))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[pursuit_tick] mesh 收尾对账失败(下轮补账): {e}")
    if counts["done"] or counts["revised"] or counts["infeasible"]:
        logger.info(f"[pursuit_tick] {counts}")
    return counts


__all__ = ["pursuit_tick", "assemble_context", "PURSUIT_MAX_ADVANCES",
           "PURSUIT_MAX_CONSECUTIVE_FAILURES"]
