"""proposal_handlers — PROPOSE ACCEPT 的真兑现 handler(接 D5 live,M3+ 拍 9.4-门2)。

设计:docs/30 §3 kind→兑现 + §5.1。registry.decide(ACCEPT) 按 kind 查这里的 handler。

诚实原则(不为对称假兑现):**只接有真实目的地的 kind**。
- `crystallize_skill`(IntentAnalyst 从习惯凝):正确兑现 = 采纳确认(结晶是 usage-driven,
  9.4 签名修复后可靠,不在此强行结晶绕过门槛)。
- `route_to_role`(9.4-门2 执行-role 流):ACCEPT → 让目标业务 role 在其域 value.md 治理下
  执行需求(in-process drive;P1 走真 A2A envelope/inbox)。
- `resolve_conflict`(闭环审计断③补):ACCEPT → 决议入台账+Trace(域内自动禁用未建,回执如实说)。
- `set_preference`:兑现子系统未建 → 不注册;registry 对无 handler 的 ACCEPT 诚实回执
  且**卡保留待决**,不假装(docs/30 §5.1 + 断③通用防御)。
"""
from __future__ import annotations

import logging
from functools import partial
from typing import Any, Callable, Dict, Tuple

from karvyloop.cognition.weekly_digest import KIND_WEEKLY_DIGEST
from karvyloop.crystallize.revision import KIND_REVISE_SKILL, apply_revision_proposal
from karvyloop.karvy.proposal_registry import (
    KIND_CONFIRM_DECISION_PREF, KIND_CONFIRM_RESULT, KIND_CRYSTALLIZE_SKILL,
    KIND_INFEASIBLE_REPORT, KIND_MERGE_ATOMS, KIND_MERGE_KNOWLEDGE, KIND_OPS_FIX,
    KIND_RESOLVE_CONFLICT, KIND_ROUNDTABLE, KIND_ROUTE_TO_ROLE, KIND_RUN_TASK,
    KIND_FS_ACCESS,
)

logger = logging.getLogger(__name__)


def _crystallize_skill_handler(proposal) -> Tuple[bool, str]:
    """采纳"把常做的事结晶成技能"的建议(docs/30 crystallize_skill)。"""
    summary = getattr(proposal, "summary", "") or "这个习惯"
    return True, f"已采纳「{summary}」— 你继续这样用,系统会自动把它结晶成技能"


def _governance_for(app: Any, payload: dict) -> str:
    """构造"role 身份 + 域 value.md"治理串(喂慢脑前缀)。

    role 身份让 forge 知道"我在以业务域 X 的 <role> 角色干活";value.md 是该域硬护栏文本。
    复用 conversation.governance_text 同款封顶(docs/28 token 纪律)。
    """
    domain_name = payload.get("domain_name") or payload.get("domain_id") or "?"
    role = payload.get("role") or "agent"
    identity = f"你正在以业务域「{domain_name}」的「{role}」角色身份工作。"
    value_text = ""
    reg = getattr(app.state, "domain_registry", None)
    if reg is not None and payload.get("domain_id"):
        try:
            domain = reg.get(payload["domain_id"])
            value_text = getattr(getattr(domain, "value_md", None), "text", "") or ""
        except Exception:
            value_text = ""
    if value_text:
        if len(value_text) > 1500:
            value_text = value_text[:1500] + "…"
        return f"{identity}\n必须遵循该域的价值观(value.md):\n{value_text}"
    return identity


def pop_report_card(app: Any, proposal_id: str) -> Any:
    """取走某提案兑现后产生的回报卡(decide 路径回显时调,取一次即清)。无则 None。"""
    store = getattr(app.state, "report_cards", None)
    return store.pop(proposal_id, None) if isinstance(store, dict) else None


def _stash_report_card(app: Any, proposal: Any, checked: Any, *, problem: str, approach: str) -> None:
    """执行后回报卡:从独立验收 verdict 建卡 → 存 app.state.report_cards[proposal_id]。

    decide 路径回显时附到 h2a_envelope。**只 ✓ 接地于真验收(非 inconclusive);没 verdict
    → 当未决,绝不伪 ✓**(还 ROADMAP 那笔诚实债)。建卡失败绝不影响兑现本身。
    """
    try:
        from karvyloop.cognition.decision_card import build_report_card
        v = getattr(checked, "verdict", None)
        card = build_report_card(
            problem=problem, approach=approach,
            passed=bool(getattr(v, "passed", False)),
            inconclusive=bool(getattr(v, "inconclusive", True)),
            feedback=(getattr(v, "feedback", "") or ""))
        pid = getattr(proposal, "proposal_id", "") or ""
        if not pid:
            return
        card["proposal_id"] = pid
        store = getattr(app.state, "report_cards", None)
        if store is None:
            store = app.state.report_cards = {}
        store[pid] = card
    except Exception:
        pass


