"""proposal_handlers — PROPOSE ACCEPT 的真兑现 handler(接 D5 live,M3+ 拍 9.4-门2)。

设计:docs/30 §3 kind→兑现 + §5.1。registry.decide(ACCEPT) 按 kind 查这里的 handler。

诚实原则(不为对称假兑现):**只接有真实目的地的 kind**。
- `crystallize_skill`(IntentAnalyst 从习惯凝):正确兑现 = 采纳确认(结晶是 usage-driven,
  9.4 签名修复后可靠,不在此强行结晶绕过门槛)。
- `route_to_role`(9.4-门2 执行-role 流):ACCEPT → 让目标业务 role 在其域 value.md 治理下
  执行需求(in-process drive;P1 走真 A2A envelope/inbox)。
- `run_task` / `set_preference` / `resolve_conflict` 处置:兑现子系统未建 → 不注册,
  registry 默认诚实"no handler"回执,不假装(docs/30 §5.1)。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Tuple

from karvyloop.karvy.proposal_registry import (
    KIND_CONFIRM_DECISION_PREF, KIND_CRYSTALLIZE_SKILL, KIND_OPS_FIX,
    KIND_ROUNDTABLE, KIND_ROUTE_TO_ROLE, KIND_RUN_TASK,
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
            from karvyloop.cli.main_loop import forge_slow_brain_factory
            from karvyloop.coding.checker import verify_and_fix_with_rk, verdict_suffix
            gov = _governance_for(app, payload)
            slow_brain = forge_slow_brain_factory(governance=gov, **rk)
            # loop step3:业务委派执行后过一道独立验收(在该域治理下产出 → 独立 checker 核验)。
            checked = verify_and_fix_with_rk(requirement, ml=ml, slow_brain=slow_brain, rk=rk)
            result = checked.result
        except Exception as e:
            logger.warning(f"[route_to_role] 执行失败: {e}")
            return False, f"委派执行失败: {e}"
        if getattr(result, "error", ""):
            return False, f"「{role}」执行出错: {result.error}"
        txt = (getattr(result, "text", "") or "").strip().replace("\n", " ")
        if len(txt) > 140:
            txt = txt[:140] + "…"
        suffix = verdict_suffix(checked)
        _stash_report_card(app, proposal, checked,
                           problem=requirement, approach=f"由「{role}」在域治理下执行")
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
            from karvyloop.cli.main_loop import forge_slow_brain_factory
            did = payload.get("domain_id", "l0")
            gov = _governance_for(app, payload) if did != "l0" else ""
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
            from karvyloop.coding.checker import verify_and_fix_with_rk, verdict_suffix
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


def build_proposal_handlers(app: Any) -> Dict[str, Callable[[object], Tuple[bool, str]]]:
    """构造 ACCEPT 兑现 handler 表(注入 app.state.proposal_handlers)。

    只放有真实目的地的 kind;其余靠 registry 默认诚实回执("no handler")。
    """
    return {
        KIND_CRYSTALLIZE_SKILL: _crystallize_skill_handler,
        KIND_ROUTE_TO_ROLE: _route_to_role_handler(app),
        KIND_ROUNDTABLE: _roundtable_handler(app),
        KIND_RUN_TASK: _run_task_handler(app),
        KIND_CONFIRM_DECISION_PREF: _confirm_decision_pref_handler(app),
        KIND_OPS_FIX: _ops_fix_handler,
    }


__all__ = ["build_proposal_handlers"]
