"""scene_signals — 「猜你想干 v2」刀1:当下场景信号采集(docs/94 刀1)。

世界雷达证实**纯习惯统计触发是死路**(采样稀 + 冷启动空转 + 骚扰),活路是**当下场景**:
"因为你刚跑完 X / X 快到点了"这类**此刻就成立**的确定性判据。本模块只做信号采集:
**纯读已有数据源**(任务看板 TaskRegistry / 定时任务 SchedulerStore),不加任何采集面,
**全程零 LLM**(刀1 铁律;LLM 的 relevance 判断是刀2)。出不出卡由 scene_gate
(统一日预算 / 指纹冷却 / REJECT 负反馈)裁,本模块不广播、不写状态。

信号判据表(每个信号 = 确定性判据 + 人话依据 + 复用既有 proposal kind):
- task_failed   刚失败:任务板最近 RECENT_FAILURE_WINDOW_S 内 status=error 的任务
  → **复用既有 run_task 重试卡**(proactive.resume_proposal_for,kind/文案一字不变;
  源A propose_from_tasks 在开机兜底做同一件事 —— 不重复:两条路共用同一 fingerprint
  ``task_failed:<task_id>``,谁先出谁记账,另一条永不再出)。
- schedule_due  日程将至:SchedulerStore 里 SCHEDULE_DUE_WINDOW_S(T-15min)内将触发、
  且**上次跑失败过**(last_status=error)的定时任务 → 「X 快到点了,上次失败了 ——
  要我先试跑一遍吗?」(kind 复用 run_task,ACCEPT=立刻按原意图先跑一次,定时任务不动)。
  第一版只做"上次失败过"这一种(确定性最强),不泛化。
- manual_repeat 手动重复:刀3c 的 manual_run_counter / schedule_suggest 已有,**不在这采集**
  (三门逻辑不动),只把它的出卡纳入 scene_gate 统一记账(见 console/schedule_suggest.py)。
- big_job_done  刚完成大活:**不独立出卡、不新造建议类型** —— 只在定时建议
  (schedule_suggest)本来就会出的时刻,由 recent_big_job_basis() 给 basis 加
  「你刚跑完 X(用时约 N 分钟)」的场景前缀(既有判据 + 场景依据文案)。
"""
from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any, Optional

from .atoms import Proposal
from .proposal_registry import KIND_RUN_TASK

logger = logging.getLogger(__name__)

# ---- 场景 kind 常量(scene_gate 冷却 / REJECT 负反馈按它分桶;wire 上落在 payload.scene_kind)----
SCENE_TASK_FAILED = "task_failed"
SCENE_SCHEDULE_DUE = "schedule_due"
SCENE_MANUAL_REPEAT = "manual_repeat"   # 刀3c schedule_suggest 的记账归类(采集不在本模块)
SCENE_BIG_JOB_DONE = "big_job_done"     # 只做 basis 前缀,不独立出卡

# ---- 确定性判据阈值(〔待 Trace 标定〕首版拍脑袋值;总表见 scene_gate.py 头注,
# docs/94 刀3 ④:全部可经 app.state 覆盖,collect_scene_signals 统一读)----
# 刚失败:只看最近这段时间内失败的任务(太老的失败不是"当下场景";开机兜底那条路
# propose_from_tasks 不设窗,历史失败由它管,两路共用指纹不重复)。
# 可配 app.state.scene_recent_failure_window_s。
RECENT_FAILURE_WINDOW_S = 30 * 60
# 日程将至:T-15min 内将触发(可配 app.state.scene_schedule_due_window_s)。
SCHEDULE_DUE_WINDOW_S = 15 * 60
# 刚完成大活:drive 任务耗时超过它才算"大活"(可配 app.state.scene_big_job_s)。
BIG_JOB_THRESHOLD_S = 120.0
# 刚完成大活:完成时刻离现在不超过它才算"刚"(schedule_suggest 在 drive 收尾旁路触发,
# 正常在几秒内;放宽到 10min 容忍慢广播/重试)。
BIG_JOB_RECENT_S = 10 * 60


@dataclasses.dataclass(frozen=True)
class SceneSignal:
    """一条当下场景信号:scene_kind(冷却/负反馈分桶)+ fingerprint(去重冷却键,
    同指纹出过永不再出)+ proposal(复用既有 kind 的现成卡,basis 即人话依据)。"""
    scene_kind: str
    fingerprint: str
    proposal: Proposal


def task_failed_fingerprint(task_id: str) -> str:
    """「刚失败」信号的去重指纹 —— 开机兜底(proactive_from_state)与场景 tick 两条路
    **必须共用**这一个派生,才保证同一条失败任务全系统只出一张卡。"""
    return f"{SCENE_TASK_FAILED}:{task_id}"


