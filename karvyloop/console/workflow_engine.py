"""console/workflow_engine.py — 群内协作 workflow 引擎(P2-e:拆 routes.py,领域引擎下沉,行为零变化)。

从 routes.py 纯搬移:@多人 → 小卡按目标+岗位职责设计 DAG → 你拍板 → 执行(上游喂下游)的引擎侧
(规划 LLM 调用 / 持久化执行 / 重启续跑 / 结果落线);/api/workflow/* HTTP 端点仍留在 routes.py。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _extract_json_obj(text: str) -> str:
    from karvyloop.karvy.fastbrain.trace_habit import _strip_code_fences
    s = _strip_code_fences(text or "")
    i, j = s.find("{"), s.rfind("}")
    return s[i:j + 1] if (i >= 0 and j > i) else s


# infra-dead 的错误指纹:基础能力没了(网关/网络/模型解析/沙箱/token 调不通)。DriveOutcome 只给
# error 字符串(不透 terminal),故按指纹判 —— 命中 → fail-loud 中止,不盲 retry(自家原则)。
# 不是任务的问题:role 重规划同一条路也没用。宁可漏判(当普通失败 skip)也别误判(把真任务失败当 infra)。
_INFRA_DEAD_FINGERPRINTS = (
    "infra_dead", "infra-dead", "resolve_model", "no model", "无可用模型", "模型解析",
    "gateway", "网关", "connection", "连接", "timeout", "超时", "econnrefused",
    "sandbox", "沙箱", "unauthorized", "api key", "api_key", "rate limit", "network",
)

# 可观测性②同款纪律:代码缺陷绝不归 infra。桥的 error 现在带真实异常类名前缀
# ("TypeError: 'GatewayClient' object is not callable")—— 这类消息哪怕碰巧含
# "gateway"/"connection" 字样也不是基础能力失效,盲 retry/误诊都耽误排查。
_CODE_DEFECT_PREFIXES = (
    "typeerror:", "attributeerror:", "keyerror:", "indexerror:", "nameerror:",
    "zerodivisionerror:", "assertionerror:", "recursionerror:", "unboundlocalerror:",
    "notimplementederror:",
)


def _is_infra_dead_error(err: str) -> bool:
    """DriveOutcome.error 是否指向"基础能力失效"(token/网/沙箱/网关 dead)。宽松指纹匹配;
    但代码缺陷类异常(带类名前缀)一票否决 —— 那是 bug,不是 infra(可观测性②)。"""
    low = (err or "").lower().strip()
    if any(low.startswith(p) for p in _CODE_DEFECT_PREFIXES):
        return False
    return any(fp in low for fp in _INFRA_DEAD_FINGERPRINTS)


def _workflow_roles_from_mentions(app, peer, mentions):
    """把 @ 的 mentions 解析成角色 [{role_id, display, agent_id, domain_id, domain_name}](去重保序)。

    mentions 元素接 dict {agent_id, domain_id?} 或纯字符串 "agent_id"(API 直调最自然的写法)。"""
    from karvyloop.karvy.capability import is_karvy_peer
    from .roundtable_engine import _member_display, _roundtable_roster
    dom_reg = getattr(app.state, "domain_registry", None)
    roster = _roundtable_roster(app, peer)
    is_world = is_karvy_peer(peer.domain_id)
    out, seen = [], set()
    for m in (mentions or []):
        if isinstance(m, str):
            m = {"agent_id": m}
        aid = (m.get("agent_id") or "").strip()
        did = (m.get("domain_id") or "").strip()
        for a in roster:
            if a.agent_id == aid and (not did or a.domain_id == did):
                key = (a.domain_id, a.agent_id)
                if key in seen:
                    break
                seen.add(key)
                dom = dom_reg.get(a.domain_id) if dom_reg is not None else None
                dname = getattr(dom, "name", "") if dom is not None else ""
                disp = _member_display(app, a)
                out.append({"role_id": f"r{len(out)}", "display": disp,
                            "agent_id": a.agent_id, "domain_id": a.domain_id,
                            "domain_name": dname if is_world else ""})
                break
    return out


async def _workflow_plan_llm(gw, model_ref, intent, roles) -> dict:
    """小卡设计 workflow DAG(显式任务用之,隐式按岗位职责+目标推)。返 {goal, steps:[...]}。"""
    import json as _json
    from karvyloop.gateway import ResolveScope
    from karvyloop.gateway.system import SystemPrompt
    roster_txt = "\n".join(
        f"- {r['role_id']}: {r['display']}" + (f"（{r['domain_name']}）" if r.get("domain_name") else "")
        for r in roles)
    sysp = (
        "你是群内协作的工作流编排者小卡。用户 @ 了下面这些角色协作完成一件事。设计一张 "
        "**workflow DAG**:给每个角色派一个步骤(必要时多个),标清**依赖**(下游依赖上游的产出)、"
        "能并行的就并行。用户给了显式任务就用它;没给就按该角色**职务/岗位职责 + 目标**推。"
        "**只输出 JSON**,别的不要:\n"
        '{"goal":"<一句话目标>","steps":[{"id":"s1","role_id":"<给定角色id>","task":"<这一步做什么>",'
        '"depends_on":[],"inputs":[],"when":null,"on_fail":"skip"}]}\n'
        "字段:id 形如 s1/s2;role_id 必须是给定角色之一;depends_on=前置 step id 列表(无前置=[])。\n"
        "**进阶(按需用,简单流程可省略)**:\n"
        "- inputs:这步真正吃哪几个上游产出(默认=depends_on);用于把多个分支**合并**。\n"
        "- when:**条件分支**,只在上游满足时才跑——"
        '{"step":"s1","status":"done"|"failed"} 或 {"step":"s1","contains":"<词>"}。'
        "做 if/else:对同一上游写两步、when 相反(如评审 done→发布 / failed→返工)。\n"
        '- on_fail:"skip"(默认,失败不挡下游)/ "retry"(可加 "max_retries":2)/ "abort"(中止全流程)。\n'
        "规则:别造环;引用的 step 必须存在;步骤别太碎,一个角色一步为主;不确定就用最简单的线性/并行,别硬塞分支。")
    usr = f"角色:\n{roster_txt}\n\n用户消息:{intent}"
    out = ""
    try:
        ref = gw.resolve_model(ResolveScope(atom_model=model_ref or None))
        async for ev in gw.complete([{"role": "user", "content": usr}], [], ref,
                                    system=SystemPrompt(static=[sysp])):
            if type(ev).__name__ == "TextDelta":
                out += getattr(ev, "text", "")
    except Exception as e:
        logger.warning(f"[workflow] 规划失败: {e}")
    try:
        plan = _json.loads(_extract_json_obj(out))
        if isinstance(plan, dict) and isinstance(plan.get("steps"), list):
            return plan
    except Exception as e:
        logger.warning(f"[workflow] 规划 JSON 解析失败: {e}")
    # 兜底:线性流水线(按 @ 顺序,各自一步)
    steps = [{"id": f"s{i+1}", "role_id": r["role_id"], "task": "完成你这部分",
              "depends_on": ([f"s{i}"] if i > 0 else [])} for i, r in enumerate(roles)]
    return {"goal": intent[:80], "steps": steps}


def _workflow_store(app):
    st = getattr(app.state, "workflow_store", None)
    if st is None:
        import pathlib
        from karvyloop.karvy.workflow_store import WorkflowStore
        cfgp = getattr(app.state, "config_path", "") or ""
        base = pathlib.Path(cfgp).parent if cfgp else (pathlib.Path.home() / ".karvyloop")
        st = WorkflowStore(base / "workflows.json")
        app.state.workflow_store = st
    return st


# ---- 逃生门:按 task_id 的协作式中止旗(圆桌无 run store,借它;workflow 也可双保险)----
# §0.7:人点"中止" → 把 task_id 记进这个集合 → 跑中的圆桌/工作流循环每轮查它 → 不再起新步/新轮。

def _mark_task_cancelled(app, task_id: str) -> None:
    if not task_id:
        return
    s = getattr(app.state, "cancelled_tasks", None)
    if not isinstance(s, set):
        s = set()
    s.add(task_id)
    try:
        app.state.cancelled_tasks = s
    except Exception:
        pass


def _is_task_cancelled(app, task_id: str) -> bool:
    if not task_id:
        return False
    s = getattr(app.state, "cancelled_tasks", None)
    return isinstance(s, set) and task_id in s


def _clear_task_cancelled(app, task_id: str) -> None:
    s = getattr(app.state, "cancelled_tasks", None)
    if isinstance(s, set):
        s.discard(task_id)


def _workflow_run_store(app):
    st = getattr(app.state, "workflow_run_store", None)
    if st is None:
        import pathlib
        from karvyloop.karvy.workflow_runs import WorkflowRunStore
        cfgp = getattr(app.state, "config_path", "") or ""
        path = (pathlib.Path(cfgp).parent / "workflow_runs.json") if cfgp else None
        st = WorkflowRunStore(path)
        app.state.workflow_run_store = st
    return st


async def execute_workflow_durable(app, *, run_id: str, goal: str, steps: list,
                                   governance: str = "", task_id=None) -> dict:
    """#39 ①:持久化执行 workflow —— 每步产出 memoize 落盘,重启后 replay 时已完成步秒命中、只续剩余。

    run_step:① 已缓存(重启续)→ 直接返回不重跑;② 否则按角色人格 drive,**成功才落盘**(失败不存→
    重启会重试)。两条路(首跑/重启续)共用这一个 run_step,所以 replay 天然续上。
    """
    from karvyloop.domain import Address
    from karvyloop.karvy.workflow import run_workflow
    from .routes import _model_for_role, _persona_for_role_addr, _rk_model, drive_in_tui
    main_loop = getattr(app.state, "main_loop", None)
    dom_reg = getattr(app.state, "domain_registry", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    ws = rk.get("workspace_root", "/")
    store = _workflow_run_store(app)
    disp_by_id = {s["id"]: s.get("display", s.get("agent_id", "?")) for s in steps}

    async def run_step(step, upstream):
        sid = step.get("id", "")
        cached = store.step_output(run_id, sid)
        if cached is not None:        # 重启续:已完成步秒命中缓存,绝不重烧 token
            return {"output": cached}
        addr = Address(domain_id=step.get("domain_id", ""), role="agent",
                       agent_id=step.get("agent_id", ""))
        dom = dom_reg.get(addr.domain_id) if dom_reg is not None else None
        persona, _speaker = _persona_for_role_addr(app, addr, dom, ws)
        up_txt = "\n\n".join(f"【{disp_by_id.get(dep, dep)} 的产出】\n{out}"
                             for dep, out in upstream.items() if out)
        intent = (f"工作流目标:{goal}\n\n你的任务:{step.get('task', '')}\n\n"
                  + (f"上游产出(基于它继续):\n{up_txt}\n\n" if up_txt else "")
                  + "请完成你这一步,产出要能交给下游。简洁、聚焦你的职责。")
        outcome = await drive_in_tui(intent, main_loop, governance=governance,
                                     persona=persona, scope="domain", fresh=True,
                                     **_rk_model(rk, _model_for_role(app, step.get("agent_id", ""))))
        err = (getattr(outcome, "error", "") or "").strip()
        out = (outcome.text or "").strip()
        # 失败但没 error 且正文也空 = 静默空产出 → 给个原因,别在文档里留空白 ✗(MAST:坏产出别静默)
        if not out and not err:
            err = "该步无产出(角色没交东西)"
        dead = bool(err) and _is_infra_dead_error(err)
        await _push_step(app, task_id, sid,
                         disp_by_id.get(sid, step.get("agent_id", "?")),
                         "done" if out else "failed",
                         ("基础能力失效(infra-dead):" + err) if dead else err)
        if not out:
            # 失败不落盘 → 重启会重试这步(失败可能是瞬时)。infra-dead → 附标,引擎据此 fail-loud 中止。
            return {"output": "", "error": err, "infra_dead": dead}
        store.set_step(run_id, sid, out)   # memoize:成功才存,这就是 durable 的家
        return {"output": out}

    def _should_cancel() -> bool:
        # §0.7 逃生门:人点了"中止"(经 run store 的 cancelled 标,或按 task_id 的中止旗)→ 不再起新步。
        return store.is_cancelled(run_id) or _is_task_cancelled(app, task_id or "")

    return await run_workflow({"goal": goal, "steps": steps}, run_step=run_step,
                              should_cancel=_should_cancel)


# 重启时超此 age 的中断 workflow 直接标 abandoned(不复活)。默认 6h:比任何合理的单轮工作流都长,
# 挂这么久的八成是跑歪 / 忘了的(逃生门:一条跑歪烧 token 的流程,重启就该能杀掉它)。0=不 sweep。
_STALE_RESUME_AGE_S = 6 * 3600


async def resume_workflows(app, *, max_age_s: float = _STALE_RESUME_AGE_S) -> dict:
    """启动时**不再无条件复活**被中断的 workflow(#54 逃生门解锁,最疼的病灶)。

    旧行为:启动 → 对所有 running 态**自动续跑**。后果:用户想靠重启杀掉一条跑歪烧 token 的 workflow,
    重启后它自动复活续烧 —— 逃生门反锁了。

    新行为(重启成为真正的逃生门):
      1. 超 age 上限(默认 6h)的 running → 直接标 **abandoned**,绝不复活(sweep_stale)。
      2. 其余 running → **挂起待人拍板**(不自动烧 token):记进 app.state.pending_resume,
         广播一条系统提示"上次有 N 条流程被中断,续跑还是丢弃?",由用户经 /api/workflow/resume(续)/
         /api/workflow/discard(丢)显式处置。重启默认 = 不动它(省 token,人坐驾驶座)。

    返回 {"abandoned": int, "pending": int, "pending_runs": [{run_id, goal, domain_id, done, total}]}。
    """
    store = _workflow_run_store(app)
    dropped = store.sweep_stale(max_age_s)
    if dropped:
        logger.info(f"[workflow] 重启:{len(dropped)} 条超时中断流程标 abandoned(不复活)")

    pending = []
    for run in list(store.running()):
        steps = run.get("steps", []) or []
        done = len(run.get("step_outputs", {}) or {})
        pending.append({"run_id": run.get("run_id", ""), "goal": run.get("goal", ""),
                        "domain_id": run.get("domain_id", "l0"),
                        "done": done, "total": len(steps)})
    # 挂起清单存 app.state,供 /api/workflow/pending_resume 查、/resume|/discard 处置。
    try:
        app.state.pending_resume = pending
    except Exception:
        pass
    if pending:
        logger.info(f"[workflow] 重启:{len(pending)} 条中断流程**挂起待拍板**(不自动续跑,逃生门)")
        # 主动冒泡(§0.7 fail-loud):别让它静默挂着,也别静默复活 —— 让人看见并拍板。
        try:
            from karvyloop.console.task_events import broadcast_system_error
            await broadcast_system_error(
                app, source="workflow_resume",
                message=f"{len(pending)} interrupted workflow(s) awaiting resume/discard")
        except Exception:
            pass
    return {"abandoned": len(dropped), "pending": len(pending), "pending_runs": pending}


async def resume_one_workflow(app, run_id: str) -> dict:
    """人显式选择"续跑"一条中断的 workflow(经 /api/workflow/resume)。已完成步秒命中缓存、只续剩余。"""
    main_loop = getattr(app.state, "main_loop", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    store = _workflow_run_store(app)
    run = store.get(run_id)
    if run is None or run.get("status") != "running":
        return {"ok": False, "reason": "no_such_running_workflow"}
    if main_loop is None or rk.get("gateway") is None:
        return {"ok": False, "reason": "no_llm"}
    mgr = getattr(app.state, "conversation_manager", None)
    try:
        result = await execute_workflow_durable(
            app, run_id=run_id, goal=run.get("goal", ""), steps=run.get("steps", []),
            governance="")
    except Exception as e:
        logger.warning(f"[workflow] 续跑 {run_id} 失败: {e}")
        return {"ok": False, "reason": f"resume_failed: {e}"}
    if mgr is not None and result.get("ok"):
        _record_workflow_line(app, run.get("domain_id", "l0"), run.get("goal", ""), result)
    # cancelled(续跑中人又踩了刹车)不覆盖回 done —— finish 只在 running 时生效
    store.finish(run_id)
    _drop_pending(app, run_id)
    return {"ok": True, "workflow": result}


def discard_workflow(app, run_id: str) -> dict:
    """人显式选择"丢弃"一条中断的 workflow(经 /api/workflow/discard):标 abandoned,不复活。"""
    store = _workflow_run_store(app)
    run = store.get(run_id)
    if run is None:
        return {"ok": False, "reason": "no_such_workflow"}
    # 复用 cancel 语义标死(cancelled=人主动叫停);再移出挂起清单。
    store.cancel(run_id)
    _drop_pending(app, run_id)
    return {"ok": True}


def _drop_pending(app, run_id: str) -> None:
    try:
        pend = getattr(app.state, "pending_resume", None) or []
        app.state.pending_resume = [p for p in pend if p.get("run_id") != run_id]
    except Exception:
        pass


def _record_workflow_line(app, domain_id: str, goal: str, result: dict) -> None:
    """把一次 workflow 结果落成独立工作流会话线(2a 的可复用版;resume 也用)。"""
    from karvyloop.domain import Address
    from karvyloop.cognition.conversation import KARVY_WORLD_DOMAIN
    import uuid as _uuid
    mgr = getattr(app.state, "conversation_manager", None)
    dom_reg = getattr(app.state, "domain_registry", None)
    if mgr is None:
        return
    doc = _workflow_result_doc(result)
    run_id = _uuid.uuid4().hex[:16]
    run_peer = Address(domain_id=domain_id, role="workflow", agent_id=run_id)
    _dom = dom_reg.get(domain_id) if dom_reg is not None else None
    origin = (getattr(_dom, "name", "") or
              ("Karvy World" if domain_id == KARVY_WORLD_DOMAIN else domain_id))
    title = (goal[:60] or "工作流").strip()
    try:
        mgr.create_record(run_peer, title=title, user_intent=f"⚙ 工作流:{goal}",
                          agent_response=doc, brain="slow",
                          data={"workflow": result, "kind": "workflow", "origin_group": origin})
    except Exception as e:
        logger.warning(f"[workflow] 落工作流线失败: {e}")


async def _push_step(app: Any, task_id: Optional[str], step_id: str, display: str,
                     status: str, error: str = "") -> None:
    """§0.7 P2:把一步的完成/失败推给 UI(实时进度,不等整体跑完)。失败不阻塞。"""
    if not task_id:
        return
    # 活动时间线(借鉴 Multica):这步**持久**记到任务身上 —— 失败 = blocked(主动报阻塞,
    # 看板卡直接冒 ⚠,不等人来问"怎么样了");刷新/重启后时间线仍在。
    try:
        reg = getattr(app.state, "task_registry", None)
        if reg is not None:
            kind = "blocked" if status == "failed" else "step"
            text = display + ((":" + (error or "")[:200]) if status == "failed" else "")
            reg.add_event(task_id, kind, text)
    except Exception:
        pass
    try:
        from karvyloop.console.task_events import broadcast_task_step
        await broadcast_task_step(app, {
            "task_id": task_id, "step_id": step_id, "display": display,
            "status": status, "error": (error or "")[:280],
        })
    except Exception:
        pass


def _workflow_result_doc(result: dict) -> str:
    parts = [f"⚙ 工作流:{result.get('goal', '')}"]
    # 逃生门/fail-loud 状态如实上镜:被中止 / infra-dead 中止,别假装跑成了。
    if result.get("cancelled"):
        parts.append("\n\n🛑 (已中止 —— 剩余步骤未执行)")
    elif result.get("infra_dead"):
        parts.append("\n\n⛔ (基础能力失效 infra-dead:中止,非任务本身的问题)")
    elif result.get("aborted"):
        parts.append("\n\n⛔ (某步 on_fail=abort → 全流程中止)")
    for s in result.get("steps", []):
        st = s.get("status", "")
        mark = "✓" if st == "done" else ("✗" if st == "failed" else "—")
        head = f"\n\n**{mark} {s.get('display', '?')} · {s.get('task', '')}**"
        body = (s.get("output") or "").strip()
        if st == "failed":
            # 失败带**原因**(#54):✗ 后写为什么,别静默空白(MAST:坏产出别静默喂下游)
            body = "原因:" + (s.get("error") or "该步无产出").strip()
        elif st == "skipped":
            body = "(未执行:被中止 / 分支未选中)"
        parts.append(head + "\n" + body)
    return "".join(parts)
