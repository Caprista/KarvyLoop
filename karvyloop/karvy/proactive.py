"""proactive — 小卡的"主动观察 + 建议"(loop-step2b)。

病根(审计):小卡从不主动开口——IntentAnalyst.daily_poll 返 None(无 LLM)。
loop 工程的第一块"自运转":观察状态 → 发现该提的事 → 提议(H2A,用户拍板)。

本步用**确定性**的第一个观察源:**持久化的任务看板**(loop-step2a 刚落盘)。
若最近有任务 error/中断没跑完 → 小卡主动提议"要我重试吗?"(run_task)。否则**沉默**
(persona:主动但不打扰)。LLM 式行为模式挖掘(habit)是 M3+,本步先把"会开口"接通。

H2A 守得死:这里只产 Proposal,ACCEPT/REJECT 永远是用户在决策面按的。
"""
from __future__ import annotations

import time
from typing import Optional

from .atoms import Proposal
from .proposal_registry import KIND_RUN_TASK, KIND_SCHEDULE_CATCHUP


def resume_proposal_for(t: dict, *, now: Optional[float] = None) -> Optional[Proposal]:
    """给一条失败/中断的任务 dict 造一张"要我重试吗"H2A 卡(run_task);intent 空 → None。

    两个调用方共用一份卡形态(单一真理源):① propose_from_tasks(开机兜底,扫第一条 error);
    ② task_monitor(持续,监控发现某条 running 陈旧/中断)。source 标区分来源。
    """
    from karvyloop import i18n
    intent = (t.get("intent") or "").strip()
    if not intent:
        return None
    short = intent if len(intent) <= 40 else intent[:40] + "…"
    # ch4 决策依据:说清"何时、谁、发生了什么、为什么提" + 跳转去那条任务看全貌。
    # 卡文案走 i18n(出卡时按当前 locale 定稿);intent/err 是运行数据原样带。
    err = (t.get("result") or t.get("error") or i18n.t("proposal.run_task.default_error")).strip().replace("\n", " ")
    if len(err) > 80:
        err = err[:80] + "…"
    who = t.get("who") or i18n.t("proposal.run_task.default_who")
    basis = i18n.t("proposal.run_task.basis", who=who, intent=short, err=err)
    return Proposal(
        summary=i18n.t("proposal.run_task.summary", intent=short),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.8,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=(now if now is not None else time.time()),
        kind=KIND_RUN_TASK,
        payload={
            "intent": intent,
            "domain_id": t.get("domain_id", "l0"),
            "role": t.get("role", ""),
            "source": str(t.get("_resume_source") or "proactive.resume_failed_task"),
        },
        basis=basis,
        context_ref={"kind": "task", "id": t.get("id", "")},  # 工作台据此跳到那条任务窗
    )


def catchup_proposal_for(t, missed_count: int, latest_missed: Optional[float], *,
                         capped: bool = False, now: Optional[float] = None) -> Optional[Proposal]:
    """给一条离线期间错过场次的定时任务造一张「要补跑一次吗」H2A 卡(骑 run_task)。

    - **聚合**:一个 schedule 一张卡;N 场错过只补跑**一次**(关机三天的 hourly=72 场,
      逐场重放才是错的)。绝不 auto-execute:卡只是问,ACCEPT 才由 schedule_catchup handler
      真跑 intent(handler 内部复用 run_task 重跑逻辑,payload 带 schedule_id → 结果记回看板)。
    - **J5 独立 kind**:kind=KIND_SCHEDULE_CATCHUP(不再骑 run_task)+ 进 silence.HIGH_RISK_KINDS →
      **绝不被"挣来的静音"自动兑现**(骑 run_task 时良性追赶卡会命中 run_task|域 静音授权被自动跑,
      违背"绝不 auto"承诺;独立 kind 硬排除堵死)。
    - **幂等**:proposal_id 按 schedule id 稳定 → 同 schedule 收敛一张卡,不重弹。
    - `t` duck type:需 id/intent/title/target_domain/target_role(karvy.scheduler.ScheduledTask)。
    """
    from karvyloop import i18n
    intent = (getattr(t, "intent", "") or "").strip()
    if not intent or missed_count < 1:
        return None
    title = (getattr(t, "title", "") or intent).strip() or intent
    if len(title) > 40:
        title = title[:40] + "…"
    n_disp = f"{missed_count}+" if capped else str(missed_count)
    when = "-"
    if latest_missed:
        import datetime
        when = datetime.datetime.fromtimestamp(latest_missed).strftime("%Y-%m-%d %H:%M")
    return Proposal(
        summary=i18n.t("proposal.schedule_catchup.summary", title=title, n=n_disp, when=when),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.7,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=(now if now is not None else time.time()),
        kind=KIND_SCHEDULE_CATCHUP,   # J5:独立 kind(不再骑 run_task)→ silence 硬排除,必人拍
        payload={
            "intent": intent,
            "domain_id": getattr(t, "target_domain", "") or "l0",
            "role": getattr(t, "target_role", "") or "",
            "source": "schedule_catchup",
            "schedule_id": getattr(t, "id", ""),
            "missed_count": int(missed_count),
        },
        proposal_id=f"schedule_catchup-{getattr(t, 'id', '')}",
        basis=i18n.t("proposal.schedule_catchup.basis", title=title, n=n_disp),
        context_ref={"kind": "schedule", "id": getattr(t, "id", "")},
    )


def propose_from_tasks(task_registry, *, now: Optional[float] = None) -> Optional[Proposal]:
    """观察任务看板:最近若有失败/中断的任务,提议重试它;否则返 None(沉默)。

    deterministic、可测;不挖 LLM 模式(那是 M3+)。
    """
    if task_registry is None:
        return None
    try:
        tasks = task_registry.list()  # newest-first 的 dict 列表
    except Exception:
        return None
    for t in tasks:
        if t.get("status") == "error":
            p = resume_proposal_for(t, now=now)
            if p is not None:
                return p
    return None


__all__ = ["propose_from_tasks", "resume_proposal_for", "catchup_proposal_for"]