# ---------------------------------------------------------------- 信号1:刚失败
def recent_failed_task_signals(task_registry: Any, *, now: Optional[float] = None,
                               window_s: float = RECENT_FAILURE_WINDOW_S) -> list:
    """任务板最近 window_s 内 status=error 的任务 → run_task 重试卡信号(卡=既有文案)。

    纯读、零 LLM、任何异常返 [](宁空勿毒)。"""
    if task_registry is None:
        return []
    now = time.time() if now is None else float(now)
    out: list = []
    try:
        from .proactive import is_user_task, resume_proposal_for
        for t in task_registry.list():   # newest-first 的 dict 列表
            if t.get("status") != "error":
                continue
            # docs/94 刀2 A4:只收**用户语义**任务(USER_TASK_KINDS 白名单;""=老记录)。
            # 机器内部预执行(kind=scene_preexec)失败=静默弃,绝不当新场景信号自我繁殖
            # (新 task_id=新指纹会绕过 attempted/指纹门);pursuit 失败有自己的挂起/修订卡
            # 回路(pursuit_tick),别双弹。
            if not is_user_task(t):
                continue
            try:
                finished = float(t.get("finished") or 0.0)
            except (TypeError, ValueError):
                continue
            if finished <= 0 or now - finished > window_s:
                continue
            tid = str(t.get("id") or "")
            if not tid:
                continue
            t2 = dict(t)
            t2["_resume_source"] = "scene.recent_failure"   # 来源标区分(卡形态同源A)
            p = resume_proposal_for(t2, now=now)
            if p is None:   # intent 空等 → 既有判定说不出卡
                continue
            out.append(SceneSignal(scene_kind=SCENE_TASK_FAILED,
                                   fingerprint=task_failed_fingerprint(tid), proposal=p))
    except Exception as e:  # noqa: BLE001 —— 旁路纪律:信号采集绝不外溢
        logger.debug("[scene_signals] 刚失败信号采集失败(忽略): %s", e)
        return []
    return out


