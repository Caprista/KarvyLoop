"""task_monitor — 本机执行任务监控(docs/80 #4 持久执行 loop · 第一环:本机层)。

Hardy 三环恢复架构的第一环(本机层,不依赖 mesh/外部层,独立落地):
**一个任务标着 running 却长时间没进展 = 疑似中断**(进程还活着但那条 drive 协程卡死/静默崩),
监控**持续**(不只重启那一刻)把它揪出来 → 老实标中断 + 加 blocked 事件让它实时可见 →
升一张"要我接着跑吗"H2A 卡请人拍板。**不再有本机任务悄悄中断没人管**(§0.7 反模式的收口)。

**判定用"陈旧度",不探协程**:
- Trace 的 atom_run 会被容量环淘汰(DROPPABLE_KINDS),但 task_run 终结事件永不淘 —— 所以"完没完成"
  靠终结,不靠会被淘汰的进度细节;
- 系统没有"在跑 drive 协程登记表",探不了"协程活没活" → 用**离最后一条事件多久**判定:
  一条真在推进的 drive 会规律吐 step 事件,长时间(> STALE_THRESHOLD_S)一条不吐 = 停滞。

**续跑 = 从头重跑**(Hardy 拍:轻、快、诚实;断点续跑是独立深水区,仅 workflow 有先例,留后续)。

**边界(诚实)**:本环只覆盖"console 进程活着时"的持续监控;重启那一刻的中断标注 tasks.py 已做。
真·关机跨天离线追赶(第一环之外)+ mesh 跨设备探活(第二环)+ 跨 runtime 重连调度(第三环)留后续。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 陈旧阈值:标 running 但离最后一条事件超过它 = 疑似中断。**保守**(> 单次正常 drive 时长,
# 含慢真模型调用),宁可晚报不误杀活着的慢任务(误杀 → 重复弹卡/双跑风险)。
# 〔待 Trace 标定〕30min 是首版拍脑袋;真数据(真实任务事件间隔分布)落了再标定,别硬编当定律。
STALE_THRESHOLD_S = 30 * 60


def _last_progress_ts(t: dict) -> float:
    """这条任务最后一次"有动静"的时刻 = max(started, 最新事件 ts)。事件坏/缺 → 退 started。"""
    ts = float(t.get("started") or 0.0)
    ev = t.get("last_event")
    if isinstance(ev, dict):
        try:
            ts = max(ts, float(ev.get("ts") or 0.0))
        except (TypeError, ValueError):
            pass
    return ts


def detect_stalled(tasks: list, *, now: float, threshold: float = STALE_THRESHOLD_S) -> list:
    """从任务看板挑出"标 running 但陈旧(> threshold 无进展)"的任务 dict。纯函数、可测。

    只看 running:done/error 已终结,不管;started 缺失(=0)会被 now-0>threshold 命中,
    但正常任务 start 即写 started,不会 0 —— 坏数据落这也无害(标中断请人拍板)。
    """
    out = []
    for t in tasks or []:
        if t.get("status") != "running":
            continue
        if now - _last_progress_ts(t) > threshold:
            out.append(t)
    return out


async def run_task_monitor(app: Any, *, now: Optional[float] = None,
                           threshold: float = STALE_THRESHOLD_S) -> int:
    """一次监控 tick:揪停滞任务 → 标中断(加 blocked 事件)→ 升"要我接着跑吗"卡。返回处理条数。

    幂等:每条停滞任务只处理一次(app.state._stalled_seen 记 task_id),下轮不重弹。
    挂进慢侧维护 loop 的短子 tick(每 _ACTIVE_TICK_S 醒一次都跑,轻量、无 LLM)。
    """
    reg = getattr(app.state, "task_registry", None)
    if reg is None:
        return 0
    now = time.time() if now is None else now
    try:
        tasks = reg.list()
    except Exception:
        return 0
    seen = getattr(app.state, "_stalled_seen", None)
    if seen is None:
        seen = set()
        app.state._stalled_seen = seen

    from karvyloop.console.proposals import broadcast_proposal
    from karvyloop.karvy.proactive import resume_proposal_for

    handled = 0
    for t in detect_stalled(tasks, now=now, threshold=threshold):
        tid = str(t.get("id") or "")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        # ① 实时可见:加 blocked 事件(复用对抗验收过的 blocked 推送,看板卡即刻显"疑似中断")。
        try:
            reg.add_event(tid, "blocked", "⚠ 疑似中断:长时间无进展")
        except Exception:
            pass
        # ② 老实标中断(同重启那套诚实标注):status→error,不假装还在跑。
        try:
            reg.finish(tid, error="疑似中断:长时间无进展,已停止推进")
        except Exception:
            pass
        # ③ 升"要我接着跑吗"卡(人拍板,K5;续跑=从头重跑)。带来源标便于区分开机兜底 vs 持续监控。
        t2 = dict(t)
        t2["_resume_source"] = "task_monitor.stalled"
        t2["status"] = "error"
        prop = resume_proposal_for(t2, now=now)
        if prop is not None:
            try:
                await broadcast_proposal(app, prop)
                handled += 1
            except Exception as e:
                logger.debug(f"[task_monitor] 升停滞卡失败(下轮再来): {e}")
    return handled


__all__ = ["STALE_THRESHOLD_S", "detect_stalled", "run_task_monitor"]
