"""schedule_suggest — docs/90 刀3c「时机能力提示」的触发编排(防 Alexa 坑:藏起来≠教会)。

用户**手动**把同一类事跑到第 N 次(默认 2)→ 小卡递一张低调的建议卡:
「你已手动跑了 N 次『X』—— 要每周自动跑吗?」。核心是**不骚扰**:三道门缺一不递。

三道门(缺一会骚扰,顺序即优先):
  ③ 达 N 门(且新鲜):计数达阈值才考虑;**只在"用户刚跑完这第 N 次"那刻判一次**
     (事件驱动,由 `schedule_suggest_after_drive` 在 drive 成功点旁路触发,绝不轮询乱弹)。
  ① already_suggested 门:这条指纹提过就永不再提(接受/拒绝/忽略都算提过)。
  ② 已有定时任务覆盖门:SchedulerStore 里已有 intent 同指纹的定时任务 → 不建议(别劝你
     自动化一件已经在自动跑的事)。

接受(前端 renderPredict 特判):**不**直接建定时任务 —— 预填聊天/开面板带上这条 intent,
让用户补"多久一次/几点"再走既有 create_schedule(NL→cron);handler 只回诚实回执(无副作用)。
忽略:already_suggested 已在出卡时置真,静默收起,永不再提。

纪律:**宁可不提也别乱提**;计数只认手动成功运行(见 manual_run_counter);零新 LLM 检测
(指纹是确定性的);全程 fail-soft —— 任何异常都吞掉,绝不冒泡到 drive 路径。
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 默认阈值:手动跑到第 N 次才递建议(docs/90 刀3c 默认 N=2)。
DEFAULT_SUGGEST_N = 2


def _counter_store(app: Any):
    """app.state 上懒加载手动运行计数器(与 schedules.json 同家 ~/.karvyloop/)。"""
    st = getattr(app.state, "manual_run_counter", None)
    if st is not None:
        return st
    from karvyloop.karvy.manual_run_counter import ManualRunCounter
    cfgp = getattr(app.state, "config_path", "") or ""
    path = (pathlib.Path(cfgp).parent / "manual_run_counts.json") if cfgp else None
    st = ManualRunCounter(path)
    app.state.manual_run_counter = st
    return st


def _suggest_n(app: Any) -> int:
    """阈值 N(默认 2;测试/配置可通过 app.state.schedule_suggest_n 覆盖)。"""
    try:
        n = int(getattr(app.state, "schedule_suggest_n", DEFAULT_SUGGEST_N))
        return n if n >= 1 else DEFAULT_SUGGEST_N
    except (TypeError, ValueError):
        return DEFAULT_SUGGEST_N


def _covered_by_existing_schedule(app: Any, fingerprint: str) -> Optional[bool]:
    """门②:已有 intent 同指纹的定时任务?返回 True=有覆盖(不提)/ False=无 / None=读不到。

    比对用**同一个** intent 指纹(两侧都过 crystallize `_intent_cluster` 归一,口径不漂移)。
    读不到 scheduler_store → None(调用方保守:读不到就别提,宁可少提)。"""
    try:
        from karvyloop.console.routes_schedules import _scheduler_store
        from karvyloop.karvy.ambient import intent_fingerprint
        st = _scheduler_store(app)
        for t in st.all():
            if intent_fingerprint(getattr(t, "intent", "") or "") == fingerprint:
                return True
        return False
    except Exception as e:
        logger.debug("[schedule_suggest] 读定时任务表失败(保守不提): %s", e)
        return None


def _already_pending(app: Any, proposal_id: str) -> Optional[bool]:
    """同一条建议卡是否已挂在待决表(防重弹;already_suggested 之外的第二保险)。
    读不到 registry → None(保守不提)。"""
    reg = getattr(app.state, "proposal_registry", None)
    if reg is None:
        return False
    try:
        return reg.get(proposal_id) is not None
    except Exception:
        return None


async def maybe_suggest_schedule(app: Any, intent: str, *,
                                 now: Optional[float] = None) -> Optional[Any]:
    """在"用户刚手动成功跑完一次"那刻调:bump 计数 → 三道门全过才递 schedule_suggest 卡。

    返回递出的卡(或 None=没递)。**任何异常都吞掉返回 None**(旁路纪律,绝不影响 drive)。
    """
    try:
        text = (intent or "").strip()
        if not text:
            return None
        counter = _counter_store(app)
        entry = counter.bump(text, now=now)   # 计数:只在此手动成功点 +1(失败/系统发起不到这)
        if not entry:
            return None   # 计数坏了 → 少提一次,不崩
        fp = entry.get("fingerprint") or counter.fingerprint(text)
        # 门③(达 N + 新鲜):没到第 N 次 → 静默(本函数只在"刚跑完"那刻调,天然新鲜、不轮询)
        if int(entry.get("count") or 0) < _suggest_n(app):
            return None
        # 门①(already_suggested):提过就永不再提(接受/拒绝/忽略都算提过)
        if entry.get("already_suggested"):
            return None
        # 门②(已有定时任务覆盖):已在自动跑的事不劝你自动化;读不到也保守不提
        if _covered_by_existing_schedule(app, fp) is not False:
            return None
        # 建卡(proposal_id 按 fingerprint 稳定派生;下面用它做"已挂待决"检查,不另算 id)
        n_ts = time.time() if now is None else float(now)
        from karvyloop.karvy.proposal_registry import proposal_for_schedule_suggest
        card = proposal_for_schedule_suggest(
            intent=text, count=int(entry.get("count") or 0), fingerprint=fp, ts=n_ts)
        # 第二保险:同一条卡已挂待决 → 不重弹(读不到也保守不提)
        if _already_pending(app, card.proposal_id) is not False:
            counter.mark_suggested(fp, now=n_ts)   # 挂着=已提过,顺手落 already_suggested
            return None
        # 全过 → **先**落 already_suggested(宁可少提也别乱提:哪怕广播失败也不再提),再出卡
        counter.mark_suggested(fp, now=n_ts)
        from karvyloop.console.proposals import broadcast_proposal
        # allow_silence 默认 True,但 schedule_suggest ∈ taste_eval.SKIP_KINDS → try_silence 直接
        # 放行不接管(永远要人点);走正门只为 register + 广播进预判象限。
        await broadcast_proposal(app, card)
        logger.info("[schedule_suggest] 手动第 %s 次『%s』→ 递每周自动跑建议卡",
                    entry.get("count"), text[:40])
        return card
    except Exception as e:  # noqa: BLE001 —— 旁路绝不外溢到 drive
        logger.debug("[schedule_suggest] 触发失败(忽略,不影响 drive): %s", e)
        return None


def schedule_suggest_after_drive(app: Any, intent: str, *, error: str = "") -> None:
    """drive 成功收尾旁路触发(fire-and-forget,镜像 ws._schedule_ambient_recall 模式)。

    - **只认成功**:error 非空(失败)→ 直接返回,不计数不建议(失败有 run_task 重试卡管)。
    - **不阻塞热路径**:存进 app.state._schedule_suggest_tasks 强引用 + done-callback 取回异常
      (否则 CPython 弱引用 task 可能被 GC 中途回收吞异常);无事件循环 → 静默跳过。
    - 任何失败只 debug/warning,**绝不**冒泡到 drive。
    """
    if (error or "").strip():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    tasks = getattr(app.state, "_schedule_suggest_tasks", None)
    if tasks is None:
        tasks = app.state._schedule_suggest_tasks = set()
    task = loop.create_task(maybe_suggest_schedule(app, intent))
    tasks.add(task)

    def _done(t) -> None:
        tasks.discard(t)
        try:
            exc = t.exception()
        except BaseException:   # CancelledError 等(关停)
            return
        if exc is not None:
            logger.warning("[schedule_suggest] 后台任务异常(少一次能力提示,不致命): %s", exc)

    task.add_done_callback(_done)


__all__ = ["maybe_suggest_schedule", "schedule_suggest_after_drive", "DEFAULT_SUGGEST_N"]