def _schedule_role_experience(app: Any, *, role: str, domain: str, requirement: str,
                              result: str = "", success: bool = False, verified: bool = False,
                              correction: str = "") -> None:
    """委派任务收尾 → fire-and-forget 调度角色经验沉淀(docs/54 模块1 Top2)。

    保守门在 `sediment_experience`/`should_distill` 内部把关(无域/l0/纯失败无纠正 → 零 LLM);
    这里只负责**不阻断决策兑现**地把它丢进事件循环。无 running loop(同步测试上下文)→ 直接
    同步跑一轮(仍 fail-soft)。fail-loud:后台任务异常上冒 system_error,不静默死。
    """
    try:
        from karvyloop.roles.experience import TaskOutcomeSignal, sediment_experience
    except Exception:
        return
    mem = getattr(app.state, "memory", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if mem is None or gw is None:
        return
    import time as _t
    sig = TaskOutcomeSignal(role=role, domain=domain or "", requirement=requirement,
                            result=result or "", success=success, verified=verified,
                            correction=correction or "", ts=_t.time())
    # 保守门先零成本判(省得没信号也起 task);不满足 → 直接不动
    try:
        from karvyloop.roles.experience import should_distill
        if not should_distill(sig):
            return
    except Exception:
        return
    coro = sediment_experience(sig, mem=mem, gateway=gw, model_ref=rk.get("model_ref", ""))
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        try:
            asyncio.run(coro)
        except Exception as e:
            logger.warning(f"[role_experience] 同步沉淀失败(role={role} domain={domain}): {e}")
        return
    tasks = getattr(app.state, "_role_experience_tasks", None)
    if tasks is None:
        tasks = app.state._role_experience_tasks = set()
    task = loop.create_task(coro)
    tasks.add(task)

    def _on_done(t: Any) -> None:
        tasks.discard(t)
        try:
            exc = t.exception()
        except Exception:
            return
        if exc is not None:
            logger.error(f"[role_experience] 沉淀后台任务异常: {exc}")
            try:
                from karvyloop.console.task_events import schedule_system_error
                schedule_system_error(app, "role_experience_sediment", str(exc))
            except Exception:
                pass

    task.add_done_callback(_on_done)


def _route_to_role_handler(app: Any) -> Callable[[object], Tuple[bool, str]]:
    """route_to_role ACCEPT 兑现:让目标 role 在其域治理下执行需求(docs/29 KC-3/KC-5)。

    K5:本 handler 只在用户 ACCEPT 后被调。in-process drive(0.1.0;P1 走真 A2A 投递)。
    注:同步 drive(一次 LLM 时长)—— REST 路径在 FastAPI 线程池;WS 路径由 ws.py 用
    asyncio.to_thread 包 decide,故不阻塞事件循环。
    """
    def handler(proposal) -> Tuple[bool, str]:
        ml = getattr(app.state, "main_loop", None)
        if ml is None:
            return False, "未注入 main_loop —— 无法执行委派(--no-llm?)"
        rk = getattr(app.state, "runtime_kwargs", None) or {}
        payload = getattr(proposal, "payload", None) or {}
        requirement = payload.get("requirement") or getattr(proposal, "summary", "")
        role = payload.get("role") or "agent"
        if not requirement:
            return False, "委派需求为空"
        try:
            from karvyloop.runtime.main_loop import forge_slow_brain_factory
            from karvyloop.coding.checker import verdict_suffix
            from karvyloop.cli.pursuit_loop import pursue
            from karvyloop.console.decision_wire import assemble_governance
            # ③A-2 范式编译器拉齐:委派执行也用 **load_paradigm 编译的 per-role persona**(七层灵魂 +
            # 域 value.md + 结构化 deontic → per-role system prompt),让 value.md/deontic 在"委派干活"
            # 路径也一致下沉(此前只在"跟角色聊天"路径编译,委派路径只软前缀 value.md 文本)。
            _did = payload.get("domain_id", "")
            persona = None
            _bound_skills: list = []   # 委派角色 COMPOSITION.yaml 声明的绑定技能名(→ pursue prefer,召回优先)
            try:
                _rreg = getattr(app.state, "role_registry", None)
                _dreg = getattr(app.state, "domain_registry", None)
                _rv = _rreg.get(str(role)) if _rreg is not None else None
                _dom = _dreg.get(_did) if (_dreg is not None and _did) else None
                if _rv is not None:
                    _bound_skills = list(getattr(_rv, "skill_ids", None) or [])
                    from karvyloop.coding.paradigm_prompt import build_role_paradigm_prompt
                    persona = build_role_paradigm_prompt(_rv, _dom, intent=requirement,
                                                         cwd=rk.get("workspace_root", "/"))
            except Exception:
                persona = None  # 编译失败 → 回退软前缀(0 回归)
            # governance = 你的决策标准(prealign)+ 相关知识。persona 编译成功时它已含 value.md/deontic
            # → 不再用 _governance_for 重复 value.md(去冗余);编译失败才回退软 value.md 前缀。
            _base = "" if persona is not None else _governance_for(app, payload)
            gov = assemble_governance(app, intent=requirement, domain=_did, role=str(role), base=_base)
            # §15.5:委派执行时挂上 create_atom(无 atom 可用→role 自造),并归属到该 role(沉淀用)。
            # minted 收集本次新造的 atom,任务收尾按结果沉淀(认可→留+入 composition / 失败→撤)。
            _minted: list = []
            slow_brain = forge_slow_brain_factory(
                governance=gov,
                persona=persona,  # ③A-2:编译后的 per-role 范式(含 value.md+deontic),None=回退默认
                atom_registry=getattr(app.state, "atom_registry", None),
                role_registry=getattr(app.state, "role_registry", None),
                self_create_role=str(role),
                self_create_minted=_minted,
                **rk)
            # docs/02 §15:role 作为尽责下属在预算内自助追求 —— drive + 独立验收,没跑完/验收不过
            # 在同一预算内 replan/修,infra-dead 立即 fail-loud,耗尽则带证据升不可行报告卡。
            # per-task token 归因:委派烧的 token 记到该提案名下(成本预估的样本单元)
            from karvyloop.llm.token_ledger import token_task
            with token_task(getattr(proposal, "proposal_id", "") or ""):
                # 角色绑定技能透传:COMPOSITION.yaml skills: → recall 优先(绑定优先于模糊召回)
                outcome = pursue(requirement, ml=ml, slow_brain=slow_brain, rk=rk,
                                 prefer=(_bound_skills or None))
            checked = outcome.checked
            result = checked.result
        except Exception as e:
            logger.warning(f"[route_to_role] 执行失败: {e}")
            return False, f"委派执行失败: {e}"
        # §15.5 沉淀(问责链 人←role←atom,Hardy 2026-06-29):人 accept 的是 role 的**结果**,不直接碰
        # atom。任务**失败** → 自造 atom 直接撤(0 引用安全);任务**成功** → 升「结果确认卡」,人 ACCEPT
        # 结果(=依据)才由 role 综合裁每个自造 atom(judge+sediment 在 confirm_result handler 里)——
        # **不在这里替人 accept**(否则又是机械闸)。人不处理 → atom 留 provisional,④ 巡检孤儿撤。
        if _minted:
            _areg = getattr(app.state, "atom_registry", None)
            _rreg = getattr(app.state, "role_registry", None)
            _failed = bool(outcome.infeasible or outcome.infra_dead or getattr(result, "error", ""))
            if _failed:
                from karvyloop.atoms.self_create import sediment_self_created
                for _aid in _minted:
                    try:
                        sediment_self_created(_aid, approved=False, atom_registry=_areg,
                                              role_registry=_rreg, role_id=str(role))
                    except Exception:
                        logger.warning(f"[route_to_role] 撤自造 atom {_aid} 失败", exc_info=True)
            else:
                try:
                    import time as _t2
                    from karvyloop.karvy.proposal_registry import proposal_for_confirm_result
                    _mints = [{"id": a, "purpose": (getattr(_areg.get(a), "prompt", "") or "")[:120]}
                              for a in _minted if _areg is not None and _areg.get(a) is not None]
                    _preg = getattr(app.state, "proposal_registry", None)
                    if _mints and _preg is not None:
                        _preg.register(proposal_for_confirm_result(
                            role=str(role), requirement=requirement, minted=_mints,
                            domain_id=payload.get("domain_id", ""), ts=_t2.time()))
                except Exception as e:
                    logger.warning(f"[route_to_role] 升结果确认卡失败: {e}")
        if getattr(result, "error", "") and not (outcome.infeasible or outcome.infra_dead):
            return False, f"「{role}」执行出错: {result.error}"
        # ① infra-dead:基础能力没了 → fail-loud,诚实说"不是任务的问题",**不发卡**(replan 没用)
        if outcome.infra_dead:
            return True, (f"「{role}」没法继续:基础能力暂时不可用(模型/网络/沙箱调不通)—— "
                          f"这不是任务本身的问题,检查后再让它接着做。")
        # ② 预算耗尽仍没成 → 带真实尝试轨迹升「不可行报告卡」(尽责下属:带证据回头,不甩裸问题)
        if outcome.infeasible:
            n = len(outcome.attempts)
            try:
                import time as _t
                from karvyloop.karvy.proposal_registry import proposal_for_infeasible_report
                card = proposal_for_infeasible_report(
                    goal=requirement, role=str(role), attempts=outcome.attempts, ts=_t.time(),
                    domain_id=payload.get("domain_id", ""), domain_name=payload.get("domain_name", ""))
                reg = getattr(app.state, "proposal_registry", None)
                if reg is not None:
                    reg.register(card)  # 进 pending → 前端 boot-fetch 取(live WS 推为 P1 增量)
            except Exception as e:
                logger.warning(f"[route_to_role] 升不可行报告卡失败: {e}")
            return True, (f"「{role}」自助重规划 {n} 次仍没拿下「{requirement}」—— "
                          f"已把带证据的不可行报告放进决策卡(🤝),等你定夺(改目标 / 补资源 / 放下)。")
        # ③ 正常:执行 + 验收结论
        txt = (getattr(result, "text", "") or "").strip().replace("\n", " ")
        if len(txt) > 140:
            txt = txt[:140] + "…"
        suffix = verdict_suffix(checked)
        _stash_report_card(app, proposal, checked,
                           problem=requirement, approach=f"由「{role}」在域治理下执行")
        # docs/54 模块1 Top2:角色经验沉淀 —— 委派任务收尾 → 保守门 → 蒸馏 → role-scoped Belief。
        # 只在**过了独立验收**的成功任务沉(should_distill 内部保守;无域/纯失败不沉)。
        # fire-and-forget、fail-soft,绝不阻断决策兑现。
        _v = getattr(checked, "verdict", None)
        _verified = bool(_v is not None and getattr(_v, "passed", False)
                         and not getattr(_v, "inconclusive", False))
        _schedule_role_experience(
            app, role=str(role), domain=_did, requirement=requirement,
            result=(getattr(result, "text", "") or ""),
            success=True, verified=_verified)
        return True, f"已由「{role}」执行:{txt or '(无输出)'}{(' ' + suffix) if suffix else ''}"

    return handler


def _run_task_handler(app: Any) -> Callable[[object], Tuple[bool, str]]:
    """run_task ACCEPT 兑现(loop-step2c:闭合主动 loop)。

    小卡主动提议"上次 X 没跑完,要我重试吗?" → 用户 ACCEPT → 这里**真的重跑** intent,
    并登记成一条**新任务**(running→done/error,落盘),让重跑出现在看板上 → loop 闭环。
    K5:只在用户 ACCEPT 后被调。同步 drive(复用 route_to_role 同款:REST 线程池 /
    WS asyncio.to_thread,不阻塞事件循环)。
    """
    def handler(proposal) -> Tuple[bool, str]:
        ml = getattr(app.state, "main_loop", None)
        if ml is None:
            return False, "未注入 main_loop —— 无法重跑(--no-llm?)"
        rk = getattr(app.state, "runtime_kwargs", None) or {}
        payload = getattr(proposal, "payload", None) or {}
        intent = (payload.get("intent") or "").strip()
        if not intent:
            return False, "重跑意图为空"
        task_reg = getattr(app.state, "task_registry", None)
        tid = None
        if task_reg is not None:
            tid = task_reg.start(
                who=(payload.get("role") or "小卡"),
                domain_id=payload.get("domain_id", "l0"),
                role=payload.get("role", ""), intent=intent,
            )
        try:
            from karvyloop.runtime.main_loop import forge_slow_brain_factory
            from karvyloop.console.decision_wire import assemble_governance
            did = payload.get("domain_id", "l0")
            # Step 0(a):你的决策标准在**重跑任务**时也生效(l0 也注入 —— 重跑就是替你做事)。
            gov = assemble_governance(app, intent=intent, domain=("" if did == "l0" else did),
                                      role=payload.get("role", ""),
                                      base=(_governance_for(app, payload) if did != "l0" else ""))
            # 重跑也用小卡人格(l0)→ 输出是小卡的声音,不是 CodingResult 八股(与人格层一致)
            persona = None
            if did == "l0":
                try:
                    from karvyloop.coding.persona import build_karvy_persona_prompt
                    persona = build_karvy_persona_prompt(cwd=rk.get("workspace_root", "/"))
                except Exception:
                    persona = None
            slow_brain = forge_slow_brain_factory(governance=gov, persona=persona, **rk)
            # loop step3:重跑后过一道**独立验收**(maker→checker→不过则修一轮),
            # 让 loop 真的"验过了"而不是作者自述。无验收能力时诚实退回单跑。
            # per-task token 归因(#42 成本预估地基):这个任务烧的每个 token 记到它名下
            from karvyloop.llm.token_ledger import token_task
            from karvyloop.coding.checker import verify_and_fix_with_rk, verdict_suffix
            with token_task(tid or ""):
                checked = verify_and_fix_with_rk(intent, ml=ml, slow_brain=slow_brain, rk=rk)
            result = checked.result
        except Exception as e:
            if task_reg is not None and tid:
                task_reg.finish(tid, error=str(e))
            logger.warning(f"[run_task] 重跑失败: {e}")
            return False, f"重跑失败: {e}"
        err = getattr(result, "error", "") or ""
        txt = (getattr(result, "text", "") or "").strip()
        if task_reg is not None and tid:
            task_reg.finish(tid, result=txt, error=err)
        if err:
            return False, f"重跑出错: {err}"
        short = txt.replace("\n", " ")
        if len(short) > 140:
            short = short[:140] + "…"
        suffix = verdict_suffix(checked)
        _stash_report_card(app, proposal, checked,
                           problem=intent, approach=f"重跑「{intent[:40]}」")
        return True, f"已重跑「{intent[:30]}」:{short or '(无输出)'}{(' ' + suffix) if suffix else ''}"

    return handler


def _confirm_decision_pref_handler(app: Any) -> Callable[[object], Tuple[bool, str]]:
    """确认决策偏好:把 provisional 升 confirmed(docs/02 §11 P1)。

    Belief 无稳定 id → 按 payload.content 在认知库里按内容匹配回查那条 provisional 偏好。
    ACCEPT 后它升 confirmed:以后 prealign 标"(暂记)"消失,且相反决策只降不静默删(尊重你拍过板)。
    """
    def handler(proposal) -> Tuple[bool, str]:
        payload = getattr(proposal, "payload", None) or {}
        content = payload.get("content", "") or getattr(proposal, "summary", "")
        mem = getattr(app.state, "memory", None)
        if mem is None:
            return False, "未接认知库"
        try:
            from karvyloop.crystallize.decision_pref import (
                confirm_pref, find_decision_pref,
            )
            beliefs = []
            for sc in ("personal", "domain"):
                for b in mem.index.all(sc):
                    beliefs.append(b)
            target = find_decision_pref(beliefs, content, status="provisional")
            if target is None:
                # 已 confirmed 过 → 幂等成功;彻底不在 → 可能被你后来的决策推翻撤销了
                if find_decision_pref(beliefs, content) is not None:
                    return True, "这条偏好已经是你的默认了"
                return False, "这条偏好已不在(可能被你后来的决策推翻了)"
            mem.archive(target)
            mem.write(confirm_pref(target))
            return True, f"已记成你的默认偏好 —— 我以后提案会提前按它对齐"
        except Exception as e:
            logger.warning(f"[confirm_decision_pref] 升级失败: {e}")
            return False, f"确认失败: {e}"
    return handler


def _ops_fix_handler(proposal) -> Tuple[bool, str]:
    """ops_fix ACCEPT 兑现(L1 自愈 slice3)。

    **诚实铁律:LLM 诊断文本永不被执行。** ACCEPT 只在 risk=reversible 且底层 finding
    在 doctor.AUTO_FIXABLE 时,跑**确定性** `doctor.repair_finding`(且重新跑 doctor 取**新鲜**
    finding,不信卡上 stale params);否则只"记下,请按诊断步骤手动处理"。
    """
    payload = getattr(proposal, "payload", None) or {}
    codes = list(payload.get("finding_codes", []) or [])
    risk = payload.get("risk", "needs_approval")
    from karvyloop.doctor import AUTO_FIXABLE, repair_finding, run_doctor

    fixable = [c for c in codes if c in AUTO_FIXABLE]
    if not fixable or risk != "reversible":
        # needs_approval / 非自动修 / 纯运行时报错 → 不碰系统,只确认
        return True, "已记下 —— 这是诊断建议,系统不会自动改,请按步骤手动处理"
    # 重新确定性自检拿新鲜 finding(卡可能已过期)→ 只修仍存在且匹配的那几个
    fresh = [f for f in run_doctor(check_port=False) if f.code in fixable]
    repaired: list[str] = []
    for f in fresh:
        try:
            r = repair_finding(f)
        except Exception:
            r = None
        if r is not None:
            repaired.append(r.code)
    if repaired:
        return True, "已做确定性可逆修复(原数据已备份成 .corrupt.bak,可找回)"
    return True, "已记下 —— 复检时该问题已不在,无需修复"


def _roundtable_handler(app: Any) -> Callable[[object], Tuple[bool, str]]:
    """roundtable ACCEPT 兑现:在目标群里**真的开一场圆桌**(Hardy 2026-06-25 编排 bug)。

    小卡识别"让几个角色开圆桌讨论X"→ 出 roundtable PROPOSE;你 ACCEPT → 这里:
    切到该群 peer → 拉选中的成员 → 建圆桌对话 + 小卡发目标对齐开场(复用 /roundtable/start 内核)。
    K5:只在 ACCEPT 后被调。本 handler 在线程池(REST)/ to_thread(WS)里跑 —— 无运行中事件循环,
    故可用 asyncio.run 跑那两个 async 的 LLM 子步(标题精炼 + 开场)。
    """
    def handler(proposal) -> Tuple[bool, str]:
        import asyncio

        mgr = getattr(app.state, "conversation_manager", None)
        dom_reg = getattr(app.state, "domain_registry", None)
        rk = getattr(app.state, "runtime_kwargs", None) or {}
        gw = rk.get("gateway")
        if mgr is None or dom_reg is None or gw is None:
            return False, "未接 LLM / 对话编排器 —— 无法开圆桌(--no-llm?)"
        payload = getattr(proposal, "payload", None) or {}
        topic = (payload.get("topic") or getattr(proposal, "summary", "")).strip()
        group_domain_id = payload.get("group_domain_id") or "l0"
        group_name = payload.get("group_name") or "Karvy World"
        participants = payload.get("participants") or []
        if not topic:
            return False, "圆桌主题为空"
        try:
            from karvyloop.domain.registry import Address
            from karvyloop.console.routes import (
                _member_display, _refine_run_title, _roundtable_clarify_opening,
                _roundtable_members, _roundtable_state, _persist_roundtable_state,
            )
            # 切到目标群 peer(圆桌挂在群场下)。
            gpeer = Address(domain_id=group_domain_id, role="group", agent_id="")
            mgr.set_peer(gpeer)
            members = _roundtable_members(app, gpeer, participants)
            if not members:
                return False, f"「{group_name}」里没有可上桌的角色(先去业务域入职 agent)"
            member_names = [_member_display(app, a) for a in members]
            model_ref = rk.get("model_ref", "")
            title = asyncio.run(_refine_run_title(gw, model_ref, topic))
            conv = mgr.new_conversation(title=f"🎡 {title}")
            opening = asyncio.run(_roundtable_clarify_opening(gw, model_ref, topic, member_names))
            mgr.record_turn(f"🎡 发起圆桌:{topic}", opening, brain="slow")
            _roundtable_state(app)[conv.id] = {
                "topic": topic, "participants": [a.agent_id for a in members],
                "domain_id": group_domain_id, "phase": "aligning",
            }
            _persist_roundtable_state(app)
        except Exception as e:
            logger.warning(f"[roundtable] 开桌失败: {e}")
            return False, f"开圆桌失败: {e}"
        who = "、".join(member_names)
        return True, f"已在「{group_name}」开圆桌,叫上 {who} —— 去 🎡「{title}」线跟他们对齐目标"

    return handler


def _infeasible_report_handler(proposal) -> Tuple[bool, str]:
    """「不可行报告」ACCEPT 兑现(docs/02 §15.3)。

    这是一份**带证据的结论**,不是可执行动作 —— 报告卡天然 unverifiable,系统**不替你重试**。
    ACCEPT = 你已知悉/接纳此结论(放下);真要换目标或补资源,走 REJECT 后另发。所以 handler
    只记录知悉,绝不跑任何执行(诚实:不假装"接纳=自动解决")。
    """
    payload = getattr(proposal, "payload", {}) or {}
    goal = (payload.get("goal") or "").strip() or "该目标"
    role = (payload.get("role") or "").strip() or "角色"
    n = len(payload.get("attempts") or [])
    return True, (f"已记录:「{role}」追求「{goal}」未达成(自助重规划 {n} 次)。"
                  f"系统不会自动重试 —— 等你的下一步(改目标 / 补资源 / 放下)。")


def _merge_atoms_handler(app: Any) -> Callable[[object], Tuple[bool, str]]:
    """原子语义合并 ACCEPT 兑现(docs/14 §11.2):人拍过这一簇 → 真 `apply_merge`(rewire-before-delete)。

    护城河资产 → 只在用户 ACCEPT 后才真改;rewire-before-delete 保证无悬空引用。成员被先前合并
    吃掉(真实存在 < 2)→ apply_merge 自身 ok=False 不动,如实回执(不假装合并了)。
    """
    def handler(proposal) -> Tuple[bool, str]:
        areg = getattr(app.state, "atom_registry", None)
        rreg = getattr(app.state, "role_registry", None)
        if areg is None or rreg is None:
            return False, "未接 atom_registry / role_registry —— 无法合并"
        payload = getattr(proposal, "payload", None) or {}
        canonical = (payload.get("canonical_id") or "").strip()
        members = list(payload.get("member_ids") or [])
        if not canonical or len(members) < 2:
            return False, "合并方案不完整(缺规范原子或成员 < 2)"
        from karvyloop.atoms.consolidate import apply_merge
        res = apply_merge(canonical, members,
                          merged_purpose=payload.get("merged_purpose", ""),
                          merged_tools=payload.get("merged_tools", []),
                          atom_registry=areg, role_registry=rreg)
        if not res.get("ok"):
            return False, f"未合并:{res.get('reason', '成员已变化')}"
        return True, (f"已合并 {res.get('merged_n', len(members))} 个原子 → 「{res.get('canonical', canonical)}」;"
                      f"改写 {len(res.get('rewired_roles', []))} 个角色引用、删 {len(res.get('removed_atoms', []))} 个冗余原子"
                      f"(rewire-before-delete,无悬空引用)。")

    return handler


def _confirm_result_handler(app: Any) -> Callable[[object], Tuple[bool, str]]:
    """「结果确认卡」ACCEPT 兑现(docs/02 §15.5):人认可了 role 的结果(=依据)→ role 综合裁自造 atom。

    对卡里每个自造 atom 跑 role 的综合判断(judge_atom_keep,human_approved=True 因为人刚 ACCEPT 了结果)
    → 留则入 role composition、撤则删。不处理这张卡 → atom 留 provisional,④ 巡检孤儿撤(故无需处理 REJECT)。
    """
    def handler(proposal) -> Tuple[bool, str]:
        import asyncio as _aio
        payload = getattr(proposal, "payload", None) or {}
        role = (payload.get("role") or "").strip() or "角色"
        minted = [str(m.get("id", "")).strip() for m in (payload.get("minted") or []) if m.get("id")]
        if not minted:
            return True, "没有待沉淀的自造原子"
        areg = getattr(app.state, "atom_registry", None)
        rreg = getattr(app.state, "role_registry", None)
        rk = getattr(app.state, "runtime_kwargs", None) or {}
        gw = rk.get("gateway")
        if areg is None:
            return False, "未接 atom_registry —— 无法沉淀"
        from karvyloop.atoms.self_create import judge_atom_keep, sediment_self_created
        role_identity = ""
        try:
            _rv = rreg.get(role) if rreg is not None else None
            role_identity = (getattr(_rv, "identity", "") or "") if _rv is not None else ""
        except Exception:
            role_identity = ""
        kept, dropped = [], []
        for aid in minted:
            spec = areg.get(aid)
            if spec is None:
                continue
            try:
                if gw is not None:
                    j = _aio.run(judge_atom_keep(
                        spec, role_id=role, role_identity=role_identity,
                        human_approved=True, contributed=True, verified=True,
                        gateway=gw, model_ref=rk.get("model_ref", "")))
                    keep = bool(j.get("keep"))
                else:
                    keep = False  # 无 gateway 无法综合判断 → 保守不留
                sediment_self_created(aid, approved=keep, atom_registry=areg,
                                      role_registry=rreg, role_id=role)
                (kept if keep else dropped).append(aid)
            except Exception:
                logger.warning(f"[confirm_result] 综合裁/沉淀 {aid} 失败", exc_info=True)
        return True, (f"已认可「{role}」的结果;它综合判断后留下 {len(kept)} 个新能力进工具箱"
                      + (f"(撤了 {len(dropped)} 个不够通用的)" if dropped else "") + "。")

    return handler


def _merge_knowledge_handler(app: Any) -> Callable[[object], Tuple[bool, str]]:
    """知识整理建议卡 ACCEPT 兑现(daily 慢侧自动升):apply_belief_merge —— 先写合并条、再删被并旧条
    (中途失败不丢数据);成员被先前操作删过(真实存在 < 2)→ ok=False 如实回执,不假装合并了。"""
    def handler(proposal) -> Tuple[bool, str]:
        mem = getattr(app.state, "memory", None)
        if mem is None:
            return False, "未接 memory —— 无法合并知识"
        payload = getattr(proposal, "payload", None) or {}
        members = list(payload.get("member_contents") or [])
        merged = (payload.get("merged_content") or "").strip()
        if len(members) < 2 or not merged:
            return False, "合并方案不完整(成员 < 2 或合并内容为空)"
        from karvyloop.cognition.consolidate import apply_belief_merge
        res = apply_belief_merge(members, merged,
                                 merged_title=payload.get("merged_title", ""), mem=mem)
        if not res.get("ok"):
            return False, f"未合并:{res.get('reason', '成员已变化')}"
        return True, f"已把 {res.get('removed', len(members))} 条近重复知识合并成一条(先写后删,不丢数据)。"

    return handler


def _resolve_conflict_handler(app: Any) -> Callable[[object], Tuple[bool, str]]:
    """resolve_conflict ACCEPT 兑现(闭环审计断③:此前无 handler,ACCEPT 空转还吞卡)。

    卡的语义(docs/31 SC-5):某全局技能疑似违反某业务域的治理规则(域入职时异步检出),
    ACCEPT = 你确认"这个冲突要当回事"。诚实边界:域内自动禁用技能的执行机制(域颗粒度的
    召回过滤)还没建 —— 运行时不拦是 Hardy 拍过的设计(SC-1),所以这里**不假装禁用**;
    真实落地 = ①决议入台账落盘(~/.karvyloop/conflict_resolutions.json,跨重启可查)
    ②落 Trace(kind=conflict_resolution,评价/周报数据源)③回执如实交代边界。
    """
    def handler(proposal) -> Tuple[bool, str]:
        import json as _json
        import time as _time
        from pathlib import Path as _P
        st = getattr(app, "state", None)   # app=None(测试)也不炸,走默认路径
        payload = getattr(proposal, "payload", None) or {}
        skill = (payload.get("skill_name") or "").strip() or "?"
        domain_id = (payload.get("domain_id") or "").strip() or "?"
        rule = (payload.get("rule") or "").strip()
        entry = {
            "ts": _time.time(),
            "proposal_id": getattr(proposal, "proposal_id", ""),
            "role": payload.get("role", ""), "domain_id": domain_id,
            "skill_name": skill, "skill_sig": payload.get("skill_sig", ""),
            "rule_type": payload.get("rule_type", ""), "rule": rule,
            "reason": payload.get("reason", ""), "decision": "ACCEPT",
        }
        # ① 决议台账(跨重启的处置记录;路径可注入供测试)
        try:
            ledger = getattr(st, "conflict_resolutions_path", None) \
                or (_P.home() / ".karvyloop" / "conflict_resolutions.json")
            ledger = _P(ledger)
            ledger.parent.mkdir(parents=True, exist_ok=True)
            existing = []
            if ledger.exists():
                try:
                    existing = _json.loads(ledger.read_text(encoding="utf-8")) or []
                except Exception:
                    existing = []
            existing.append(entry)
            ledger.write_text(_json.dumps(existing, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        except Exception as e:
            logger.warning(f"[resolve_conflict] 决议台账落盘失败: {e}")
            return False, f"处置记录落盘失败:{e}"
        # ② Trace 审计(所有评价的唯一数据源;无 trace 时不阻断,台账已落)
        try:
            trace = getattr(getattr(st, "main_loop", None), "trace", None)
            if trace is not None:
                from karvyloop.cognition.trace import TraceEntry
                trace.append(TraceEntry(
                    task_id=entry["proposal_id"], kind="conflict_resolution",
                    payload=entry, agent=entry["role"], source="resolve_conflict"))
        except Exception as e:
            logger.warning(f"[resolve_conflict] 落 Trace 失败(台账已落,不阻断): {e}")
        return True, (f"已记录处置:技能「{skill}」与域「{domain_id}」的规则冲突(决议入台账+运行记录)。"
                      f"域内自动禁用还未接线 —— 在该域用到此技能时请自行留意,或直接删改该技能")
    return handler


def _weekly_digest_handler(proposal) -> Tuple[bool, str]:
    """周报卡 ACCEPT = 收下归档(卡本身就是"已读即价值";此前无 handler → ACCEPT 回执是
    内部错误串 no handler,前端 i18n 却承诺"接受=归档" —— 对齐承诺,一行知悉回执)。"""
    return True, "已归档本周周报 —— 数字都来自你真实的运行记录"


def _fs_access_handler(proposal: Any) -> Tuple[bool, str]:
    """KIND_FS_ACCESS 兑现:你 ACCEPT 了授权卡 → 授权台账落一条(能力总览可见、可撤)。

    敏感路径双保险:note_denied 出卡前滤过一次,store.record 再拒一次(硬地板)。"""
    from karvyloop.capability.fs_grants import get_store
    payload = getattr(proposal, "payload", {}) or {}
    path = (payload.get("path") or "").strip()
    ops = payload.get("ops") or ["read"]
    role = payload.get("role") or ""
    st = get_store()
    if st is None:
        return False, "授权台账未接(fs_grants store 未注册)"
    if not path:
        return False, "缺 path"
    g = st.record(path, ops, role=role, origin="h2a")
    if g is None:
        return False, f"敏感路径,硬地板拒绝(不该出这张卡):{path}"
    return True, f"已放行 {path}({'/'.join(g['ops'])});能力总览可撤"


def _silence_grant_handler(app: Any) -> Callable[[object], Tuple[bool, str]]:
    """KIND_SILENCE_GRANT 兑现(「挣来的静音」,docs/49 机制2 / docs/50 决定1)。

    ACCEPT = 授权该桶(kind+可选 domain)静音处理 30 天(payload.renew=True 即续期,
    同一 handler:重新 grant 就是刷新 granted_at/expires_at)→ 落
    ~/.karvyloop/silence_grants.json(可撤销;押错一次自动吊销;到期回逐张问人)。
    高危 kind / 不可逆语义双保险:出卡前滤过一次,store.grant 再拒一次(硬地板 ——
    卡被伪造也授不出权)。
    """
    def handler(proposal) -> Tuple[bool, str]:
        from karvyloop.karvy.silence import (
            SILENCE_MIN_CONFIDENCE, get_store,
        )
        payload = getattr(proposal, "payload", None) or {}
        kind = (payload.get("kind") or "").strip()
        domain = (payload.get("domain") or "").strip()
        if not kind:
            return False, "缺 kind —— 无法授权"
        try:
            n = int(payload.get("n") or 0)
            hits = int(payload.get("hits") or 0)
        except (TypeError, ValueError):
            n = hits = 0
        g = get_store(app).grant(kind, domain, n=n, hits=hits)
        if g is None:
            return False, f"高危/不可逆类型「{kind}」不允许静音授权(硬地板,不该出这张卡)"
        scope = f"(域「{domain}」)" if domain else ""
        verb = "已续期" if payload.get("renew") else "已授权"
        return True, (f"{verb}:「{kind}」{scope}这类卡 30 天内我按你的口味先办 —— 只办我押"
                      f"你会 ACCEPT 且把握 ≥{int(SILENCE_MIN_CONFIDENCE * 100)}% 的;不定期"
                      f"抽一部分照常问你对答案;每次完整留痕、满 30 天要你亲手续期;"
                      f"我押错一次立即自动收回,你随时可撤")
    return handler


def _silence_revoked_handler(proposal) -> Tuple[bool, str]:
    """KIND_SILENCE_REVOKED 兑现:纯知悉卡(授权已在押错/翻案时**自动**吊销,不在此执行)。"""
    payload = getattr(proposal, "payload", None) or {}
    kind = (payload.get("kind") or "").strip() or "该类"
    return True, f"已知悉 —— 「{kind}」这类卡已恢复逐张问你;要再静音得重新挣(攒新鲜命中)"


def build_proposal_handlers(app: Any) -> Dict[str, Callable[[object], Tuple[bool, str]]]:
    """构造 ACCEPT 兑现 handler 表(注入 app.state.proposal_handlers)。

    只放有真实目的地的 kind;其余靠 registry 默认诚实回执("no handler")。
    """
    # revise_skill(技能大改 H2A 卡):ACCEPT → apply_revision_proposal 落 SKILL.md +
    # Changelog + Trace 审计(「改了再批」生效:落的是 ACCEPT 时 payload 里的 new_steps)。
    # trace 从 main_loop 取(--no-llm / 测试 app=None 时为 None → 只落盘不写审计,handler 自身兜底)。
    _ml = getattr(getattr(app, "state", None), "main_loop", None) if app is not None else None
    # cocreate_finalize(共创定稿卡):ACCEPT → 真建域+角色(模板 instantiate / 自建 finalize)
    from karvyloop.karvy.cocreation import KIND_COCREATE_FINALIZE, make_cocreate_finalize_handler
    # resident_referral(原住民引荐卡,docs/60):ACCEPT → 真入住(RoleRegistry.create 契约
    # seed + fs_grants 目录白名单);REJECT 后由 residents 状态文件保证永不纠缠。
    from karvyloop.karvy.residents import KIND_RESIDENT_REFERRAL, make_resident_referral_handler
    # butler_plan(文件管家第一课方案卡):ACCEPT → 按卡上方案真执行(只 move,绝不删/覆盖,
    # 全量台账可逆)。这里注册保证**重启后恢复的待决卡**也能兑现(路由层 setdefault 只兜运行时)。
    from karvyloop.karvy.butler_lesson import KIND_BUTLER_PLAN, make_butler_plan_handler
    from karvyloop.karvy.silence import KIND_SILENCE_GRANT, KIND_SILENCE_REVOKED
    # inbox_pipe(收件箱→决策卡管道):inbox_decision(需拍板,记台账)/ inbox_reply(代拟草稿,存台账+
    # 显示,不代发)。handler 结构上只写本地台账,零外部副作用(未经确认绝不外发是硬规矩)。
    from karvyloop.channels.inbox_pipe import make_inbox_handlers
    _inbox = make_inbox_handlers()
    return {
        KIND_COCREATE_FINALIZE: make_cocreate_finalize_handler(app),
        KIND_RESIDENT_REFERRAL: make_resident_referral_handler(app),
        KIND_BUTLER_PLAN: make_butler_plan_handler(app),
        KIND_REVISE_SKILL: partial(apply_revision_proposal, trace=getattr(_ml, "trace", None)),
        KIND_CRYSTALLIZE_SKILL: _crystallize_skill_handler,
        KIND_ROUTE_TO_ROLE: _route_to_role_handler(app),
        KIND_ROUNDTABLE: _roundtable_handler(app),
        KIND_RUN_TASK: _run_task_handler(app),
        KIND_CONFIRM_DECISION_PREF: _confirm_decision_pref_handler(app),
        KIND_OPS_FIX: _ops_fix_handler,
        KIND_INFEASIBLE_REPORT: _infeasible_report_handler,
        KIND_MERGE_ATOMS: _merge_atoms_handler(app),
        KIND_CONFIRM_RESULT: _confirm_result_handler(app),
        KIND_MERGE_KNOWLEDGE: _merge_knowledge_handler(app),
        KIND_FS_ACCESS: _fs_access_handler,
        KIND_RESOLVE_CONFLICT: _resolve_conflict_handler(app),   # 断③:处置决议真落地(台账+Trace)
        KIND_WEEKLY_DIGEST: _weekly_digest_handler,              # ACCEPT=归档(对齐前端承诺)
        KIND_SILENCE_GRANT: _silence_grant_handler(app),         # 挣来的静音:授权落台账(可撤)
        KIND_SILENCE_REVOKED: _silence_revoked_handler,          # 吊销告知:纯知悉
        **_inbox,                                                # 收件箱管道:记台账/存草稿(只进不出)
    }


__all__ = ["build_proposal_handlers"]
