"""loop_watchdog — 事件循环二层心跳(监督者悖论收口;docs/80 三环之外的地基层)。

task_monitor(第一环)能揪"任务停滞",但它自己活在 console 的 asyncio 事件循环里——
**循环整体被一个同步调用堵死时,看门狗跟着停摆,没人发现**(实例:LLM 同步调用曾冻死
事件循环;跨程对照:等一个永不返回的东西且无兜底 = 一夜无人知)。

这里补第二层:**独立 daemon 线程**周期向事件循环投一个 noop 协程——
- 正常循环毫秒级返回;超时不回 = 循环真被堵死(不是忙,是 hang:asyncio 忙时仍会调度)。
- 报警只能走线程侧(循环死了 WS/事件推不出去):logger.error 大声写 console 终端/日志,
  连续报一次后冷却,恢复时再报一声"已恢复"。同时把状态记在 `last_status`,doctor 可读。
- 保守防误报:连续 2 次超时才算堵死(单次可能是重 GC / 大同步计算的瞬时尖峰)。

零依赖、纯 stdlib;线程是 daemon,不阻进程退出。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

#: 心跳周期(秒)。
PING_INTERVAL_S = 60.0
#: 单次 noop 的响应超时(秒)——正常是毫秒级,10s 不回基本就是堵死了。
PING_TIMEOUT_S = 10.0
#: 连续超时多少次才报堵死(防瞬时尖峰误报)。
STALL_AFTER_MISSES = 2


class LoopWatchdog:
    """独立线程盯 asyncio 事件循环的响应度。start() 后自转;stop() 幂等。"""

    def __init__(self, loop: asyncio.AbstractEventLoop, *,
                 interval_s: float = PING_INTERVAL_S,
                 timeout_s: float = PING_TIMEOUT_S,
                 stall_after: int = STALL_AFTER_MISSES) -> None:
        self._loop = loop
        self._interval = interval_s
        self._timeout = timeout_s
        self._stall_after = max(1, int(stall_after))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._misses = 0
        self._alerted = False
        #: doctor 可读:{"ok": bool, "misses": int, "last_ping_ts": float}
        self.last_status: dict = {"ok": True, "misses": 0, "last_ping_ts": 0.0}

    # ---- 生命周期 ----
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="karvyloop-loop-watchdog",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ---- 心跳 ----
    async def _noop(self) -> bool:
        return True

    def _ping_once(self) -> bool:
        """向循环投一个 noop,timeout 内回来 = 活着。循环已 close → 视为停机(不报堵死)。"""
        if self._loop.is_closed():
            self._stop.set()
            return True
        try:
            fut = asyncio.run_coroutine_threadsafe(self._noop(), self._loop)
        except RuntimeError:          # loop 正在关闭
            self._stop.set()
            return True
        try:
            return bool(fut.result(timeout=self._timeout))
        except TimeoutError:
            fut.cancel()
            return False
        except Exception:             # loop 关闭竞态等 → 不当堵死
            return True

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            ok = self._ping_once()
            self.last_status = {"ok": ok, "misses": self._misses,
                                "last_ping_ts": time.time()}
            if ok:
                if self._alerted:
                    logger.error("[loop-watchdog] 事件循环已恢复响应。")
                self._misses = 0
                self._alerted = False
                continue
            self._misses += 1
            if self._misses >= self._stall_after and not self._alerted:
                self._alerted = True   # 报一次即冷却,恢复后才可能再报
                logger.error(
                    "[loop-watchdog] 事件循环已 ~%.0fs 无响应 —— 界面/任务看门狗可能全部冻结。"
                    "多半是某个同步慢调用堵住了主循环(该进线程的没进线程)。"
                    "重启 console 可恢复;请把此日志上下文报给我们定位堵点。",
                    self._misses * (self._interval + self._timeout))


def start_loop_watchdog(app) -> Optional[LoopWatchdog]:
    """console lifespan 里调:为当前运行循环起二层心跳,挂 app.state 供 doctor/停机。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    wd = LoopWatchdog(loop)
    wd.start()
    app.state.loop_watchdog = wd
    return wd


__all__ = ["LoopWatchdog", "start_loop_watchdog",
           "PING_INTERVAL_S", "PING_TIMEOUT_S", "STALL_AFTER_MISSES"]