# ---------------------------------------------------------------- 信号2:日程将至
def upcoming_schedule_signals(scheduler_store: Any, *, now: Optional[float] = None,
                              window_s: float = SCHEDULE_DUE_WINDOW_S) -> list:
    """T-window 内将触发、且上次失败过的定时任务 → 「快到点了,要我先试跑一遍吗」信号。

    - 只做「上次这个定时任务失败过 → 提前提醒」这一种(第一版,确定性最强)。
    - kind 复用 run_task:ACCEPT = 立刻按原意图先跑一次(既有 handler),定时任务本身不动。
    - fingerprint 带本场触发时刻 → 每一场至多提醒一次,窗口内多次 tick 不重复。
    纯读、零 LLM、任何异常返 []。"""
    if scheduler_store is None:
        return []
    now = time.time() if now is None else float(now)
    out: list = []
    try:
        from karvyloop import i18n

        from .scheduler import next_run_after
        for t in scheduler_store.all():
            if not getattr(t, "enabled", False):
                continue
            if (getattr(t, "last_status", "") or "") != "error":
                continue   # 第一版判据:上次失败过才提前提醒
            intent = (getattr(t, "intent", "") or "").strip()
            if not intent:
                continue
            nxt = next_run_after(getattr(t, "cron", "") or "", now)
            if nxt is None or nxt - now > window_s:
                continue
            title = (getattr(t, "title", "") or intent).strip() or intent
            if len(title) > 40:
                title = title[:40] + "…"
            err = (getattr(t, "last_error", "") or "").strip().replace("\n", " ")
            if len(err) > 80:
                err = err[:80] + "…"
            if not err:
                err = i18n.t("proposal.run_task.default_error")
            mins = max(1, int((nxt - now) // 60))
            sid = str(getattr(t, "id", "") or "")
            p = Proposal(
                summary=i18n.t("proposal.scene_schedule_due.summary", title=title, mins=mins),
                options=("ACCEPT", "DEFER", "REJECT"),
                strength=0.6,
                evidence_refs=(),
                habit_id=0,
                model_ref="",
                ts=now,
                kind=KIND_RUN_TASK,   # 复用既有重跑 handler(不新造建议类型)
                payload={
                    "intent": intent,
                    "domain_id": getattr(t, "target_domain", "") or "l0",
                    "role": getattr(t, "target_role", "") or "",
                    "source": "scene.schedule_due",
                    "schedule_id": sid,
                },
                proposal_id=f"scene-schedule_due-{sid}-{int(nxt)}",
                basis=i18n.t("proposal.scene_schedule_due.basis",
                             title=title, mins=mins, err=err),
                context_ref={"kind": "schedule", "id": sid},
            )
            # 指纹粒度 = 同 schedule_id **每日**至多一条(不是每场):高频失败定时任务
            # (如 */5 一直挂)每场都是新 next_run_ts,会日日吃满整份日预算 —— Hardy 拍收紧。
            # REJECT 的 7 天负反馈仍在其上兜底。
            _day = time.strftime("%Y-%m-%d", time.localtime(now))
            out.append(SceneSignal(scene_kind=SCENE_SCHEDULE_DUE,
                                   fingerprint=f"{SCENE_SCHEDULE_DUE}:{sid}:{_day}",
                                   proposal=p))
    except Exception as e:  # noqa: BLE001
        logger.debug("[scene_signals] 日程将至信号采集失败(忽略): %s", e)
        return []
    return out


# ---------------------------------------------------------------- 汇总采集(scene_tick 用)
def _window_override(app: Any, attr: str, default: float) -> float:
    """docs/94 刀3 ④:判据窗口的 app.state 覆盖(模式照 scene_big_job_s:读不到/坏值/
    非正数退默认;不改默认值,内测真数据来了照 scene_gate.py 头注待标定清单表标定)。"""
    try:
        v = float(getattr(app.state, attr, default))
        return v if v > 0 else float(default)
    except (TypeError, ValueError):
        return float(default)


def collect_scene_signals(app: Any, *, now: Optional[float] = None) -> list:
    """从既有数据源采集全部当下场景信号(信号1+2;信号3 在 schedule_suggest 侧记账,
    信号4 只做 basis 前缀)。每类各自兜异常,坏一类不连坐。"""
    now = time.time() if now is None else float(now)
    out: list = []
    try:
        out.extend(recent_failed_task_signals(
            getattr(app.state, "task_registry", None), now=now,
            window_s=_window_override(app, "scene_recent_failure_window_s",
                                      RECENT_FAILURE_WINDOW_S)))
    except Exception:
        pass
    try:
        store = getattr(app.state, "scheduler_store", None)
        if store is None:
            # 生产同款懒加载(schedules.json 同家);失败当无(保守不提)
            from karvyloop.console.routes_schedules import _scheduler_store
            store = _scheduler_store(app)
        out.extend(upcoming_schedule_signals(
            store, now=now,
            window_s=_window_override(app, "scene_schedule_due_window_s",
                                      SCHEDULE_DUE_WINDOW_S)))
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- 信号4:刚完成大活(只产 basis 前缀)
def recent_big_job_basis(app: Any, intent: str, *, now: Optional[float] = None) -> str:
    """这条 intent 是否"刚跑完一票大活"(kind=drive、done、耗时>阈值、刚结束)——
    是 → 返回场景依据人话(i18n,给本来就会出的建议卡当 basis 前缀);否则返 ""。

    不新造建议类型、不独立出卡;任何异常返 ""(旁路)。"""
    try:
        reg = getattr(app.state, "task_registry", None)
        if reg is None:
            return ""
        text = (intent or "").strip()
        if not text:
            return ""
        now = time.time() if now is None else float(now)
        try:
            threshold = float(getattr(app.state, "scene_big_job_s", BIG_JOB_THRESHOLD_S))
        except (TypeError, ValueError):
            threshold = BIG_JOB_THRESHOLD_S
        for t in reg.list():   # newest-first
            if t.get("status") != "done" or (t.get("kind") or "") != "drive":
                continue
            if (t.get("intent") or "").strip() != text:
                continue
            try:
                started = float(t.get("started") or 0.0)
                finished = float(t.get("finished") or 0.0)
            except (TypeError, ValueError):
                continue
            if finished <= 0 or now - finished > BIG_JOB_RECENT_S:
                continue
            dur = finished - started
            if dur <= threshold:
                continue
            from karvyloop import i18n
            short = text if len(text) <= 40 else text[:40] + "…"
            return i18n.t("scene.big_job.basis_prefix",
                          intent=short, mins=max(1, int(round(dur / 60.0))))
        return ""
    except Exception as e:  # noqa: BLE001
        logger.debug("[scene_signals] 刚完成大活判定失败(忽略): %s", e)
        return ""


__all__ = [
    "SCENE_TASK_FAILED", "SCENE_SCHEDULE_DUE", "SCENE_MANUAL_REPEAT", "SCENE_BIG_JOB_DONE",
    "RECENT_FAILURE_WINDOW_S", "SCHEDULE_DUE_WINDOW_S", "BIG_JOB_THRESHOLD_S", "BIG_JOB_RECENT_S",
    "SceneSignal", "task_failed_fingerprint",
    "recent_failed_task_signals", "upcoming_schedule_signals", "collect_scene_signals",
    "recent_big_job_basis",
]
